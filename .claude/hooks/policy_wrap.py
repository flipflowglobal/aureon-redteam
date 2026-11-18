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

The hook reads the PreToolUse event JSON on stdin and prints a Claude Code
``hookSpecificOutput`` decision on stdout. It never fabricates authorization:
when it cannot prove a command is in scope, it denies.

Exit codes: 0 always (the decision is carried in the JSON payload); a hard
internal error prints a deny decision and still exits 0 so the tool is blocked
rather than silently allowed.
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import os
import re
import shlex
import sys
from typing import Any

# --- engagement record parsing ------------------------------------------------

# Documentation / test ranges that never require a live engagement record.
_LOCAL_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("203.0.113.0/24"),  # RFC 5737 TEST-NET-3
    ipaddress.ip_network("198.51.100.0/24"),  # RFC 5737 TEST-NET-2
    ipaddress.ip_network("192.0.2.0/24"),  # RFC 5737 TEST-NET-1
]
_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_LOCAL_DOMAIN_SUFFIXES = (".example.com", ".example.net", ".example.org", ".localhost")

# Offensive / active-network tooling that the policy gates.
_OFFENSIVE_BINS = {
    "nmap", "masscan", "zmap", "unicornscan", "rustscan",
    "sqlmap", "nikto", "wpscan", "gobuster", "ffuf", "dirb", "dirbuster",
    "nuclei", "amass", "subfinder", "httpx", "feroxbuster",
    "hydra", "medusa", "ncrack", "patator", "crackmapexec", "nxc",
    "msfconsole", "msfvenom", "responder", "bettercap", "ettercap",
    "aircrack-ng", "airodump-ng", "aireplay-ng", "bully", "reaver",
    "enum4linux", "smbclient", "onesixtyone", "snmpwalk",
}
# Denial-of-service / flood tooling — never permitted, non-overridable.
_FORBIDDEN_BINS = {"t50", "slowloris", "slowhttptest", "mhddos", "goldeneye"}
_FLOOD_FLAGS = ("--flood", "--dos")

# Generic egress tools: only offensive when aimed off-box.
_EGRESS_BINS = {"curl", "wget", "nc", "ncat", "netcat", "socat", "telnet"}

_URL_RE = re.compile(r"https?://([^/\s:]+)", re.IGNORECASE)
_HOSTISH_RE = re.compile(r"^(?:[A-Za-z0-9_-]+\.)+[A-Za-z]{2,}$")


def _strip_scalar(value: str) -> Any:
    value = value.strip()
    if not value or value == "~":
        return None
    if value[0] in "\"'" and value[-1:] == value[0]:
        value = value[1:-1]
    low = value.lower()
    if low in ("null", "none"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    return value


_KEYLIKE_RE = re.compile(r"^[A-Za-z0-9_.-]+:(\s|$)")


def _clean_line(raw: str) -> str | None:
    """Strip comments outside quotes; return None for blank/comment-only lines."""
    line = raw.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    in_s = in_d = False
    cut = len(line)
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d and i > 0 and line[i - 1] == " ":
            cut = i
            break
    line = line[:cut].rstrip()
    return line if line.strip() else None


def _parse_engagement(text: str) -> dict:
    """Parse the engagement-record YAML subset into nested dict/list structures.

    Supports block mappings, nested mappings by indentation, block sequences
    (``- scalar`` and ``- key: value`` records, including multi-key records),
    quoted scalars, inline flow lists (``[a, b]``), comments, and null markers.
    This is exactly the shape the engagement schema uses; it is a focused,
    deterministic parser for that schema, not a general YAML implementation.
    """
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        cleaned = _clean_line(raw)
        if cleaned is None:
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        lines.append((indent, cleaned.strip()))

    idx = [0]

    def parse_block(min_indent: int) -> Any:
        if idx[0] >= len(lines):
            return {}
        first_indent, first_content = lines[idx[0]]
        if first_content.startswith("- ") or first_content == "-":
            return parse_sequence(first_indent)
        return parse_mapping(first_indent)

    def _child_value(cur_indent: int) -> Any:
        # Value is a nested block iff the next line is indented deeper, or is a
        # sequence at the same indent (YAML permits a dash at the key's column).
        if idx[0] < len(lines):
            ni, nc = lines[idx[0]]
            if ni > cur_indent:
                return parse_block(ni)
            if ni == cur_indent and (nc.startswith("- ") or nc == "-"):
                return parse_sequence(ni)
        return None

    def parse_mapping(indent: int) -> dict:
        result: dict = {}
        while idx[0] < len(lines):
            ci, cc = lines[idx[0]]
            if ci != indent or cc.startswith("- "):
                break
            idx[0] += 1
            key, _, rest = cc.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "":
                result[key] = _child_value(ci)
            else:
                result[key] = _parse_inline(rest)
        return result

    def parse_sequence(indent: int) -> list:
        result: list = []
        while idx[0] < len(lines):
            ci, cc = lines[idx[0]]
            if ci != indent or not (cc.startswith("- ") or cc == "-"):
                break
            idx[0] += 1
            body = cc[2:].strip() if cc.startswith("- ") else ""
            if body == "":
                result.append(_child_value(ci))
            elif _KEYLIKE_RE.match(body):
                node: dict = {}
                item_indent = ci + 2  # column where the body (first key) starts
                k, _, v = body.partition(":")
                node[k.strip()] = _parse_inline(v.strip()) if v.strip() else _child_value(item_indent)
                # Additional keys of this record share item_indent.
                while idx[0] < len(lines):
                    ni, nc = lines[idx[0]]
                    if ni != item_indent or nc.startswith("- "):
                        break
                    idx[0] += 1
                    k2, _, v2 = nc.partition(":")
                    node[k2.strip()] = _parse_inline(v2.strip()) if v2.strip() else _child_value(item_indent)
                result.append(node)
            else:
                result.append(_parse_inline(body))
        return result

    parsed = parse_block(lines[0][0]) if lines else {}
    return parsed if isinstance(parsed, dict) else {"__root__": parsed}


def _parse_flow_list(text: str) -> list:
    inner = text.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    return [_strip_scalar(p) for p in inner.split(",") if p.strip()]


def _parse_inline(text: str):
    if text.startswith("[") and text.endswith("]"):
        return _parse_flow_list(text)
    return _strip_scalar(text)


# --- authorization evaluation -------------------------------------------------


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse_ts(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_wildcard_target(value: str) -> bool:
    v = value.strip().lower()
    return v in ("0.0.0.0/0", "::/0", "*", "0/0") or v.startswith("*.") or "all" in v.split()


def _record_is_authorized(rec: dict) -> tuple[bool, str]:
    if str(rec.get("status", "")).lower() != "active":
        return False, "record status is not 'active'"
    approvals = rec.get("approvals") or {}
    internal = (approvals.get("internal") or {}).get("approved") is True
    owner = (approvals.get("owner") or {}).get("approved") is True
    if not (internal and owner):
        return False, "record lacks both internal and owner approval"
    window = rec.get("time_window") or {}
    start = _parse_ts(window.get("valid_from"))
    end = _parse_ts(window.get("valid_until"))
    now = _now_utc()
    if start is None or end is None:
        return False, "record has no valid time window"
    if not (start <= now <= end):
        return False, "record is outside its valid_from/valid_until window"
    net = rec.get("network_access") or {}
    if net.get("enabled") is not True:
        return False, "record does not enable network access"
    if net.get("allowed_targets_only") is not True:
        return False, "record does not restrict to allowed targets"
    for tgt in rec.get("targets") or []:
        if isinstance(tgt, dict) and _is_wildcard_target(str(tgt.get("value", ""))):
            return False, "record contains a forbidden wildcard target"
    return True, "authorized"


def _target_in_record(target: str, rec: dict) -> bool:
    excluded = rec.get("excluded_targets") or []
    for ex in excluded:
        if isinstance(ex, dict) and _host_matches(target, str(ex.get("value", ""))):
            return False
    for tgt in rec.get("targets") or []:
        if isinstance(tgt, dict) and _host_matches(target, str(tgt.get("value", ""))):
            return True
    return False


def _host_matches(target: str, scope_value: str) -> bool:
    scope_value = scope_value.strip()
    if not scope_value:
        return False
    tip = _as_ip(target)
    try:
        net = ipaddress.ip_network(scope_value, strict=False)
        if tip is not None:
            return tip in net
    except ValueError:
        pass
    # domain comparison (exact or subdomain of scope)
    t = target.lower().rstrip(".")
    s = scope_value.lower().rstrip(".")
    return t == s or t.endswith("." + s)


def _as_ip(value: str):
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_local_target(target: str) -> bool:
    if target.lower() in _LOCAL_HOSTNAMES:
        return True
    if any(target.lower().endswith(sfx) for sfx in _LOCAL_DOMAIN_SUFFIXES):
        return True
    if target.lower() == "localhost":
        return True
    tip = _as_ip(target)
    if tip is not None:
        for net in _LOCAL_NETS:
            if tip.version == net.version and tip in net:
                return True
    return False


# --- command classification ---------------------------------------------------


def _tokenize(command: str) -> list[str]:
    tokens: list[str] = []
    for part in re.split(r"(?:\|\||&&|;|\||\n)", command):
        part = part.strip()
        if not part:
            continue
        try:
            tokens.extend(shlex.split(part))
        except ValueError:
            tokens.extend(part.split())
    return tokens


def _base(binary: str) -> str:
    return os.path.basename(binary).lower()


def _extract_targets(tokens: list[str]) -> list[str]:
    targets: list[str] = []
    for tok in tokens:
        if tok.startswith("-"):
            continue
        m = _URL_RE.search(tok)
        if m:
            targets.append(m.group(1))
            continue
        host = tok.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        if _as_ip(host) is not None:
            targets.append(host)
        elif "/" in tok and _as_ip(tok.split("/", 1)[0]) is not None:
            targets.append(tok)  # CIDR
        elif _HOSTISH_RE.match(host):
            targets.append(host)
    # de-duplicate, preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def _classify(command: str) -> tuple[str, list[str], str]:
    """Return (kind, targets, reason). kind in {offensive, forbidden, benign}."""
    tokens = _tokenize(command)
    if not tokens:
        return "benign", [], "empty command"

    bins = {_base(t) for t in tokens if not t.startswith("-")}

    if bins & _FORBIDDEN_BINS or any(f in command for f in _FLOOD_FLAGS):
        return "forbidden", _extract_targets(tokens), "denial-of-service tooling"
    if "0.0.0.0/0" in command or "::/0" in command:
        return "forbidden", _extract_targets(tokens), "blanket/wildcard network scope"

    if bins & _OFFENSIVE_BINS:
        return "offensive", _extract_targets(tokens), "offensive network tool"

    if bins & _EGRESS_BINS:
        targets = _extract_targets(tokens)
        off_box = [t for t in targets if not _is_local_target(t)]
        if off_box:
            return "offensive", targets, "network egress to off-box host"
        return "benign", targets, "local egress only"

    return "benign", [], "non-offensive command"


# --- engagement loading -------------------------------------------------------


def _load_records(project_dir: str) -> list[dict]:
    records: list[dict] = []
    eng_dir = os.path.join(project_dir, "engagements")
    if not os.path.isdir(eng_dir):
        return records
    for name in sorted(os.listdir(eng_dir)):
        if not name.endswith((".yaml", ".yml")):
            continue
        if name == "engagement.example.yaml":
            continue  # explicit template authorizes nothing
        path = os.path.join(eng_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                rec = _parse_engagement(fh.read())
            rec["__source__"] = name
            records.append(rec)
        except OSError:
            continue
    return records


# --- decision -----------------------------------------------------------------


def _decide(command: str, project_dir: str) -> tuple[str, str]:
    kind, targets, reason = _classify(command)
    if kind == "benign":
        return "defer", reason
    if kind == "forbidden":
        return "deny", f"Blocked non-overridable class: {reason}. No engagement record can enable this."

    # offensive
    local = [t for t in targets if _is_local_target(t)]
    remote = [t for t in targets if not _is_local_target(t)]
    if targets and not remote:
        return "allow", f"Offensive tool against local/documentation target(s) {', '.join(local)} — permitted testbed scope."

    records = _load_records(project_dir)
    if not records:
        return "deny", (
            "Deny-by-default: offensive network operation with no active engagement "
            "record under engagements/. Add an approved, active record authorizing "
            f"target(s): {', '.join(remote) or 'unspecified'}."
        )

    authorized = []
    rejections = []
    for rec in records:
        ok, why = _record_is_authorized(rec)
        if ok:
            authorized.append(rec)
        else:
            rejections.append(f"{rec.get('__source__', '?')}: {why}")

    if not authorized:
        return "deny", (
            "Deny-by-default: no engagement record is currently authorizing. "
            + "; ".join(rejections)
        )

    if not remote:
        return "allow", "Authorized engagement active; command has no explicit off-box target."

    for tgt in remote:
        if not any(_target_in_record(tgt, rec) for rec in authorized):
            srcs = ", ".join(r.get("__source__", "?") for r in authorized)
            return "deny", (
                f"Out of scope: target '{tgt}' is not listed in any active engagement "
                f"record ({srcs}). Add it to `targets` and re-approve, or exclude it."
            )

    srcs = ", ".join(r.get("__source__", "?") for r in authorized)
    return "allow", f"Target(s) {', '.join(remote)} authorized by active engagement record(s): {srcs}."


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        event = {}

    tool_name = event.get("tool_name", "")
    if tool_name != "Bash":
        # Not a shell command — nothing for this wrap to gate.
        return 0

    command = (event.get("tool_input") or {}).get("command", "")
    project_dir = (
        event.get("cwd")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )

    try:
        decision, reason = _decide(command, project_dir)
    except Exception as exc:  # fail closed: deny on any internal fault
        decision, reason = "deny", f"policy_wrap internal error, failing closed: {exc!r}"

    if decision == "defer":
        return 0  # no output — Claude Code's normal permission flow applies

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,  # "allow" | "deny"
            "permissionDecisionReason": f"[aureon-redteam policy wrap] {reason}",
        }
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
