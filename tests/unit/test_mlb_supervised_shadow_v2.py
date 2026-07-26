from __future__ import annotations

from datetime import date, timedelta

from hello_world import mlb_supervised_features_v2 as feature_module
from hello_world import mlb_supervised_model_v2 as model_module


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


def _record(day, game_id, home, away, home_won, home_probability=0.5):
    return {
        "slateDateEt": day,
        "officialGamePk": str(game_id),
        "homeTeam": home,
        "awayTeam": away,
        "homeWon": home_won,
        "homeSignal": _signal(home_probability),
        "awaySignal": _signal(1.0 - home_probability),
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
    }


def test_team_history_never_uses_same_slate_results():
    rows = [
        _record("2026-04-01", 1, "A", "B", 1),
        _record("2026-04-01", 2, "A", "C", 0),
        _record("2026-04-02", 3, "A", "B", 1),
    ]
    augmented = feature_module.add_strictly_past_team_history(rows)
    first, second, third = augmented
    assert first["teamHistoryFeatures"]["team_history_available"] == 0.0
    assert second["teamHistoryFeatures"]["team_history_available"] == 0.0
    # Both same-day rows saw the identical pre-slate ledger for team A.
    assert first["teamHistoryFeatures"]["team_home_elo"] == second["teamHistoryFeatures"]["team_home_elo"]
    # The next slate can use the now-completed prior day.
    assert third["teamHistoryFeatures"]["team_home_elo"] != first["teamHistoryFeatures"]["team_home_elo"]
    assert third["teamHistoryLeakageBoundary"] == "strictly_prior_complete_slate_days"


def test_v8_and_fundamental_missingness_are_explicit():
    row = _record("2026-04-01", 1, "Home Club", "Away Club", 1, 0.55)
    missing = feature_module.feature_map(row)
    assert missing["v8_available"] == 0.0
    assert missing["v8_f5_available"] == 0.0
    assert missing["fundamentals_available"] == 0.0

    row["homeSignal"]["oddsMarketExpansionFeatures"] = {
        "h2h_Home_ClubMedianImpliedProbability": 0.60,
        "h2h_Away_ClubMedianImpliedProbability": 0.45,
        "h2h_1st_5_innings_Home_ClubMedianImpliedProbability": 0.58,
        "h2h_1st_5_innings_Away_ClubMedianImpliedProbability": 0.47,
    }
    row["frozenFundamentalsSnapshot"] = {
        "home": {"starterQuality": 0.8, "bullpenFreshness": 0.7},
        "away": {"starterQuality": 0.4, "bullpenFreshness": 0.2},
    }
    present = feature_module.feature_map(row)
    assert present["v8_available"] == 1.0
    assert present["v8_f5_available"] == 1.0
    assert present["v8_h2h_home_minus_away"] > 0
    assert present["fundamentals_available"] == 1.0
    assert present["fund_starter_quality_diff"] > 0
    assert present["fund_bullpen_freshness_diff"] > 0


def test_expanding_folds_are_strictly_chronological():
    days = [(date(2025, 4, 1) + timedelta(days=index)).isoformat() for index in range(90)]
    folds = model_module.inner_expanding_folds(days)
    assert len(folds) == 3
    for train, validation in folds:
        assert max(train) < min(validation)
        assert set(train).isdisjoint(validation)


def test_residual_model_learns_non_market_signal_and_bounds_probability():
    examples = []
    names = feature_module.FEATURE_GROUPS["market"]
    for index in range(800):
        signal = 1.0 if index % 4 in (0, 1, 2) else -1.0
        outcome = 1 if signal > 0 else 0
        values = {name: 0.0 for name in names}
        values["market_home_centered"] = signal
        examples.append(
            feature_module.Example(
                day=f"2026-04-{(index % 28) + 1:02d}",
                game_id=str(index),
                outcome=outcome,
                market_probability=0.5,
                features=values,
                home_team="Home",
                away_team="Away",
            )
        )
    model = model_module.fit_residual_logistic(
        examples[:600], feature_group="market", l2=0.02, seed=17, steps=400
    )
    probabilities = [model.raw_probability(row) for row in examples[600:]]
    metrics = model_module.evaluate_probabilities(examples[600:], probabilities)
    assert metrics["overallAccuracy"] > 0.90
    assert all(model_module.PROBABILITY_FLOOR <= value <= model_module.PROBABILITY_CEILING for value in probabilities)


def test_v8_dataset_patch_advances_rematerialization_contract():
    from hello_world import mlb_supervised_v8_dataset_patch_v1 as patch

    class Optimizer:
        def _signal(self, game, observations, side, expected_slots):
            return {"side": side}

        def build_slate_dataset(self, *args, **kwargs):
            return {
                "records": [
                    {
                        "homeSignal": {
                            "oddsMarketExpansionFeatures": {"version": "v8"}
                        }
                    }
                ]
            }

    class Rematerialization:
        FEATURE_DATASET_VERSION = "old"
        VERSION = "old"

    optimizer = Optimizer()
    rematerialization = Rematerialization()
    patch.install(optimizer, rematerialization)
    signal = optimizer._signal(
        {},
        [{"oddsMarketExpansionFeatures": {"h2h_home": 0.55}, "oddsMarketExpansionVersion": "v8"}],
        "home",
        4,
    )
    assert signal["oddsMarketExpansionAvailable"] is True
    assert signal["oddsMarketExpansionFeatures"]["h2h_home"] == 0.55
    dataset = optimizer.build_slate_dataset()
    assert dataset["featureDatasetVersion"] == patch.FEATURE_DATASET_VERSION
    assert dataset["v8TrainableCoverage"] == 1.0
    assert rematerialization.FEATURE_DATASET_VERSION == patch.FEATURE_DATASET_VERSION
