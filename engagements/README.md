# Engagement Authorization Records

One record per engagement, one engagement per authorized target set.
The policy layer is **deny-by-default**: anything not explicitly listed in an
`approved` record is out of scope.

> A record in this directory is **inert documentation** unless the policy engine
> loads and validates it. This repository currently contains agent definitions,
> not the engine. Do not treat a YAML file here as proof of authorization.

## Hard rules (must be enforced by `src/policy.rs`)

1. **No blanket scope.** Records authorizing "all external networks", wildcard
   CIDRs (`0.0.0.0/0`, `::/0`), wildcard TLDs (`*.com`), or unnamed SSIDs are
   rejected. There is deliberately no `full_scope`, `all_networks`, or
   `bypass_approval` field.
2. **Two independent approvals are required:**
   - *Internal approval* — the named approver (DarcelKing) signs off on the
     engagement internally.
   - *Owner authorization* — the network owner's written authorization
     (RoE/SOW/letter) with a verifiable reference and document hash.
   Neither alone is sufficient. Internal approval never substitutes for owner
   consent.
3. **Explicit targets only.** Every host, CIDR, domain, or SSID is enumerated.
   Network access is restricted to the listed targets, even when
   `network_access.enabled: true`.
4. **Mandatory validity window.** Expired or not-yet-valid records are inert.
   Revocation is immediate and does not require a new record.
5. **Forbidden classes cannot be overridden.** DoS, social engineering, and
   data exfiltration are denied by default and cannot be enabled by a record.
6. **Any scope change invalidates approval.** Editing targets, test classes, or
   the time window requires re-approval and a new record hash.
7. **Rate limits and stop conditions apply**, including an emergency contact
   who can halt the engagement.

## Scope modes

A record's `scope_mode` selects how targets are resolved. The default is
`explicit`.

- **`explicit`** (default) — every host, CIDR, or domain is enumerated under
  `targets`. Network access is restricted to that list, honouring
  `excluded_targets`. This is the only mode that can authorize routable hosts,
  and only those named.
- **`local_network`** — authorizes the operator's **own local segment** without
  enumerating each device. It is bounded by the policy wrap and cannot become a
  blanket bypass: a target is authorized only when it is a **private**
  address/CIDR (RFC 1918 / CGNAT / link-local / IPv6 ULA) **and** inside a subnet
  the host is directly attached to. Public/routable targets, other private
  subnets, `excluded_targets`, DoS classes, and blanket scope stay denied; if no
  local subnet is detected the record authorizes nothing (fail-closed). Host
  subnets are read from the interface table or pinned via `AUREON_LOCAL_SUBNETS`.
  Setting `approvals.owner.approved: true` attests ownership of, or written
  authorization for, every device on that segment — it never authorizes shared
  or public networks beyond devices you are entitled to test. See
  [`local-network.yaml`](local-network.yaml).

This does not relax rule 1: there is still no `full_scope`, `all_networks`, or
`bypass_approval` field, and `local_network` never reaches public address space.

## Record lifecycle

```
draft -> approved -> active -> closed
             \-> revoked (from any state)
```

- `draft` — being prepared; policy treats it as out of scope.
- `approved` — both approvals present and signature fields complete.
- `active` — within `valid_from`/`valid_until` and not revoked.
- `closed` — engagement finished; retain for audit.
- `revoked` — owner or approver withdrew consent; effective immediately.

## Review checklist

- [ ] `approvals.internal.approver` matches the authorized internal approver.
- [ ] `approvals.owner.document_reference` points to a signed document naming
      the same targets listed under `targets`.
- [ ] `approvals.owner.document_sha256` matches the stored document.
- [ ] Every target is explicit; no wildcards; no RFC 1918 ranges you do not own.
- [ ] `valid_from`/`valid_until` are narrow and timezone-aware.
- [ ] `forbidden_tests` is left at its defaults.
- [ ] Emergency contact is reachable for the entire window.

## Enterprise Production Standard (Zero Mock Policy)

Active engagement records must contain 100% complete, cryptographically verified data.
- **Zero Mock / Placeholder Data**: No `null` hashes, dummy contacts, RFC 5737 documentation ranges, or placeholder IDs are permitted in active records.
- **Full Verification**: `approvals.owner.document_sha256` and `audit.record_sha256` must be calculated from real signed documents.
- **Real Working Actions**: All authorized tests must execute real operations against the specified targets. Simulated or mock actions are strictly prohibited.

See `engagement.example.yaml` for the schema template (documentation schema only; authorizes nothing).
