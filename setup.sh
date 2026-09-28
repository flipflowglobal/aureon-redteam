#!/usr/bin/env bash
#
# setup.sh - install the flipflowglobal opencode agent library and launch opencode.
#
# Agents live in .opencode/agents/. Each <name>.md file (YAML frontmatter) is the
# canonical auto-loaded agent form. opencode discovers them in two places:
#   project scope:  .opencode/agents/              (used automatically when you
#                                                  open opencode in this repo)
#   global scope:   ~/.config/opencode/agents/     (used in every repo)
#
# This script syncs the library into the global scope so the agents are available
# everywhere, not just inside this checkout. Existing global files with the same
# name are never silently destroyed: differing files are backed up to
# <AGENT_DIR>/.setup-backup/ before being replaced.
#
# Usage:
#   ./setup.sh install [--force]      Install/sync agents to the global config dir
#   ./setup.sh open [--no-install]    Install (unless --no-install), then open opencode
#   ./setup.sh status                 Compare global copies against this repo
#   ./setup.sh uninstall              Remove global copies that exactly match this repo
#   ./setup.sh --help
#
# Environment:
#   OPENCODE_CONFIG_DIR   override the opencode config dir (default: ~/.config/opencode,
#                         or $XDG_CONFIG_HOME/opencode)
#
# opencode reads its config once at startup: quit and restart the TUI after install.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_SRC="$REPO_ROOT/.opencode/agents"

if [[ -n "${OPENCODE_CONFIG_DIR:-}" ]]; then
  CONFIG_DIR="$OPENCODE_CONFIG_DIR"
elif [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
  CONFIG_DIR="$XDG_CONFIG_HOME/opencode"
else
  CONFIG_DIR="$HOME/.config/opencode"
fi
AGENT_DIR="$CONFIG_DIR/agents"
BACKUP_DIR="$AGENT_DIR/.setup-backup"

die() { printf 'setup: %s\n' "$*" >&2; exit 1; }
info() { printf '  %-10s %s\n' "$@"; }

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

require_sources() {
  [[ -d "$AGENT_SRC" ]] || die "agent library not found at $AGENT_SRC"
  local any=0
  for f in "$AGENT_SRC"/*; do
    [[ -e "$f" ]] && any=1
  done
  [[ "$any" -eq 1 ]] || die "agent library directory is empty: $AGENT_SRC"
}

validate_json() {
  local bad=0
  for j in "$AGENT_SRC"/*.json "$AGENT_DIR"/*.json; do
    [[ -e "$j" ]] || continue
    if python3 -m json.tool "$j" >/dev/null 2>&1; then
      info "ok          json: $(basename "$j")"
    else
      printf '  !! invalid JSON: %s\n' "$j" >&2
      bad=1
    fi
  done
  [[ "$bad" -eq 0 ]] || die "invalid JSON found (JSON files are metadata companions; the .md files are what opencode loads)"
}

cmd_install() {
  local force=0
  [[ "${1:-}" == "--force" ]] && force=1
  require_sources
  mkdir -p "$AGENT_DIR"
  for src in "$AGENT_SRC"/*; do
    local name dest
    name="$(basename "$src")"
    dest="$AGENT_DIR/$name"
    if [[ -f "$dest" ]]; then
      if cmp -s "$src" "$dest"; then
        info "up-to-date   $name"
        continue
      fi
      if [[ "$force" -eq 0 ]]; then
        mkdir -p "$BACKUP_DIR"
        cp "$dest" "$BACKUP_DIR/$name.backup"
        info "backed up    $BACKUP_DIR/$name.backup"
      elif [[ "$force" -eq 1 ]]; then
        info "replaced     $name (--force, no backup)"
      fi
    fi
    cp "$src" "$dest"
    info "installed    $name"
  done
  validate_json
  printf '\nInstalled into %s\n' "$AGENT_DIR"
  printf 'Quit and restart opencode for the agents to load (config is read once at startup).\n'
}

cmd_status() {
  require_sources
  printf 'Source: %s\nGlobal: %s\n\n' "$AGENT_SRC" "$AGENT_DIR"
  local missing=0
  for src in "$AGENT_SRC"/*; do
    local name dest
    name="$(basename "$src")"
    dest="$AGENT_DIR/$name"
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
  # Report global agents that are not part of this library (informational only).
  if [[ -d "$AGENT_DIR" ]]; then
    for dest in "$AGENT_DIR"/*.md; do
      [[ -e "$dest" ]] || continue
      name="$(basename "$dest")"
      [[ -e "$AGENT_SRC/$name" ]] || info "external     $name (not from this repo)"
    done
  fi
  local changed=0
  for src in "$AGENT_SRC"/*; do
    local name dest
    name="$(basename "$src")"
    dest="$AGENT_DIR/$name"
    [[ -e "$dest" ]] && ! cmp -s "$src" "$dest" && changed=1
    [[ -e "$dest" ]] || changed=1
  done
  printf '\n'
  [[ "$changed" -eq 0 ]] && info "result       all agents in sync" || info "result       agents need installation (run: ./setup.sh install)"
}

cmd_open() {
  local bin
  require_sources
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
  require_sources
  local removed=0
  for src in "$AGENT_SRC"/*; do
    local name dest
    name="$(basename "$src")"
    dest="$AGENT_DIR/$name"
    if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
      rm "$dest"
      info "removed      $name"
      removed=1
    fi
  done
  [[ "$removed" -eq 0 ]] && info "nothing      no repo-matching global copies to remove"
}

case "${1:-}" in
  install)    cmd_install "${2:-}";;
  open)       shift; cmd_open "$@";;
  status)     cmd_status;;
  uninstall)  cmd_uninstall;;
  --help|-h|help) sed -n '2,20p' "$0";;
  *)          echo "usage: $0 {install [--force]|open [--no-install]|status|uninstall|--help}" >&2; exit 2;;
esac