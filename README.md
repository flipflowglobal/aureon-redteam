# AUREON + Red Team Agent Suite

Enterprise-grade quantitative engineering and offensive security testing agent library maintained by the flipflowglobal organization, configured for **Antigravity (AGY)**, **Claude Code**, and **OpenCode**.

All deliverables generated with these agent configurations are strictly governed by the **Enterprise Engineering Guidelines & Zero-Toy-Code Standard**: full codebase implementations, exhaustive error handling, strict type safety, real working actions, and zero tolerance for stubs, placeholders, or fake examples.

---

## Agent Roster

| Agent | Scope | Focus & Enterprise Mission |
|---|---|---|
| **AUREON** (`aureon`) | Primary / Subagent | Autonomous DeFi trading & quantitative engineering platform: stochastic calculus, Almgren-Chriss optimal execution, Cornish-Fisher VaR, and zero-toy-code production systems. |
| **Red Team** (`redteam`) | Primary / Subagent | Offensive security testing with authorization gating: recon, web exploitation, credential attacks, wireless analysis, payload generation, reverse shell listening, cloud misconfiguration, and supply-chain auditing with real working actions. |
| **Auditor** (`auditor`) | Primary / Subagent | Security audit and code review agent: policy enforcement verification, authorization-gate auditing, findings validation, append-only ledger integrity, and zero-toy-code compliance checking. |
| **Coder** (`coder`) | Primary / Subagent | Production-grade feature implementation and refactoring (Rust 2024, Python strict typing, systems code) following the file-by-file iterative quality loop. |
| **Debugger** (`debugger`) | Primary / Subagent | Defect triage, compiler-error tracing, test-regression repair, and runtime panic diagnosis with zero temporary stubs or hack bypasses. |
| **Deep Dive** (`deepdive`) | Primary / Subagent | Root-cause investigation, cognitive reasoning pipelines, belief propagation across attack graphs, and neural language model perplexity diagnostics. |

---

## Antigravity (AGY) Integration

Antigravity natively discovers agents and rules in two scopes:

1. **Workspace Scope (`.agents/` and `AGENTS.md`)**:
   - Agent definitions: `.agents/agents/*.md`
   - Workspace rules: `AGENTS.md` and `.agents/rules/AGENTS.md` (automatically applied by AGY across this directory and subdirectories)
   - Procedures and runbooks: `.agents/skills/`
2. **Global Plugin Scope (`~/.gemini/config/plugins/aureon-redteam`)**:
   - The entire suite is packaged as a standard Antigravity plugin in `plugins/aureon-redteam/`.
   - Installable globally across all repositories using `agy plugin install` or `./setup.sh agy-install`.

### AGY Quick Start

```sh
# Verify AGY plugin integrity and zero-toy-code compliance
./setup.sh agy-validate

# Install AGY plugin globally
./setup.sh agy-install

# List available AGY agents
agy agents

# Launch an interactive or command session with a specific agent
agy --agent aureon
agy --agent redteam
agy --agent coder
agy --agent auditor
agy --agent debugger
agy --agent deepdive
```

---

## OpenCode Integration

OpenCode discovers agents via `.opencode/agents/*.md` at project scope and in `~/.config/opencode/agents/` at global scope:

```sh
# Sync agents into ~/.config/opencode/agents/
./setup.sh install

# Launch opencode
./setup.sh open

# Compare global copies against this repo
./setup.sh status
```

To install AGY, Claude Code, and OpenCode configurations globally in one command:
```sh
./setup.sh all
```

---

## Claude Code Integration

Claude Code discovers project configuration under `.claude/` in this repo and
global agents/skills in `~/.claude/`:

```
.claude/
  agents/<agent>.md        # the six subagents, invoked with @agent-<name> or the Task tool
  skills/<skill>/SKILL.md   # redteam-ops and aureon-quant operational procedures
  hooks/policy_wrap.py      # PreToolUse authorization wrap (deny-by-default)
  hooks/test_policy_wrap.py # behavioural tests for the wrap
  settings.json             # permissions + the policy-wrap hook wiring
CLAUDE.md                   # project memory; imports AGENTS.md (Zero-Toy-Code standard)
```

```sh
# Install Claude Code agents + skills into ~/.claude (global, all repos)
./setup.sh claude-install

# Compare global Claude Code copies against this repo
./setup.sh claude-status

# Remove repo-matching global Claude Code copies
./setup.sh claude-uninstall
```

`auditor` is read-only (no `Write`/`Edit`); the other five may edit. The policy
wrap and `settings.json` are **project-scoped** — they stay in this repo's
`.claude/` and are not copied globally, because the authorization wrap resolves
engagement records relative to the active project.

### Authorization policy wrap

`.claude/hooks/policy_wrap.py` is a `PreToolUse` hook that gates every `Bash`
call against the engagement model in [`engagements/`](engagements/README.md).
It is **deny-by-default** and fails closed:

- Non-offensive commands defer to Claude Code's normal permission flow.
- Offensive tooling (`nmap`, `sqlmap`, `hydra`, …) and off-box egress against
  loopback / RFC 5737 documentation ranges are allowed as local-testbed traffic.
- Against any other target, the operation is **denied** unless an engagement
  record is `status: active`, carries both internal and owner approval, is
  inside its validity window, enables network access, and explicitly lists the
  target (honouring `excluded_targets`).
- Denial-of-service tooling and blanket scope (`0.0.0.0/0`, `::/0`) are denied
  unconditionally and cannot be enabled by any record.

```sh
python3 .claude/hooks/test_policy_wrap.py   # 21 behavioural tests for the wrap
```

---

## Prompt Parity

Every agent prompt body is identical across harnesses; only platform-specific frontmatter differs.

- **Canonical body**: `.opencode/agents/*.md` (content after the frontmatter block).
- **AGY frontmatter**: `.agents/agents/*.md` supplies `name`, `description`, `mainAgent`, `subagent`, and the `tools` allowlist.
- **AGY mirror**: `plugins/aureon-redteam/agents/*.md` is byte-identical to `.agents/agents/`.
- **Claude Code frontmatter**: `.claude/agents/*.md` supplies `name`, `description`, `tools`, `model`, and `color` over the same canonical body.

```sh
tools/sync_prompts.sh check   # exit non-zero on body, description, plugin, Claude Code, or roster drift
tools/sync_prompts.sh sync    # rewrite AGY, plugin, and Claude Code files from the OpenCode bodies
tools/test_sync_prompts.sh    # behavioural tests for the parity tooling
```

`sync` preserves each AGY and Claude Code frontmatter block except `description`, which is aligned to the canonical OpenCode value. A new agent must exist in `.opencode/agents/`, `.agents/agents/`, and `.claude/agents/` before it can be synced; `check` reports a missing counterpart instead of inventing one.

---

## Enterprise Zero-Toy-Code Standard

All agents operate under the mandatory rules defined in [AGENTS.md](file:///data/data/com.termux/files/home/aureon-redteam/AGENTS.md):

- **No Placeholders or Stubs**: Zero tolerance for `TODO`, `FIXME`, `pass`, `...`, `todo!()`, `unimplemented!()`, or truncated code blocks.
- **No Mock / Toy Data**: Real schemas, typed interfaces, and production bindings only.
- **Real Working Actions**: All tools and network commands must execute functional operations and process live data. Mock exploits or simulated command outputs are strictly prohibited.
- **Full Codebase Completeness**: Every deliverable must be emitted in full, compile with zero warnings, pass all automated tests, and be immediately deployable.
