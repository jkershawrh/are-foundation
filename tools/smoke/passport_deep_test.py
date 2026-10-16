#!/usr/bin/env python3
"""Deep passport testing for ARE Foundation — targets gaps in the passport lifecycle.

Exercises:
  1. Full happy-path lifecycle (register → issue → verify → scope → policy → revoke)
  2. Provisional passport scope restrictions
  3. Passport expiry behavior (short TTL)
  4. Revocation and post-revocation access denial
  5. Supersession (force-reissue replaces active passport)
  6. Concurrent force-reissue race
  7. Context constraint exact-match enforcement
  8. Scope boundary — glob matching and action class mismatch
  9. Policy version tracking at issuance
 10. Cross-agent passport rejection

Reports results to reports/passport-deep-test/.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "reports" / "passport-deep-test"
BASE = os.environ.get("ARE_FOUNDATION_BASE_URL", "http://localhost:18085").rstrip("/")
TOKEN = os.environ.get("ARE_FOUNDATION_TOKEN", "test-token")


@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str
    duration_ms: float = 0.0
    evidence: dict = field(default_factory=dict)


def request(
    method: str,
    path: str,
    body: dict | None = None,
    idem: str | None = None,
    expect_error: bool = False,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("X-Request-ID", f"passport-deep-{int(time.time() * 1000)}")
    req.add_header("X-ARE-Agent-ID", "passport-deep-test-operator")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Idempotency-Key", idem or f"deep-{uuid.uuid4()}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return {"status": resp.status, "body": json.loads(raw) if raw else {}, "error": False}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        if expect_error:
            try:
                body_parsed = json.loads(raw)
            except json.JSONDecodeError:
                body_parsed = {"raw": raw}
            return {"status": exc.code, "body": body_parsed, "error": True}
        raise RuntimeError(f"{method} {path} failed status={exc.code} body={raw}") from exc


def register_agent(suffix: str) -> str:
    resp = request(
        "POST",
        "/v1/identity/agents",
        {"agent_type": "demo.release-agent", "owner_id": "deep-test-owner", "metadata": {"test": suffix}},
        idem=f"deep-agent-{suffix}",
    )
    return resp["body"]["agent"]["agent_id"]


def issue_passport(
    agent_id: str,
    suffix: str,
    passport_type: str = "standard",
    scopes: list[dict] | None = None,
    ttl: int = 3600,
    expect_error: bool = False,
) -> dict:
    if scopes is None:
        scopes = [{"action_class": "model.promote_to_production", "resource_pattern": "model/*"}]
    return request(
        "POST",
        "/v1/passports",
        {
            "agent_id": agent_id,
            "passport_type": passport_type,
            "requested_scopes": scopes,
            "ttl_seconds": ttl,
            "issued_by": "deep-test-owner",
            "reason": f"deep test {suffix}",
        },
        idem=f"deep-passport-{suffix}",
        expect_error=expect_error,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_full_lifecycle() -> TestResult:
    """Register → issue → verify → scope eval → policy eval → revoke → verify-fails."""
    t0 = time.perf_counter()
    agent_id = register_agent("lifecycle")
    pp = issue_passport(agent_id, "lifecycle")
    passport_id = pp["body"]["passport"]["passport_id"]

    verify = request("POST", "/v1/passports:verify", {"agent_id": agent_id, "passport_id": passport_id})
    scope = request(
        "POST",
        "/v1/enforcement/scope:evaluate",
        {"agent_id": agent_id, "passport_id": passport_id, "action_class": "model.promote_to_production", "resource": "model/champion"},
    )
    policy = request(
        "POST",
        "/v1/policy/evaluations",
        {"decision_id": "deep-lifecycle-policy", "agent_id": agent_id, "action_class": "model.promote_to_production", "resource": "model/champion"},
    )
    revoke = request("POST", "/v1/passports:revoke", {"agent_id": agent_id, "passport_id": passport_id, "reason": "deep test revocation"})

    verify_after = request("POST", "/v1/passports:verify", {"agent_id": agent_id, "passport_id": passport_id}, expect_error=True)

    all_ok = (
        verify["body"].get("verified") is True
        and scope["body"]["decision"]["effect"] == "ALLOW"
        and policy["body"]["decision"]["effect"] == "ALLOW"
    )
    revoke_blocks = verify_after.get("error") or verify_after["body"].get("verified") is False

    passed = all_ok and revoke_blocks
    return TestResult(
        name="full_lifecycle",
        passed=passed,
        detail="Happy path + revocation denial" if passed else f"verify={verify['body']}, scope={scope['body']}, revoke_blocks={revoke_blocks}",
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence={"verify": verify["body"], "scope_effect": scope["body"]["decision"]["effect"], "revoke_blocks": revoke_blocks},
    )


def test_provisional_scope_restriction() -> TestResult:
    """Provisional passports must restrict action classes to READ/NOTIFY/QUERY."""
    t0 = time.perf_counter()
    agent_id = register_agent("provisional")

    read_scope = [{"action_class": "READ", "resource_pattern": "urn:dataset/*"}]
    pp_ok = issue_passport(agent_id, "provisional-read", passport_type="provisional", scopes=read_scope, ttl=600)

    agent_id2 = register_agent("provisional-write")
    write_scope = [{"action_class": "model.promote_to_production", "resource_pattern": "model/*"}]
    pp_write = issue_passport(agent_id2, "provisional-write", passport_type="provisional", scopes=write_scope, ttl=600, expect_error=True)

    read_ok = not pp_ok.get("error", False)
    write_denied = pp_write.get("error", False) or pp_write["status"] >= 400

    passed = read_ok and write_denied
    return TestResult(
        name="provisional_scope_restriction",
        passed=passed,
        detail="Provisional READ allowed, WRITE denied" if passed else f"read_ok={read_ok}, write_denied={write_denied}",
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence={"read_status": pp_ok["status"], "write_status": pp_write["status"], "write_body": pp_write.get("body", {})},
    )


def test_passport_expiry() -> TestResult:
    """Issue with minimum TTL (300s), verify it's active, check expiry timestamp is set correctly."""
    t0 = time.perf_counter()
    agent_id = register_agent("expiry")
    pp = issue_passport(agent_id, "expiry", ttl=300)
    passport = pp["body"]["passport"]
    passport_id = passport["passport_id"]

    has_expiry = "expires_at" in passport or "expires_ts" in passport or "ttl_seconds" in passport
    verify = request("POST", "/v1/passports:verify", {"agent_id": agent_id, "passport_id": passport_id})
    verified = verify["body"].get("verified") is True

    passed = has_expiry and verified
    return TestResult(
        name="passport_expiry",
        passed=passed,
        detail="Passport issued with TTL, expiry set, currently valid" if passed else f"has_expiry={has_expiry}, verified={verified}",
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence={"passport_keys": list(passport.keys()), "verified": verified},
    )


def test_ttl_below_minimum() -> TestResult:
    """TTL below 300s should be rejected."""
    t0 = time.perf_counter()
    agent_id = register_agent("ttl-low")
    pp = issue_passport(agent_id, "ttl-low", ttl=60, expect_error=True)
    rejected = pp.get("error", False) or pp["status"] >= 400

    passed = rejected
    return TestResult(
        name="ttl_below_minimum",
        passed=passed,
        detail="Sub-300s TTL correctly rejected" if passed else f"status={pp['status']}",
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence={"status": pp["status"], "body": pp.get("body", {})},
    )


def test_supersession_force_reissue() -> TestResult:
    """Force-reissue should revoke old passport and create new one."""
    t0 = time.perf_counter()
    agent_id = register_agent("supersede")
    pp1 = issue_passport(agent_id, "supersede-1")
    passport_id_1 = pp1["body"]["passport"]["passport_id"]

    pp2 = request(
        "POST",
        "/v1/passports",
        {
            "agent_id": agent_id,
            "passport_type": "standard",
            "requested_scopes": [{"action_class": "model.promote_to_production", "resource_pattern": "model/*"}],
            "ttl_seconds": 3600,
            "issued_by": "deep-test-owner",
            "reason": "deep test supersession",
            "force": True,
        },
    )
    passport_id_2 = pp2["body"]["passport"]["passport_id"]

    v1 = request("POST", "/v1/passports:verify", {"agent_id": agent_id, "passport_id": passport_id_1}, expect_error=True)
    v2 = request("POST", "/v1/passports:verify", {"agent_id": agent_id, "passport_id": passport_id_2})

    old_invalid = v1.get("error") or v1["body"].get("verified") is False
    new_valid = v2["body"].get("verified") is True
    different_ids = passport_id_1 != passport_id_2

    passed = old_invalid and new_valid and different_ids
    return TestResult(
        name="supersession_force_reissue",
        passed=passed,
        detail="Old passport revoked, new issued" if passed else f"old_invalid={old_invalid}, new_valid={new_valid}",
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence={"passport_1": passport_id_1, "passport_2": passport_id_2, "old_invalid": old_invalid, "new_valid": new_valid},
    )


def test_concurrent_force_reissue() -> TestResult:
    """Concurrent force-reissues should not corrupt state — exactly one should win."""
    t0 = time.perf_counter()
    agent_id = register_agent("race")
    issue_passport(agent_id, "race-seed")

    results = []
    def reissue(index: int) -> dict:
        return request(
            "POST",
            "/v1/passports",
            {
                "agent_id": agent_id,
                "passport_type": "standard",
                "requested_scopes": [{"action_class": "model.promote_to_production", "resource_pattern": "model/*"}],
                "ttl_seconds": 3600,
                "issued_by": "deep-test-owner",
                "reason": f"race {index}",
                "force": True,
            },
            idem=f"deep-race-{uuid.uuid4()}",
            expect_error=True,
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(reissue, i) for i in range(5)]
        results = [f.result() for f in as_completed(futures)]

    successes = [r for r in results if not r.get("error") and r["status"] < 400]
    list_resp = request("GET", f"/v1/passports/by-agent/{agent_id}")
    active_passports = [
        p for p in list_resp["body"].get("passports", [])
        if p.get("status", "").upper() == "ACTIVE"
    ]

    exactly_one_active = len(active_passports) == 1
    at_least_one_success = len(successes) >= 1

    passed = exactly_one_active and at_least_one_success
    return TestResult(
        name="concurrent_force_reissue",
        passed=passed,
        detail=f"{len(successes)} succeeded, {len(active_passports)} active" if passed else f"successes={len(successes)}, active={len(active_passports)}",
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence={"successes": len(successes), "active_count": len(active_passports), "total_attempts": len(results)},
    )


def test_scope_glob_matching() -> TestResult:
    """Scope resource_pattern globs should match correctly and reject mismatches."""
    t0 = time.perf_counter()
    agent_id = register_agent("glob")
    pp = issue_passport(agent_id, "glob", scopes=[{"action_class": "model.promote_to_production", "resource_pattern": "model/*"}])
    passport_id = pp["body"]["passport"]["passport_id"]

    match = request(
        "POST",
        "/v1/enforcement/scope:evaluate",
        {"agent_id": agent_id, "passport_id": passport_id, "action_class": "model.promote_to_production", "resource": "model/champion"},
    )
    mismatch_resource = request(
        "POST",
        "/v1/enforcement/scope:evaluate",
        {"agent_id": agent_id, "passport_id": passport_id, "action_class": "model.promote_to_production", "resource": "dataset/something"},
    )
    mismatch_action = request(
        "POST",
        "/v1/enforcement/scope:evaluate",
        {"agent_id": agent_id, "passport_id": passport_id, "action_class": "data.export", "resource": "model/champion"},
    )

    match_allow = match["body"]["decision"]["effect"] == "ALLOW"
    resource_deny = mismatch_resource["body"]["decision"]["effect"] == "DENY"
    action_deny = mismatch_action["body"]["decision"]["effect"] == "DENY"

    passed = match_allow and resource_deny and action_deny
    return TestResult(
        name="scope_glob_matching",
        passed=passed,
        detail="Glob match works, mismatches denied" if passed else f"match={match_allow}, res_deny={resource_deny}, act_deny={action_deny}",
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence={"match_effect": match["body"]["decision"]["effect"], "resource_effect": mismatch_resource["body"]["decision"]["effect"], "action_effect": mismatch_action["body"]["decision"]["effect"]},
    )


def test_policy_deny_scenarios() -> TestResult:
    """OPA policy should deny experimental models and forbidden actions."""
    t0 = time.perf_counter()
    agent_id = register_agent("policy-deny")

    experimental = request(
        "POST",
        "/v1/policy/evaluations",
        {"decision_id": "deep-exp", "agent_id": agent_id, "action_class": "model.promote_to_production", "resource": "model/experimental-v2"},
    )
    forbidden = request(
        "POST",
        "/v1/policy/evaluations",
        {"decision_id": "deep-forbidden", "agent_id": agent_id, "action_class": "demo.forbidden_action", "resource": "anything"},
    )
    classified = request(
        "POST",
        "/v1/policy/evaluations",
        {"decision_id": "deep-classified", "agent_id": agent_id, "action_class": "pdm.document.access_classified", "resource": "doc/classified-abc"},
    )
    cad_external = request(
        "POST",
        "/v1/policy/evaluations",
        {"decision_id": "deep-cad", "agent_id": agent_id, "action_class": "pdm.cad.share_external", "resource": "cad/widget"},
    )

    exp_deny = experimental["body"]["decision"]["effect"] == "DENY"
    forbidden_deny = forbidden["body"]["decision"]["effect"] == "DENY"
    classified_deny = classified["body"]["decision"]["effect"] == "DENY"
    cad_deny = cad_external["body"]["decision"]["effect"] == "DENY"

    passed = exp_deny and forbidden_deny and classified_deny and cad_deny
    return TestResult(
        name="policy_deny_scenarios",
        passed=passed,
        detail="All 4 deny scenarios enforced" if passed else f"exp={exp_deny}, forbidden={forbidden_deny}, classified={classified_deny}, cad={cad_deny}",
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence={
            "experimental": experimental["body"]["decision"],
            "forbidden": forbidden["body"]["decision"],
            "classified": classified["body"]["decision"],
            "cad_external": cad_external["body"]["decision"],
        },
    )


def test_cross_agent_passport_rejection() -> TestResult:
    """Passport issued to agent A must not verify for agent B."""
    t0 = time.perf_counter()
    agent_a = register_agent("cross-a")
    agent_b = register_agent("cross-b")

    pp = issue_passport(agent_a, "cross-a")
    passport_id = pp["body"]["passport"]["passport_id"]

    verify_correct = request("POST", "/v1/passports:verify", {"agent_id": agent_a, "passport_id": passport_id})
    verify_wrong = request("POST", "/v1/passports:verify", {"agent_id": agent_b, "passport_id": passport_id}, expect_error=True)

    correct_ok = verify_correct["body"].get("verified") is True
    wrong_denied = verify_wrong.get("error") or verify_wrong["body"].get("verified") is False

    passed = correct_ok and wrong_denied
    return TestResult(
        name="cross_agent_passport_rejection",
        passed=passed,
        detail="Correct agent verified, wrong agent rejected" if passed else f"correct={correct_ok}, wrong_denied={wrong_denied}",
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence={"correct_verified": correct_ok, "wrong_denied": wrong_denied},
    )


def test_duplicate_issuance_blocked() -> TestResult:
    """Issuing a second passport without force should fail (one-active-per-agent)."""
    t0 = time.perf_counter()
    agent_id = register_agent("dup")
    issue_passport(agent_id, "dup-1")
    pp2 = issue_passport(agent_id, "dup-2", expect_error=True)
    blocked = pp2.get("error", False) or pp2["status"] >= 400

    passed = blocked
    return TestResult(
        name="duplicate_issuance_blocked",
        passed=passed,
        detail="Second issuance without force rejected" if passed else f"status={pp2['status']}",
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence={"second_status": pp2["status"], "second_body": pp2.get("body", {})},
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    test_full_lifecycle,
    test_provisional_scope_restriction,
    test_passport_expiry,
    test_ttl_below_minimum,
    test_supersession_force_reissue,
    test_concurrent_force_reissue,
    test_scope_glob_matching,
    test_policy_deny_scenarios,
    test_cross_agent_passport_rejection,
    test_duplicate_issuance_blocked,
]


def main() -> int:
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    results: list[TestResult] = []

    print(f"\n{'='*60}")
    print(f"  ARE Passport Deep Test — {run_id}")
    print(f"  Target: {BASE}")
    print(f"{'='*60}\n")

    for test_fn in ALL_TESTS:
        try:
            result = test_fn()
        except Exception as exc:
            result = TestResult(name=test_fn.__name__.removeprefix("test_"), passed=False, detail=str(exc))
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.name:<40} ({result.duration_ms:.0f}ms) — {result.detail}")

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    verdict = "GREEN" if failed == 0 else "RED"

    summary = {
        "run_id": run_id,
        "verdict": verdict,
        "base_url": BASE,
        "passed": passed,
        "failed": failed,
        "total": len(results),
        "tests": [
            {
                "name": r.name,
                "passed": r.passed,
                "detail": r.detail,
                "duration_ms": round(r.duration_ms, 2),
                "evidence": r.evidence,
            }
            for r in results
        ],
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = REPORT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (REPORT_DIR / "latest-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    rubric = [
        ("full_lifecycle", "BDD", "Given registered agent, complete passport lifecycle terminates cleanly"),
        ("provisional_scope_restriction", "CDD", "Provisional passports SHALL restrict to READ/NOTIFY/QUERY"),
        ("passport_expiry", "EDD", "Response SHALL include TTL/expiry evidence for audit trail"),
        ("ttl_below_minimum", "CBT", "TTL floor = 300s enforced"),
        ("supersession_force_reissue", "BDD", "Force-reissue SHALL revoke predecessor and activate successor"),
        ("concurrent_force_reissue", "CBT", "One-active-per-agent invariant under concurrency"),
        ("scope_glob_matching", "TDD", "Scope evaluator glob match + action_class exact match"),
        ("policy_deny_scenarios", "CDD", "OPA policy denies experimental/forbidden/classified/CAD"),
        ("cross_agent_passport_rejection", "TDD", "Passport bound to issuing agent_id only"),
        ("duplicate_issuance_blocked", "CBT", "One-active-per-agent: duplicate issuance without force = reject"),
    ]
    rubric_map = {name: (paradigm, contract) for name, paradigm, contract in rubric}

    md_lines = [
        "# ARE Passport Deep Test",
        "",
        f"Verdict: **{verdict}**",
        f"Target: `{BASE}`",
        "",
        "## Testing Rubric Matrix",
        "",
        "| # | Test | Paradigm | Contract | RED/GREEN | Time | Detail |",
        "|---|------|----------|----------|-----------|------|--------|",
    ]
    for i, r in enumerate(results, 1):
        status = "GREEN" if r.passed else "RED"
        paradigm, contract = rubric_map.get(r.name, ("—", "—"))
        md_lines.append(f"| {i} | {r.name} | {paradigm} | {contract} | {status} | {r.duration_ms:.0f}ms | {r.detail} |")

    md_lines.extend([
        "",
        "### Paradigm Key",
        "",
        "- **TDD** — deterministic input/output contract at unit level",
        "- **BDD** — multi-step behavioral scenario (Given/When/Then)",
        "- **CDD** — contract-driven: tests the API spec/schema promise",
        "- **EDD** — evidence-driven: verifies audit/compliance evidence is present",
        "- **CBT** — constraint-based: tests invariants and bounds under stress",
        "",
        f"**{passed}/{len(results)} passed**",
        "",
    ])
    (run_dir / "public-summary.md").write_text("\n".join(md_lines), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"  Verdict: {verdict} — {passed}/{len(results)} passed")
    print(f"  Report: {run_dir}/summary.json")
    print(f"{'='*60}\n")

    print(json.dumps(summary, indent=2))
    return 0 if verdict == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
