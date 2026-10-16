# ARE × ASAGO Alignment

ASAGO translates governance policies into deployment-ready controls. This note identifies a gap in the controls catalog — the absence of a runtime authorization artifact — and proposes how a scoped credential addresses it.

## The Governance Problem

An enterprise defines a policy: *"Model-promotion agents may only promote governed, non-experimental models to production, with a maximum session of one hour, and all authorization decisions must be auditable."*

ASAGO can translate this into deployment-ready controls:

```
Governance Intent
    │
    ▼
ASAGO ──► deployment-ready controls
    │
    ├──► Network: OpenShell egress rules (agent can reach model-registry:8080)
    ├──► Identity: AuthBridge config (agent authenticated via SPIFFE, user delegation chain)
    ├──► Enforcement: OPA policy bundle (deny experimental models, deny classified docs)
    └──► Authorization: ??? (no runtime artifact carries the scoped authority)
```

Network controls govern *where* traffic flows. Identity controls govern *who* is acting. Enforcement controls govern *what policy evaluates to*. But nothing governs *what specific authority was issued to this agent, when, by whom, and under which policy version*.

Without that artifact, three governance outcomes are unachievable:

### 1. Post-Incident Attribution

After an agent promotes an unauthorized model, the investigation asks: "What was this agent authorized to do at the time of the action?"

With identity + enforcement alone, you can reconstruct what the RBAC ceiling *was* and what the OPA policy *would have evaluated to*. But you cannot prove what authority the agent *actually held* — because no artifact was issued. The ceiling existed; whether the agent was operating within a specific authorized scope is unverifiable.

A scoped credential captures: agent_id, specific action classes, resource patterns, TTL, issuing authority, and policy version at issuance. Post-incident, the credential *is* the evidence.

### 2. Graduated Trust for New Agent Deployments

Enterprises don't deploy new agents at full authority. A governance-aware deployment would:

1. Issue a **provisional** credential (read-only, 5-minute TTL) for initial validation
2. Observe behavior under restricted scope
3. Upgrade to a **standard** credential (full scope, 1-hour TTL) after validation passes

Neither SPIFFE identity nor OPA policy supports this progression. Identity is binary (authenticated or not). Policy evaluates the same for a new agent as for a proven one. Graduated trust requires a credential that progresses through states.

### 3. Revocation Without Destruction

When a governance violation is detected, the response options today are:

- **Kill the pod** (SPIFFE SVID revocation, Kubernetes scale-to-zero)
- **Update the OPA policy** (global, affects all agents of that class)

Neither option is scoped to a single agent's authority. Killing the pod destroys in-flight work. Updating policy is a blunt instrument.

A revocable credential allows: revoke *this agent's* authority, leave the pod running, let it request new authority under updated scope. The agent is contained, not destroyed. In-flight work can be checkpointed rather than lost.

## Where a Credential Fits in the ASAGO Controls Catalog

ASAGO's controls translation pipeline outputs deployment artifacts for network, identity, and enforcement layers. A credential would be a fourth output type:

```yaml
# ASAGO CredentialPolicy (proposed control output)
kind: CredentialPolicy
metadata:
  name: model-promoter-policy
spec:
  agentClass: model-promoter
  credentialType: STANDARD
  provisionalFirst: true
  provisionalTTL: 300
  maxTTL: 3600
  maxScopes:
    - actionClass: model.promote_to_production
      resourcePattern: model/*
    - actionClass: pdm.parts.lookup
      resourcePattern: parts/*
  auditRequirements:
    ledgerEnabled: true
    policyVersionTracking: true
    retentionDays: 90
```

The governance policy says "model-promotion agents get restricted, time-bound, auditable authority." ASAGO translates that into a `CredentialPolicy`. A runtime controller (proposed for Kagenti) reads the policy and issues credentials that conform to it. CPEX/OPA enforces the credential's scopes at request time. The immutable ledger records every issuance, verification, and revocation.

**ASAGO defines the governance intent. The credential is the runtime proof that the intent was enforced.**

## The Compliance Angle

Gartner predicts that through 2026, 80% of unauthorized AI transactions will stem from internal policy violations, not external compromise. Auditors will ask:

- "What authority did this agent hold when it acted?" → credential
- "Under which policy version was that authority granted?" → policy-version-at-issuance
- "Can you prove the authorization record hasn't been tampered with?" → hash-chained ledger
- "How quickly can you revoke a compromised agent's authority?" → credential revocation (seconds, not pod restart)
- "Do new agents start with full access?" → graduated trust (provisional → standard)

These questions cannot be answered with identity + policy alone. They require an issued, signed, auditable authorization artifact.

## What Already Exists

A reference implementation of the credential lifecycle has been tested:

| Capability | Status |
|---|---|
| Issue, verify, revoke, renew (7 gRPC RPCs) | Working |
| Graduated trust (provisional → standard) | Working, tested |
| One-active-per-agent under concurrent stress | Working, tested (5 concurrent reissues → exactly 1 active) |
| TTL enforcement (min 300s, max per type) | Working, tested |
| Policy-version-at-issuance tracking | Working |
| Immutable ledger integration | Working (separate repo, integrating with CPEX) |
| 5 clean integration interfaces | All abstract — runtime-agnostic |

The implementation is deliberately abstract — it does not depend on any specific orchestrator, runtime, or deployment model. It could be adapted to consume ASAGO's output format as the policy source for credential issuance.

## Ask

Two questions:

1. **Is ASAGO planning to define an authorization or credential artifact as part of its controls catalog?** If so, would like to align on the data model early to avoid divergence.

2. **Would a credential runtime be a useful reference implementation for ASAGO's enforcement layer?** The existing code is Apache 2.0 and designed to be folded into whichever project owns the runtime.

Happy to discuss async, on a call, or at an IJCAI follow-up session.
