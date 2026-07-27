from __future__ import annotations

from datetime import date, timedelta

from hello_world import mlb_supervised_features_v2 as feature_module
from hello_world import mlb_supervised_model_v2_2 as model


def _signal(probability=0.5):
    return {
        "fairProbability": probability,
        "marketConsensusProbability": probability,
        "americanOdds": -110,
        "bookCount": 6,
        "bookDivergence": 0.01,
        "delta": 0.0,
        "reversalCount": 0,
        "pullCountForGame": 12,
        "marketSide": "pickem",
        "tags": ["BOOK_AGREEMENT"],
        "temporalFeatures": {
            "horizons": {
                name: {
                    "coverageRatio": 1.0,
                    "velocityPpHr": 0.0,
                    "accelerationPpHr2": 0.0,
                    "volatilityPpPerPull": 0.0,
                }
                for name in ("15m", "60m", "180m", "full")
            }
        },
    }


def _record(day, game_id, home, away, home_won):
    return {
        "slateDateEt": day,
        "officialGamePk": str(game_id),
        "homeTeam": home,
        "awayTeam": away,
        "homeWon": home_won,
        "homeSignal": _signal(0.55),
        "awaySignal": _signal(0.45),
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
    }


def test_offseason_resets_recent_form_and_current_season_experience():
    rows = []
    for index in range(10):
        rows.append(_record(f"2025-09-{index + 1:02d}", index, "A", "B", 1))
    rows.append(_record("2026-03-25", 100, "A", "B", 0))
    rows.append(_record("2026-03-26", 101, "A", "B", 0))
    augmented = model.add_season_aware_team_history(rows)
    opening = next(row for row in augmented if row["slateDateEt"] == "2026-03-25")
    next_day = next(row for row in augmented if row["slateDateEt"] == "2026-03-26")
    opening_features = opening["teamHistoryFeatures"]
    next_features = next_day["teamHistoryFeatures"]
    assert opening_features["team_recent10_diff"] == 0.0
    assert opening_features["team_streak_diff"] == 0.0
    assert opening_features["team_history_available"] == 0.0
    assert abs(opening_features["team_elo_diff"]) < 0.25
    assert next_features["team_recent10_diff"] < 0.0
    assert opening["teamHistoryLeakageBoundary"].endswith("offseason_reset")


def test_day_balanced_weights_give_each_slate_equal_total_mass():
    examples = []
    for index in range(2):
        examples.append(
            feature_module.Example(
                day="2026-04-01",
                game_id=f"a-{index}",
                outcome=index % 2,
                market_probability=0.5,
                features={},
                home_team="H",
                away_team="A",
            )
        )
    for index in range(10):
        examples.append(
            feature_module.Example(
                day="2026-04-02",
                game_id=f"b-{index}",
                outcome=index % 2,
                market_probability=0.5,
                features={},
                home_team="H",
                away_team="A",
            )
        )
    weights = model._example_weights(
        examples, model.WeightingConfig("day", True)
    )
    first = sum(weights[:2])
    second = sum(weights[2:])
    assert abs(first - second) < 1e-12
    assert abs(sum(weights) - len(examples)) < 1e-12


def test_current_season_boost_increases_recent_year_mass():
    examples = [
        feature_module.Example("2025-09-01", "old", 1, 0.5, {}, "H", "A"),
        feature_module.Example("2026-04-01", "new", 0, 0.5, {}, "H", "A"),
    ]
    weights = model._example_weights(
        examples,
        model.WeightingConfig("season", False, current_season_boost=3.0),
    )
    assert weights[1] > weights[0] * 2.9


def test_robust_key_rejects_aggregate_gain_with_unstable_folds():
    market = {
        "meanDailyAccuracy": 0.56,
        "overallAccuracy": 0.56,
        "brierScore": 0.245,
        "logLoss": 0.69,
    }

    def metrics(mean, pass_rate, minimum=0.3, overall=None, brier=0.244, log_loss=0.688):
        return {
            "meanDailyAccuracy": mean,
            "dailyPassRate": pass_rate,
            "minimumDailyAccuracy": minimum,
            "overallAccuracy": mean if overall is None else overall,
            "brierScore": brier,
            "logLoss": log_loss,
            "expectedCalibrationError": 0.04,
        }

    stable = {
        "oofMetrics": metrics(0.59, 0.20),
        "oofMarketBaseline": market,
        "folds": [
            {"metrics": metrics(0.58, 0.18)},
            {"metrics": metrics(0.59, 0.20)},
            {"metrics": metrics(0.60, 0.22)},
        ],
    }
    unstable = {
        "oofMetrics": metrics(0.61, 0.25),
        "oofMarketBaseline": market,
        "folds": [
            {"metrics": metrics(0.45, 0.05)},
            {"metrics": metrics(0.65, 0.30)},
            {"metrics": metrics(0.70, 0.40)},
        ],
    }
    assert model._robust_key(stable) < model._robust_key(unstable)


def test_weighted_model_learns_synthetic_direction():
    examples = []
    names = feature_module.FEATURE_GROUPS["market"]
    start = date(2025, 4, 1)
    for day_offset in range(80):
        game_day = (start + timedelta(days=day_offset)).isoformat()
        for game in range(6):
            direction = 1.0 if (day_offset + game) % 3 else -1.0
            values = {name: 0.0 for name in names}
            values["market_home_centered"] = direction
            examples.append(
                feature_module.Example(
                    game_day,
                    f"{day_offset}-{game}",
                    1 if direction > 0 else 0,
                    0.5,
                    values,
                    "Home",
                    "Away",
                )
            )
    fitted = model.fit_weighted_residual_logistic(
        examples[:360],
        feature_group="market",
        l2=0.02,
        seed=17,
        weighting=model.WeightingConfig("day", True),
        steps=300,
    )
    probabilities = [fitted.raw_probability(row) for row in examples[360:]]
    metrics = model.base.evaluate_probabilities(examples[360:], probabilities)
    assert metrics["overallAccuracy"] > 0.95
