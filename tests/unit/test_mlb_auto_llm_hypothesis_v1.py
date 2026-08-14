from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import mlb_auto_llm_hypothesis_v1 as llm


def test_deterministic_hypotheses_are_bounded_and_non_authoritative():
    rows = llm.deterministic_hypotheses()
    assert rows
    assert len(rows) <= llm.MAX_HYPOTHESES
    assert all(row["productionAuthority"] is False for row in rows)
    assert all(row["automaticWagerAllowed"] is False for row in rows)
    assert all(row["requiresSupervisedWalkForward"] is True for row in rows)
    assert all(row["hypothesisDigest"] for row in rows)


def test_unknown_feature_is_rejected():
    with pytest.raises(llm.HypothesisContractError):
        llm.validate_hypotheses(
            {
                "hypotheses": [
                    {
                        "hypothesisId": "bad",
                        "features": ["postgame_secret"],
                        "operation": "single",
                        "modelFamily": "threshold_regime",
                        "timingWindowsMinutes": [45],
                        "expectedDirection": "nonlinear",
                        "rationale": "This must fail.",
                    }
                ]
            }
        )


def test_bedrock_failure_falls_back_without_production_authority():
    class BrokenBedrock:
        def converse(self, **kwargs):
            raise RuntimeError("quota")

    result = llm.generate_hypotheses(
        {"latestAccuracy": 0.55},
        bedrock_client=BrokenBedrock(),
        model_id="example-model",
    )
    assert result["ok"] is True
    assert result["source"] == "DETERMINISTIC_FALLBACK_AFTER_BEDROCK_FAILURE"
    assert result["bedrockAuthoritativeForProduction"] is False
    assert result["hypotheses"]


def _synthetic_rows():
    rows = []
    first_day = date(2026, 5, 1)
    # Sixty complete slates with twenty games each ensures that validation and
    # untouched partitions both clear the evaluator's 100-row floor.
    for day in range(60):
        slate = (first_day + timedelta(days=day)).isoformat()
        for game in range(20):
            value = float(game) - 9.5
            home_won = int(value >= 0.0)
            rows.append(
                {
                    "slateDateEt": slate,
                    "homeWon": home_won,
                    "features": {
                        "deltaGapHome": value,
                        "reversalGapHome": 0.0,
                        # Deliberately weak baseline: always selects home.
                        "homeMarketDeVigProbability": 0.5,
                    },
                }
            )
    return rows


def test_hypothesis_is_selected_on_training_and_test_is_untouched():
    hypothesis = llm.validate_hypotheses(
        {
            "hypotheses": [
                {
                    "hypothesisId": "synthetic_direction",
                    "features": ["deltaGapHome"],
                    "operation": "single",
                    "modelFamily": "threshold_regime",
                    "timingWindowsMinutes": [60],
                    "expectedDirection": "home_positive",
                    "rationale": "Synthetic test signal.",
                }
            ]
        }
    )[0]
    result = llm.evaluate_hypothesis(_synthetic_rows(), hypothesis)
    assert result["ok"] is True
    assert result["status"] == "WALK_FORWARD_PASSED"
    assert result["wholeSlateChronologyPreserved"] is True
    assert result["untouchedHoldoutUsedForSelection"] is False
    assert result["productionAuthority"] is False
    assert result["validation"]["rowCount"] >= 100
    assert result["untouchedHoldout"]["rowCount"] >= 100
    assert result["untouchedHoldout"]["overallAccuracy"] > 0.9


def test_shadow_cycle_never_changes_winners_or_weights():
    result = llm.run_shadow_cycle(
        _synthetic_rows(),
        research_summary={"objective": "test"},
        bedrock_client=None,
    )
    assert result["ok"] is True
    assert result["productionAuthority"] is False
    assert result["productionWeightMutation"] is False
    assert result["winnerSelectionMutation"] is False
    assert result["automaticWagerAllowed"] is False
