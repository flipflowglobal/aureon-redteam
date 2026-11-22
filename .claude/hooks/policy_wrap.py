#!/usr/bin/env python3
"""Authorization policy wrap for Claude Code.

A ``PreToolUse`` hook that gates ``Bash`` tool calls against the engagement
authorization model defined in ``engagements/README.md`` and
``engagements/engagement.example.yaml``. The wrap is **deny-by-default** for
active offensive / network-egress operations:

* Commands that are not offensive network operations are deferred to Claude
  Code's normal permission flow (the hook emits nothing).
* Offensive commands against loopback / RFC 5737 documentation ranges are
  treated as local-testbed traffic and allowed (DoS classes still denied).
* Offensive commands against any other target require an engagement record
  that is ``status: active``, carries both internal and owner approval, is
  inside its ``valid_from``/``valid_until`` window, enables network access,
  and explicitly lists the target. Absent that, the command is denied.
* Forbidden classes (denial-of-service, blanket scope ``0.0.0.0/0`` / ``::/0``)
  are denied unconditionally and cannot be enabled by any record.

Commands are analysed together with every command they run indirectly: the
argument of a shell's ``-c`` option, ``eval`` arguments, and ``$(...)``,
``<(...)``, ``>(...)`` and backtick substitutions, to a fixed nesting depth.
Deeper nesting is denied.

The hook reads the PreToolUse event JSON on stdin and writes a Claude Code
``hookSpecificOutput`` decision on stdout. It never fabricates authorization:
when it cannot prove a command is in scope, it denies. A malformed event, an
unreadable or malformed engagement record, and any internal fault all resolve
to a denial; a bad record only disables itself, never the other records.

Exit status: always 0. The decision is carried in the JSON payload, so an
internal error still blocks the tool call instead of letting it through.

Cost: classifying a command of n characters takes O(n * d) time for nesting
depth d <= ``MAX_NESTING``; loading k engagement records takes O(total bytes),
each record capped at ``MAX_RECORD_BYTES``.
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from typing import TextIO

LOGGER: Final = logging.getLogger("aureon.policy_wrap")

IPAddress: TypeAlias = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork: TypeAlias = ipaddress.IPv4Network | ipaddress.IPv6Network
Scalar: TypeAlias = str | bool | None
YamlValue: TypeAlias = Scalar | list["YamlValue"] | dict[str, "YamlValue"]
Record: TypeAlias = dict[str, YamlValue]
DecisionKind: TypeAlias = Literal["allow", "deny", "defer"]
CommandKind: TypeAlias = Literal["benign", "offensive", "forbidden"]
ScopeMode: TypeAlias = Literal["explicit", "local_network"]

HOOK_TAG: Final = "[aureon-redteam policy wrap]"
_TEMPLATE_RECORD: Final = "engagement.example.yaml"
_RECORD_SUFFIXES: Final = (".yaml", ".yml")
MAX_RECORD_BYTES: Final = 64 * 1024
MAX_RECORD_NESTING: Final = 32
MAX_NESTING: Final = 8
_IP_COMMAND_TIMEOUT_SECONDS: Final = 3
_PROBE_PREFIX: Final = "/24"
PROBE_ADDRESS: Final = ("192.168.0.1", 9)
ERROR_ID_HEX_DIGITS: Final = 12

# --- engagement record parsing ------------------------------------------------

# Documentation / test ranges that never require a live engagement record.
_LOCAL_NETS: Final[tuple[IPNetwork, ...]] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("203.0.113.0/24"),  # RFC 5737 TEST-NET-3
    ipaddress.ip_network("198.51.100.0/24"),  # RFC 5737 TEST-NET-2
    ipaddress.ip_network("192.0.2.0/24"),  # RFC 5737 TEST-NET-1
)
# "0.0.0.0" is a destination that resolves to this host, not a bind address.
_LOCAL_HOSTNAMES: Final = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})  # noqa: S104 - destination name, not a bind
_LOCAL_DOMAIN_SUFFIXES: Final = (
    ".example.com",
    ".example.net",
    ".example.org",
    ".localhost",
)

# Private / non-routable address space. A ``local_network`` engagement may only
# authorize targets inside these ranges AND inside one of the host's own
# interface subnets — never public, routable addresses.
_PRIVATE_NETS: Final[tuple[IPNetwork, ...]] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("fc00::/7"),  # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
)

# Offensive / active-network tooling that the policy gates.
_OFFENSIVE_BINS: Final = frozenset(
    {
        "nmap", "masscan", "zmap", "unicornscan", "rustscan",
        "sqlmap", "nikto", "wpscan", "gobuster", "ffuf", "dirb", "dirbuster",
        "nuclei", "amass", "subfinder", "httpx", "feroxbuster",
        "hydra", "medusa", "ncrack", "patator", "crackmapexec", "nxc",
        "msfconsole", "msfvenom", "responder", "bettercap", "ettercap",
        "aircrack-ng", "airodump-ng", "aireplay-ng", "bully", "reaver",
        "enum4linux", "smbclient", "onesixtyone", "snmpwalk",
    },
)  # fmt: skip
# Denial-of-service / flood tooling — never permitted, non-overridable.
_FORBIDDEN_BINS: Final = frozenset(
    {"t50", "slowloris", "slowhttptest", "mhddos", "goldeneye"},
)
_FLOOD_FLAGS: Final = ("--flood", "--dos")
_BLANKET_SCOPES: Final = ("0.0.0.0/0", "::/0")

# Generic egress tools: only offensive when aimed off-box.
_EGRESS_BINS: Final = frozenset(
    {"curl", "wget", "nc", "ncat", "netcat", "socat", "telnet"},
)
# Shells whose ``-c`` argument is itself a command line.
_SHELL_BINS: Final = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash"})
_EVAL_BIN: Final = "eval"

_URL_RE: Final = re.compile(r"https?://([^/\s:]+)", re.IGNORECASE)
_HOSTISH_RE: Final = re.compile(r"^(?:[A-Za-z0-9_-]+\.)+[A-Za-z]{2,}$")
_KEYLIKE_RE: Final = re.compile(r"^[A-Za-z0-9_.-]+:(\s|$)")
_SEPARATOR_RE: Final = re.compile(r"\|\||&&|;|\||\n")
_SHELL_COMMAND_FLAG_RE: Final = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")
_LEADING_WRAPPERS_RE: Final = re.compile(r"^(?:\$\(|[<>]\(|\(|\{|`)+")
_TRAILING_WRAPPERS_RE: Final = re.compile(r"(?:\)|\}|`)+$")
_SUBSTITUTION_OPENERS: Final = ("$(", "<(", ">(")
_SUBSTITUTION_OPENER_LENGTH: Final = 2
_SUBNET_SPLIT_RE: Final = re.compile(r"[,\s]+")
_INET_RE: Final = re.compile(r"\binet6?\s+([0-9A-Fa-f:.]+/\d+)")
_WILDCARD_TARGETS: Final = frozenset({"0.0.0.0/0", "::/0", "*", "0/0"})
_LOCAL_MODE_NAMES: Final = frozenset({"local_network", "local-network", "lan"})


class RecordError(ValueError):
    """An engagement record cannot be parsed."""


class MalformedEventError(ValueError):
    """The PreToolUse event is not a well-formed Bash tool call."""


class NestingLimitError(ValueError):
    """A command nests other commands deeper than the analysis limit."""


@dataclass(frozen=True, slots=True)
class Decision:
    """A permission decision and the reason shown to the operator."""

    kind: DecisionKind
    reason: str


@dataclass(frozen=True, slots=True)
class Classification:
    """How a command line is classified, with the network targets it names."""

    kind: CommandKind
    targets: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class Engagement:
    """One engagement record, validated at load time.

    ``rejection`` is ``None`` when the record currently authorizes testing and
    otherwise states why it does not. A rejected record authorizes nothing.
    """

    source: str
    mode: ScopeMode
    targets: tuple[str, ...]
    excluded: tuple[str, ...]
    rejection: str | None


@dataclass(frozen=True, slots=True)
class BashCall:
    """The command and project directory of a Bash PreToolUse event."""

    command: str
    project_dir: Path


def _strip_scalar(value: str) -> Scalar:
    value = value.strip()
    if not value or value == "~":
        return None
    if value[0] in "\"'" and value[-1:] == value[0]:
        value = value[1:-1]
    low = value.lower()
    if low in {"null", "none"}:
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    return value


def _clean_line(raw: str) -> str | None:
    """Strip comments outside quotes; return None for blank/comment-only lines."""
    line = raw.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    in_single = in_double = False
    cut = len(line)
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif (
            char == "#"
            and not in_single
            and not in_double
            and index > 0
            and line[index - 1] == " "
        ):
            cut = index
            break
    # The first non-space character is not '#', so the kept prefix is non-empty.
    return line[:cut].rstrip()


def _parse_flow_list(text: str) -> list[YamlValue]:
    """Parse ``[a, b]``; the caller guarantees the surrounding brackets."""
    return [_strip_scalar(part) for part in text[1:-1].split(",") if part.strip()]


def _parse_inline(text: str) -> YamlValue:
    if text.startswith("[") and text.endswith("]"):
        return _parse_flow_list(text)
    return _strip_scalar(text)


def _is_sequence_item(content: str) -> bool:
    return content.startswith("- ") or content == "-"


@dataclass(slots=True)
class _RecordParser:
    """Indentation-driven parser for the engagement-record YAML subset.

    Supports block mappings, nested mappings by indentation, block sequences
    (``- scalar`` and ``- key: value`` records, including multi-key records),
    quoted scalars, inline flow lists (``[a, b]``), comments, and null markers.
    This is exactly the shape the engagement schema uses; it is a focused,
    deterministic parser for that schema, not a general YAML implementation.
    Every line is consumed once, so parsing is O(lines); nesting deeper than
    ``MAX_RECORD_NESTING`` levels raises ``RecordError``.
    """

    lines: list[tuple[int, str]]
    position: int = 0
    depth: int = field(default=0)

    def parse(self) -> Record:
        """Return the record's top-level mapping.

        Returns:
            The parsed mapping; a top-level sequence is returned under
            ``__root__``, and an empty document is an empty mapping.

        Raises:
            RecordError: The record nests deeper than the parser allows.
        """
        if not self.lines:
            return {}
        indent, content = self.lines[0]
        parsed = self._block(indent, content)
        return parsed if isinstance(parsed, dict) else {"__root__": parsed}

    def _peek(self) -> tuple[int, str] | None:
        return self.lines[self.position] if self.position < len(self.lines) else None

    def _block(self, indent: int, content: str) -> YamlValue:
        if _is_sequence_item(content):
            return self._sequence(indent)
        return self._mapping(indent)

    def _child(self, indent: int) -> YamlValue:
        # The value is a nested block iff the next line is indented deeper, or
        # is a sequence at the same indent (YAML permits a dash at the key's
        # column).
        line = self._peek()
        if line is None:
            return None
        next_indent, next_content = line
        if next_indent > indent:
            return self._nested(lambda: self._block(next_indent, next_content))
        if next_indent == indent and _is_sequence_item(next_content):
            return self._nested(lambda: self._sequence(next_indent))
        return None

    def _nested(self, parse_block: Callable[[], YamlValue]) -> YamlValue:
        if self.depth >= MAX_RECORD_NESTING:
            message = f"record nests deeper than {MAX_RECORD_NESTING} levels"
            raise RecordError(message)
        self.depth += 1
        try:
            return parse_block()
        finally:
            self.depth -= 1

    def _value(self, text: str, indent: int) -> YamlValue:
        stripped = text.strip()
        return _parse_inline(stripped) if stripped else self._child(indent)

    def _mapping(self, indent: int) -> Record:
        result: Record = {}
        while (line := self._peek()) is not None:
            current_indent, content = line
            if current_indent != indent or content.startswith("- "):
                break
            self.position += 1
            key, _, rest = content.partition(":")
            result[key.strip()] = self._value(rest, current_indent)
        return result

    def _sequence(self, indent: int) -> list[YamlValue]:
        result: list[YamlValue] = []
        while (line := self._peek()) is not None:
            current_indent, content = line
            if current_indent != indent or not _is_sequence_item(content):
                break
            self.position += 1
            body = content[2:].strip() if content.startswith("- ") else ""
            if not body:
                result.append(self._child(current_indent))
            elif _KEYLIKE_RE.match(body):
                result.append(self._sequence_record(body, current_indent + 2))
            else:
                result.append(_parse_inline(body))
        return result

    def _sequence_record(self, first_line: str, item_indent: int) -> Record:
        node: Record = {}
        key, _, value = first_line.partition(":")
        node[key.strip()] = self._value(value, item_indent)
        # Additional keys of this record share item_indent.
        while (line := self._peek()) is not None:
            next_indent, next_content = line
            if next_indent != item_indent or next_content.startswith("- "):
                break
            self.position += 1
            next_key, _, next_value = next_content.partition(":")
            node[next_key.strip()] = self._value(next_value, item_indent)
        return node


def parse_engagement(text: str) -> Record:
    """Parse engagement-record text into nested mappings, lists and scalars.

    Args:
        text: The record's YAML text (the engagement-schema subset).

    Returns:
        The record's top-level mapping.

    Raises:
        RecordError: The record nests deeper than ``MAX_RECORD_NESTING``.
    """
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        cleaned = _clean_line(raw)
        if cleaned is None:
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        lines.append((indent, cleaned.strip()))
    return _RecordParser(lines).parse()


# --- authorization evaluation -------------------------------------------------


def now_utc() -> _dt.datetime:
    """Return the current UTC time; tests replace it to pin the clock."""
    return _dt.datetime.now(_dt.timezone.utc)


def _mapping(value: YamlValue) -> Record:
    # Absent or malformed sub-mappings read as empty, which can only deny.
    return value if isinstance(value, dict) else {}


def _parse_timestamp(value: YamlValue) -> _dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A naive timestamp cannot be ordered against the UTC clock.
    return parsed if parsed.tzinfo is not None else None


def _is_wildcard_target(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        lowered in _WILDCARD_TARGETS
        or lowered.startswith("*.")
        or "all" in lowered.split()
    )


def _scope_values(record: Record, key: str) -> tuple[tuple[str, ...], str | None]:
    """Return the ``value`` strings of a scope list, or why the list is malformed.

    A malformed ``excluded_targets`` list must never be read as "no exclusions",
    so any shape other than a list of mappings with a non-empty string
    ``value`` rejects the whole record.
    """
    raw = record.get(key)
    if raw is None:
        return (), None
    if not isinstance(raw, list):
        return (), f"record `{key}` is not a list"
    values: list[str] = []
    for entry in raw:
        value = entry.get("value") if isinstance(entry, dict) else None
        if not isinstance(value, str) or not value.strip():
            return (), f"record `{key}` has an entry without a string `value`"
        values.append(value.strip())
    return tuple(values), None


def _approval_failure(record: Record) -> str | None:
    approvals = _mapping(record.get("approvals"))
    internal = _mapping(approvals.get("internal")).get("approved") is True
    owner = _mapping(approvals.get("owner")).get("approved") is True
    if not (internal and owner):
        return "record lacks both internal and owner approval"
    return None


def _window_failure(record: Record, now: _dt.datetime) -> str | None:
    window = _mapping(record.get("time_window"))
    start = _parse_timestamp(window.get("valid_from"))
    end = _parse_timestamp(window.get("valid_until"))
    if start is None or end is None:
        return "record has no valid timezone-aware time window"
    if not (start <= now <= end):
        return "record is outside its valid_from/valid_until window"
    return None


def _network_failure(record: Record) -> str | None:
    network = _mapping(record.get("network_access"))
    if network.get("enabled") is not True:
        return "record does not enable network access"
    if network.get("allowed_targets_only") is not True:
        return "record does not restrict to allowed targets"
    return None


def _authorization_failure(
    record: Record,
    targets: Sequence[str],
    now: _dt.datetime,
) -> str | None:
    if str(record.get("status", "")).lower() != "active":
        return "record status is not 'active'"
    failure = (
        _approval_failure(record)
        or _window_failure(record, now)
        or _network_failure(record)
    )
    if failure is not None:
        return failure
    if any(_is_wildcard_target(target) for target in targets):
        return "record contains a forbidden wildcard target"
    return None


def _record_mode(record: Record) -> ScopeMode:
    mode = str(record.get("scope_mode") or record.get("scope") or "explicit")
    return "local_network" if mode.strip().lower() in _LOCAL_MODE_NAMES else "explicit"


def _rejected(source: str, reason: str) -> Engagement:
    return Engagement(source, "explicit", (), (), reason)


def evaluate_record(source: str, record: Record, now: _dt.datetime) -> Engagement:
    """Validate one parsed engagement record at time ``now``.

    Args:
        source: The record's file name, used in decision reasons.
        record: The parsed record.
        now: The current time (timezone-aware).

    Returns:
        The engagement, with ``rejection`` set when it authorizes nothing.
    """
    targets, target_error = _scope_values(record, "targets")
    excluded, excluded_error = _scope_values(record, "excluded_targets")
    malformed = target_error or excluded_error
    if malformed is not None:
        return _rejected(source, malformed)
    return Engagement(
        source=source,
        mode=_record_mode(record),
        targets=targets,
        excluded=excluded,
        rejection=_authorization_failure(record, targets, now),
    )


def load_engagement(path: Path, now: _dt.datetime) -> Engagement:
    """Load one record; any read, size, decode or parse failure rejects it."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        return _rejected(path.name, f"unreadable record ({error.strerror or error})")
    if len(raw) > MAX_RECORD_BYTES:
        return _rejected(path.name, f"record exceeds {MAX_RECORD_BYTES} bytes")
    try:
        record = parse_engagement(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return _rejected(path.name, "record is not valid UTF-8")
    except RecordError as error:
        return _rejected(path.name, str(error))
    return evaluate_record(path.name, record, now)


def load_engagements(project_dir: Path, now: _dt.datetime) -> list[Engagement]:
    """Load every engagement record under ``project_dir/engagements``.

    The template ``engagement.example.yaml`` is skipped: it authorizes nothing.
    An unreadable or malformed record is returned as a rejected engagement, so
    it is reported in denials and never disables the other records.

    Args:
        project_dir: The project root.
        now: The current time (timezone-aware).

    Returns:
        The engagements in file-name order.
    """
    directory = project_dir / "engagements"
    if not directory.is_dir():
        return []
    return [
        load_engagement(path, now)
        for path in sorted(directory.iterdir())
        if path.suffix in _RECORD_SUFFIXES
        and path.name != _TEMPLATE_RECORD
        and path.is_file()
    ]


# --- address helpers ------------------------------------------------------------


def _as_ip(value: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def as_network(value: str) -> IPNetwork | None:
    """Parse ``value`` as an IP network, or return ``None`` when it is not one.

    Non-network scope values (domain names, empty strings) resolve to ``None``
    so callers fall through to domain comparison instead of raising.
    """
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None


def _domain_match(target: str, scope: str) -> bool:
    host = target.lower().rstrip(".")
    scope = scope.lower().rstrip(".")
    return host == scope or host.endswith("." + scope)


def in_scope(target: str, scope_value: str) -> bool:
    """True iff the whole target lies inside ``scope_value``.

    An address or CIDR target must be a subnet of a network scope, so a sweep
    wider than the authorized range is never authorized by its first address.
    Otherwise the target is compared as a domain: exact match or subdomain.
    """
    scope_value = scope_value.strip()
    if not scope_value:
        return False
    target_network = as_network(target)
    scope_network = as_network(scope_value)
    if target_network is not None and scope_network is not None:
        return _within(target_network, scope_network)
    return _domain_match(target, scope_value)


def is_excluded(target: str, excluded_value: str) -> bool:
    """True iff the target's address or name matches an exclusion.

    For a CIDR target the address before the ``/`` is compared, so a sweep is
    excluded when its base address is. Whether a sweep that merely contains an
    excluded host should be refused is an open policy decision for the owner.
    """
    return in_scope(target.split("/", 1)[0], excluded_value)


def is_local_target(target: str) -> bool:
    """True iff the target is loopback, a documentation range or a reserved name.

    A CIDR target is local only when the whole range is inside one of them.
    """
    lowered = target.lower()
    if lowered in _LOCAL_HOSTNAMES or lowered.endswith(_LOCAL_DOMAIN_SUFFIXES):
        return True
    network = as_network(target)
    return network is not None and any(_within(network, local) for local in _LOCAL_NETS)


def _within(inner: IPNetwork, outer: IPNetwork) -> bool:
    """True iff ``inner`` is a subnet of ``outer``; mixed IP versions never are."""
    if isinstance(inner, ipaddress.IPv4Network) and isinstance(
        outer,
        ipaddress.IPv4Network,
    ):
        return inner.subnet_of(outer)
    if isinstance(inner, ipaddress.IPv6Network) and isinstance(
        outer,
        ipaddress.IPv6Network,
    ):
        return inner.subnet_of(outer)
    return False


def _is_private_net(network: IPNetwork) -> bool:
    return any(_within(network, private) for private in _PRIVATE_NETS)


def _probe_primary_ipv4() -> str | None:
    """Return the host's primary IPv4 source address, or ``None`` on failure.

    A connected UDP socket selects a source address from the routing table
    without sending any packets. Any socket error resolves to ``None`` so the
    caller leaves its subnet list empty and fails closed.
    """
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        probe.connect(PROBE_ADDRESS)
        return str(probe.getsockname()[0])
    except OSError:
        return None
    finally:
        probe.close()


def interface_networks() -> list[IPNetwork]:
    """Return the networks of the host's interfaces, read from ``ip -o addr show``."""
    ip_binary = shutil.which("ip")
    if ip_binary is None:
        return []
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv; binary resolved by shutil.which
            [ip_binary, "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=_IP_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    networks: list[IPNetwork] = []
    for line in completed.stdout.splitlines():
        match = _INET_RE.search(line)
        network = as_network(match.group(1)) if match else None
        if network is not None:
            networks.append(network)
    return networks


def probed_networks() -> list[IPNetwork]:
    """Return the ``/24`` around the host's primary IPv4 address, if it can be found."""
    host_ip = _probe_primary_ipv4()
    probed = as_network(host_ip + _PROBE_PREFIX) if host_ip is not None else None
    return [probed] if probed is not None else []


def local_subnets() -> list[IPNetwork]:
    """Return the private subnets the host is directly attached to.

    Resolution order: an explicit ``AUREON_LOCAL_SUBNETS`` override (comma or
    space separated CIDRs), then the kernel routing/interface table via ``ip``,
    then a best-effort primary-IPv4 probe assuming a ``/24``. Only private,
    non host-only subnets are returned; anything else is dropped so a
    ``local_network`` record can never reach routable space. Detection failing
    yields an empty list, i.e. the record authorizes nothing (fail closed).

    Returns:
        The private, multi-host subnets in discovery order.
    """
    override = os.environ.get("AUREON_LOCAL_SUBNETS")
    if override:
        candidates = [
            network
            for token in _SUBNET_SPLIT_RE.split(override.strip())
            if (network := as_network(token)) is not None
        ]
    else:
        candidates = interface_networks() or probed_networks()
    return [
        network
        for network in candidates
        if _is_private_net(network) and network.prefixlen < network.max_prefixlen
    ]


def target_in_local(target: str, subnets: Iterable[IPNetwork]) -> bool:
    """True iff ``target`` is a private address/CIDR inside a host subnet."""
    target_network = as_network(target)
    # Hostnames are not provably same-subnet: deny them in local mode.
    if target_network is None or not _is_private_net(target_network):
        return False
    return any(_within(target_network, subnet) for subnet in subnets)


# --- command classification ---------------------------------------------------


def _base(binary: str) -> str:
    return Path(binary).name.lower()


def _split_segment(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _normalize(token: str) -> str:
    """Strip subshell, group and substitution delimiters wrapped around a token."""
    return _TRAILING_WRAPPERS_RE.sub("", _LEADING_WRAPPERS_RE.sub("", token))


def _closing_paren(text: str, start: int) -> int:
    depth = 1
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return len(text)


def substitutions(command: str) -> list[str]:
    """Return the text inside each ``$(..)``, ``<(..)``, ``>(..)`` and backtick span."""
    payloads: list[str] = []
    index = 0
    while index < len(command):
        if command.startswith(_SUBSTITUTION_OPENERS, index):
            start = index + _SUBSTITUTION_OPENER_LENGTH
            end = _closing_paren(command, start)
        elif command[index] == "`":
            start = index + 1
            end = command.find("`", start)
            end = len(command) if end == -1 else end
        else:
            index += 1
            continue
        payloads.append(command[start:end])
        index = end + 1
    return [payload for payload in payloads if payload.strip()]


def _shell_command_argument(arguments: Sequence[str]) -> str | None:
    for index, argument in enumerate(arguments[:-1]):
        if _SHELL_COMMAND_FLAG_RE.match(argument):
            return arguments[index + 1]
    return None


def nested_payloads(tokens: Sequence[str]) -> list[str]:
    """Return command lines run by ``eval`` or a shell's ``-c`` option."""
    payloads: list[str] = []
    for index, token in enumerate(tokens):
        binary = _base(_normalize(token))
        if binary == _EVAL_BIN:
            payloads.append(" ".join(tokens[index + 1 :]))
        elif binary in _SHELL_BINS:
            payload = _shell_command_argument(tokens[index + 1 :])
            if payload is not None:
                payloads.append(payload)
    return payloads


@dataclass(slots=True)
class CommandAnalyzer:
    """Collects the simple commands of a command line and of every nested one.

    Identical nested payloads are analysed once, so the work is bounded by the
    distinct substrings visited: O(n * MAX_NESTING) for n characters.
    """

    seen: set[str] = field(default_factory=set)
    commands: list[list[str]] = field(default_factory=list)

    def analyze(self, command: str, depth: int = 0) -> list[list[str]]:
        """Return the normalized tokens of each simple command, nested ones included.

        Raises:
            NestingLimitError: Nesting is deeper than ``MAX_NESTING``.
        """
        if depth > MAX_NESTING:
            message = f"command nesting exceeds {MAX_NESTING} levels of analysis"
            raise NestingLimitError(message)
        nested: list[str] = []
        for segment in _SEPARATOR_RE.split(command):
            if not segment.strip():
                continue
            tokens = _split_segment(segment.strip())
            self.commands.append([_normalize(token) for token in tokens])
            nested.extend(nested_payloads(tokens))
        nested.extend(substitutions(command))
        for payload in nested:
            if payload not in self.seen:
                self.seen.add(payload)
                self.analyze(payload, depth + 1)
        return self.commands


def _token_target(token: str) -> str | None:
    """Return the network target a token names, or ``None``.

    Address literals (IPv4 and IPv6) and CIDRs are kept whole, so a CIDR is
    checked over its full width rather than by its first address.
    """
    url = _URL_RE.search(token)
    if url:
        return url.group(1)
    if _as_ip(token) is not None or ("/" in token and as_network(token)):
        return token
    host = token.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if _as_ip(host) is not None or _HOSTISH_RE.match(host):
        return host
    if "/" in token and _as_ip(token.split("/", 1)[0]) is not None:
        return token
    return None


def _extract_targets(tokens: Iterable[str]) -> tuple[str, ...]:
    targets = [
        target
        for token in tokens
        if not token.startswith("-") and (target := _token_target(token)) is not None
    ]
    # De-duplicate, preserving first-seen order.
    return tuple(dict.fromkeys(targets))


def _forbidden_reason(command: str, binaries: set[str]) -> str | None:
    if binaries & _FORBIDDEN_BINS or any(flag in command for flag in _FLOOD_FLAGS):
        return "denial-of-service tooling"
    if any(scope in command for scope in _BLANKET_SCOPES):
        return "blanket/wildcard network scope"
    return None


def _classify_tokens(command: str, tokens: Sequence[str]) -> Classification:
    binaries = {_base(token) for token in tokens if not token.startswith("-")}
    targets = _extract_targets(tokens)
    forbidden = _forbidden_reason(command, binaries)
    if forbidden is not None:
        return Classification("forbidden", targets, forbidden)
    if binaries & _OFFENSIVE_BINS:
        return Classification("offensive", targets, "offensive network tool")
    if not binaries & _EGRESS_BINS:
        return Classification("benign", (), "non-offensive command")
    if any(not is_local_target(target) for target in targets):
        return Classification("offensive", targets, "network egress to off-box host")
    return Classification("benign", targets, "local egress only")


def classify(command: str) -> Classification:
    """Classify a command line as benign, offensive or forbidden.

    Args:
        command: The full Bash command line.

    Returns:
        The classification, its network targets and the reason.
    """
    try:
        commands = CommandAnalyzer().analyze(command)
    except NestingLimitError as error:
        return Classification("forbidden", (), str(error))
    tokens = [token for simple in commands for token in simple]
    if not tokens:
        return Classification("benign", (), "empty command")
    return _classify_tokens(command, tokens)


# --- decision -----------------------------------------------------------------


def _excluded_by(target: str, engagements: Iterable[Engagement]) -> bool:
    return any(
        is_excluded(target, excluded)
        for engagement in engagements
        for excluded in engagement.excluded
    )


def _in_explicit_scope(target: str, engagement: Engagement) -> bool:
    if _excluded_by(target, (engagement,)):
        return False
    return any(in_scope(target, scope) for scope in engagement.targets)


def _out_of_scope(
    target: str,
    authorized: Sequence[Engagement],
    subnets: Sequence[IPNetwork],
    *,
    local_mode: bool,
) -> Decision:
    sources = ", ".join(engagement.source for engagement in authorized)
    hint = ""
    if local_mode:
        detected = ", ".join(str(subnet) for subnet in subnets) or "none detected"
        hint = f" It is not on the host's local subnet ({detected})."
    return Decision(
        "deny",
        f"Out of scope: target '{target}' is not authorized by any active engagement "
        f"record ({sources}).{hint} Add it to `targets` and re-approve, or exclude it.",
    )


def _decide_scope(remote: Sequence[str], authorized: Sequence[Engagement]) -> Decision:
    explicit = [e for e in authorized if e.mode == "explicit"]
    local = [e for e in authorized if e.mode == "local_network"]
    subnets = local_subnets() if local else []
    for target in remote:
        by_explicit = any(_in_explicit_scope(target, e) for e in explicit)
        # A local_network record never overrides an explicit exclusion.
        by_local = (
            bool(local)
            and not _excluded_by(target, local)
            and target_in_local(target, subnets)
        )
        if not (by_explicit or by_local):
            return _out_of_scope(target, authorized, subnets, local_mode=bool(local))
    sources = ", ".join(engagement.source for engagement in authorized)
    return Decision(
        "allow",
        f"Target(s) {', '.join(remote)} authorized by active engagement "
        f"record(s): {sources}.",
    )


def _decide_offensive(targets: Sequence[str], project_dir: Path) -> Decision:
    local = [target for target in targets if is_local_target(target)]
    remote = [target for target in targets if not is_local_target(target)]
    if targets and not remote:
        return Decision(
            "allow",
            f"Offensive tool against local/documentation target(s) "
            f"{', '.join(local)} — permitted testbed scope.",
        )
    engagements = load_engagements(project_dir, now_utc())
    if not engagements:
        return Decision(
            "deny",
            "Deny-by-default: offensive network operation with no active engagement "
            "record under engagements/. Add an approved, active record authorizing "
            f"target(s): {', '.join(remote) or 'unspecified'}.",
        )
    authorized = [e for e in engagements if e.rejection is None]
    if not authorized:
        rejections = "; ".join(f"{e.source}: {e.rejection}" for e in engagements)
        return Decision(
            "deny",
            "Deny-by-default: no engagement record is currently authorizing. "
            + rejections,
        )
    if not remote:
        return Decision(
            "allow",
            "Authorized engagement active; command has no explicit off-box target.",
        )
    return _decide_scope(remote, authorized)


def decide(command: str, project_dir: Path) -> Decision:
    """Decide whether a Bash command may run.

    Args:
        command: The full Bash command line.
        project_dir: The project root holding ``engagements/``.

    Returns:
        ``defer`` for non-offensive commands, otherwise ``allow`` or ``deny``.
    """
    classification = classify(command)
    if classification.kind == "benign":
        return Decision("defer", classification.reason)
    if classification.kind == "forbidden":
        return Decision(
            "deny",
            f"Blocked non-overridable class: {classification.reason}. "
            "No engagement record can enable this.",
        )
    return _decide_offensive(classification.targets, project_dir)


# --- hook entry point ------------------------------------------------------------


def parse_event(raw: str) -> BashCall | None:
    """Parse a PreToolUse event.

    Args:
        raw: The event JSON read from stdin.

    Returns:
        The Bash call, or ``None`` for an event about another tool.

    Raises:
        MalformedEventError: The event is not a JSON object, or a Bash event
            has no string command.
    """
    try:
        event: object = json.loads(raw)
    except json.JSONDecodeError as error:
        message = "event is not valid JSON"
        raise MalformedEventError(message) from error
    if not isinstance(event, dict):
        message = "event is not a JSON object"
        raise MalformedEventError(message)
    if event.get("tool_name") != "Bash":
        return None
    tool_input: object = event.get("tool_input")
    command: object = (
        tool_input.get("command") if isinstance(tool_input, dict) else None
    )
    if not isinstance(command, str):
        message = "Bash event has no string tool_input.command"
        raise MalformedEventError(message)
    cwd: object = event.get("cwd")
    project = (
        cwd
        if isinstance(cwd, str) and cwd
        else os.environ.get("CLAUDE_PROJECT_DIR") or str(Path.cwd())
    )
    return BashCall(command=command, project_dir=Path(project))


def decide_event(raw: str) -> Decision:
    """Return the decision for one raw PreToolUse event.

    Args:
        raw: The event JSON read from stdin.

    Returns:
        ``defer`` for other tools; a denial for a malformed event.
    """
    try:
        call = parse_event(raw)
    except MalformedEventError as error:
        return Decision("deny", f"Malformed PreToolUse event, failing closed: {error}.")
    if call is None:
        return Decision("defer", "not a Bash tool call")
    return decide(call.command, call.project_dir)


def _emit(sink: TextIO, decision: Decision) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision.kind,
            "permissionDecisionReason": f"{HOOK_TAG} {decision.reason}",
        },
    }
    sink.write(json.dumps(payload) + "\n")


def main(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run the hook: read one event, write a decision unless it defers.

    Args:
        stdin: Event source; ``None`` reads ``sys.stdin``.
        stdout: Decision sink; ``None`` writes ``sys.stdout``.

    Returns:
        Always 0; denials, including internal faults, are carried in the JSON.
    """
    source = sys.stdin if stdin is None else stdin
    sink = sys.stdout if stdout is None else stdout
    try:
        decision = decide_event(source.read())
    # Process boundary: any internal fault must deny the call, never allow it.
    except Exception as error:
        error_id = uuid.uuid4().hex[:ERROR_ID_HEX_DIGITS]
        LOGGER.exception("policy_wrap internal error %s", error_id)
        decision = Decision(
            "deny",
            f"policy_wrap internal error {error_id}, failing closed: {error!r}",
        )
    if decision.kind != "defer":
        _emit(sink, decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
