from __future__ import annotations

import mlb_historical_v7_selective_objective_v1 as selective


class FakeOptimizer:
    @staticmethod
    def predict_record(record, policy):
        probability = float(record[policy["probabilityField"]])
        predicted_home = probability >= 0.5
        return {
            "slateDateEt": record["slateDateEt"],
            "officialGamePk": record["officialGamePk"],
            "homeWinProbability": probability,
            "homeWon": int(record["homeWon"]),
            "correct": predicted_home == bool(record["homeWon"]),
        }


def _records():
    rows = []
    for index in range(240):
        home_won = index % 2 == 0
        rows.append(
            {
                "slateDateEt": f"2026-06-{(index % 30) + 1:02d}",
                "officialGamePk": str(index),
                "homeWon": int(home_won),
                "candidateProbability": 0.80 if home_won else 0.20,
                "baselineProbability": 0.55 if home_won else 0.45,
            }
        )
    return rows


def test_selective_evaluator_uses_pick_pass_threshold():
    metrics = selective._evaluate(
        FakeOptimizer,
        _records(),
        {"probabilityField": "candidateProbability"},
        {row["slateDateEt"] for row in _records()},
        0.75,
    )
    assert metrics["pickCount"] == 240
    assert metrics["passCount"] == 0
    assert metrics["accuracy"] == 1.0
    assert metrics["coverage"] == 1.0


def test_threshold_is_frozen_before_untouched_evaluation():
    rows = _records()
    result = {
        "ok": True,
        "partitions": {
            "walkForward": sorted({row["slateDateEt"] for row in rows}),
            "untouchedHoldout": sorted({row["slateDateEt"] for row in rows}),
        },
        "candidate": {"policy": {"probabilityField": "candidateProbability"}},
        "baseline": {"policy": {"probabilityField": "baselineProbability"}},
    }
    evaluated = selective.evaluate_search_result(FakeOptimizer, rows, result)
    contract = evaluated["selectiveObjective"]
    assert evaluated["objective"] == "selective_individual_game_accuracy"
    assert contract["pickPassEnabled"] is True
    assert contract["thresholdFrozenBeforeUntouchedHoldout"] is True
    assert contract["thresholdSelectionUsedHoldoutLabels"] is False
    assert contract["frozenThreshold"] in selective.THRESHOLDS
