# Enterprise Engineering Guidelines & Zero-Toy-Code Standard

This repository enforces **enterprise-level production standards** across all deliverables, agents, workflows, and tools. Every agent operating within this workspace is strictly bound by these rules.

---

## 1. Absolute Zero-Toy-Code Policy

All code, configurations, tests, and deliverables must be **complete, production-grade, and enterprise-ready**. The following patterns are strictly forbidden:

| Forbidden Pattern | Reason & Enforcement |
|---|---|
| `TODO`, `FIXME`, `HACK`, `XXX`, `TBD` | Unfinished markers are strictly prohibited. Implement the logic fully or omit the feature cleanly. |
| `pass`, `...`, `raise NotImplementedError`, `todo!()`, `unimplemented!()` | Placeholder function or method bodies are rejected. Every function must execute real logic. |
| `# placeholder`, `// stub`, `/* sample */`, `/* mock */` | Marker comments signaling incomplete or fake implementations are forbidden. |
| Mock data, dummy constants, fake sample values | Toy data does not validate systems. Production code uses real schemas and valid live data bindings; cryptographically seeded fixtures are permitted only in test-only code and must never be imported by production. |
| Truncated code blocks (`// rest of code here`, `/* ... */`) | All files and edits must be emitted in full with complete, functional context. |
| Toy functions, minimal sketches, demo scripts | Deliverables must be production components ready for live deployment and adversarial code review. |
| Simulated actions or fake tool execution | Agents must execute real, working commands and tools. Never pretend an action succeeded or output fake command logs. |
| `print("debug")`, `console.log("test")` | Production code requires structured logging (`tracing`, `log`, Python `logging`), not ad-hoc stdout prints. |
| Dead code, unreachable branches (`if True:`, `while False:`) | Unreachable code creates technical debt and obscures behavior. Remove immediately. |
| Incomplete error handling or suppressed errors | Every error path must be explicitly typed, handled, guarded, or propagated. Never ignore or swallow errors. |

---

## 2. Enterprise Deliverable Standards

Every file, module, algorithm, and configuration produced must satisfy ALL ten enterprise quality criteria:

1. **Full Codebase Completeness**: Complete implementations from imports to exports. Zero placeholders, zero omitted routines, zero stubbed interfaces.
2. **Exhaustive Error Handling**: Every public interface validates inputs at boundaries, handles network/resource degradation, and reports structured, typed errors.
3. **Strict Type Safety**: Full static typing across all interfaces (Rust static types, Python strict typing with `mypy --strict`, Kotlin sealed classes). No unbounded `Any` or unvalidated type casts.
4. **Verifiable Test Coverage**: Every public function, algorithm, and data transition includes deterministic unit and integration tests exercising edge cases (empty inputs, boundaries, errors).
5. **Architectural Documentation**: Clear docstrings and module documentation explaining architectural invariants, computational complexity, and mathematical proofs.
6. **Performance & Complexity**: Explicit Big-O algorithmic time and space complexity analysis. Zero unnecessary memory allocations or unbounded buffer growth.
7. **Security & Least Privilege**: Zero secrets or credentials in source code. All unsafe operations guarded with documented safety justifications. Authorization-first execution.
8. **Real Working Actions**: All offensive, defensive, and quantitative operations execute real tools, validate live processes, and interact with actual filesystem/network/process targets.
9. **Observability**: Structured metrics hooks, contextual error IDs, and auditable event telemetry.
10. **Immediate Deployability**: Code must cleanly compile, pass linters/formatters with zero warnings, pass all automated test suites, and be ready for immediate merge to `main`. Deployment claims require executed evidence; never claim a deployment that did not occur.

---

## 3. Engagement & Authorization Boundaries

- All offensive security operations require explicit authorization records before network operations.
- Offline-by-default execution is maintained unless authorized by signed engagement records.
- Never bypass the policy engine (`PolicyEngine::authorize()`).
- All tools must execute verified, real binaries or native implementations—no mock scripts or dummy simulators.
- Simulation is permitted only when it is the product itself (backtester, Monte Carlo, stress engine), fully implemented, deterministic, tested, and explicitly labeled; it may never stand in for a live integration or produce results presented as live.
- Test doubles are permitted only in test-only code, must implement the same interface as the real dependency, must never ship in a production binary, and never count as integration coverage.
- Every integration boundary requires at least one real or provenance-recorded test.
- No hand-authored fixture may be presented as live market, chain, or customer data.
