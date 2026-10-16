# ARE × Kagenti Gap Analysis

## 1. Identity

**Kagenti has this.** SPIFFE SVIDs provide cryptographic workload identity. AuthBridge adds RFC 8693 token exchange and permission intersection (agent ≤ user). No gap.

Passport would consume SVIDs as input — SVID → agent_id mapping to bootstrap credential issuance. Not a replacement.

## 2. Authority

**This is the gap.**

Permission intersection computes a ceiling at request time. It answers: "what is the maximum this agent could do?" But there is no *issued artifact* that captures what was actually authorized, when, by whom, with what scope, under which policy version.

What's missing:

| Capability | Status in Kagenti |
|---|---|
| Scoped behavioral credential (action class + resource pattern) | Not present |
| Time-bound authority (TTL with min/max enforcement) | Not present — SVIDs rotate but don't carry scope |
| Graduated trust (provisional → standard progression) | Not present |
| One-active-per-agent invariant | Not present |
| Revocation without workload destruction | Not present — SVID revocation kills the pod |
| Policy-version-at-issuance (drift detection) | Not present |

**Proposed:** A scoped credential issued to agents after identity verification. Carries behavioral scope, enforces TTL, supports graduated trust. One active credential per agent. Revocable independently of the workload.

This would live inside Kagenti as a controller + CRD (`AgentCredential` or similar), not as an external service. It sits between AuthBridge (identity) and CPEX (enforcement) — AuthBridge proves who the agent is, the credential defines what it may do, CPEX enforces it.

**Reference implementation:** Tested at 50 RPS, 10 governance invariants validated (lifecycle, graduated trust, concurrent safety, scope matching, policy deny, cross-agent rejection, duplicate blocking). Code available to pull from.

## 3. Enforcement

**Kagenti has this.** CPEX coprocessor + OPA policy evaluation. OpenShell adds application-layer sandboxing with per-binary egress policy.

The scoped credential would be *consumed by* CPEX — read scopes from the credential, evaluate against OPA policy. No duplication of enforcement logic.

## 4. Evidence

**In progress.** Immutable ledger (Rust, hash-chained, per-entry-type chains) is being integrated with CPEX. The credential would emit lifecycle events to the ledger:

- Issued (agent_id, scopes, TTL, policy version, issued_by)
- Verified (result, timestamp)
- Revoked (reason, revoked_by)
- Superseded (old → new credential)

Correlation key: `agent_id` joins credential events (authorization) with CPEX events (enforcement) and OpenShell events (execution).

---

## Summary

| Discipline | Owner | Gap |
|---|---|---|
| Identity | AuthBridge / SPIFFE | None |
| **Authority** | **—** | **Scoped credential with TTL, graduated trust, revocation** |
| Enforcement | CPEX / OPA / OpenShell | None |
| Evidence | Immutable Ledger + CPEX | In progress |

One gap. One proposal. Happy to discuss in a community call or async.
