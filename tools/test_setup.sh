#!/usr/bin/env bash
#
# test_setup.sh - behavioural tests for setup.sh.
#
# Every case runs the real setup.sh with HOME, CLAUDE_CONFIG_DIR,
# OPENCODE_CONFIG_DIR and AGY_CONFIG_DIR pointed into a throwaway directory and
# a PATH that holds no agy or opencode binary, so no real configuration is read
# or written. SETUP_UNDER_TEST overrides the script path (default: ../setup.sh).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP="${SETUP_UNDER_TEST:-${ROOT}/setup.sh}"
BASH_BIN="${BASH:-bash}"

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

failures=0
pass() { printf '  ok           %s\n' "$1"; }
fail() { printf '  FAIL         %s\n' "$1" >&2; failures=$((failures + 1)); }

# Run setup.sh inside the sandbox with the given arguments; stdout and stderr
# go to ${work}/out, and the exit status is compared with the expected one.
sandbox_path="/usr/local/bin:/usr/bin:/bin"
expect_exit() {
  local want="$1" label="$2" got=0 output
  shift 2
  HOME="${work}/home" \
    CLAUDE_CONFIG_DIR="${work}/claude" \
    OPENCODE_CONFIG_DIR="${work}/opencode" \
    AGY_CONFIG_DIR="${work}/agy" \
    PATH="${sandbox_path}" \
    "${BASH_BIN}" "${SETUP}" "$@" >"${work}/out" 2>&1 || got=$?
  if [[ "${got}" -eq "${want}" ]]; then
    pass "${label}"
  else
    output="$(tr '\n' ' ' <"${work}/out")"
    fail "${label} (exit ${got}, expected ${want}): ${output}"
  fi
}

expect_output() {
  local label="$1" pattern="$2"
  if grep -qE -- "${pattern}" "${work}/out"; then
    pass "${label}"
  else
    fail "${label} (no line matching ${pattern})"
  fi
}

mkdir -p "${work}/home"

# --- opencode agents --------------------------------------------------------
expect_exit 0 "install exits 0" install
expect_exit 0 "status after install exits 0" status
expect_output "status reports every agent in sync" "all opencode agents in sync"
expect_exit 0 "uninstall that removes copies exits 0" uninstall
expect_output "uninstall reports removed copies" "removed +aureon"
expect_exit 0 "uninstall with nothing to remove exits 0" uninstall
expect_output "uninstall with nothing to remove says so" "no repo-matching global copies"

# --- Claude Code agents and skills -----------------------------------------
expect_exit 0 "claude-install exits 0" claude-install
expect_exit 0 "claude-status after install exits 0" claude-status
expect_output "claude-status reports agents in sync" "all Claude Code agents in sync"
expect_exit 0 "claude-uninstall that removes copies exits 0" claude-uninstall
leftover="$(find "${work}/claude/agents" -name '*.md' -print -quit)"
if [[ -z "${leftover}" ]]; then
  pass "claude-uninstall removes every installed agent"
else
  fail "claude-uninstall left agent files behind"
fi
expect_exit 0 "claude-uninstall with nothing to remove exits 0" claude-uninstall

# --- AGY plugin without an agy binary (directory copy) ----------------------
expect_exit 0 "agy-install without agy copies the plugin" agy-install
if [[ -f "${work}/agy/plugins/aureon-redteam/plugin.json" ]]; then
  pass "agy-install copied plugin.json into AGY_CONFIG_DIR"
else
  fail "agy-install did not copy plugin.json"
fi
expect_exit 0 "agy-status without agy exits 0" agy-status
expect_output "agy-status warns that agy is absent" "agy command not found"
expect_exit 0 "agy-uninstall removes the copied plugin" agy-uninstall
if [[ ! -e "${work}/agy/plugins/aureon-redteam" ]]; then
  pass "agy-uninstall left no plugin directory"
else
  fail "agy-uninstall left the plugin directory"
fi
expect_exit 1 "agy-validate without agy fails" agy-validate

# --- all ----------------------------------------------------------------------
expect_exit 0 "all exits 0" all
if grep -qF '\n' "${work}/out"; then
  fail "all printed a literal backslash-n"
else
  pass "all prints no literal backslash-n"
fi

# --- open: --no-install is consumed, remaining arguments reach opencode ------
# A recording executable named opencode stands in for the real CLI here: it
# writes its arguments to a file, which is all this case needs to observe.
mkdir -p "${work}/bin"
cat >"${work}/bin/opencode" <<'RECORDER'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
  printf 'opencode 0.0.0-test\n'
  exit 0
fi
printf '%s\n' "$@" >"${OPENCODE_ARGS_FILE}"
RECORDER
chmod +x "${work}/bin/opencode"
sandbox_path="${work}/bin:/usr/local/bin:/usr/bin:/bin"
export OPENCODE_ARGS_FILE="${work}/opencode-args"
expect_exit 0 "open --no-install launches opencode" open --no-install --model test-model
forwarded="$(tr '\n' ' ' <"${OPENCODE_ARGS_FILE}")"
if [[ "${forwarded}" == "--model test-model " ]]; then
  pass "open forwards only the arguments after --no-install"
else
  fail "open forwarded: ${forwarded}"
fi
expect_output "open reports the opencode version" "opencode 0.0.0-test"
sandbox_path="/usr/local/bin:/usr/bin:/bin"
expect_exit 1 "open without opencode on PATH fails" open --no-install

# --- dispatcher ---------------------------------------------------------------
expect_exit 0 "--help exits 0" --help
expect_output "--help prints the usage block" "^Usage:"
expect_output "--help lists the scan command" "setup.sh scan"
expect_exit 2 "an unknown command exits 2" no-such-command

if [[ "${failures}" -ne 0 ]]; then
  printf '\n%d test failure(s)\n' "${failures}" >&2
  exit 1
fi
printf '\nall setup.sh tests passed\n'
