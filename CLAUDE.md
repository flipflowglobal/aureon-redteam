# AUREON + Red Team Suite — Claude Code Workspace

Claude Code is configured as a first-class harness for this repository alongside
Antigravity (AGY) and OpenCode. All three share the same agent prompts and the
same enterprise rules.

## Governing rules (binding)

Every response in this workspace is bound by the **Enterprise Engineering
Guidelines & Zero-Toy-Code Standard**. The full standard is imported here so it
loads as project memory:

@AGENTS.md

In short: no stubs, placeholders, `TODO`/`FIXME`, mock data, simulated tool
output, or truncated code; full error handling and strict typing; real,
executed actions only; and authorization-first execution for any offensive
operation (never bypass `PolicyEngine::authorize()`, offline-by-default unless a
signed engagement record permits egress).

## Subagents

Project subagents live in `.claude/agents/` and are invoked with the `Task`
tool or `@agent-<name>`:

| Agent | Role |
|---|---|
| `aureon` | Autonomous DeFi trading & quantitative engineering (stochastic calculus, Almgren-Chriss execution, Cornish-Fisher VaR). |
| `redteam` | Offensive security testing with authorization gating; evidence-backed findings only. |
| `auditor` | Read-only security audit, policy-enforcement and zero-toy-code compliance review. |
| `coder` | Production-grade feature implementation and refactoring. |
| `debugger` | Defect triage, compiler-error tracing, test-regression and panic repair. |
| `deepdive` | Root-cause investigation and attack-path / cognitive-pipeline analysis. |

`auditor` is read-only (no `Write`/`Edit`); the rest may edit. The prompt bodies
are byte-identical to the OpenCode canonical bodies in `.opencode/agents/`.

## Skills

Operational procedures are available as skills in `.claude/skills/`:

- `redteam-ops` — authorized offensive assessment and policy-authorization procedure.
- `aureon-quant` — quantitative modeling, risk-engine and DeFi-execution procedure.

## Prompt parity

The canonical agent body is the content of `.opencode/agents/*.md` after its
frontmatter. The Claude Code files in `.claude/agents/*.md` reuse that body
verbatim and only supply Claude-Code frontmatter (`name`, `description`,
`tools`, `model`, `color`). Keep them in sync when the canonical body changes.
