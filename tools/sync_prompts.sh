#!/usr/bin/env bash
#
# sync_prompts.sh - keep agent prompt bodies identical across harnesses.
#
# Canonical sources:
#   prompt body        -> .opencode/agents/<agent>.md   (content after frontmatter)
#   AGY frontmatter    -> .agents/agents/<agent>.md     (name, description, tools)
#   plugin mirror      -> plugins/aureon-redteam/agents/<agent>.md
#   Claude Code mirror -> .claude/agents/<agent>.md      (name, description, tools, model, color)
#
# `sync` rewrites each AGY and Claude Code agent as its own preserved
# frontmatter plus the OpenCode body, then mirrors the AGY file into the
# packaged plugin. `check` exits non-zero on any drift: body mismatch,
# description mismatch, plugin divergence, Claude Code divergence, or an agent
# present in one harness tree but missing from another.
#
# Usage:
#   tools/sync_prompts.sh check
#   tools/sync_prompts.sh sync
#
# AGENTS_ROOT overrides the repository root (used by test_sync_prompts.sh).

set -euo pipefail

ROOT="${AGENTS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OC_AGENTS="${ROOT}/.opencode/agents"
AGY_AGENTS="${ROOT}/.agents/agents"
PLUGIN_AGENTS="${ROOT}/plugins/aureon-redteam/agents"
CLAUDE_AGENTS="${ROOT}/.claude/agents"

failures=0
synced=0

die() { printf 'sync_prompts: %s\n' "$*" >&2; exit 1; }
info() { printf '  %-12s %s\n' "$@"; }
fail() { printf '  %-12s %s\n' "FAIL" "$*" >&2; failures=$((failures + 1)); }

# The awk programs are constants run directly by `awk` rather than wrapped in
# shell functions: a function called on the left of `||` runs with `set -e`
# disabled, while a failing `awk` here propagates its own exit status (2 on a
# malformed file) to the explicit handler at each call site. Each program reads
# its input once: O(n) time in the file size, O(1) extra memory.

# Print the frontmatter block, including both '---' delimiters.
readonly AWK_FRONTMATTER='
  { print }
  /^---[[:space:]]*$/ { count++; if (count == 2) exit }
  END { if (count < 2) exit 2 }
'

# Print every line after the closing '---' delimiter, byte for byte.
readonly AWK_BODY='
  body { print; next }
  /^---[[:space:]]*$/ { count++; if (count == 2) { body = 1 } ; next }
  END { if (count < 2 || body == 0) exit 2 }
'

# Print the frontmatter with its description replaced by ENVIRON["CANONICAL_DESC"].
# The value is read from the environment, not `awk -v`, because `-v` expands
# backslash escapes and would rewrite a description such as `C:\path`.
readonly AWK_REWRITE_DESCRIPTION='
  /^---[[:space:]]*$/ { count++; print; next }
  count == 1 && !done && /^description:/ {
    print "description: " ENVIRON["CANONICAL_DESC"]; done = 1; next
  }
  { print }
  END { if (count < 2 || !done) exit 2 }
'

# Print the value of the frontmatter `description:` field.
readonly AWK_DESCRIPTION='
  /^---[[:space:]]*$/ { count++; next }
  count == 1 && /^description:/ { sub(/^description:[[:space:]]*/, ""); print; exit }
  count >= 2 { exit }
'

require_dirs() {
  [[ -d "${OC_AGENTS}" ]] || die "missing canonical directory: ${OC_AGENTS}"
  [[ -d "${AGY_AGENTS}" ]] || die "missing AGY directory: ${AGY_AGENTS}"
  [[ -d "${PLUGIN_AGENTS}" ]] || die "missing plugin directory: ${PLUGIN_AGENTS}"
  [[ -d "${CLAUDE_AGENTS}" ]] || die "missing Claude Code directory: ${CLAUDE_AGENTS}"
}

check_agent() {
  local name="$1"
  local src="${OC_AGENTS}/${name}"
  local agy="${AGY_AGENTS}/${name}"
  local plug="${PLUGIN_AGENTS}/${name}"
  local claude="${CLAUDE_AGENTS}/${name}"
  local before="${failures}"

  if [[ ! -f "${agy}" ]]; then
    fail "${name}: missing AGY definition"
    return
  fi
  if [[ ! -f "${plug}" ]]; then
    fail "${name}: missing plugin definition"
    return
  fi
  if [[ ! -f "${claude}" ]]; then
    fail "${name}: missing Claude Code definition"
    return
  fi

  # pipefail makes each assignment fail when awk rejects a malformed file.
  local src_body agy_body plug_body claude_body
  src_body="$(awk "${AWK_BODY}" "${src}" | sha256sum | cut -d' ' -f1)" \
    || { fail "${name}: unreadable OpenCode body"; return; }
  agy_body="$(awk "${AWK_BODY}" "${agy}" | sha256sum | cut -d' ' -f1)" \
    || { fail "${name}: unreadable AGY body"; return; }
  plug_body="$(awk "${AWK_BODY}" "${plug}" | sha256sum | cut -d' ' -f1)" \
    || { fail "${name}: unreadable plugin body"; return; }
  claude_body="$(awk "${AWK_BODY}" "${claude}" | sha256sum | cut -d' ' -f1)" \
    || { fail "${name}: unreadable Claude Code body"; return; }

  [[ "${src_body}" == "${agy_body}" ]] || fail "${name}: AGY body differs from OpenCode body (${agy_body} != ${src_body})"
  [[ "${agy_body}" == "${plug_body}" ]] || fail "${name}: plugin body differs from AGY body (${plug_body} != ${agy_body})"
  [[ "${src_body}" == "${claude_body}" ]] || fail "${name}: Claude Code body differs from OpenCode body (${claude_body} != ${src_body})"
  cmp -s "${agy}" "${plug}" || fail "${name}: plugin file is not byte-identical to AGY file"

  local src_desc agy_desc claude_desc
  src_desc="$(awk "${AWK_DESCRIPTION}" "${src}")"
  agy_desc="$(awk "${AWK_DESCRIPTION}" "${agy}")"
  claude_desc="$(awk "${AWK_DESCRIPTION}" "${claude}")"
  [[ -n "${src_desc}" ]] || fail "${name}: OpenCode frontmatter has no description"
  [[ -n "${agy_desc}" ]] || fail "${name}: AGY frontmatter has no description"
  [[ -n "${claude_desc}" ]] || fail "${name}: Claude Code frontmatter has no description"
  [[ "${src_desc}" == "${agy_desc}" ]] || fail "${name}: AGY description differs from OpenCode"
  [[ "${src_desc}" == "${claude_desc}" ]] || fail "${name}: Claude Code description differs from OpenCode"

  if [[ "${failures}" -eq "${before}" ]]; then
    info "ok" "${name}"
  fi
}

check_orphans() {
  local f name
  for f in "${AGY_AGENTS}"/*.md; do
    [[ -e "${f}" ]] || continue
    name="$(basename "${f}")"
    [[ -f "${OC_AGENTS}/${name}" ]] || fail "${name}: AGY agent has no OpenCode source"
  done
  for f in "${PLUGIN_AGENTS}"/*.md; do
    [[ -e "${f}" ]] || continue
    name="$(basename "${f}")"
    [[ -f "${OC_AGENTS}/${name}" ]] || fail "${name}: plugin agent has no OpenCode source"
  done
  for f in "${CLAUDE_AGENTS}"/*.md; do
    [[ -e "${f}" ]] || continue
    name="$(basename "${f}")"
    [[ -f "${OC_AGENTS}/${name}" ]] || fail "${name}: Claude Code agent has no OpenCode source"
  done
}

cmd_check() {
  require_dirs
  local src name
  for src in "${OC_AGENTS}"/*.md; do
    [[ -e "${src}" ]] || continue
    name="$(basename "${src}")"
    check_agent "${name}"
  done
  check_orphans
  if [[ "${failures}" -ne 0 ]]; then
    printf '\nprompt parity: %d failure(s)\n' "${failures}" >&2
    exit 1
  fi
  printf '\nprompt parity: all agent bodies and descriptions match\n'
}

cmd_sync() {
  require_dirs
  local tmp
  tmp="$(mktemp)"
  trap 'rm -f "${tmp:-}"' EXIT

  local src name agy plug claude fm cfm src_desc
  for src in "${OC_AGENTS}"/*.md; do
    [[ -e "${src}" ]] || continue
    name="$(basename "${src}")"
    agy="${AGY_AGENTS}/${name}"
    plug="${PLUGIN_AGENTS}/${name}"
    claude="${CLAUDE_AGENTS}/${name}"

    [[ -f "${agy}" ]] || die "no AGY frontmatter source for ${name}"
    [[ -f "${plug}" ]] || die "no plugin target for ${name}"
    [[ -f "${claude}" ]] || die "no Claude Code frontmatter source for ${name}"

    src_desc="$(awk "${AWK_DESCRIPTION}" "${src}")"
    [[ -n "${src_desc}" ]] || die "no description in ${src}"

    # AGY + plugin mirror: AGY frontmatter + canonical body.
    fm="$(awk "${AWK_FRONTMATTER}" "${agy}")" || die "cannot read frontmatter from ${agy}"
    fm="$(CANONICAL_DESC="${src_desc}" awk "${AWK_REWRITE_DESCRIPTION}" <<<"${fm}")" \
      || die "cannot align description in ${agy}"
    printf '%s\n' "${fm}" > "${tmp}"
    awk "${AWK_BODY}" "${src}" >> "${tmp}" || die "cannot read body from ${src}"
    cp "${tmp}" "${agy}"
    cp "${agy}" "${plug}"

    # Claude Code mirror: its own preserved frontmatter + canonical body.
    cfm="$(awk "${AWK_FRONTMATTER}" "${claude}")" || die "cannot read frontmatter from ${claude}"
    cfm="$(CANONICAL_DESC="${src_desc}" awk "${AWK_REWRITE_DESCRIPTION}" <<<"${cfm}")" \
      || die "cannot align description in ${claude}"
    printf '%s\n' "${cfm}" > "${tmp}"
    awk "${AWK_BODY}" "${src}" >> "${tmp}" || die "cannot read body from ${src}"
    cp "${tmp}" "${claude}"

    synced=$((synced + 1))
  done
  printf 'sync_prompts: wrote %d AGY/plugin/Claude agents from OpenCode bodies\n' "${synced}"
}

case "${1:-}" in
  check) cmd_check ;;
  sync) cmd_sync ;;
  *) printf 'usage: %s {check|sync}\n' "$0" >&2; exit 2 ;;
esac
