# AUREON + Red Team — opencode agent definitions

Opencode agent definitions for a set of specialized engineering agents
maintained by the flipflowglobal org:

- **AUREON** — autonomous DeFi trading & quantitative engineering agent
  (`.opencode/agents/aureon.{json,md}`)
- **Red Team** — offensive security testing agent: recon, web exploitation,
  credential attacks, wireless analysis, payload generation, reverse-shell
  listening, cloud misconfiguration, and supply-chain auditing
  (`.opencode/agents/redteam.{json,md}`)
- **Auditor** — security audit and code review agent with a deny-by-default,
  edit-limited permission surface (`.opencode/agents/auditor.md`)
- **Coder** — production-grade Rust implementation and refactoring agent
  (`.opencode/agents/coder.md`)
- **Debugger** — failure triage, compile-error tracing, and test-regression
  diagnosis agent (`.opencode/agents/debugger.md`)
- **Deep Dive** — root-cause investigation agent for attack-path modeling,
  belief propagation, and language-model diagnostics
  (`.opencode/agents/deepdive.md`)

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
| `auditor.md` | Auditor agent: audit/review, deny-by-default permissions |
| `coder.md` | Coder agent: feature implementation and refactoring |
| `debugger.md` | Debugger agent: failure triage and regression diagnosis |
| `deepdive.md` | Deep Dive agent: root-cause and model diagnostics |

The `.md` agents carry their own YAML frontmatter (`description`, `mode`,
`permission`) and are referenced by filename; the two `.json` agents point at
companion prompt files.

Local per-install state (`environment.d`, session files, local agent JSON)
is excluded via `.gitignore`.