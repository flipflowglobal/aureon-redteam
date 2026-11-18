---
name: redteam-ops
description: Execute authorized offensive security assessments, policy authorization checks, and real tool operations adhering to zero-toy-code standards.
---

# Red Team Operations & Authorization Procedures

This skill provides operational procedures for executing authorized offensive security testing with real working tools.

## 1. Zero-Toy-Code & Real-Actions Standard

- All testing operations must execute real, functional tools or native modules.
- Never simulate command outputs or use placeholder payloads.
- All network operations require active engagement authorization.

## 2. Pre-Operation Authorization Procedure

1. **Verify Authorization Record**:
   - Check engagement file status (`engagements/*.yaml`).
   - Confirm target IP/CIDR/domain is explicitly listed under `targets`.
   - Ensure the current time is within `valid_from` and `valid_until`.
   - Verify that forbidden test classes are not violated.
2. **Execute Policy Check**:
   - Validate scope with the policy engine (`PolicyEngine::authorize()`).
   - If unauthorized, halt immediately and report scope restriction.

## 3. Operational Execution Workflow

1. **Reconnaissance**: Run real TCP port scanning or service fingerprinting within authorized intensity limits.
2. **Vulnerability Analysis**: Execute targeted scanners against authorized web endpoints.
3. **Audit Logging**: Persist immutable records into the append-only findings ledger.
4. **Post-Execution Cleanup**: Terminate all background listeners, close sockets, and remove any temporary artifacts.
