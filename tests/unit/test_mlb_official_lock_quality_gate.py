#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_official_lock_quality_gate as gate

FAMILY = "MLB_GENERAL_OFFICIAL_PICK"


def _module():
    module = SimpleNamespace()
    module._is_official = lambda row: bool(row.get("canonical"))
    module._selected_signal = lambda row: row.get("awaySignal") or {}
    module._team_probability = lambda row: row.get("teamWinProbabilityPct") / 100.0
    return module


def _valid_evidence() -> dict:
    return {
        "signalFamily": FAMILY,
        "signatureMatchMode": "family",
        "prospective": True,
        "chronologicalHoldout": True,
        "outcomeUntouched": True,
        "ruleFrozenBeforeEvaluation": True,
        "postDiscoveryTuning": False,
        "holdout": {
            "games": 50,
            "correct": 46,
            "distinctSlateDates": 22,
            "recentGames": 20,
            "recentCorrect": 18,
            "folds": [
                {"games": 16, "correct": 15},
                {"games": 17, "correct": 16},
                {"games": 17, "correct": 15},
            ],
        },
    }


def _clean_temporal() -> dict:
    return {
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
    }


def _row(probability: float = 60.0, *, evidence: bool = True, **signal):
    row = {
        "canonical": True,
        "predictedWinner": "Away Team",
        "predictedSide": "away",
        "teamWinProbabilityPct": probability,
        "signalFamily": FAMILY,
        "tags": [],
        "awaySignal": {
            "delta": 0.01,
            "reversalCount": 0,
            "bookDivergence": 0.01,
            "tags": [],
            "temporalFeatures": _clean_temporal(),
            **signal,
        },
    }
    if evidence:
        row["precisionAdmissionEvidence"] = _valid_evidence()
    return row


def test_quality_gate_contract() -> None:
    module = _module()
    gate.apply(module)

    missing_evidence = _row(75.0, evidence=False)
    assert module._is_official(missing_evidence) is False
    assert "precision_admission_evidence_missing" in missing_evidence["officialLockQualityGate"]["reasons"]

    below_floor = _row(59.99)
    assert module._is_official(below_floor) is False
    assert "selected_team_probability_below_60pct" in below_floor["officialLockQualityGate"]["reasons"]

    at_floor = _row(60.0)
    assert module._is_official(at_floor) is True, at_floor["officialLockQualityGate"]

    movement_against = _row(60.16, delta=-0.005, reversalCount=2)
    movement_against["tags"] = ["PROBABILITY_DIRECTION_INTEGRITY_CORRECTION"]
    assert module._is_official(movement_against) is False
    assert {
        "movement_against_selected_team",
        "multiple_reversals_without_independent_confirmation",
        "probability_direction_integrity_correction",
    }.issubset(set(movement_against["officialLockQualityGate"]["reasons"]))

    agreement_only = _row(62.0, reversalCount=2, tags=["BOOK_AGREEMENT"])
    assert module._is_official(agreement_only) is False

    confirmed = _row(62.0, reversalCount=2, tags=["BOOK_AGREEMENT", "STEAM"])
    assert module._is_official(confirmed) is True, confirmed["officialLockQualityGate"]

    late_temporal = _clean_temporal()
    late_temporal["horizons"]["15m"]["velocityPpHr"] = 0.2
    late_temporal["horizons"]["60m"]["velocityPpHr"] = -0.1
    late_conflict = _row(63.0, temporalFeatures=late_temporal)
    assert module._is_official(late_conflict) is False
    assert "late_direction_conflict_without_independent_confirmation" in late_conflict["officialLockQualityGate"]["reasons"]


def main() -> int:
    test_quality_gate_contract()
    print("MLB empirical 70% precision-admission and direction-quality gate verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
