from __future__ import annotations

import copy

import mlb_v8_observational_audit_v1_3 as audit


def _candidate(version: str):
    value = {
        "proofType": "MLB_V8_FROZEN_OBSERVATIONAL_CANDIDATE",
        "version": version,
        "authority": "SHADOW_ONLY",
        "observationalOnly": True,
        "promotionEligible": False,
        "promotionRequested": False,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
        "sourceTrainingIdentity": "training-old",
        "frozenCorpusLastDate": "2026-08-01",
        "modelBundle": {"modelDigest": "model-digest"},
        "modelDigest": "model-digest",
    }
    value["candidateDigest"] = audit._core._sha(value)
    return value


def test_known_legacy_candidate_is_revalidated_without_mutation(monkeypatch):
    candidate = _candidate(
        "MLB-V8-OBSERVATIONAL-AUDIT-v1.2-latched-evidence-window"
    )
    original = copy.deepcopy(candidate)
    monkeypatch.setattr(audit._core.runtime, "verify_bundle", lambda _bundle: None)

    audit.verify_candidate(candidate)

    assert candidate == original
    assert candidate["version"] != audit.VERSION


def test_unknown_candidate_version_fails_closed(monkeypatch):
    candidate = _candidate("MLB-V8-OBSERVATIONAL-AUDIT-v0-unknown")
    monkeypatch.setattr(audit._core.runtime, "verify_bundle", lambda _bundle: None)

    try:
        audit.verify_candidate(candidate)
    except ValueError as exc:
        assert "version mismatch" in str(exc)
    else:
        raise AssertionError("unknown observational candidate version must fail closed")


def test_legacy_candidate_tampering_still_fails_closed(monkeypatch):
    candidate = _candidate(
        "MLB-V8-OBSERVATIONAL-AUDIT-v1.2-latched-evidence-window"
    )
    candidate["promotionEligible"] = True
    candidate["candidateDigest"] = audit._core._sha(
        {key: value for key, value in candidate.items() if key != "candidateDigest"}
    )
    monkeypatch.setattr(audit._core.runtime, "verify_bundle", lambda _bundle: None)

    try:
        audit.verify_candidate(candidate)
    except ValueError as exc:
        assert "authority" in str(exc)
    else:
        raise AssertionError("legacy candidate authority tampering must fail closed")
