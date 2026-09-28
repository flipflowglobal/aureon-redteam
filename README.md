# AUREON + Red Team — opencode agent library

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

## How opencode loads these agents

The canonical auto-discovered form is a **Markdown file with YAML frontmatter**
(`description`, `mode`, `permission`, ...). The file name becomes the agent
name (`aureon.md` ⇒ agent `aureon`). opencode discovers them in two scopes:

| Scope | Directory | When it applies |
|-------|-----------|-----------------|
| Project | `.opencode/agents/` | Always, as long as you run opencode in this repo |
| Global  | `~/.config/opencode/agents/` | Every repo, user-wide |

The `.json` files in this repo are **metadata companions** (mode/color/steps and
a `promptFile` reference) — opencode itself discovers agents from the `.md`
files. Nothing beyond the `.md` files is required to use the agents.

Config is read once at startup: **quit and restart opencode** after installing.

## Quick start

Clone the repo and the agents work immediately at project scope:

```sh
git clone https://github.com/flipflowglobal/aureon-redteam
cd aureon-redteam
opencode          # Tab → aureon, or @mention any subagent below
```

To make the whole library available **in every repo** (recommended when you use
these agents with other checkouts), use `setup.sh`:

```sh
./setup.sh install       # sync agents into ~/.config/opencode/agents/
./setup.sh open          # install (if needed), then launch opencode
./setup.sh status        # compare global copies against this repo
./setup.sh uninstall     # remove global copies that exactly match this repo
```

`install` never silently destroys existing files: if a same-named global file
differs, it is first backed up to `~/.config/opencode/agents/.setup-backup/`.
Pass `--force` to skip back-ups. Respect `OPENCODE_CONFIG_DIR` (or
`XDG_CONFIG_HOME`) to target a non-default config dir.

## Agents

| File | Mode | Purpose |
|------|------|---------|
| `aureon.md` | primary | Autonomous DeFi trading & quantitative engineering platform |
| `redteam.md` | primary | Offensive security testing with authorization gating |
| `auditor.md` | subagent | Security audit/review — deny-by-default, edit-limited |
| `coder.md` | subagent | Feature implementation and refactoring (Rust-first) |
| `debugger.md` | subagent | Failure triage, compile-error tracing, regressions |
| `deepdive.md` | subagent | Root-cause investigation and model diagnostics |
| `aureon.json` | — | Metadata companion (mode/color/steps, `promptFile`) |
| `redteam.json` | — | Metadata companion (mode/color/steps, `promptFile`) |

The `.md` agents carry their own YAML frontmatter (`description`, `mode`,
`permission`) and are referenced by filename.

Local per-install state (`environment.d`, session files, local agent JSON)
is excluded via `.gitignore`.