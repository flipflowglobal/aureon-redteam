#!/usr/bin/env bash
#
# setup.sh - install and manage aureon-redteam agent library for Antigravity (agy),
# Claude Code, and opencode environments, adhering to strict enterprise zero-toy-code standards.
#
# AGY Discovery & Integration:
#   workspace scope: .agents/agents/*.md + AGENTS.md   (auto-discovered in this repo)
#   global scope:    ~/.gemini/config/plugins/aureon-redteam (via `agy plugin install`)
#
# Opencode Discovery & Integration:
#   project scope:   .opencode/agents/*.md             (auto-loaded in this repo)
#   global scope:    ~/.config/opencode/agents/        (installed via setup.sh)
#
# Usage:
#   ./setup.sh agy-install            Install/sync AGY plugin globally (~/.gemini/config/plugins/)
#   ./setup.sh agy-validate           Validate AGY plugin manifest, agents, and rules
#   ./setup.sh agy-status             Check AGY plugin and workspace configuration status
#   ./setup.sh agy-uninstall          Uninstall global AGY plugin
#   ./setup.sh claude-install [--force]  Install Claude Code agents+skills+policy wrap to ~/.claude
#   ./setup.sh claude-status          Compare global Claude Code copies against this repo
#   ./setup.sh claude-uninstall       Remove global Claude Code copies matching this repo
#   ./setup.sh install [--force]      Install/sync opencode agents to global config dir
#   ./setup.sh open [--no-install]    Install opencode agents (unless --no-install), then open opencode
#   ./setup.sh status                 Compare global opencode copies against this repo
#   ./setup.sh uninstall              Remove global opencode copies matching this repo
#   ./setup.sh all                    Install AGY plugin, Claude Code config, and opencode agents globally
#   ./setup.sh toolchain              Build the hash-verified gate toolchain in .venv (Python 3.11+)
#   ./setup.sh scan                   Run every offline gate (lint, types, tests, coverage, shell, secrets)
#   ./setup.sh audit-deps             Audit the pinned toolchain for known vulnerabilities (network)
#   ./setup.sh gate-install           Install the Zero-Toy-Code pre-commit hook (git core.hooksPath)
#   ./setup.sh --help
#
# Claude Code Discovery & Integration:
#   project scope:   .claude/ (agents, skills, hooks, settings.json)  (auto-loaded in this repo)
#   global scope:    ~/.claude/agents + ~/.claude/skills              (installed via setup.sh)
#

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCODE_SRC="${REPO_ROOT}/.opencode/agents"
AGY_PLUGIN_SRC="${REPO_ROOT}/plugins/aureon-redteam"
AGY_WORKSPACE_DIR="${REPO_ROOT}/.agents"

# Opencode Config Directory
if [[ -n "${OPENCODE_CONFIG_DIR:-}" ]]; then
  OPENCODE_DIR="${OPENCODE_CONFIG_DIR}"
elif [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
  OPENCODE_DIR="${XDG_CONFIG_HOME}/opencode"
else
  OPENCODE_DIR="${HOME}/.config/opencode"
fi
OPENCODE_AGENT_DIR="${OPENCODE_DIR}/agents"
OPENCODE_BACKUP_DIR="${OPENCODE_AGENT_DIR}/.setup-backup"

# AGY Config Directory
AGY_CONFIG_DIR="${AGY_CONFIG_DIR:-${HOME}/.gemini/config}"
AGY_PLUGIN_DEST="${AGY_CONFIG_DIR}/plugins/aureon-redteam"

# Claude Code Config Directory
CLAUDE_SRC="${REPO_ROOT}/.claude"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"

die() { printf 'setup: %s\n' "$*" >&2; exit 1; }
info() { printf '  %-12s %s\n' "$@"; }

# Binary lookups always succeed and print the resolved path, or nothing when the
# binary is absent. Callers test for an empty result, so the lookup is never
# invoked in a condition where `set -e` would be suspended inside it.
find_opencode() {
  if command -v opencode >/dev/null 2>&1; then
    command -v opencode
  elif [[ -x "${HOME}/.local/bin/opencode" ]]; then
    printf '%s\n' "${HOME}/.local/bin/opencode"
  fi
}

find_agy() {
  if command -v agy >/dev/null 2>&1; then
    command -v agy
  elif [[ -x "${HOME}/.gemini/antigravity-cli/bin/agy" ]]; then
    printf '%s\n' "${HOME}/.gemini/antigravity-cli/bin/agy"
  fi
}

require_opencode_sources() {
  [[ -d "${OPENCODE_SRC}" ]] || die "opencode agent library not found at ${OPENCODE_SRC}"
  local any=0 f
  for f in "${OPENCODE_SRC}"/*; do
    [[ -e "${f}" ]] && any=1
  done
  [[ "${any}" -eq 1 ]] || die "opencode agent directory is empty: ${OPENCODE_SRC}"
}

require_agy_sources() {
  [[ -d "${AGY_PLUGIN_SRC}" ]] || die "AGY plugin not found at ${AGY_PLUGIN_SRC}"
  [[ -f "${AGY_PLUGIN_SRC}/plugin.json" ]] || die "AGY plugin.json missing at ${AGY_PLUGIN_SRC}/plugin.json"
}

validate_json() {
  local bad=0
  for j in "${OPENCODE_SRC}"/*.json "${AGY_PLUGIN_SRC}"/*.json; do
    [[ -e "${j}" ]] || continue
    if python3 -m json.tool "${j}" >/dev/null 2>&1; then
      info "ok           json: $(basename "${j}")"
    else
      printf '  !! invalid JSON: %s\n' "${j}" >&2
      bad=1
    fi
  done
  [[ "${bad}" -eq 0 ]] || die "invalid JSON found"
}

# -----------------------------------------------------------------------------
# AGY Commands
# -----------------------------------------------------------------------------

cmd_agy_validate() {
  require_agy_sources
  local agy_bin
  agy_bin="$(find_agy)"
  [[ -n "${agy_bin}" ]] || die "agy executable not found on PATH"
  info "validating" "${AGY_PLUGIN_SRC} with ${agy_bin}"
  "${agy_bin}" plugin validate "${AGY_PLUGIN_SRC}"
}

cmd_agy_install() {
  require_agy_sources
  local agy_bin
  agy_bin="$(find_agy)"
  if [[ -n "${agy_bin}" ]]; then
    info "installing   aureon-redteam AGY plugin globally"
    "${agy_bin}" plugin install "${AGY_PLUGIN_SRC}"
    printf '\nAGY plugin installed successfully!\n'
    printf 'List available agents: agy agents\n'
    printf 'Launch with agent:     agy --agent aureon (or redteam, coder, debugger, auditor, deepdive)\n'
  else
    # Fallback to direct directory sync if agy command unavailable
    info "syncing      ${AGY_PLUGIN_SRC} to ${AGY_PLUGIN_DEST}"
    mkdir -p "${AGY_CONFIG_DIR}/plugins"
    rm -rf "${AGY_PLUGIN_DEST}"
    cp -r "${AGY_PLUGIN_SRC}" "${AGY_PLUGIN_DEST}"
    info "installed    copied to ${AGY_PLUGIN_DEST}"
  fi
}

cmd_agy_status() {
  require_agy_sources
  printf 'AGY Workspace:  %s\n' "${AGY_WORKSPACE_DIR}"
  printf 'AGY Plugin Src: %s\n' "${AGY_PLUGIN_SRC}"
  printf 'AGY Global Dst: %s\n\n' "${AGY_PLUGIN_DEST}"
  local agy_bin
  agy_bin="$(find_agy)"
  if [[ -z "${agy_bin}" ]]; then
    info "warning" "agy command not found"
    return
  fi
  info "agy-binary" "${agy_bin}"
  printf 'Installed Plugins:\n'
  if ! "${agy_bin}" plugin list 2>&1; then
    info "warning" "agy plugin list exited non-zero"
  fi
  printf '\nAvailable AGY Agents:\n'
  if ! "${agy_bin}" agents 2>&1; then
    info "warning" "agy agents exited non-zero"
  fi
}

cmd_agy_uninstall() {
  local agy_bin
  agy_bin="$(find_agy)"
  if [[ -n "${agy_bin}" ]] && ! "${agy_bin}" plugin uninstall aureon-redteam 2>&1; then
    info "warning" "agy plugin uninstall exited non-zero; removing the copied plugin directly"
  fi
  if [[ -d "${AGY_PLUGIN_DEST}" ]]; then
    rm -rf "${AGY_PLUGIN_DEST}"
    info "removed      ${AGY_PLUGIN_DEST}"
  else
    info "nothing      AGY plugin not found at ${AGY_PLUGIN_DEST}"
  fi
}

# -----------------------------------------------------------------------------
# Claude Code Commands
# -----------------------------------------------------------------------------
#
# Agents and skills are copied into the global ~/.claude tree so every repo can
# use them. The policy wrap hook and settings.json are deliberately NOT installed
# globally: the authorization wrap resolves engagement records relative to the
# active project, so it stays project-scoped in this repo's .claude/ directory.

require_claude_sources() {
  [[ -d "${CLAUDE_SRC}/agents" ]] || die "Claude Code agents not found at ${CLAUDE_SRC}/agents"
  local any=0 f
  for f in "${CLAUDE_SRC}"/agents/*.md; do
    [[ -e "${f}" ]] && any=1
  done
  [[ "${any}" -eq 1 ]] || die "Claude Code agent directory is empty: ${CLAUDE_SRC}/agents"
}

cmd_claude_install() {
  local force=0
  [[ "${1:-}" == "--force" ]] && force=1
  require_claude_sources
  local agent_dest="${CLAUDE_DIR}/agents"
  local skill_dest="${CLAUDE_DIR}/skills"
  local backup_dir="${CLAUDE_DIR}/.setup-backup"
  mkdir -p "${agent_dest}"

  local src name dest
  for src in "${CLAUDE_SRC}"/agents/*.md; do
    name="$(basename "${src}")"
    dest="${agent_dest}/${name}"
    if [[ -f "${dest}" ]] && cmp -s "${src}" "${dest}"; then
      info "up-to-date   agents/${name}"
      continue
    fi
    if [[ -f "${dest}" && "${force}" -eq 0 ]]; then
      mkdir -p "${backup_dir}/agents"
      cp "${dest}" "${backup_dir}/agents/${name}.backup"
      info "backed up    ${backup_dir}/agents/${name}.backup"
    elif [[ -f "${dest}" && "${force}" -eq 1 ]]; then
      info "replaced     agents/${name} (--force, no backup)"
    fi
    cp "${src}" "${dest}"
    info "installed    agents/${name}"
  done

  if [[ -d "${CLAUDE_SRC}/skills" ]]; then
    mkdir -p "${skill_dest}"
    for src in "${CLAUDE_SRC}"/skills/*/; do
      [[ -d "${src}" ]] || continue
      name="$(basename "${src}")"
      dest="${skill_dest}/${name}"
      if [[ -d "${dest}" && "${force}" -eq 0 ]] && ! diff -r -q "${src}" "${dest}" >/dev/null 2>&1; then
        mkdir -p "${backup_dir}/skills"
        rm -rf "${backup_dir}/skills/${name}"
        cp -r "${dest}" "${backup_dir}/skills/${name}"
        info "backed up    ${backup_dir}/skills/${name}"
      fi
      rm -rf "${dest}"
      cp -r "${src}" "${dest}"
      info "installed    skills/${name}"
    done
  fi

  printf '\nInstalled Claude Code agents into %s\n' "${agent_dest}"
  printf 'Policy wrap + settings.json remain project-scoped in %s (loaded automatically in this repo).\n' "${CLAUDE_SRC}"
}

cmd_claude_status() {
  require_claude_sources
  local agent_dest="${CLAUDE_DIR}/agents"
  printf 'Source: %s\nGlobal: %s\n\n' "${CLAUDE_SRC}/agents" "${agent_dest}"
  local src name dest missing=0
  for src in "${CLAUDE_SRC}"/agents/*.md; do
    name="$(basename "${src}")"
    dest="${agent_dest}/${name}"
    if [[ ! -e "${dest}" ]]; then
      info "missing      agents/${name}"
      missing=1
    elif cmp -s "${src}" "${dest}"; then
      info "in-sync      agents/${name}"
    else
      info "diverged     agents/${name}"
      missing=1
    fi
  done
  printf '\nProject-scoped (this repo): %s\n' "${CLAUDE_SRC}/settings.json"
  info "hook" "${CLAUDE_SRC}/hooks/policy_wrap.py"
  printf '\n'
  if [[ "${missing}" -eq 0 ]]; then
    info "result" "all Claude Code agents in sync"
  else
    info "result" "Claude Code agents need installation (run: ./setup.sh claude-install)"
  fi
}

cmd_claude_uninstall() {
  require_claude_sources
  local agent_dest="${CLAUDE_DIR}/agents"
  local skill_dest="${CLAUDE_DIR}/skills"
  local src name dest removed=0
  for src in "${CLAUDE_SRC}"/agents/*.md; do
    name="$(basename "${src}")"
    dest="${agent_dest}/${name}"
    if [[ -f "${dest}" ]] && cmp -s "${src}" "${dest}"; then
      rm "${dest}"
      info "removed      agents/${name}"
      removed=1
    fi
  done
  for src in "${CLAUDE_SRC}"/skills/*/; do
    [[ -d "${src}" ]] || continue
    name="$(basename "${src}")"
    dest="${skill_dest}/${name}"
    if [[ -d "${dest}" ]] && diff -r -q "${src}" "${dest}" >/dev/null 2>&1; then
      rm -rf "${dest}"
      info "removed      skills/${name}"
      removed=1
    fi
  done
  if [[ "${removed}" -eq 0 ]]; then
    info "nothing" "no repo-matching global Claude Code copies to remove"
  fi
}

# -----------------------------------------------------------------------------
# Opencode Commands
# -----------------------------------------------------------------------------

cmd_install() {
  local force=0 src name dest
  [[ "${1:-}" == "--force" ]] && force=1
  require_opencode_sources
  mkdir -p "${OPENCODE_AGENT_DIR}"
  for src in "${OPENCODE_SRC}"/*; do
    name="$(basename "${src}")"
    dest="${OPENCODE_AGENT_DIR}/${name}"
    if [[ -f "${dest}" ]]; then
      if cmp -s "${src}" "${dest}"; then
        info "up-to-date   ${name}"
        continue
      fi
      if [[ "${force}" -eq 0 ]]; then
        mkdir -p "${OPENCODE_BACKUP_DIR}"
        cp "${dest}" "${OPENCODE_BACKUP_DIR}/${name}.backup"
        info "backed up    ${OPENCODE_BACKUP_DIR}/${name}.backup"
      elif [[ "${force}" -eq 1 ]]; then
        info "replaced     ${name} (--force, no backup)"
      fi
    fi
    cp "${src}" "${dest}"
    info "installed    ${name}"
  done
  validate_json
  printf '\nInstalled into %s\n' "${OPENCODE_AGENT_DIR}"
  printf 'Quit and restart opencode for the agents to load (config is read once at startup).\n'
}

cmd_status() {
  require_opencode_sources
  printf 'Source: %s\nGlobal: %s\n\n' "${OPENCODE_SRC}" "${OPENCODE_AGENT_DIR}"
  local missing=0 src name dest
  for src in "${OPENCODE_SRC}"/*; do
    name="$(basename "${src}")"
    dest="${OPENCODE_AGENT_DIR}/${name}"
    if [[ ! -e "${dest}" ]]; then
      info "missing      ${name}"
      missing=1
    elif cmp -s "${src}" "${dest}"; then
      info "in-sync      ${name}"
    else
      info "diverged     ${name}"
      missing=1
    fi
  done
  if [[ -d "${OPENCODE_AGENT_DIR}" ]]; then
    for dest in "${OPENCODE_AGENT_DIR}"/*.md; do
      [[ -e "${dest}" ]] || continue
      name="$(basename "${dest}")"
      [[ -e "${OPENCODE_SRC}/${name}" ]] || info "external     ${name} (not from this repo)"
    done
  fi
  printf '\n'
  if [[ "${missing}" -eq 0 ]]; then
    info "result" "all opencode agents in sync"
  else
    info "result" "opencode agents need installation (run: ./setup.sh install)"
  fi
}

cmd_open() {
  local bin version
  require_opencode_sources
  # `--no-install` is consumed here; every remaining argument goes to opencode.
  if [[ "${1:-}" == "--no-install" ]]; then
    shift
  else
    cmd_install
    printf '\n'
  fi
  bin="$(find_opencode)"
  [[ -n "${bin}" ]] || die "opencode not found on PATH — install it first (https://opencode.ai), then re-run"
  if ! version="$("${bin}" --version 2>/dev/null)"; then
    version="version unavailable"
  fi
  info "launching" "${bin} (${version})"
  exec "${bin}" "$@"
}

cmd_uninstall() {
  require_opencode_sources
  local removed=0 src name dest
  for src in "${OPENCODE_SRC}"/*; do
    name="$(basename "${src}")"
    dest="${OPENCODE_AGENT_DIR}/${name}"
    if [[ -f "${dest}" ]] && cmp -s "${src}" "${dest}"; then
      rm "${dest}"
      info "removed      ${name}"
      removed=1
    fi
  done
  if [[ "${removed}" -eq 0 ]]; then
    info "nothing" "no repo-matching global copies to remove"
  fi
}

cmd_all() {
  info "installing   opencode agents..."
  cmd_install "${1:-}"
  printf '\n'
  info "installing   Claude Code config..."
  cmd_claude_install "${1:-}"
  printf '\n'
  info "installing   AGY plugin..."
  cmd_agy_install
  printf '\n'
  info "complete" "opencode agents, Claude Code config, and AGY plugin are installed globally."
}

# -----------------------------------------------------------------------------
# Zero-Toy-Code gate
# -----------------------------------------------------------------------------
#
# The enforcement point for the Enterprise Zero-Toy-Code standard (AGENTS.md).
# `scan` runs every offline gate, reports each result, and exits non-zero when
# any gate fails or a required tool is missing, so a commit or CI run is
# blocked instead of passing with a gate silently skipped. Tools resolve from
# the hash-pinned toolchain in .venv (`./setup.sh toolchain`) first, then PATH.
# `audit-deps` needs the network to reach the vulnerability service, so CI runs
# it separately and the pre-commit hook stays offline.

SCAN_TOOL="${REPO_ROOT}/tools/zero_toy_scan.py"
VENV_DIR="${REPO_ROOT}/.venv"
REQUIREMENTS_DEV="${REPO_ROOT}/requirements-dev.txt"
GATE_FAILURES=0

# Print the path of a gate tool: the .venv toolchain first, then PATH. Prints
# nothing when the tool is unavailable.
gate_tool() {
  if [[ -x "${VENV_DIR}/bin/$1" ]]; then
    printf '%s\n' "${VENV_DIR}/bin/$1"
  elif command -v "$1" >/dev/null 2>&1; then
    command -v "$1"
  fi
}

# Print a Python interpreter that can run the test suites with coverage: the
# .venv interpreter first, then python3. Prints nothing when neither has
# pytest and pytest-cov.
gate_python() {
  if [[ -x "${VENV_DIR}/bin/python" ]] \
    && "${VENV_DIR}/bin/python" -c 'import pytest, pytest_cov' 2>/dev/null; then
    printf '%s\n' "${VENV_DIR}/bin/python"
  elif python3 -c 'import pytest, pytest_cov' 2>/dev/null; then
    command -v python3
  fi
}

# Record a gate that cannot run because its tool is missing (fail closed).
gate_missing() {
  info "FAIL" "$1: $2 is not installed (run ./setup.sh toolchain)"
  GATE_FAILURES=$((GATE_FAILURES + 1))
}

# gate_run LABEL COMMAND [ARG ...]: run one gate and record a failure without
# stopping, so a single invocation reports every failing gate.
gate_run() {
  local label="$1" status=0
  shift
  info "gate" "${label}"
  "$@" || status=$?
  if [[ "${status}" -eq 0 ]]; then
    info "pass" "${label}"
  else
    info "FAIL" "${label} (exit ${status})"
    GATE_FAILURES=$((GATE_FAILURES + 1))
  fi
}

# gate_tool_run LABEL TOOL [ARG ...]: resolve TOOL, then run it as a gate.
gate_tool_run() {
  local label="$1" bin
  bin="$(gate_tool "$2")"
  if [[ -z "${bin}" ]]; then
    gate_missing "${label}" "$2"
    return 0
  fi
  shift 2
  gate_run "${label}" "${bin}" "$@"
}

# Print the commit that diff-based gates compare against: the merge base with
# origin/main when that ref exists (CI, fetched clones), otherwise HEAD.
diff_base() {
  if git rev-parse --verify --quiet origin/main >/dev/null; then
    git merge-base origin/main HEAD
  else
    printf 'HEAD\n'
  fi
}

cmd_scan() {
  [[ -f "${SCAN_TOOL}" ]] || die "scanner not found at ${SCAN_TOOL}"
  command -v python3 >/dev/null 2>&1 || die "python3 is required to run the gate"
  command -v git >/dev/null 2>&1 || die "git is required to run the gate"
  cd "${REPO_ROOT}"
  GATE_FAILURES=0

  local base scripts_list py
  local -a scripts
  base="$(diff_base)"
  scripts_list="$(git ls-files --cached --others --exclude-standard -- '*.sh' '.githooks/*')"
  mapfile -t scripts <<<"${scripts_list}"
  py="$(gate_python)"

  gate_run "zero-toy scanner" python3 "${SCAN_TOOL}"
  gate_run "whitespace and conflict markers since ${base}" git diff --check "${base}"
  gate_tool_run "ruff format" ruff format --check .
  gate_tool_run "ruff lint (select = ALL)" ruff check .
  gate_tool_run "mypy strict" mypy
  if [[ -n "${py}" ]]; then
    gate_run "pytest with 100% branch coverage" "${py}" -m pytest -q \
      --cov --cov-report=term-missing --cov-report=xml
  else
    gate_missing "pytest with 100% branch coverage" "pytest with pytest-cov"
  fi
  gate_tool_run "ShellCheck (all checks)" shellcheck "${scripts[@]}"
  gate_run "setup.sh suite" bash tools/test_setup.sh
  gate_run "prompt-sync suite" bash tools/test_sync_prompts.sh
  gate_run "agent prompt parity" tools/sync_prompts.sh check
  gate_tool_run "actionlint" actionlint
  gate_tool_run "yamllint (strict)" yamllint --strict .github .yamllint.yaml
  gate_tool_run "gitleaks: history" gitleaks detect --source . \
    --config .gitleaks.toml --redact --no-banner
  gate_tool_run "gitleaks: working tree" gitleaks detect --no-git --source . \
    --config .gitleaks.toml --redact --no-banner

  if [[ "${GATE_FAILURES}" -ne 0 ]]; then
    die "Zero-Toy-Code gate failed: ${GATE_FAILURES} gate(s)"
  fi
  printf '\nZero-Toy-Code gate passed.\n'
}

cmd_toolchain() {
  [[ -f "${REQUIREMENTS_DEV}" ]] || die "missing ${REQUIREMENTS_DEV}"
  command -v python3 >/dev/null 2>&1 || die "python3 is required to build the toolchain"
  python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
    || die "the gate toolchain needs Python 3.11 or later"
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    info "creating" "${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
  fi
  info "installing" "hash-verified toolchain from requirements-dev.txt"
  "${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check --quiet \
    --require-hashes -r "${REQUIREMENTS_DEV}"
  info "ready" "${VENV_DIR}"
  if ! command -v gitleaks >/dev/null 2>&1; then
    info "note" "gitleaks is not on PATH; install it with the system package manager"
    info "" "(apt-get install gitleaks, pkg install gitleaks, or brew install gitleaks)"
  fi
}

cmd_audit_deps() {
  local bin
  bin="$(gate_tool pip-audit)"
  [[ -n "${bin}" ]] || die "pip-audit is not installed (run ./setup.sh toolchain)"
  info "audit" "requirements-dev.txt against the PyPI vulnerability service"
  "${bin}" --strict --require-hashes --disable-pip -r "${REQUIREMENTS_DEV}"
}

cmd_gate_install() {
  command -v git >/dev/null 2>&1 || die "git is required to install the gate hook"
  git -C "${REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1 \
    || die "not a git repository: ${REPO_ROOT}"
  local hook="${REPO_ROOT}/.githooks/pre-commit"
  [[ -f "${hook}" ]] || die "pre-commit hook missing at ${hook}"
  chmod +x "${hook}"
  git -C "${REPO_ROOT}" config core.hooksPath ".githooks"
  info "installed" "core.hooksPath -> .githooks"
  printf '\nZero-Toy-Code pre-commit hook active.\n'
  printf 'Disable with: git config --unset core.hooksPath\n'
}

case "${1:-}" in
  agy-install)      cmd_agy_install;;
  agy-validate)     cmd_agy_validate;;
  agy-status)       cmd_agy_status;;
  agy-uninstall)    cmd_agy_uninstall;;
  claude-install)   cmd_claude_install "${2:-}";;
  claude-status)    cmd_claude_status;;
  claude-uninstall) cmd_claude_uninstall;;
  install)          cmd_install "${2:-}";;
  open)             shift; cmd_open "$@";;
  status)           cmd_status;;
  uninstall)        cmd_uninstall;;
  all)              cmd_all "${2:-}";;
  scan)             cmd_scan;;
  toolchain)        cmd_toolchain;;
  audit-deps)       cmd_audit_deps;;
  gate-install)     cmd_gate_install;;
  --help|-h|help)   awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0";;
  *)                echo "usage: $0 {agy-install|agy-validate|agy-status|agy-uninstall|claude-install [--force]|claude-status|claude-uninstall|install [--force]|open [--no-install]|status|uninstall|all|scan|toolchain|audit-deps|gate-install|--help}" >&2; exit 2;;
esac
