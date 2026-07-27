from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_mlb_v9_pointer_reconciliation as reconcile


def _state(count=4, version=None):
    target = version or reconcile.EXPECTED_DATASET
    return {
        "completeSlateCount": count,
        "completedSlates": [
            {"slateDateEt": f"2026-05-{index + 1:02d}", "featureDatasetVersion": target}
            for index in range(count)
        ],
        "featureDatasetVersion": reconcile.EXPECTED_DATASET,
        "featureRematerializationTargetDatasetVersion": reconcile.EXPECTED_DATASET,
        "featureRematerializationComplete": True,
        "featureRematerializedSlateCount": count,
        "featureRematerializationTotalSlateCount": count,
        "featureRematerializationPaidHistoricalCalls": 0,
        "featureRematerializationErrors": [],
        "lastError": None,
    }


def test_integrity_requires_every_completed_pointer():
    state = _state(4)
    assert all(reconcile.integrity_checks(state).values())
    state["completedSlates"].append(
        {"slateDateEt": "2026-05-05", "featureDatasetVersion": "older-dataset"}
    )
    state["completeSlateCount"] = 5
    checks = reconcile.integrity_checks(state)
    assert checks["everyPointerVersionMatches"] is False
    assert checks["rematerializedCountMatchesPointers"] is False
    assert checks["rematerializationTotalMatchesPointers"] is False
    mismatch = reconcile.first_mismatch(state)
    assert mismatch["index"] == 4
    assert mismatch["slateDateEt"] == "2026-05-05"


def test_pointer_version_counts_include_missing_values():
    state = _state(3)
    state["completedSlates"][1].pop("featureDatasetVersion")
    counts = reconcile.pointer_version_counts(state)
    assert counts[reconcile.EXPECTED_DATASET] == 2
    assert counts["MISSING"] == 1


def test_paid_rematerialization_calls_must_remain_zero():
    state = _state(2)
    state["featureRematerializationPaidHistoricalCalls"] = 1
    assert reconcile.integrity_checks(state)["paidRematerializationCallsZero"] is False


def test_source_never_contains_provider_api_key_or_odds_request():
    source = Path("scripts/run_mlb_v9_pointer_reconciliation.py").read_text()
    assert "ODDS_API_KEY" not in source
    assert "requests.get" not in source
    assert "urllib.request" not in source
    assert '"mode": "orchestrate"' in source
    assert '"providerCallsMadeByReconciliation": 0' in source
    assert '"productionAuthorityChanged": False' in source
