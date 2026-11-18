#!/usr/bin/env bash
#
# setup.sh - install and manage aureon-redteam agent library for both Antigravity (agy)
# and opencode environments, adhering to strict enterprise zero-toy-code standards.
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
#   ./setup.sh install [--force]      Install/sync opencode agents to global config dir
#   ./setup.sh open [--no-install]    Install opencode agents (unless --no-install), then open opencode
#   ./setup.sh status                 Compare global opencode copies against this repo
#   ./setup.sh uninstall              Remove global opencode copies matching this repo
#   ./setup.sh all                    Install both AGY plugin and opencode agents globally
#   ./setup.sh --help
#

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCODE_SRC="$REPO_ROOT/.opencode/agents"
AGY_PLUGIN_SRC="$REPO_ROOT/plugins/aureon-redteam"
AGY_WORKSPACE_DIR="$REPO_ROOT/.agents"

# Opencode Config Directory
if [[ -n "${OPENCODE_CONFIG_DIR:-}" ]]; then
  OPENCODE_DIR="$OPENCODE_CONFIG_DIR"
elif [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
  OPENCODE_DIR="$XDG_CONFIG_HOME/opencode"
else
  OPENCODE_DIR="$HOME/.config/opencode"
fi
OPENCODE_AGENT_DIR="$OPENCODE_DIR/agents"
OPENCODE_BACKUP_DIR="$OPENCODE_AGENT_DIR/.setup-backup"

# AGY Config Directory
AGY_CONFIG_DIR="${AGY_CONFIG_DIR:-$HOME/.gemini/config}"
AGY_PLUGIN_DEST="$AGY_CONFIG_DIR/plugins/aureon-redteam"

die() { printf 'setup: %s\n' "$*" >&2; exit 1; }
info() { printf '  %-12s %s\n' "$@"; }

find_opencode() {
  local bin
  if command -v opencode >/dev/null 2>&1; then
    bin="$(command -v opencode)"
  elif [[ -x "$HOME/.local/bin/opencode" ]]; then
    bin="$HOME/.local/bin/opencode"
  else
    return 1
  fi
  printf '%s' "$bin"
}

find_agy() {
  local bin
  if command -v agy >/dev/null 2>&1; then
    bin="$(command -v agy)"
  elif [[ -x "$HOME/.gemini/antigravity-cli/bin/agy" ]]; then
    bin="$HOME/.gemini/antigravity-cli/bin/agy"
  else
    return 1
  fi
  printf '%s' "$bin"
}

require_opencode_sources() {
  [[ -d "$OPENCODE_SRC" ]] || die "opencode agent library not found at $OPENCODE_SRC"
  local any=0
  for f in "$OPENCODE_SRC"/*; do
    [[ -e "$f" ]] && any=1
  done
  [[ "$any" -eq 1 ]] || die "opencode agent directory is empty: $OPENCODE_SRC"
}

require_agy_sources() {
  [[ -d "$AGY_PLUGIN_SRC" ]] || die "AGY plugin not found at $AGY_PLUGIN_SRC"
  [[ -f "$AGY_PLUGIN_SRC/plugin.json" ]] || die "AGY plugin.json missing at $AGY_PLUGIN_SRC/plugin.json"
}

validate_json() {
  local bad=0
  for j in "$OPENCODE_SRC"/*.json "$AGY_PLUGIN_SRC"/*.json; do
    [[ -e "$j" ]] || continue
    if python3 -m json.tool "$j" >/dev/null 2>&1; then
      info "ok           json: $(basename "$j")"
    else
      printf '  !! invalid JSON: %s\n' "$j" >&2
      bad=1
    fi
  done
  [[ "$bad" -eq 0 ]] || die "invalid JSON found"
}

# -----------------------------------------------------------------------------
# AGY Commands
# -----------------------------------------------------------------------------

cmd_agy_validate() {
  require_agy_sources
  local agy_bin
  if agy_bin="$(find_agy)"; then
    info "validating   $AGY_PLUGIN_SRC with $agy_bin"
    "$agy_bin" plugin validate "$AGY_PLUGIN_SRC"
  else
    die "agy executable not found on PATH"
  fi
}

cmd_agy_install() {
  require_agy_sources
  local agy_bin
  if agy_bin="$(find_agy)"; then
    info "installing   aureon-redteam AGY plugin globally"
    "$agy_bin" plugin install "$AGY_PLUGIN_SRC"
    printf '\nAGY plugin installed successfully!\n'
    printf 'List available agents: agy agents\n'
    printf 'Launch with agent:     agy --agent aureon (or redteam, coder, debugger, auditor, deepdive)\n'
  else
    # Fallback to direct directory sync if agy command unavailable
    info "syncing      $AGY_PLUGIN_SRC to $AGY_PLUGIN_DEST"
    mkdir -p "$AGY_CONFIG_DIR/plugins"
    rm -rf "$AGY_PLUGIN_DEST"
    cp -r "$AGY_PLUGIN_SRC" "$AGY_PLUGIN_DEST"
    info "installed    copied to $AGY_PLUGIN_DEST"
  fi
}

cmd_agy_status() {
  require_agy_sources
  printf 'AGY Workspace:  %s\n' "$AGY_WORKSPACE_DIR"
  printf 'AGY Plugin Src: %s\n' "$AGY_PLUGIN_SRC"
  printf 'AGY Global Dst: %s\n\n' "$AGY_PLUGIN_DEST"
  local agy_bin
  if agy_bin="$(find_agy)"; then
    info "agy-binary   $agy_bin"
    printf 'Installed Plugins:\n'
    "$agy_bin" plugin list 2>&1 || true
    printf '\nAvailable AGY Agents:\n'
    "$agy_bin" agents 2>&1 || true
  else
    info "warning      agy command not found"
  fi
}

cmd_agy_uninstall() {
  local agy_bin
  if agy_bin="$(find_agy)"; then
    "$agy_bin" plugin uninstall aureon-redteam 2>&1 || true
  fi
  if [[ -d "$AGY_PLUGIN_DEST" ]]; then
    rm -rf "$AGY_PLUGIN_DEST"
    info "removed      $AGY_PLUGIN_DEST"
  else
    info "nothing      AGY plugin not found at $AGY_PLUGIN_DEST"
  fi
}

# -----------------------------------------------------------------------------
# Opencode Commands
# -----------------------------------------------------------------------------

cmd_install() {
  local force=0
  [[ "${1:-}" == "--force" ]] && force=1
  require_opencode_sources
  mkdir -p "$OPENCODE_AGENT_DIR"
  for src in "$OPENCODE_SRC"/*; do
    local name dest
    name="$(basename "$src")"
    dest="$OPENCODE_AGENT_DIR/$name"
    if [[ -f "$dest" ]]; then
      if cmp -s "$src" "$dest"; then
        info "up-to-date   $name"
        continue
      fi
      if [[ "$force" -eq 0 ]]; then
        mkdir -p "$OPENCODE_BACKUP_DIR"
        cp "$dest" "$OPENCODE_BACKUP_DIR/$name.backup"
        info "backed up    $OPENCODE_BACKUP_DIR/$name.backup"
      elif [[ "$force" -eq 1 ]]; then
        info "replaced     $name (--force, no backup)"
      fi
    fi
    cp "$src" "$dest"
    info "installed    $name"
  done
  validate_json
  printf '\nInstalled into %s\n' "$OPENCODE_AGENT_DIR"
  printf 'Quit and restart opencode for the agents to load (config is read once at startup).\n'
}

cmd_status() {
  require_opencode_sources
  printf 'Source: %s\nGlobal: %s\n\n' "$OPENCODE_SRC" "$OPENCODE_AGENT_DIR"
  local missing=0
  for src in "$OPENCODE_SRC"/*; do
    local name dest
    name="$(basename "$src")"
    dest="$OPENCODE_AGENT_DIR/$name"
    if [[ ! -e "$dest" ]]; then
      info "missing      $name"
      missing=1
    elif cmp -s "$src" "$dest"; then
      info "in-sync      $name"
    else
      info "diverged     $name"
      missing=1
    fi
  done
  if [[ -d "$OPENCODE_AGENT_DIR" ]]; then
    for dest in "$OPENCODE_AGENT_DIR"/*.md; do
      [[ -e "$dest" ]] || continue
      name="$(basename "$dest")"
      [[ -e "$OPENCODE_SRC/$name" ]] || info "external     $name (not from this repo)"
    done
  fi
  printf '\n'
  [[ "$missing" -eq 0 ]] && info "result       all opencode agents in sync" || info "result       opencode agents need installation (run: ./setup.sh install)"
}

cmd_open() {
  local bin
  require_opencode_sources
  if [[ "${1:-}" != "--no-install" ]]; then
    cmd_install
    printf '\n'
  fi
  if bin="$(find_opencode)"; then
    info "launching    $bin ($("$bin" --version 2>/dev/null || printf 'version unknown'))"
    exec "$bin" "$@"
  else
    die "opencode not found on PATH — install it first (https://opencode.ai), then re-run"
  fi
}

cmd_uninstall() {
  require_opencode_sources
  local removed=0
  for src in "$OPENCODE_SRC"/*; do
    local name dest
    name="$(basename "$src")"
    dest="$OPENCODE_AGENT_DIR/$name"
    if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
      rm "$dest"
      info "removed      $name"
      removed=1
    fi
  done
  [[ "$removed" -eq 0 ]] && info "nothing      no repo-matching global copies to remove"
}

cmd_all() {
  info "installing   opencode agents..."
  cmd_install "${1:-}"
  printf '\n'
  info "installing   AGY plugin..."
  cmd_agy_install
  printf '\n'
  info "complete     both opencode agents and AGY plugin are installed globally.\n"
}

case "${1:-}" in
  agy-install)   cmd_agy_install;;
  agy-validate)  cmd_agy_validate;;
  agy-status)    cmd_agy_status;;
  agy-uninstall) cmd_agy_uninstall;;
  install)       cmd_install "${2:-}";;
  open)          shift; cmd_open "$@";;
  status)        cmd_status;;
  uninstall)     cmd_uninstall;;
  all)           cmd_all "${2:-}";;
  --help|-h|help) sed -n '2,24p' "$0";;
  *)             echo "usage: $0 {agy-install|agy-validate|agy-status|agy-uninstall|install [--force]|open [--no-install]|status|uninstall|all|--help}" >&2; exit 2;;
esac
