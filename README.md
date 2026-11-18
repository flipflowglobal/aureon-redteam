# AUREON + Red Team Agent Suite

Enterprise-grade quantitative engineering and offensive security testing agent library maintained by the flipflowglobal organization, configured for both **Antigravity (AGY)** and **OpenCode**.

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

To install both AGY and OpenCode configurations globally in one command:
```sh
./setup.sh all
```

---

## Enterprise Zero-Toy-Code Standard

All agents operate under the mandatory rules defined in [AGENTS.md](file:///data/data/com.termux/files/home/aureon-redteam/AGENTS.md):

- **No Placeholders or Stubs**: Zero tolerance for `TODO`, `FIXME`, `pass`, `...`, `todo!()`, `unimplemented!()`, or truncated code blocks.
- **No Mock / Toy Data**: Real schemas, typed interfaces, and production bindings only.
- **Real Working Actions**: All tools and network commands must execute functional operations and process live data. Mock exploits or simulated command outputs are strictly prohibited.
- **Full Codebase Completeness**: Every deliverable must be emitted in full, compile with zero warnings, pass all automated tests, and be immediately deployable.
