#!/usr/bin/env bash
#
# test_sync_prompts.sh - behavioural tests for tools/sync_prompts.sh.
#
# Each case builds a throwaway AGENTS_ROOT with synthetic test-only prompt
# files, runs the real sync/check implementation against it, and asserts the
# exit status and resulting file contents. No production tree is touched.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYNC="${ROOT}/tools/sync_prompts.sh"
# Invoke the script through bash so the suite runs where `/usr/bin/env` is
# absent (e.g. Termux), independent of the script's executable bit.
BASH_BIN="${BASH:-bash}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

failures=0
pass() { printf '  ok           %s\n' "$1"; }
fail() { printf '  FAIL         %s\n' "$1" >&2; failures=$((failures + 1)); }

seed() {
  local root="$1"
  mkdir -p "${root}/.opencode/agents" "${root}/.agents/agents" \
           "${root}/plugins/aureon-redteam/agents" "${root}/.claude/agents"
  cat > "${root}/.opencode/agents/probe.md" <<'PROMPT'
---
description: Probe agent for parity tests.
mode: subagent
---

# Probe Agent

Body line one.
Body line two.
PROMPT
  cat > "${root}/.agents/agents/probe.md" <<'PROMPT'
---
name: probe
description: Probe agent for parity tests.
mainAgent: true
subagent: true
tools: [read_file]
---

# Probe Agent

Body line one.
Body line two.
PROMPT
  cp "${root}/.agents/agents/probe.md" "${root}/plugins/aureon-redteam/agents/probe.md"
  cat > "${root}/.claude/agents/probe.md" <<'PROMPT'
---
name: probe
description: Probe agent for parity tests.
tools: Read, Bash
model: inherit
color: "#123456"
---

# Probe Agent

Body line one.
Body line two.
PROMPT
}

expect_pass() {
  local root="$1" label="$2"
  if AGENTS_ROOT="${root}" "${BASH_BIN}" "${SYNC}" check >/dev/null 2>&1; then
    pass "${label}"
  else
    fail "${label} (check exited non-zero)"
  fi
}

expect_fail() {
  local root="$1" label="$2"
  if AGENTS_ROOT="${root}" "${BASH_BIN}" "${SYNC}" check >/dev/null 2>&1; then
    fail "${label} (check exited zero)"
  else
    pass "${label}"
  fi
}

case_root="${work}/case"
seed "${case_root}"
expect_pass "${case_root}" "synchronised tree passes"

printf '\nBody line three.\n' >> "${case_root}/plugins/aureon-redteam/agents/probe.md"
expect_fail "${case_root}" "plugin body drift is detected"
AGENTS_ROOT="${case_root}" "${BASH_BIN}" "${SYNC}" sync >/dev/null
expect_pass "${case_root}" "sync repairs plugin body drift"

printf '\nBody line four.\n' >> "${case_root}/.agents/agents/probe.md"
expect_fail "${case_root}" "AGY body drift is detected"
AGENTS_ROOT="${case_root}" "${BASH_BIN}" "${SYNC}" sync >/dev/null
expect_pass "${case_root}" "sync repairs AGY body drift"

sed -i 's/^description: Probe agent for parity tests\.$/description: Diverged description./' "${case_root}/.agents/agents/probe.md"
expect_fail "${case_root}" "description drift is detected"
AGENTS_ROOT="${case_root}" "${BASH_BIN}" "${SYNC}" sync >/dev/null
expect_pass "${case_root}" "sync aligns AGY description with OpenCode"

printf '\nBody line five.\n' >> "${case_root}/.claude/agents/probe.md"
expect_fail "${case_root}" "Claude Code body drift is detected"
AGENTS_ROOT="${case_root}" "${BASH_BIN}" "${SYNC}" sync >/dev/null
expect_pass "${case_root}" "sync repairs Claude Code body drift"
if grep -q '^tools: Read, Bash$' "${case_root}/.claude/agents/probe.md" \
   && grep -q '^color: "#123456"$' "${case_root}/.claude/agents/probe.md"; then
  pass "sync preserves Claude Code frontmatter (tools, color)"
else
  fail "sync clobbered Claude Code frontmatter"
fi

sed -i 's/^description: Probe agent for parity tests\.$/description: Claude drift./' "${case_root}/.claude/agents/probe.md"
expect_fail "${case_root}" "Claude Code description drift is detected"
AGENTS_ROOT="${case_root}" "${BASH_BIN}" "${SYNC}" sync >/dev/null
expect_pass "${case_root}" "sync aligns Claude Code description with OpenCode"

seed "${work}/missing"
rm "${work}/missing/.agents/agents/probe.md"
expect_fail "${work}/missing" "missing AGY agent is detected"

seed "${work}/missing_claude"
rm "${work}/missing_claude/.claude/agents/probe.md"
expect_fail "${work}/missing_claude" "missing Claude Code agent is detected"

# A canonical description containing backslashes must be copied byte for byte.
seed "${work}/backslash"
backslash_desc='description: Probe agent \t with C:\path\name escapes.'
sed -i "s|^description: Probe agent for parity tests\.\$|${backslash_desc//\\/\\\\}|" \
  "${work}/backslash/.opencode/agents/probe.md"
AGENTS_ROOT="${work}/backslash" "${BASH_BIN}" "${SYNC}" sync >/dev/null
if grep -qxF "${backslash_desc}" "${work}/backslash/.agents/agents/probe.md" \
  && grep -qxF "${backslash_desc}" "${work}/backslash/.claude/agents/probe.md"; then
  pass "sync copies a description containing backslashes verbatim"
else
  fail "sync altered a description containing backslashes"
fi
expect_pass "${work}/backslash" "backslash description passes parity check after sync"

if [[ "${failures}" -ne 0 ]]; then
  printf '\n%d test failure(s)\n' "${failures}" >&2
  exit 1
fi
printf '\nall sync_prompts tests passed\n'
