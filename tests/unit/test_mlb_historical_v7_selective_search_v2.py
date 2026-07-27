from __future__ import annotations

import mlb_historical_v7_selective_search_v2 as subject


def signal(probability: float, *, reversals: int = 0, divergence: float = 0.01):
    return {
        "probLatest": probability,
        "marketConsensusProbability": probability,
        "americanOdds": -150 if probability >= 0.5 else 130,
        "pullCountForGame": 10,
        "bookDivergence": divergence,
        "reversalCount": reversals,
        "tags": ["BOOK_AGREEMENT", "STEAM"],
        "temporalFeatures": {
            "sourcePointCount": 10,
            "horizons": {
                "full": {"coverageRatio": 0.95},
                "180m": {"volatilityPpPerPull": 0.01},
            },
        },
    }


class Config:
    maximum_candidates = 2

    def validate(self):
        return self


class Optimizer:
    SearchConfig = Config

    @staticmethod
    def candidate_policies(config):
        yield {"name": "baseline"}
        yield {"name": "strong"}

    @staticmethod
    def chronological_partitions(records, config, untouched_holdout_dates=None):
        dates = sorted({row["slateDateEt"] for row in records})
        return {
            "train": dates[:20],
            "walkForward": dates[20:70],
            "untouchedHoldout": dates[70:120],
        }

    @staticmethod
    def predict_record(record, policy):
        home_probability = record["homeSignal"]["probLatest"]
        side = "home" if home_probability >= 0.5 else "away"
        home_won = int(record["homeWon"])
        return {
            "slateDateEt": record["slateDateEt"],
            "predictedSide": side,
            "homeWinProbability": home_probability,
            "correct": (side == "home") == bool(home_won),
        }


def records():
    output = []
    for day in range(120):
        date = f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}"
        for game in range(5):
            home_won = (day + game) % 5 != 0
            probability = 0.78 if home_won else 0.22
            output.append(
                {
                    "slateDateEt": date,
                    "homeWon": int(home_won),
                    "homeSignal": signal(probability),
                    "awaySignal": signal(1.0 - probability),
                }
            )
    return output


def test_reliability_rejects_reversal_instability():
    record = {
        "homeSignal": signal(0.75, reversals=3),
        "awaySignal": signal(0.25),
    }
    prediction = {"predictedSide": "home"}
    ok, reasons = subject._reliable(record, prediction, subject.RELIABILITY_PROFILES["balanced"])
    assert ok is False
    assert "reversals" in reasons


def test_selective_search_freezes_choices_before_holdout():
    optimizer = Optimizer()
    result = subject.search(optimizer, records(), Config())
    assert result["ok"] is True
    assert result["objective"] == "selective_individual_game_accuracy"
    assert result["thresholdFrozenBeforeUntouchedHoldout"] is True
    assert result["thresholdSelectionUsedHoldoutLabels"] is False
    assert result["walkForward"]["pickCount"] >= 200
    assert result["untouchedHoldout"]["pickCount"] >= 200
    assert result["promotionAuthority"] is False


def test_install_exposes_independent_v7_search():
    optimizer = Optimizer()
    subject.install(optimizer)
    assert optimizer.V7_SELECTIVE_SEARCH_VERSION == subject.VERSION
    assert callable(optimizer.v7_selective_search)
