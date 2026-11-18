#!/usr/bin/env python3
"""Behavioural tests for .claude/hooks/policy_wrap.py.

Each case drives the real hook entry point with a synthetic PreToolUse event
and a throwaway project directory holding test-only engagement records. No
production engagement file is created or trusted. Run: python3 test_policy_wrap.py
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("policy_wrap", os.path.join(_HERE, "policy_wrap.py"))
pw = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(pw)

_failures = 0


def _decide(command: str, project_dir: str) -> tuple[str, str]:
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": project_dir}
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO(json.dumps(event))
    try:
        with redirect_stdout(buf):
            rc = pw.main()
    finally:
        sys.stdin = old
    assert rc == 0, f"hook must exit 0, got {rc}"
    out = buf.getvalue().strip()
    if not out:
        return "defer", ""
    payload = json.loads(out)["hookSpecificOutput"]
    return payload["permissionDecision"], payload["permissionDecisionReason"]


def expect(command: str, project_dir: str, want: str, label: str) -> None:
    global _failures
    got, reason = _decide(command, project_dir)
    if got == want:
        print(f"  ok           {label}")
    else:
        _failures += 1
        print(f"  FAIL         {label}: expected {want}, got {got} ({reason})", file=sys.stderr)


def _write_active_record(path: str, *, approved: bool = True, status: str = "active",
                         valid: bool = True, net: bool = True) -> None:
    now = _dt.datetime.now(_dt.timezone.utc)
    if valid:
        start = (now - _dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (now + _dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        start = (now - _dt.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (now - _dt.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    a = "true" if approved else "false"
    n = "true" if net else "false"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "schema: 1\n"
            "engagement_id: ENG-T\n"
            f"status: {status}\n"
            "approvals:\n"
            "  internal:\n"
            f"    approved: {a}\n"
            "  owner:\n"
            f"    approved: {a}\n"
            "targets:\n"
            "  - type: cidr\n"
            '    value: "198.18.0.0/24"\n'
            "  - type: domain\n"
            '    value: "target.internal.test"\n'
            "excluded_targets:\n"
            "  - type: ip\n"
            '    value: "198.18.0.5"\n'
            "network_access:\n"
            f"  enabled: {n}\n"
            "  allowed_targets_only: true\n"
            "time_window:\n"
            f'  valid_from: "{start}"\n'
            f'  valid_until: "{end}"\n'
        )


def main() -> int:
    # --- no engagement directory: deny-by-default for offensive, defer benign ---
    with tempfile.TemporaryDirectory() as empty:
        expect("ls -la", empty, "defer", "benign command defers to normal flow")
        expect("git status", empty, "defer", "git command defers")
        expect("curl http://127.0.0.1:8080/health", empty, "defer", "local curl defers")
        expect("nmap -sV 127.0.0.1", empty, "allow", "offensive vs loopback allowed (testbed)")
        expect("nmap -sV 203.0.113.9", empty, "allow", "offensive vs RFC5737 doc range allowed")
        expect("nmap -sS scanme.nmap.org", empty, "deny", "offensive vs real host denied w/o record")
        expect("curl https://8.8.8.8/", empty, "deny", "egress to off-box IP denied w/o record")
        expect("masscan 0.0.0.0/0 -p80", empty, "deny", "blanket scope denied (non-overridable)")
        expect("hping3 --flood -S 198.18.0.9", empty, "deny", "DoS flood denied (non-overridable)")
        expect("t50 198.18.0.9", empty, "deny", "DoS tool denied (non-overridable)")

    # --- active, fully approved record ---
    with tempfile.TemporaryDirectory() as root:
        os.mkdir(os.path.join(root, "engagements"))
        rec = os.path.join(root, "engagements", "active.yaml")
        _write_active_record(rec)
        expect("nmap -sV 198.18.0.10", root, "allow", "in-scope CIDR member allowed")
        expect("sqlmap -u https://target.internal.test/i?id=1", root, "allow", "in-scope domain allowed")
        expect("nmap -sV 198.18.0.5", root, "deny", "excluded target denied despite active record")
        expect("nmap 10.9.9.9", root, "deny", "out-of-scope target denied")
        expect("masscan 0.0.0.0/0", root, "deny", "blanket scope denied even with active record")

    # --- record present but not authorizing ---
    for label, kwargs in (
        ("unapproved record denies", {"approved": False}),
        ("draft status denies", {"status": "draft"}),
        ("expired window denies", {"valid": False}),
        ("network disabled denies", {"net": False}),
    ):
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, "engagements"))
            _write_active_record(os.path.join(root, "engagements", "r.yaml"), **kwargs)
            expect("nmap -sV 198.18.0.10", root, "deny", label)

    # --- local_network scope mode (operator-owned LAN) ---
    def _write_local_record(path: str, *, approved: bool = True) -> None:
        now = _dt.datetime.now(_dt.timezone.utc)
        start = (now - _dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (now + _dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        a = "true" if approved else "false"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "schema: 1\n"
                "engagement_id: ENG-LAN\n"
                "status: active\n"
                "scope_mode: local_network\n"
                "approvals:\n"
                "  internal:\n"
                f"    approved: {a}\n"
                "  owner:\n"
                f"    approved: {a}\n"
                "targets: []\n"
                "excluded_targets:\n"
                "  - type: ip\n"
                '    value: "192.168.50.1"\n'
                "network_access:\n"
                "  enabled: true\n"
                "  allowed_targets_only: true\n"
                "  private_only: true\n"
                "time_window:\n"
                f'  valid_from: "{start}"\n'
                f'  valid_until: "{end}"\n'
            )

    _prev = os.environ.get("AUREON_LOCAL_SUBNETS")
    os.environ["AUREON_LOCAL_SUBNETS"] = "192.168.50.0/24"
    try:
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, "engagements"))
            _write_local_record(os.path.join(root, "engagements", "lan.yaml"))
            expect("nmap -sV 192.168.50.10", root, "allow", "local_network: same-subnet host allowed")
            expect("nmap -sn 192.168.50.0/24", root, "allow", "local_network: same-subnet CIDR sweep allowed")
            expect("hydra -l a -p b 192.168.50.20 ssh", root, "allow", "local_network: credential attack on LAN allowed")
            expect("nmap 192.168.50.1", root, "deny", "local_network: excluded LAN device denied")
            expect("nmap 192.168.99.5", root, "deny", "local_network: other private subnet denied")
            expect("nmap 10.0.0.9", root, "deny", "local_network: different private range denied")
            expect("nmap 8.8.8.8", root, "deny", "local_network: public target denied")
            expect("curl https://example.org", root, "deny", "local_network: public egress denied")
            expect("hping3 --flood 192.168.50.10", root, "deny", "local_network: DoS on LAN denied (non-overridable)")
            expect("masscan 0.0.0.0/0", root, "deny", "local_network: blanket scope denied")

        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, "engagements"))
            _write_local_record(os.path.join(root, "engagements", "lan.yaml"), approved=False)
            expect("nmap 192.168.50.10", root, "deny", "local_network: unapproved record denies even on LAN")

        # Subnet not detected / empty override -> record authorizes nothing.
        os.environ["AUREON_LOCAL_SUBNETS"] = "203.0.113.0/24"  # non-private -> dropped
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, "engagements"))
            _write_local_record(os.path.join(root, "engagements", "lan.yaml"))
            expect("nmap 192.168.50.10", root, "deny", "local_network: no private subnet detected -> deny (fail closed)")
    finally:
        if _prev is None:
            os.environ.pop("AUREON_LOCAL_SUBNETS", None)
        else:
            os.environ["AUREON_LOCAL_SUBNETS"] = _prev

    # --- template file must never authorize ---
    with tempfile.TemporaryDirectory() as root:
        eng = os.path.join(root, "engagements")
        os.mkdir(eng)
        repo_example = os.path.join(_HERE, "..", "..", "engagements", "engagement.example.yaml")
        if os.path.isfile(repo_example):
            with open(repo_example, encoding="utf-8") as src, \
                 open(os.path.join(eng, "engagement.example.yaml"), "w", encoding="utf-8") as dst:
                dst.write(src.read())
            expect("nmap -sV 203.0.113.5", root, "allow", "example template: doc range still testbed-allowed")
            expect("nmap -sV 8.8.4.4", root, "deny", "example template authorizes no real target")

    if _failures:
        print(f"\n{_failures} test failure(s)", file=sys.stderr)
        return 1
    print("\nall policy_wrap tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
