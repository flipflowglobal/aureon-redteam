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
# Validate the AGY plugin manifest with agy
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
  hooks/test_policy_wrap.py # pytest suite for the wrap (100% branch coverage)
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
- Offensive tooling (`nmap`, `sqlmap`, `hydra`, …) and egress tools (`curl`,
  `wget`, `nc`, …) are allowed without a record only against testbed targets:
  loopback, the RFC 5737 documentation ranges, `localhost` / `*.localhost`, and
  `*.example.com` / `*.example.net` / `*.example.org`. (Those reserved domains
  are delegated and can resolve to public hosts; whether to keep them as testbed
  names is an open owner decision.)
- Engagement records are read from the project root Claude Code sets for the
  hook (`CLAUDE_PROJECT_DIR`), never from the directory the session has changed
  into, so a record outside this repository cannot authorize anything.
- Against any other target, the operation is **denied** unless an engagement
  record is `status: active`, carries both internal and owner approval, is
  inside its validity window, enables network access, and explicitly lists the
  target (honouring `excluded_targets`).
- A CIDR target is checked over its full width: a sweep is authorized only when
  the whole range lies inside an authorized scope (or a documentation range for
  testbed traffic), never by its first address. IPv6 address literals are
  targets like IPv4 ones, and numeric IPv4 spellings that the system resolver
  accepts (32-bit decimal, hexadecimal, octal) are normalized and checked like
  dotted-quad addresses.
- Commands run indirectly are analysed too: a shell's `-c` argument, `eval`, and
  `$(…)`, `<(…)`, `>(…)` and backtick substitutions, up to 8 levels deep.
  Deeper nesting is denied.
- Denial-of-service tooling and blanket scope (any network with a zero-length
  prefix, such as `0.0.0.0/0` or `::/0`, however it is spelled) are denied
  unconditionally and cannot be enabled by any record.
- A malformed hook event, any internal fault (logged with an error ID), and an
  unreadable, oversized or malformed engagement record all fail closed. A bad
  record only disables itself; it is listed in denials and never disables the
  other records. Timestamps must carry a timezone, and `targets` /
  `excluded_targets` must be lists of entries with a string `value`. At most
  64 KiB of a record is read.

The wrap analyses command text. It does not see commands that another
interpreter runs internally (for example `python -c` or `perl -e`) or the
contents of script files, and it is enforced only under Claude Code: OpenCode
and Antigravity have no equivalent hook.

#### Local-network scope mode

A record with `scope_mode: local_network` (see
[`engagements/local-network.yaml`](engagements/local-network.yaml)) grants
full-scope testing of the **operator's own LAN** without enumerating each host.
It is bounded so it can never become a blanket bypass: a target is authorized
only when it is a **private** address/CIDR (RFC 1918 / CGNAT / link-local / IPv6
ULA) **and** falls inside a subnet the host is directly attached to (the "same
network"). Public/routable targets, other private subnets, excluded devices,
DoS tooling, and blanket scope all remain denied. The host subnets are detected
from the interface table and can be pinned with the `AUREON_LOCAL_SUBNETS`
environment variable; if none are detected the record authorizes nothing
(fail-closed). Setting `approvals.owner.approved: true` is your attestation that
you own or are authorized to test every device on that segment — it does **not**
authorize shared or public networks (café / hotel / office Wi-Fi) beyond devices
you are entitled to test.

```sh
python3 -m pytest .claude/hooks/test_policy_wrap.py   # 205 tests, deterministic clock and network
```

---

## Zero-Toy-Code Gate

The forbidden-pattern rules in [AGENTS.md](AGENTS.md) are enforced by a real
scanner, `tools/zero_toy_scan.py` (Python 3.10+, standard library only). It
parses Python with `ast`/`tokenize` and lexes Rust, Solidity, JavaScript,
TypeScript, JSON and hash-comment formats to separate code from comments and
strings, then reports placeholder bodies, elided code, toy/hedging phrases,
test-double vocabulary in production, placeholder strings, coverage-exclusion
pragmas and unfinished-work tags (rule codes `ZT001`–`ZT013`). Exit status is
`0` clean, `1` findings, `2` the scan could not run.

```sh
python3 tools/zero_toy_scan.py                # scan the whole git work tree
python3 tools/zero_toy_scan.py PATH [PATH]    # scan specific files or directories
```

### Gates

`./setup.sh scan` runs every offline gate, reports each result, and exits
non-zero if any gate fails **or a required tool is missing** — no gate is ever
skipped silently. There is no hosted CI: run it locally (on Termux too), and
let the pre-commit hook run it before every commit.

| Gate | Tool and configuration |
|---|---|
| Zero-Toy-Code scanner | `tools/zero_toy_scan.py` over every tracked and untracked file |
| Whitespace / conflict markers | `git diff --check` against the merge base with `origin/main` |
| Format | `ruff format --check` |
| Lint | `ruff check` with `select = ["ALL"]` (`pyproject.toml`) |
| Types | `mypy` strict, `disallow_any_explicit`, `warn_unreachable`, extra error codes |
| Tests + coverage | `pytest` (strict markers/config, warnings are errors) with **100% line and branch coverage** of `tools/` and `.claude/hooks/` |
| Shell | ShellCheck with every optional check enabled (`.shellcheckrc`) |
| Shell suites | `tools/test_setup.sh` (sandboxed HOME), `tools/test_sync_prompts.sh` |
| Prompt parity | `tools/sync_prompts.sh check` |
| YAML | `yamllint --strict` over every tracked YAML file except `engagements/` (`.yamllint.yaml`, default rules) |
| Secrets | `gitleaks` over history and the working tree (`.gitleaks.toml`, default rules) |

`./setup.sh audit-deps` checks the pinned toolchain against the PyPI
vulnerability service with `pip-audit --strict`. It needs the network, so it
is a separate command and the pre-commit hook stays offline.

### Toolchain

The gate tools are pinned with sha256 hashes and installed with
`pip install --require-hashes` into `.venv`. They need Python 3.11+; the
production code itself stays on the standard library and supports Python 3.10+.

| Lockfile (generated from the `.in` file by `uv pip compile --generate-hashes`) | Contents | Used on |
|---|---|---|
| `requirements-gate.txt` | mypy, pytest, pytest-cov, coverage, pip-audit, yamllint | Termux |
| `requirements-dev.txt` | the above plus ruff and ShellCheck (`shellcheck-py`) | glibc Linux |

`./setup.sh toolchain` picks the lockfile for the platform. mypy is pinned to
1.18.2, the newest release with no native dependency (1.19+ needs a C extension
and 2.x a Rust one, and neither has a Termux wheel). ruff is pinned to the
version Termux packages, and `scan` warns when the ruff in use differs.

```sh
./setup.sh toolchain       # build the hash-verified toolchain in .venv
./setup.sh scan            # run every offline gate
./setup.sh audit-deps      # vulnerability audit of the pinned toolchain (network)
./setup.sh gate-install    # run `scan` before every commit (git core.hooksPath)
```

#### Termux

Termux's Python is not glibc-based, so pip there can use only pure-Python
wheels. ruff, ShellCheck and gitleaks therefore come from `pkg`, and the two
remaining source builds (`msgpack`, `pyyaml`) fall back to pure Python, so no
compiler or Rust toolchain is needed:

```sh
pkg install python git ruff shellcheck gitleaks
./setup.sh toolchain       # installs requirements-gate.txt; reports any missing pkg tool
./setup.sh gate-install
./setup.sh scan
```

The scripts start with `#!/usr/bin/env bash`, which Termux resolves through
`termux-exec` (installed by default). Without it, run them as
`bash setup.sh scan`.

On other platforms gitleaks is not on PyPI either: install it with the system
package manager (`apt-get install gitleaks`, `brew install gitleaks`).

The scanner and the gate configuration are **project-scoped** — they are not
copied into `~/.claude` by `claude-install`.

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

All agents operate under the mandatory rules defined in [AGENTS.md](AGENTS.md):

- **No Placeholders or Stubs**: Zero tolerance for `TODO`, `FIXME`, `pass`, `...`, `todo!()`, `unimplemented!()`, or truncated code blocks.
- **No Mock / Toy Data**: Real schemas, typed interfaces, and production bindings only.
- **Real Working Actions**: All tools and network commands must execute functional operations and process live data. Mock exploits or simulated command outputs are strictly prohibited.
- **Full Codebase Completeness**: Every deliverable must be emitted in full, compile with zero warnings, pass all automated tests, and be immediately deployable.
