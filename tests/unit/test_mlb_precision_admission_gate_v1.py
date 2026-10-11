from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_precision_admission_gate_v1 as gate

FAMILY = "MLB_REVERSAL_LARGEST_TOWARD_MARKET_FLIP_2_TO_3PP"


def valid_evidence() -> dict:
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
                {"games": 16, "correct": 15, "startDate": "2026-08-01", "endDate": "2026-08-10"},
                {"games": 17, "correct": 16, "startDate": "2026-08-11", "endDate": "2026-08-20"},
                {"games": 17, "correct": 15, "startDate": "2026-08-21", "endDate": "2026-08-31"},
            ],
        },
    }


def test_valid_prospective_family_is_admitted() -> None:
    result = gate.evaluate(valid_evidence(), expected_signal_family=FAMILY)
    assert result["admitted"] is True, result
    assert result["wilsonLower95Pct"] >= 70.0


def test_nine_for_nine_posthoc_candidate_is_not_admitted() -> None:
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
            "folds": [
                {"games": 6, "correct": 6},
                {"games": 3, "correct": 3},
            ],
        },
    }
    result = gate.evaluate(evidence, expected_signal_family=FAMILY)
    assert result["observedAccuracyPct"] == 100.0
    assert result["wilsonLower95Pct"] >= 70.0
    assert result["admitted"] is False
    assert "precision_holdout_sample_below_minimum" in result["reasons"]
    assert "precision_evidence_not_prospective" in result["reasons"]
