"""Tests for .claude/hooks/policy_wrap.py.

End-to-end cases drive the real hook entry point with a PreToolUse event and a
throwaway project directory holding test-only engagement records; no
production engagement file is created or trusted. The clock is pinned and
network discovery is replaced per test, so every case is deterministic.

Run: python3 -m pytest .claude/hooks/test_policy_wrap.py
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import io
import ipaddress
import json
import logging
import runpy
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import policy_wrap as pw
import pytest

HOOK = Path(__file__).with_name("policy_wrap.py")
REPO_EXAMPLE = Path(__file__).parents[2] / "engagements" / "engagement.example.yaml"
FIXED_NOW = dt.datetime(2026, 6, 15, 12, 0, tzinfo=dt.timezone.utc)
REAL_NOW_UTC = pw.now_utc
REAL_INTERFACE_NETWORKS = pw.interface_networks
REAL_PROBED_NETWORKS = pw.probed_networks

EXPLICIT_TARGETS = (
    "targets:\n"
    "  - type: cidr\n"
    '    value: "198.18.0.0/24"\n'
    "  - type: domain\n"
    '    value: "target.internal.test"\n'
)
EXPLICIT_EXCLUDED = 'excluded_targets:\n  - type: ip\n    value: "198.18.0.5"\n'
LAN_EXCLUDED = 'excluded_targets:\n  - type: ip\n    value: "192.168.50.1"\n'


@dataclasses.dataclass(frozen=True)
class RecordSpec:
    """Fields of a test engagement record; ``text`` renders it as YAML."""

    status: str = "active"
    approved: str = "true"
    network_enabled: str = "true"
    restrict_targets: str = "true"
    valid_from: str = "2026-01-01T00:00:00Z"
    valid_until: str = "2026-12-31T23:59:59Z"
    scope_line: str = ""
    targets: str = EXPLICIT_TARGETS
    excluded: str = EXPLICIT_EXCLUDED

    def text(self) -> str:
        return (
            "schema: 1\n"
            "engagement_id: ENG-T\n"
            f"status: {self.status}\n"
            f"{self.scope_line}"
            "approvals:\n"
            "  internal:\n"
            f"    approved: {self.approved}\n"
            "  owner:\n"
            f"    approved: {self.approved}\n"
            f"{self.targets}"
            f"{self.excluded}"
            "network_access:\n"
            f"  enabled: {self.network_enabled}\n"
            f"  allowed_targets_only: {self.restrict_targets}\n"
            "time_window:\n"
            f'  valid_from: "{self.valid_from}"\n'
            f'  valid_until: "{self.valid_until}"\n'
        )


EXPLICIT = RecordSpec()
LAN = RecordSpec(
    scope_line="scope_mode: local_network\n",
    targets="targets: []\n",
    excluded=LAN_EXCLUDED,
)


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the clock and disable host network discovery for every test."""
    monkeypatch.setattr(pw, "now_utc", lambda: FIXED_NOW)
    monkeypatch.setattr(pw, "interface_networks", list)
    monkeypatch.setattr(pw, "probed_networks", list)
    monkeypatch.delenv("AUREON_LOCAL_SUBNETS", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def project(tmp_path: Path, *records: tuple[str, str]) -> Path:
    """Create a project with the given (file name, YAML text) records."""
    engagements = tmp_path / "engagements"
    engagements.mkdir()
    for name, text in records:
        (engagements / name).write_text(text, encoding="utf-8")
    return tmp_path


def run_hook(raw: str) -> tuple[str, str]:
    """Run the hook on raw stdin text; return (decision, reason)."""
    stdout = io.StringIO()
    assert pw.main(io.StringIO(raw), stdout) == 0
    output = stdout.getvalue()
    if not output:
        return "defer", ""
    payload: object = json.loads(output)
    assert isinstance(payload, dict)
    inner = payload["hookSpecificOutput"]
    assert inner["hookEventName"] == "PreToolUse"
    return str(inner["permissionDecision"]), str(inner["permissionDecisionReason"])


def bash_event(command: str, cwd: Path) -> str:
    return json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)},
    )


def decision(command: str, cwd: Path) -> str:
    return run_hook(bash_event(command, cwd))[0]


# --- original behavioural cases (33) ----------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls -la", "defer"),
        ("git status", "defer"),
        ("curl http://127.0.0.1:8080/health", "defer"),
        ("nmap -sV 127.0.0.1", "allow"),
        ("nmap -sV 203.0.113.9", "allow"),
        ("nmap -sS scanme.nmap.org", "deny"),
        ("curl https://8.8.8.8/", "deny"),
        ("masscan 0.0.0.0/0 -p80", "deny"),
        ("hping3 --flood -S 198.18.0.9", "deny"),
        ("t50 198.18.0.9", "deny"),
    ],
)
def test_without_engagements(tmp_path: Path, command: str, expected: str) -> None:
    assert decision(command, tmp_path) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("nmap -sV 198.18.0.10", "allow"),
        ("sqlmap -u https://target.internal.test/i?id=1", "allow"),
        ("nmap -sV 198.18.0.5", "deny"),
        ("nmap 10.9.9.9", "deny"),
        ("masscan 0.0.0.0/0", "deny"),
    ],
)
def test_active_explicit_record(tmp_path: Path, command: str, expected: str) -> None:
    root = project(tmp_path, ("active.yaml", EXPLICIT.text()))
    assert decision(command, root) == expected


@pytest.mark.parametrize(
    "spec",
    [
        dataclasses.replace(EXPLICIT, approved="false"),
        dataclasses.replace(EXPLICIT, status="draft"),
        dataclasses.replace(
            EXPLICIT,
            valid_from="2026-01-01T00:00:00Z",
            valid_until="2026-01-02T00:00:00Z",
        ),
        dataclasses.replace(EXPLICIT, network_enabled="false"),
    ],
    ids=["unapproved", "draft", "expired", "network-disabled"],
)
def test_non_authorizing_record_denies(tmp_path: Path, spec: RecordSpec) -> None:
    root = project(tmp_path, ("r.yaml", spec.text()))
    assert decision("nmap -sV 198.18.0.10", root) == "deny"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("nmap -sV 192.168.50.10", "allow"),
        ("nmap -sn 192.168.50.0/24", "allow"),
        ("hydra -l a -p b 192.168.50.20 ssh", "allow"),
        ("nmap 192.168.50.1", "deny"),
        ("nmap 192.168.99.5", "deny"),
        ("nmap 10.0.0.9", "deny"),
        ("nmap 8.8.8.8", "deny"),
        ("curl https://example.org", "deny"),
        ("hping3 --flood 192.168.50.10", "deny"),
        ("masscan 0.0.0.0/0", "deny"),
    ],
)
def test_local_network_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    expected: str,
) -> None:
    monkeypatch.setenv("AUREON_LOCAL_SUBNETS", "192.168.50.0/24")
    root = project(tmp_path, ("lan.yaml", LAN.text()))
    assert decision(command, root) == expected


def test_local_network_unapproved_record_denies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUREON_LOCAL_SUBNETS", "192.168.50.0/24")
    root = project(
        tmp_path, ("lan.yaml", dataclasses.replace(LAN, approved="false").text())
    )
    assert decision("nmap 192.168.50.10", root) == "deny"


def test_local_network_without_private_subnet_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUREON_LOCAL_SUBNETS", "203.0.113.0/24")
    root = project(tmp_path, ("lan.yaml", LAN.text()))
    verdict, reason = run_hook(bash_event("nmap 192.168.50.10", root))
    assert verdict == "deny"
    assert "(none detected)" in reason


@pytest.mark.parametrize(
    ("command", "expected"),
    [("nmap -sV 203.0.113.5", "allow"), ("nmap -sV 8.8.4.4", "deny")],
)
def test_example_template_authorizes_nothing(
    tmp_path: Path,
    command: str,
    expected: str,
) -> None:
    template = REPO_EXAMPLE.read_text(encoding="utf-8")
    root = project(tmp_path, ("engagement.example.yaml", template))
    assert decision(command, root) == expected


# --- regression: commands run indirectly are gated ------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'nmap -sS 8.8.8.8'",
        "bash -lc 'nmap 8.8.8.8'",
        'sh -c "curl https://8.8.8.8/"',
        "echo $(nmap 8.8.8.8)",
        'echo "$(nmap 8.8.8.8)"',
        "x=`nmap 8.8.8.8`",
        "diff <(nmap 8.8.8.8) /dev/null",
        "eval nmap 8.8.8.8",
    ],
)
def test_nested_offensive_command_is_gated(tmp_path: Path, command: str) -> None:
    assert decision(command, tmp_path) == "deny"


def test_nested_command_against_testbed_is_allowed(tmp_path: Path) -> None:
    assert decision("bash -c 'nmap -sV 127.0.0.1'", tmp_path) == "allow"


def test_nesting_beyond_the_limit_is_denied(tmp_path: Path) -> None:
    command = "ls"
    for _ in range(pw.MAX_NESTING + 1):
        command = "bash -c " + shlex.quote(command)
    verdict, reason = run_hook(bash_event(command, tmp_path))
    assert verdict == "deny"
    assert "nesting exceeds" in reason


def test_nesting_at_the_limit_is_still_analysed(tmp_path: Path) -> None:
    command = "nmap 8.8.8.8"
    for _ in range(pw.MAX_NESTING):
        command = "bash -c " + shlex.quote(command)
    assert pw.classify(command).kind == "offensive"
    assert decision(command, tmp_path) == "deny"


def test_repeated_nested_payload_is_analysed_once() -> None:
    analyzer = pw.CommandAnalyzer()
    commands = analyzer.analyze("bash -c 'ls' ; bash -c 'ls'")
    assert analyzer.seen == {"ls"}
    assert commands.count(["ls"]) == 1


# --- regression: a CIDR is checked over its full width ----------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("nmap 198.18.0.0/16", "deny"),
        ("nmap 198.18.0.0/25", "allow"),
        ("nmap 198.18.0.0/24", "allow"),
    ],
)
def test_cidr_wider_than_scope_is_denied(
    tmp_path: Path,
    command: str,
    expected: str,
) -> None:
    spec = dataclasses.replace(EXPLICIT, excluded="")
    root = project(tmp_path, ("r.yaml", spec.text()))
    assert decision(command, root) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("nmap 203.0.113.0/8", "deny"),
        ("nmap 127.0.0.0/7", "deny"),
        ("nmap 203.0.113.0/28", "allow"),
        ("nmap 127.0.0.0/8", "allow"),
    ],
)
def test_testbed_cidr_must_fit_inside_documentation_range(
    tmp_path: Path,
    command: str,
    expected: str,
) -> None:
    assert decision(command, tmp_path) == expected


def test_local_sweep_wider_than_host_subnet_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUREON_LOCAL_SUBNETS", "192.168.50.0/24")
    root = project(tmp_path, ("lan.yaml", LAN.text()))
    assert decision("nmap -sn 192.168.50.0/16", root) == "deny"


# --- regression: IPv6 literals are targets ------------------------------------------


def test_ipv6_target_under_active_record_is_checked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUREON_LOCAL_SUBNETS", "192.168.50.0/24, fd00:50::/64")
    root = project(tmp_path, ("lan.yaml", LAN.text()))
    verdict, reason = run_hook(bash_event("nmap -6 2001:4860:4860::8888", root))
    assert verdict == "deny"
    assert "2001:4860:4860::8888" in reason
    assert decision("nmap -6 fd00:50::9", root) == "allow"
    assert decision("nmap -6 ::1", root) == "allow"


# --- regression: malformed events fail closed -----------------------------------


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not json", "not valid JSON"),
        ("[1, 2]", "not a JSON object"),
        ('{"tool_name": "Bash", "tool_input": "ls"}', "no string tool_input.command"),
        ('{"tool_name": "Bash", "tool_input": {"command": 7}}', "no string"),
        ('{"tool_name": "Bash"}', "no string tool_input.command"),
    ],
)
def test_malformed_event_is_denied(raw: str, message: str) -> None:
    verdict, reason = run_hook(raw)
    assert verdict == "deny"
    assert message in reason
    assert reason.startswith(pw.HOOK_TAG)


def test_event_for_another_tool_defers() -> None:
    assert run_hook('{"tool_name": "Read", "tool_input": {"file_path": "x"}}') == (
        "defer",
        "",
    )


def test_project_dir_falls_back_to_environment_then_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = '{"tool_name": "Bash", "tool_input": {"command": "ls"}}'
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    call = pw.parse_event(raw)
    assert call is not None
    assert call.project_dir == tmp_path
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    monkeypatch.chdir(tmp_path)
    call = pw.parse_event(raw)
    assert call is not None
    assert call.project_dir == tmp_path


def test_internal_error_denies_with_an_error_id(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def explode(raw: str) -> pw.Decision:
        message = f"fault while handling {len(raw)} bytes"
        raise RuntimeError(message)

    monkeypatch.setattr(pw, "decide_event", explode)
    with caplog.at_level(logging.ERROR, logger="aureon.policy_wrap"):
        verdict, reason = run_hook("{}")
    assert verdict == "deny"
    assert "internal error" in reason
    assert "RuntimeError" in reason
    error_id = reason.split("internal error ", 1)[1].split(",", 1)[0]
    assert len(error_id) == pw.ERROR_ID_HEX_DIGITS
    assert error_id in caplog.text


def test_main_uses_process_streams_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(bash_event("nmap 8.8.8.8", tmp_path)))
    monkeypatch.setattr(sys, "stdout", stdout)
    assert pw.main() == 0
    assert '"permissionDecision": "deny"' in stdout.getvalue()


def test_module_runs_as_a_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(bash_event("ls", tmp_path)))
    monkeypatch.setattr(sys, "stdout", stdout)
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(HOOK), run_name="__main__")
    assert raised.value.code == 0
    assert stdout.getvalue() == ""


# --- regression: a bad record disables only itself ------------------------------


@pytest.mark.parametrize(
    ("bad", "expected_reason"),
    [
        (
            dataclasses.replace(
                EXPLICIT,
                valid_from="2026-01-01T00:00:00",
                valid_until="2026-12-31T00:00:00",
            ).text(),
            "timezone-aware",
        ),
        (
            EXPLICIT.text().replace(
                'excluded_targets:\n  - type: ip\n    value: "198.18.0.5"\n',
                'excluded_targets: "198.18.0.5"\n',
            ),
            "`excluded_targets` is not a list",
        ),
    ],
    ids=["naive-timestamps", "scalar-exclusions"],
)
def test_bad_record_is_rejected_without_disabling_others(
    tmp_path: Path,
    bad: str,
    expected_reason: str,
) -> None:
    root = project(tmp_path, ("a-bad.yaml", bad), ("b-good.yaml", EXPLICIT.text()))
    assert decision("nmap -sV 198.18.0.10", root) == "allow"
    engagements = pw.load_engagements(root, FIXED_NOW)
    assert engagements[0].rejection is not None
    assert expected_reason in engagements[0].rejection
    assert engagements[1].rejection is None


def test_scalar_exclusion_no_longer_authorizes_the_excluded_host(
    tmp_path: Path,
) -> None:
    scalar = EXPLICIT.text().replace(
        'excluded_targets:\n  - type: ip\n    value: "198.18.0.5"\n',
        'excluded_targets: "198.18.0.5"\n',
    )
    root = project(tmp_path, ("r.yaml", scalar))
    assert decision("nmap -sV 198.18.0.5", root) == "deny"


def test_non_utf8_and_oversized_records_are_rejected(tmp_path: Path) -> None:
    root = project(tmp_path, ("good.yaml", EXPLICIT.text()))
    (root / "engagements" / "binary.yaml").write_bytes(b"status: \xff\n")
    oversized = "x: y\n" * (pw.MAX_RECORD_BYTES // 5 + 1)
    (root / "engagements" / "large.yaml").write_text(oversized, encoding="utf-8")
    reasons = {e.source: e.rejection for e in pw.load_engagements(root, FIXED_NOW)}
    assert reasons["binary.yaml"] == "record is not valid UTF-8"
    assert reasons["large.yaml"] == f"record exceeds {pw.MAX_RECORD_BYTES} bytes"
    assert reasons["good.yaml"] is None
    assert decision("nmap -sV 198.18.0.10", root) == "allow"


def test_deeply_nested_record_is_rejected(tmp_path: Path) -> None:
    deep = (
        "".join(f"{'  ' * level}k{level}:\n" for level in range(40))
        + "  " * 40
        + "v: 1\n"
    )
    root = project(tmp_path, ("deep.yaml", deep))
    [engagement] = pw.load_engagements(root, FIXED_NOW)
    assert engagement.rejection == (
        f"record nests deeper than {pw.MAX_RECORD_NESTING} levels"
    )


def test_unreadable_record_is_rejected(tmp_path: Path) -> None:
    engagement = pw.load_engagement(tmp_path / "missing.yaml", FIXED_NOW)
    assert engagement.rejection is not None
    assert engagement.rejection.startswith("unreadable record (")


def test_loader_skips_template_other_suffixes_and_directories(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        ("engagement.example.yaml", EXPLICIT.text()),
        ("notes.txt", "status: active\n"),
        ("b.yml", EXPLICIT.text()),
        ("a.yaml", EXPLICIT.text()),
    )
    (root / "engagements" / "dir.yaml").mkdir()
    assert [e.source for e in pw.load_engagements(root, FIXED_NOW)] == [
        "a.yaml",
        "b.yml",
    ]
    assert pw.load_engagements(tmp_path / "absent", FIXED_NOW) == []


def test_no_authorizing_record_lists_every_rejection(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        ("a.yaml", dataclasses.replace(EXPLICIT, status="closed").text()),
        ("b.yaml", dataclasses.replace(EXPLICIT, approved="false").text()),
    )
    verdict, reason = run_hook(bash_event("nmap 198.18.0.10", root))
    assert verdict == "deny"
    assert "a.yaml: record status is not 'active'" in reason
    assert "b.yaml: record lacks both internal and owner approval" in reason


# --- record evaluation --------------------------------------------------------


def evaluate(text: str) -> str | None:
    return pw.evaluate_record("r.yaml", pw.parse_engagement(text), FIXED_NOW).rejection


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (
            dataclasses.replace(EXPLICIT, restrict_targets="false"),
            "record does not restrict to allowed targets",
        ),
        (
            dataclasses.replace(
                EXPLICIT,
                valid_from="2027-01-01T00:00:00Z",
                valid_until="2027-02-01T00:00:00Z",
            ),
            "record is outside its valid_from/valid_until window",
        ),
        (
            dataclasses.replace(EXPLICIT, valid_from="not a date"),
            "record has no valid timezone-aware time window",
        ),
        (
            dataclasses.replace(
                EXPLICIT, targets='targets:\n  - type: cidr\n    value: "0.0.0.0/0"\n'
            ),
            "record contains a forbidden wildcard target",
        ),
        (
            dataclasses.replace(
                EXPLICIT, targets="targets:\n  - type: domain\n    value: '*.corp'\n"
            ),
            "record contains a forbidden wildcard target",
        ),
        (
            dataclasses.replace(
                EXPLICIT, targets="targets:\n  - type: x\n    value: all networks\n"
            ),
            "record contains a forbidden wildcard target",
        ),
        (
            dataclasses.replace(EXPLICIT, targets="targets: [a, b]\n"),
            "record `targets` has an entry without a string `value`",
        ),
        (
            dataclasses.replace(
                EXPLICIT, targets="targets:\n  - type: ip\n    value: true\n"
            ),
            "record `targets` has an entry without a string `value`",
        ),
        (
            dataclasses.replace(
                EXPLICIT, targets="targets:\n  - type: ip\n    value: ''\n"
            ),
            "record `targets` has an entry without a string `value`",
        ),
    ],
    ids=[
        "unrestricted",
        "outside-window",
        "bad-date",
        "blanket",
        "star-domain",
        "all-networks",
        "scalar-entries",
        "bool-value",
        "empty-value",
    ],
)
def test_record_rejections(spec: RecordSpec, reason: str) -> None:
    assert evaluate(spec.text()) == reason


def test_record_without_time_window_is_rejected() -> None:
    text = EXPLICIT.text().split("time_window:", 1)[0]
    assert evaluate(text) == "record has no valid timezone-aware time window"


def test_record_without_target_lists_is_authorizing() -> None:
    spec = dataclasses.replace(EXPLICIT, targets="", excluded="")
    engagement = pw.evaluate_record(
        "r.yaml", pw.parse_engagement(spec.text()), FIXED_NOW
    )
    assert engagement.rejection is None
    assert engagement.targets == ()
    assert engagement.excluded == ()


def test_malformed_sub_mappings_read_as_missing() -> None:
    text = EXPLICIT.text().replace(
        "approvals:\n  internal:\n", "approvals: yes\nx:\n  internal:\n"
    )
    assert evaluate(text) == "record lacks both internal and owner approval"


@pytest.mark.parametrize(
    ("line", "mode"),
    [
        ("scope_mode: local_network\n", "local_network"),
        ("scope: LAN\n", "local_network"),
        ("scope_mode: local-network\n", "local_network"),
        ("scope_mode: explicit\n", "explicit"),
        ("", "explicit"),
    ],
)
def test_record_mode(line: str, mode: str) -> None:
    spec = dataclasses.replace(EXPLICIT, scope_line=line)
    record = pw.parse_engagement(spec.text())
    assert pw.evaluate_record("r.yaml", record, FIXED_NOW).mode == mode


def test_real_clock_is_timezone_aware() -> None:
    assert REAL_NOW_UTC().tzinfo is dt.timezone.utc


# --- decisions ------------------------------------------------------------------


def test_offensive_command_without_target_and_no_record_denies(tmp_path: Path) -> None:
    verdict, reason = run_hook(bash_event("nmap --help", tmp_path))
    assert verdict == "deny"
    assert "target(s): unspecified" in reason


def test_offensive_command_without_target_under_active_record_allows(
    tmp_path: Path,
) -> None:
    root = project(tmp_path, ("r.yaml", EXPLICIT.text()))
    verdict, reason = run_hook(bash_event("nmap --help", root))
    assert verdict == "allow"
    assert "no explicit off-box target" in reason


def test_out_of_scope_reason_lists_detected_subnets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUREON_LOCAL_SUBNETS", "192.168.50.0/24, 10.1.0.0/16")
    root = project(tmp_path, ("lan.yaml", LAN.text()))
    verdict, reason = run_hook(bash_event("nmap 172.16.0.9", root))
    assert verdict == "deny"
    assert "(192.168.50.0/24, 10.1.0.0/16)" in reason


def test_explicit_out_of_scope_reason_has_no_subnet_hint(tmp_path: Path) -> None:
    root = project(tmp_path, ("r.yaml", EXPLICIT.text()))
    verdict, reason = run_hook(bash_event("nmap 10.9.9.9", root))
    assert verdict == "deny"
    assert "local subnet" not in reason
    assert "Out of scope: target '10.9.9.9'" in reason


def test_explicit_and_local_records_combine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUREON_LOCAL_SUBNETS", "192.168.50.0/24")
    root = project(tmp_path, ("a.yaml", EXPLICIT.text()), ("b.yaml", LAN.text()))
    verdict, reason = run_hook(bash_event("nmap 198.18.0.10 192.168.50.10", root))
    assert verdict == "allow"
    assert "a.yaml, b.yaml" in reason
    # An exclusion in the local record wins over the local subnet.
    assert decision("nmap 192.168.50.1", root) == "deny"


# --- classification ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "kind", "targets"),
    [
        ("", "benign", ()),
        (" ; && ", "benign", ()),
        ("cat notes.txt", "benign", ()),
        ("curl http://localhost:3000/", "benign", ("localhost",)),
        ("curl https://app.example.com/x", "benign", ("app.example.com",)),
        ("wget https://example.org/x", "offensive", ("example.org",)),
        ("nmap -sV 10.0.0.0/24", "offensive", ("10.0.0.0/24",)),
        ("nmap host.internal.test:22", "offensive", ("host.internal.test",)),
        ("nmap 8.8.8.8 8.8.8.8", "offensive", ("8.8.8.8",)),
        ("slowloris 10.0.0.1", "forbidden", ("10.0.0.1",)),
        ("nmap --dos 10.0.0.1", "forbidden", ("10.0.0.1",)),
        ("nmap ::/0", "forbidden", ("::/0",)),
        ("nmap -6 2001:db8::1", "offensive", ("2001:db8::1",)),
        ("curl http://10.0.0.1/admin", "offensive", ("10.0.0.1",)),
        ("curl 10.0.0.1/admin", "offensive", ("10.0.0.1",)),
        ("nmap fd00::/abc", "offensive", ("fd00::/abc",)),
        ("echo 'unbalanced nmap 10.0.0.1", "offensive", ("10.0.0.1",)),
        ("(nmap 10.0.0.1)", "offensive", ("10.0.0.1",)),
        ("{ nmap 10.0.0.1; }", "offensive", ("10.0.0.1",)),
        ("/usr/bin/NMAP 10.0.0.1", "offensive", ("10.0.0.1",)),
    ],
)
def test_classify(command: str, kind: str, targets: tuple[str, ...]) -> None:
    result = pw.classify(command)
    assert (result.kind, result.targets) == (kind, targets)


@pytest.mark.parametrize(
    ("command", "payloads"),
    [
        ("echo $(a) <(b) >(c) `d`", ["a", "b", "c", "d"]),
        ("echo $(a $(b) c)", ["a $(b) c"]),
        ("echo $(unclosed", ["unclosed"]),
        ("echo `unclosed", ["unclosed"]),
        ("echo $( ) ` `", []),
        ("echo plain", []),
    ],
)
def test_substitutions(command: str, payloads: list[str]) -> None:
    assert pw.substitutions(command) == payloads


@pytest.mark.parametrize(
    ("tokens", "payloads"),
    [
        (["bash", "-c", "ls"], ["ls"]),
        (["zsh", "-ec", "ls -l"], ["ls -l"]),
        (["bash", "script.sh"], []),
        (["bash", "-c"], []),
        (["bash", "-o", "pipefail", "-c", "ls"], ["ls"]),
        (["eval", "ls", "-l"], ["ls -l"]),
        (["echo", "bash"], []),
    ],
)
def test_nested_payloads(tokens: list[str], payloads: list[str]) -> None:
    assert pw.nested_payloads(tokens) == payloads


# --- parser ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", {}),
        ("# only a comment\n\n", {}),
        (
            "a: 1\nb: 'two'\nc: \"x # y\"\nd: four # comment\nurl: http://h/p#f\n",
            {"a": "1", "b": "two", "c": "x # y", "d": "four", "url": "http://h/p#f"},
        ),
        (
            "n: null\nt: ~\ne:\nyes: TRUE\nno: false\nnone: None\n",
            {"n": None, "t": None, "e": None, "yes": True, "no": False, "none": None},
        ),
        (
            "l: [a, 'b', null, true, ]\nempty: []\n",
            {"l": ["a", "b", None, True], "empty": []},
        ),
        (
            "top:\n  mid:\n    leaf: x\n  other: y\nnext: z\n",
            {"top": {"mid": {"leaf": "x"}, "other": "y"}, "next": "z"},
        ),
        (
            "seq:\n  - a\n  - c: 1\n    d: 2\n  -\n    nested: 3\n  - e:\n      f: 4\n",
            {"seq": ["a", {"c": "1", "d": "2"}, {"nested": "3"}, {"e": {"f": "4"}}]},
        ),
        ("seq:\n- x\n- y\nafter: 1\n", {"seq": ["x", "y"], "after": "1"}),
        (
            "- top\n- k: v\n  k2: v2\n- k: w\n- [a]\n",
            {"__root__": ["top", {"k": "v", "k2": "v2"}, {"k": "w"}, ["a"]]},
        ),
        ("a: 1\n- stray\n", {"a": "1"}),
        ("s:\n  - k: v\n  - w\n", {"s": [{"k": "v"}, "w"]}),
        ("s:\n  - k: v\n    x:\n  - w\n", {"s": [{"k": "v", "x": None}, "w"]}),
        ("s:\n  -\n", {"s": [None]}),
        ("indent:\n   three: 3\n  two: 2\n", {"indent": {"three": "3"}}),
    ],
)
def test_parse_engagement(text: str, expected: dict[str, object]) -> None:
    assert pw.parse_engagement(text) == expected


def test_parse_repository_records() -> None:
    for path in sorted(REPO_EXAMPLE.parent.glob("*.yaml")):
        record = pw.parse_engagement(path.read_text(encoding="utf-8"))
        assert isinstance(record.get("approvals"), dict)
        assert isinstance(record.get("time_window"), dict)


# --- addresses and subnets -------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "scope", "expected"),
    [
        ("10.0.0.5", "10.0.0.0/24", True),
        ("10.0.1.5", "10.0.0.0/24", False),
        ("10.0.0.0/25", "10.0.0.0/24", True),
        ("10.0.0.0/24", "10.0.0.0/24", True),
        ("10.0.0.0/16", "10.0.0.0/24", False),
        ("fd00::7", "10.0.0.0/8", False),
        ("a.b.example.test", "example.test", True),
        ("example.test.", "EXAMPLE.test", True),
        ("badexample.test", "example.test", False),
        ("10.0.0.5", "example.test", False),
        ("10.0.0.5", "   ", False),
    ],
)
def test_in_scope(target: str, scope: str, *, expected: bool) -> None:
    assert pw.in_scope(target, scope) is expected


@pytest.mark.parametrize(
    ("target", "excluded", "expected"),
    [
        ("198.18.0.5", "198.18.0.5", True),
        ("198.18.0.5/32", "198.18.0.5", True),
        ("198.18.0.0/24", "198.18.0.5", False),
        ("host.example.test", "example.test", True),
    ],
)
def test_is_excluded(target: str, excluded: str, *, expected: bool) -> None:
    assert pw.is_excluded(target, excluded) is expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("LOCALHOST", True),
        ("0.0.0.0", True),  # noqa: S104 - destination name under test, not a bind
        ("svc.localhost", True),
        ("x.example.net", True),
        ("::1", True),
        ("192.0.2.7", True),
        ("203.0.113.0/28", True),
        ("127.0.0.0/8", True),
        ("203.0.113.0/8", False),
        ("127.0.0.0/7", False),
        ("8.8.8.8", False),
        ("2001:db8::1", False),
        ("example.com", False),
    ],
)
def test_is_local_target(target: str, *, expected: bool) -> None:
    assert pw.is_local_target(target) is expected


@pytest.mark.parametrize(
    ("target", "subnets", "expected"),
    [
        ("192.168.50.7", ["192.168.50.0/24"], True),
        ("192.168.50.0/25", ["192.168.50.0/24"], True),
        ("192.168.51.7", ["192.168.50.0/24"], False),
        ("8.8.8.8", ["8.8.8.0/24"], False),
        ("host.lan", ["192.168.50.0/24"], False),
        ("fd00::7", ["192.168.50.0/24", "fd00::/64"], True),
        ("fd00::7", ["192.168.50.0/24"], False),
    ],
)
def test_target_in_local(target: str, subnets: list[str], *, expected: bool) -> None:
    networks = [net for raw in subnets if (net := pw.as_network(raw)) is not None]
    assert pw.target_in_local(target, networks) is expected


def test_override_subnets_keep_only_private_multi_host_networks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AUREON_LOCAL_SUBNETS",
        " 192.168.1.0/24,10.0.0.5/32 8.8.8.0/24, nonsense ,fd00::/64",
    )
    assert [str(net) for net in pw.local_subnets()] == ["192.168.1.0/24", "fd00::/64"]


def test_discovery_prefers_interfaces_then_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interfaces = [ipaddress.ip_network("10.2.0.0/16")]
    probed = [ipaddress.ip_network("192.168.7.0/24")]
    monkeypatch.setattr(pw, "interface_networks", lambda: interfaces)
    monkeypatch.setattr(pw, "probed_networks", lambda: probed)
    assert pw.local_subnets() == interfaces
    monkeypatch.setattr(pw, "interface_networks", list)
    assert pw.local_subnets() == probed


IP_ADDR_OUTPUT = (
    "1: lo    inet 127.0.0.1/8 scope host lo\n"
    "2: eth0    inet 192.168.4.20/24 brd 192.168.4.255 scope global eth0\n"
    "2: eth0    inet6 fd12::5/64 scope global\n"
    "3: bad    inet 999.1.1.1/24 scope global\n"
    "4: none    link/ether 02:00:00:00:00:01\n"
)


def completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ip"], returncode=0, stdout=stdout, stderr=""
    )


def test_interface_networks_parse_ip_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/sbin/ip")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: completed(IP_ADDR_OUTPUT))
    assert [str(net) for net in REAL_INTERFACE_NETWORKS()] == [
        "127.0.0.0/8",
        "192.168.4.0/24",
        "fd12::/64",
    ]


def test_interface_networks_without_ip_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert REAL_INTERFACE_NETWORKS() == []


@pytest.mark.parametrize(
    "error",
    [OSError("exec failed"), subprocess.TimeoutExpired(cmd="ip", timeout=3)],
)
def test_interface_networks_on_command_failure(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise error

    monkeypatch.setattr(shutil, "which", lambda _name: "/sbin/ip")
    monkeypatch.setattr(subprocess, "run", fail)
    assert REAL_INTERFACE_NETWORKS() == []


class RecordingSocket:
    """Socket test double with the connect/getsockname/close interface."""

    def __init__(self, address: str, *, connect_error: OSError | None = None) -> None:
        self.address = address
        self.connect_error = connect_error
        self.closed = False

    def connect(self, destination: tuple[str, int]) -> None:
        assert destination == pw.PROBE_ADDRESS
        if self.connect_error is not None:
            raise self.connect_error

    def getsockname(self) -> tuple[str, int]:
        return (self.address, 40000)

    def close(self) -> None:
        self.closed = True


def test_probe_derives_a_slash_24(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = RecordingSocket("192.168.9.33")
    monkeypatch.setattr(socket, "socket", lambda *_args: probe)
    assert [str(net) for net in REAL_PROBED_NETWORKS()] == ["192.168.9.0/24"]
    assert probe.closed


def test_probe_with_unparseable_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "socket", lambda *_args: RecordingSocket("not-an-ip"))
    assert REAL_PROBED_NETWORKS() == []


def test_probe_connect_failure_closes_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = RecordingSocket("192.168.9.33", connect_error=OSError("unreachable"))
    monkeypatch.setattr(socket, "socket", lambda *_args: probe)
    assert REAL_PROBED_NETWORKS() == []
    assert probe.closed


def test_probe_socket_creation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object) -> socket.socket:
        message = "no sockets"
        raise OSError(message)

    monkeypatch.setattr(socket, "socket", refuse)
    assert REAL_PROBED_NETWORKS() == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
