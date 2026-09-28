# AUREON + Red Team — opencode agent definitions

Opencode agent definitions for a pair of specialized engineering agents
maintained by the flipflowglobal org:

- **AUREON** — autonomous DeFi trading & quantitative engineering agent
  (`.opencode/agents/aureon.{json,md}`)
- **Red Team** — offensive security testing agent: recon, web exploitation,
  credential attacks, wireless analysis, payload generation, reverse-shell
  listening, cloud misconfiguration, and supply-chain auditing
  (`.opencode/agents/redteam.{json,md}`)

## Install

Drop the contents of `.opencode/agents/` into your project's `.opencode/agents/`
directory (or `~/.config/opencode/agents/` for user-wide availability), then
restart opencode. The agent is referenced by its `name` (`aureon`, `redteam`).

## Contents

| File | Purpose |
|------|---------|
| `aureon.json` | AUREON agent definition (mode, color, steps, prompt file) |
| `aureon.md` | AUREON system prompt with platform goals and standards |
| `redteam.json` | Red Team agent definition |
| `redteam.md` | Red Team system prompt, module map, authorization rules |

Local per-install state (`environment.d`, session files, local agent JSON)
is excluded via `.gitignore`.