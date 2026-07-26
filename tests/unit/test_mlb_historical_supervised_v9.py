from __future__ import annotations

import copy
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))

import mlb_historical_daily_optimizer_v1 as optimizer
import mlb_historical_policy_v1 as policy
import mlb_historical_supervised_v9 as supervised


def _signal(side: str, team: str, delta: float):
    return {
        "side": side,
        "team": team,
        "fairProbability": 0.5,
        "marketConsensusProbability": 0.5,
        "probLatest": 0.5,
        "delta": delta,
        "bookDivergence": 0.01,
        "reversalCount": 0,
        "americanOdds": -105,
        "bookCount": 6,
        "marketSide": "pickem",
        "pullCountForGame": 20,
        "temporalFeatures": {
            "sourcePointCount": 20,
            "horizons": {
                "60m": {"velocityPpHr": delta * 100, "coverageRatio": 1.0},
                "180m": {"accelerationPpHr2": delta * 10, "volatilityPpPerPull": 0.1},
                "full": {"coverageRatio": 1.0},
            },
        },
        "tags": ["BOOK_AGREEMENT"],
    }


def _records():
    rows = []
    start = date(2025, 4, 1)
    for offset in range(60):
        day = (start + timedelta(days=offset)).isoformat()
        for game in range(4):
            home_won = (offset + game) % 2 == 0
            home_delta = 0.03 if home_won else -0.03
            rows.append(
                {
                    "slateDateEt": day,
                    "officialGamePk": f"{day}-{game}",
                    "homeTeam": f"Home {game}",
                    "awayTeam": f"Away {game}",
                    "homeWon": 1 if home_won else 0,
                    "homeSignal": _signal("home", f"Home {game}", home_delta),
                    "awaySignal": _signal("away", f"Away {game}", -home_delta),
                    "postLockDataExcluded": True,
                    "gameSpecificLockClipping": True,
                }
            )
    return rows


def _with_runtime(callback):
    original_select = policy.select_winner
    original_complement = policy.complementary_probabilities
    original_baseline = copy.deepcopy(policy.BASELINE_POLICY)
    original_bounds = copy.deepcopy(policy._NUMERIC_BOUNDS)
    had_flag = getattr(policy, "_INQSI_MLB_SUPERVISED_V9_POLICY_INSTALLED", False)
    try:
        supervised.install_policy_runtime(policy)
        return callback()
    finally:
        policy.select_winner = original_select
        policy.complementary_probabilities = original_complement
        policy.BASELINE_POLICY.clear()
        policy.BASELINE_POLICY.update(original_baseline)
        policy._NUMERIC_BOUNDS.clear()
        policy._NUMERIC_BOUNDS.update(original_bounds)
        if had_flag:
            policy._INQSI_MLB_SUPERVISED_V9_POLICY_INSTALLED = True
        elif hasattr(policy, "_INQSI_MLB_SUPERVISED_V9_POLICY_INSTALLED"):
            delattr(policy, "_INQSI_MLB_SUPERVISED_V9_POLICY_INSTALLED")


def test_supervised_schema_is_numeric_and_fail_closed():
    def run():
        assert policy.validate_policy(policy.BASELINE_POLICY) == ()
        assert policy.BASELINE_POLICY["supervisedEnabled"] == 0.0
        coefficient = supervised._field("Coefficient", "deltaDiff")
        assert coefficient in policy.BASELINE_POLICY
        invalid = copy.deepcopy(policy.BASELINE_POLICY)
        invalid["supervisedTemperature"] = 0.1
        assert "policy_field_out_of_bounds:supervisedTemperature" in policy.validate_policy(invalid)

    _with_runtime(run)


def test_v8_features_become_trainable_signal_inputs():
    game = {"homeTeam": "Boston Red Sox", "awayTeam": "New York Yankees"}
    latest = {
        "oddsMarketExpansionFeatures": {
            "h2h_Boston_Red_SoxMedianImpliedProbability": 0.58,
            "h2h_1st_5_innings_Boston_Red_SoxMedianImpliedProbability": 0.56,
            "spreads_Boston_Red_SoxMedianPoint": -1.5,
            "homeStarterBullpenSpreadDivergence": -1.0,
        }
    }
    value = supervised._v8_trainable(game, latest, "home")
    assert value["available"] is True
    assert value["h2hMedianImpliedProbability"] == 0.58
    assert value["firstFiveH2HMedianImpliedProbability"] == 0.56
    assert value["fullGameSpreadMedian"] == -1.5


def test_nested_supervised_fit_learns_direction_without_holdout_labels(monkeypatch):
    def run():
        monkeypatch.setattr(supervised, "L2_GRID", (0.1,))
        monkeypatch.setattr(supervised, "BLEND_GRID", (1.0,))
        monkeypatch.setattr(supervised, "TEMPERATURE_GRID", (1.0,))
        monkeypatch.setattr(supervised, "EPOCHS", 100)
        rows = _records()
        dates = sorted({row["slateDateEt"] for row in rows})
        candidate, diagnostics = supervised.fit_supervised_policy(
            optimizer, rows, dates[:50], policy.BASELINE_POLICY
        )
        validation = optimizer.evaluate_policy(rows, candidate, dates[50:])
        assert diagnostics["holdoutLabelsUsedForFitOrSelection"] is False
        assert candidate["supervisedEnabled"] == 1.0
        assert validation["meanDailyAccuracy"] >= 0.90
        assert validation["brierScore"] < 0.20

    _with_runtime(run)
