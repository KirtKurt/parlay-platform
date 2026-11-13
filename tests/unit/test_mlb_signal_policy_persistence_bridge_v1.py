from __future__ import annotations

from types import SimpleNamespace

import mlb_signal_policy_persistence_bridge_v1 as bridge
import mlb_signal_policy_v12 as signal_policy


def _row():
    return {
        "gameId": "provider-event",
        "gameIdentity": "provider-event",
        "gameKey": "mlb|2026-07-23|away|home",
        "homeTeam": "Home Team",
        "awayTeam": "Away Team",
        "commenceTime": "2026-07-23T18:00:00+00:00",
        "predictedWinner": "Home Team",
        "predictedSide": "home",
        "score": 62.0,
        "confidenceTier": "Solid",
        "winProbability": 0.58,
        "winProbabilityPct": 58.0,
        "teamWinProbabilityPct": 58.0,
        "tags": ["BOOK_AGREEMENT"],
        "homeSignal": {
            "marketConsensusProbability": 0.58,
            "probLatest": 0.58,
            "delta": 0.01,
            "latestGap": 0.16,
            "reversalCount": 1,
            "tags": ["BOOK_AGREEMENT"],
        },
        "awaySignal": {
            "marketConsensusProbability": 0.42,
            "probLatest": 0.42,
            "delta": -0.01,
            "latestGap": 0.16,
            "reversalCount": 1,
            "tags": [],
        },
    }


def test_bridge_restores_policy_after_later_wrapper_replaced_predict_all():
    module = SimpleNamespace()
    module.predict_all = lambda *_args, **_kwargs: {
        "ok": True,
        "modelVersion": "test-model",
        "predictions": [_row()],
    }

    signal_policy.apply(module)
    first_policy_result = module.predict_all()
    assert first_policy_result["predictions"][0]["signalPolicyV13"]["version"] == signal_policy.VERSION

    # Reproduce the live failure: a later public wrapper replaces predict_all,
    # while the old module-level applied flag remains true.
    module.predict_all = lambda *_args, **_kwargs: {
        "ok": True,
        "modelVersion": "test-model+public-authority",
        "predictions": [_row()],
    }
    assert getattr(module, "_INQSI_MLB_SIGNAL_POLICY_V12_APPLIED", False) is True

    bridge.apply(module, signal_policy)
    result = module.predict_all(store=False)
    row = result["predictions"][0]

    assert row["signalPolicyV13"]["version"] == signal_policy.VERSION
    assert result["signalPolicyPersistenceBridge"]["version"] == bridge.VERSION
    assert row["predictedWinner"] == "Home Team"
    assert row["predictedSide"] == "home"
    assert row["winProbability"] == 0.58
    assert row["teamWinProbabilityPct"] == 58.0


def test_bridge_is_idempotent_and_does_not_double_adjust_current_rows():
    module = SimpleNamespace()
    base = {
        "ok": True,
        "modelVersion": "test-model",
        "predictions": [_row()],
    }
    module.predict_all = lambda *_args, **_kwargs: base

    bridge.apply(module, signal_policy)
    first = module.predict_all()
    first_row = first["predictions"][0]
    first_score = first_row["scoreAfterSignalPolicyV13"]
    first_adjustment = first_row["signalPolicyV13Adjustment"]

    bridge.apply(module, signal_policy)
    second = module.predict_all()
    second_row = second["predictions"][0]

    assert second_row["scoreAfterSignalPolicyV13"] == first_score
    assert second_row["signalPolicyV13Adjustment"] == first_adjustment
    assert second_row["predictedWinner"] == first_row["predictedWinner"]
    assert second_row["winProbability"] == first_row["winProbability"]
