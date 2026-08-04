from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_official_lock_quality_gate as gate
from test_mlb_precision_admission_gate_v1 import FAMILY, valid_evidence


def _module():
    module = SimpleNamespace()
    module._is_official = lambda row: bool(row.get("canonical"))
    module._selected_signal = lambda row: row.get("awaySignal") or {}
    module._team_probability = lambda row: row.get("teamWinProbabilityPct") / 100.0
    return module


def _row(probability: float = 75.0, evidence=None, **signal):
    row = {
        "canonical": True,
        "predictedWinner": "Away Team",
        "predictedSide": "away",
        "teamWinProbabilityPct": probability,
        "tags": [],
        "signalFamily": FAMILY,
        "awaySignal": {
            "delta": 0.01,
            "reversalCount": 0,
            "bookDivergence": 0.01,
            "tags": [],
            "temporalFeatures": {
                "available": True,
                "horizons": {
                    "15m": {"velocityPpHr": 0.1, "reversalCount": 0},
                    "60m": {"velocityPpHr": 0.1, "reversalCount": 0},
                    "180m": {"velocityPpHr": 0.1, "reversalCount": 0},
                    "full": {
                        "pullCount": 8,
                        "durationMinutes": 105,
                        "coverageRatio": 1.0,
                        "reversalCount": 0,
                        "grossMovePp": 1.0,
                        "netMovePp": 1.0,
                        "pathEfficiency": 1.0,
                        "latestLegSignedMovePp": 1.0,
                        "latestLegMovePp": 1.0,
                        "latestLegDurationMinutes": 105,
                        "priorLegMovePp": 0.0,
                        "reversalRecoveryRatio": 0.0,
                        "minutesSinceLastReversal": 0.0,
                        "marketFlipCount": 0,
                        "largestMarketFlipTowardSidePp": 0.0,
                        "largestMarketFlipAgainstSidePp": 0.0,
                        "largestMarketFlipAgeMinutes": 0.0,
                        "maxReversalSwingPp": 0.0,
                        "marketFlip2To3PpCandidate": False,
                    },
                },
            },
            **signal,
        },
    }
    if evidence is not None:
        row["precisionAdmissionEvidence"] = evidence
    return row


def test_missing_precision_evidence_forces_abstention() -> None:
    module = _module()
    gate.apply(module)
    row = _row()
    assert module._is_official(row) is False
    assert "precision_admission_evidence_missing" in row["officialLockQualityGate"]["reasons"]


def test_valid_precision_evidence_allows_clean_row() -> None:
    module = _module()
    gate.apply(module)
    row = _row(evidence=valid_evidence())
    assert module._is_official(row) is True, row["officialLockQualityGate"]
    assert row["officialLockQualityGate"]["precisionAdmission"]["wilsonLower95Pct"] >= 70.0


def test_precision_evidence_does_not_override_direction_risk() -> None:
    module = _module()
    gate.apply(module)
    row = _row(evidence=valid_evidence(), delta=-0.005, reversalCount=2)
    row["tags"] = ["PROBABILITY_DIRECTION_INTEGRITY_CORRECTION"]
    assert module._is_official(row) is False
    reasons = set(row["officialLockQualityGate"]["reasons"])
    assert "movement_against_selected_team" in reasons
    assert "probability_direction_integrity_correction" in reasons


def test_research_candidate_remains_blocked_on_nine_for_nine_posthoc_evidence() -> None:
    module = _module()
    gate.apply(module)
    evidence = {
        "signalFamily": FAMILY,
        "prospective": False,
        "chronologicalHoldout": True,
        "outcomeUntouched": True,
        "ruleFrozenBeforeEvaluation": False,
        "postDiscoveryTuning": True,
        "holdout": {
            "games": 9,
            "correct": 9,
            "distinctSlateDates": 7,
            "recentGames": 9,
            "recentCorrect": 9,
            "folds": [{"games": 6, "correct": 6}, {"games": 3, "correct": 3}],
        },
    }
    temporal = {
        "available": True,
        "horizons": {
            "15m": {"velocityPpHr": 0.2, "reversalCount": 0},
            "60m": {"velocityPpHr": 0.1, "reversalCount": 1},
            "180m": {"velocityPpHr": 0.05, "reversalCount": 1},
            "full": {
                "pullCount": 12,
                "durationMinutes": 180,
                "coverageRatio": 1.0,
                "reversalCount": 1,
                "grossMovePp": 4.0,
                "netMovePp": 2.5,
                "pathEfficiency": 0.7,
                "latestLegSignedMovePp": 2.5,
                "latestLegMovePp": 2.5,
                "latestLegDurationMinutes": 30,
                "priorLegMovePp": 0.5,
                "reversalRecoveryRatio": 5.0,
                "minutesSinceLastReversal": 30,
                "marketFlipCount": 1,
                "largestMarketFlipTowardSidePp": 2.5,
                "largestMarketFlipAgainstSidePp": 0.0,
                "largestMarketFlipAgeMinutes": 0.0,
                "maxReversalSwingPp": 2.5,
                "marketFlip2To3PpCandidate": True,
            },
        },
    }
    row = _row(evidence=evidence, temporalFeatures=temporal, reversalCount=1)
    assert module._is_official(row) is False
    decision = row["officialLockQualityGate"]
    assert decision["reversalSimilarity"]["researchCandidate"] is True
    assert "precision_holdout_sample_below_minimum" in decision["reasons"]
