from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))

import mlb_historical_policy_v1 as policy


import pytest


@pytest.fixture(autouse=True)
def _reset_authority_caches():
    policy._CACHE.update(
        {"expires": 0.0, "champion": None, "status": "UNINITIALIZED", "error": None}
    )
    policy._CUTOVER_CACHE.update(
        {
            "expires": 0.0,
            "cutover": None,
            "status": "UNINITIALIZED",
            "error": None,
            "everActivated": False,
        }
    )
    yield


def valid_gate():
    return {
        "version": policy.PROMOTION_GATE_VERSION,
        "passed": True,
        "settledGameCount": 1400,
        "trainingGameCount": 1000,
        "walkForwardGameCount": 200,
        "untouchedHoldoutGameCount": 200,
        "walkForwardDayCount": 20,
        "untouchedHoldoutDayCount": 15,
        "walkForwardMinimumDailyAccuracy": 0.80,
        "walkForwardMeanDailyAccuracy": 0.84,
        "untouchedHoldoutMinimumDailyAccuracy": 0.80,
        "untouchedHoldoutMeanDailyAccuracy": 0.83,
        "walkForwardSlateCoverage": 1.0,
        "untouchedHoldoutSlateCoverage": 1.0,
        "holdoutWasUntouchedDuringSearch": True,
        "chronologicalWholeSlateSplits": True,
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
        "overfitChecksPassed": True,
    }


def valid_champion():
    artifact = {"bucket": "bucket", "key": "artifact.json", "versionId": "1", "sha256": "a" * 64}
    data = {
        "version": policy.VERSION,
        "recordType": policy.CHAMPION_RECORD_TYPE,
        "liveAuthorityEnabled": True,
        "shadowOnly": False,
        "policy": copy.deepcopy(policy.BASELINE_POLICY),
        "policyDigest": policy.policy_digest(policy.BASELINE_POLICY),
        "promotionGate": valid_gate(),
        "artifact": artifact,
        "activatedAtUtc": "2026-07-23T12:00:00+00:00",
    }
    return {"record_type": policy.CHAMPION_RECORD_TYPE, "data": data}


def valid_cutover():
    champion = valid_champion()["data"]
    data = policy.build_cutover_payload(champion)
    return {"record_type": policy.CUTOVER_RECORD_TYPE, "data": data}


def test_champion_requires_1000_training_games_and_1400_total_evidence_rows():
    item = valid_champion()
    item["data"]["promotionGate"]["trainingGameCount"] = 999
    result = policy.validate_champion(item)
    assert not result.ok
    assert "training_game_floor_not_met" in result.errors

    item = valid_champion()
    item["data"]["promotionGate"]["settledGameCount"] = 1399
    result = policy.validate_champion(item)
    assert not result.ok
    assert "settled_game_floor_not_met" in result.errors


def test_champion_requires_every_daily_metric_at_or_above_80_percent():
    item = valid_champion()
    item["data"]["promotionGate"]["untouchedHoldoutMinimumDailyAccuracy"] = 0.7999
    result = policy.validate_champion(item)
    assert not result.ok
    assert any("untouchedHoldoutMinimumDailyAccuracy" in error for error in result.errors)


def test_champion_rejects_policy_checksum_change():
    item = valid_champion()
    item["data"]["policy"]["movementWeight"] = 1.1
    result = policy.validate_champion(item)
    assert not result.ok
    assert "policy_digest_mismatch" in result.errors


def test_active_champion_is_fail_closed_and_cacheable(monkeypatch):
    policy._CACHE.update({"expires": 0.0, "champion": None})
    monkeypatch.setenv("MLB_HISTORICAL_POLICY_ENABLED", "true")
    calls = {"count": 0}

    def loader():
        calls["count"] += 1
        return valid_champion()

    first = policy.active_champion(force_refresh=True, loader=loader)
    second = policy.active_champion(loader=lambda: (_ for _ in ()).throw(RuntimeError("must not load")))
    assert first and second
    assert first["policyDigest"] == second["policyDigest"]
    assert calls["count"] == 1


def test_policy_signal_formula_uses_temporal_inputs_and_preserves_bounds():
    signal = {
        "side": "home",
        "team": "Home",
        "fairProbability": 0.55,
        "probLatest": 0.55,
        "delta": 0.01,
        "bookDivergence": 0.01,
        "reversalCount": 0,
        "americanOdds": -120,
        "bookCount": 5,
        "marketSide": "favorite",
        "pullCountForGame": 20,
        "temporalFeatures": {
            "sourcePointCount": 20,
            "horizons": {
                "60m": {"velocityPpHr": 1.5},
                "180m": {"accelerationPpHr2": 0.5, "volatilityPpPerPull": 0.2},
                "full": {"coverageRatio": 1.0},
            },
        },
        "tags": ["BOOK_AGREEMENT", "STEAM"],
    }
    candidate = copy.deepcopy(policy.BASELINE_POLICY)
    candidate["velocity60mWeight"] = 0.004
    updated = policy.apply_policy_to_signal(signal, candidate)
    assert updated["historicalPolicyApplied"] is True
    assert 0.05 <= updated["winProbability"] <= 0.95
    assert updated["historicalPolicyTemporalInputs"]["velocity60mPpHr"] == 1.5
    assert updated["historicalPolicyDigest"] == policy.policy_digest(candidate)


def test_runtime_patch_is_inert_without_valid_champion(monkeypatch):
    class Engine:
        pass

    def side_score(series, side):
        return {"side": side, "score": 51.0, "fairProbability": 0.51}

    Engine._side_score = staticmethod(side_score)
    monkeypatch.setattr(policy, "active_champion", lambda: None)
    monkeypatch.setattr(policy, "active_production_cutover", lambda: None)
    monkeypatch.setattr(policy, "champion_load_status", lambda: {"status": "ABSENT"})
    monkeypatch.setattr(policy, "production_cutover_status", lambda: {"status": "ABSENT"})
    policy.apply(Engine)
    assert Engine._side_score([], "home") == {"side": "home", "score": 51.0, "fairProbability": 0.51}


def _runtime_signal(side, team, fair, price):
    return {
        "side": side,
        "team": team,
        "fairProbability": fair,
        "marketConsensusProbability": fair,
        "probLatest": fair,
        "delta": 0.0,
        "bookDivergence": 0.01,
        "reversalCount": 0,
        "americanOdds": price,
        "bookCount": 6,
        "marketSide": "favorite" if price < -110 else "underdog",
        "pullCountForGame": 20,
        "temporalFeatures": {
            "sourcePointCount": 20,
            "horizons": {
                "60m": {"velocityPpHr": 0.0},
                "180m": {"accelerationPpHr2": 0.0, "volatilityPpPerPull": 0.0},
                "full": {"coverageRatio": 1.0},
            },
        },
        "tags": ["BOOK_AGREEMENT"],
    }


def test_promoted_historical_champion_is_outermost_and_old_direction_becomes_diagnostic(monkeypatch):
    class Engine:
        @staticmethod
        def predict_all(*args, **kwargs):
            return {
                "predictions": [
                    {
                        "homeTeam": "Home Club",
                        "awayTeam": "Away Club",
                        "predictedSide": "away",
                        "predictedWinner": "Away Club",
                        "modelVersion": "legacy-ranked-v15.10",
                        "winProbability": 0.55,
                        "homeSignal": _runtime_signal("home", "Home Club", 0.65, -185),
                        "awaySignal": _runtime_signal("away", "Away Club", 0.35, 165),
                        "tags": ["NO_PICK", "RELEASE_BLOCKED"],
                        "blockedReasons": ["legacy_negative_ev"],
                    }
                ]
            }

        read_persisted_predictions = predict_all

    champion = valid_champion()["data"]
    monkeypatch.setattr(policy, "active_champion", lambda *args, **kwargs: champion)
    monkeypatch.setattr(
        policy,
        "active_production_cutover",
        lambda *args, **kwargs: valid_cutover()["data"],
    )
    policy.apply_runtime_authority(Engine)
    result = Engine.predict_all()
    row = result["predictions"][0]
    assert row["predictedWinner"] == "Home Club"
    assert row["modelVersion"] == policy.VERSION
    assert row["soleProductionAlgorithm"] is True
    assert row["legacyAlgorithmAuthorityDisabled"] is True
    assert row["dailySlateAccuracyGatePassed"] is True
    assert row["accuracyEvidenceScope"] == "complete_day_slate_not_individual_game"
    assert row["precisionQualified"] is False
    assert row["selectionStatus"] == "PICK"
    assert row["productionSelectionAllowed"] is True
    assert row["officialPrediction"] is True
    assert row["actionablePick"] is False
    assert row["playable"] is False
    assert row["playablePick"] is False
    assert row["automaticWagerAllowed"] is False
    assert row["wagerAuthorization"] == "DISABLED"
    assert "automatic_wagering_disabled" in row["blockedReasons"]
    assert "legacy_negative_ev" in row["blockedReasons"]
    assert "PREDICTION_ONLY" in row["tags"]
    assert "WAGER_DISABLED" in row["tags"]
    assert result["automaticWagerAllowed"] is False
    assert result["wagerAuthorization"] == "DISABLED"
    assert result["primaryAlgorithm"] == policy.VERSION
    assert Engine.read_persisted_predictions()["predictions"][0]["predictedWinner"] == "Home Club"


def test_complementary_probabilities_sum_to_one_without_changing_direction():
    home = {"winProbability": 0.72}
    away = {"winProbability": 0.44}
    home_probability, away_probability = policy.complementary_probabilities(home, away)
    assert home_probability + away_probability == 1.0
    assert home_probability > away_probability


def test_immutable_locked_persisted_prediction_is_never_rewritten_by_later_champion(monkeypatch):
    locked = {
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedSide": "away",
        "predictedWinner": "Away Club",
        "modelVersion": "locked-incumbent",
        "winProbability": 0.55,
        "homeSignal": _runtime_signal("home", "Home Club", 0.65, -185),
        "awaySignal": _runtime_signal("away", "Away Club", 0.35, 165),
        "lockedPrediction": True,
        "lockStatus": "LOCKED_PREDICTION",
    }

    class Engine:
        @staticmethod
        def predict_all(*args, **kwargs):
            return {"predictions": [copy.deepcopy(locked)]}

        read_persisted_predictions = predict_all

    champion = valid_champion()["data"]
    monkeypatch.setattr(policy, "active_champion", lambda *args, **kwargs: champion)
    monkeypatch.setattr(
        policy,
        "active_production_cutover",
        lambda *args, **kwargs: valid_cutover()["data"],
    )
    policy.apply_runtime_authority(Engine)

    live = Engine.predict_all()["predictions"][0]
    persisted = Engine.read_persisted_predictions()["predictions"][0]
    assert live["predictedWinner"] == "Home Club"
    assert live["modelVersion"] == policy.VERSION
    assert persisted == locked


def test_runtime_fails_closed_when_champion_lookup_is_invalid_or_unavailable(monkeypatch):
    class Engine:
        @staticmethod
        def predict_all(*args, **kwargs):
            return {"predictions": []}

        read_persisted_predictions = predict_all

    monkeypatch.setattr(policy, "active_champion", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        policy,
        "champion_load_status",
        lambda: {"status": "ERROR", "error": "dynamodb unavailable"},
    )
    policy.apply_runtime_authority(Engine)
    try:
        Engine.predict_all()
        assert False, "expected fail-closed runtime error"
    except RuntimeError as exc:
        assert "historical_champion_authority_unavailable_fail_closed" in str(exc)


def test_last_known_good_champion_survives_transient_lookup_failure(monkeypatch):
    policy._CACHE.update(
        {"expires": 0.0, "champion": None, "status": "UNINITIALIZED", "error": None}
    )
    monkeypatch.setenv("MLB_HISTORICAL_POLICY_ENABLED", "true")
    first = policy.active_champion(force_refresh=True, loader=valid_champion)
    assert first

    def broken_loader():
        raise RuntimeError("temporary ddb failure")

    retained = policy.active_champion(force_refresh=True, loader=broken_loader)
    assert retained and retained["policyDigest"] == first["policyDigest"]
    status = policy.champion_load_status()
    assert status["status"] == "ERROR"
    assert status["hasLastKnownGoodChampion"] is True



def test_cutover_is_write_once_historical_only_and_disables_legacy_fallback():
    validation = policy.validate_cutover(valid_cutover())
    assert validation.ok
    assert validation.cutover["historicalOnly"] is True
    assert validation.cutover["legacyFallbackAllowed"] is False
    assert validation.cutover["incumbentProductionAuthorityDestroyed"] is True


def test_runtime_rejects_champion_without_atomic_cutover(monkeypatch):
    class Engine:
        @staticmethod
        def predict_all(*args, **kwargs):
            return {"predictions": []}

        read_persisted_predictions = predict_all

    champion = valid_champion()["data"]
    monkeypatch.setattr(policy, "active_champion", lambda *args, **kwargs: champion)
    monkeypatch.setattr(policy, "active_production_cutover", lambda *args, **kwargs: None)
    monkeypatch.setattr(policy, "champion_load_status", lambda: {"status": "ACTIVE"})
    monkeypatch.setattr(policy, "production_cutover_status", lambda: {"status": "ABSENT"})
    policy.apply_runtime_authority(Engine)
    with pytest.raises(RuntimeError, match="without_atomic_cutover"):
        Engine.predict_all()


def test_runtime_rejects_cutover_without_champion_and_never_restores_v15_10(monkeypatch):
    class Engine:
        @staticmethod
        def predict_all(*args, **kwargs):
            return {"predictions": [{"predictedWinner": "legacy"}]}

        read_persisted_predictions = predict_all

    monkeypatch.setattr(policy, "active_champion", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        policy,
        "active_production_cutover",
        lambda *args, **kwargs: valid_cutover()["data"],
    )
    monkeypatch.setattr(policy, "champion_load_status", lambda: {"status": "ABSENT"})
    monkeypatch.setattr(policy, "production_cutover_status", lambda: {"status": "ACTIVE"})
    policy.apply_runtime_authority(Engine)
    with pytest.raises(RuntimeError, match="cutover_missing_champion"):
        Engine.predict_all()


def test_cutover_cache_never_reverts_to_absent_after_activation(monkeypatch):
    monkeypatch.setenv("MLB_HISTORICAL_POLICY_ENABLED", "true")
    first = policy.active_production_cutover(
        force_refresh=True, loader=valid_cutover
    )
    assert first and first["historicalOnly"] is True
    retained = policy.active_production_cutover(
        force_refresh=True, loader=lambda: None
    )
    assert retained and retained["historicalOnly"] is True
    status = policy.production_cutover_status()
    assert status["status"] == "MISSING_AFTER_ACTIVATION"
    assert status["everActivated"] is True
    assert status["legacyFallbackAllowed"] is False


def test_promoted_champion_fails_closed_when_any_current_slate_row_cannot_be_rescored(monkeypatch):
    class Engine:
        @staticmethod
        def predict_all(*args, **kwargs):
            return {
                "predictions": [
                    {
                        "homeTeam": "Home Club",
                        "awayTeam": "Away Club",
                        "predictedWinner": "Away Club",
                        "homeSignal": _runtime_signal("home", "Home Club", 0.65, -185),
                        # A promoted champion must not silently retain the old
                        # selector for a row missing its frozen away signal.
                        "awaySignal": None,
                    }
                ]
            }

        read_persisted_predictions = predict_all

    monkeypatch.setattr(
        policy, "active_champion", lambda *args, **kwargs: valid_champion()["data"]
    )
    monkeypatch.setattr(
        policy,
        "active_production_cutover",
        lambda *args, **kwargs: valid_cutover()["data"],
    )
    policy.apply_runtime_authority(Engine)
    with pytest.raises(RuntimeError, match="incomplete_slate_rescore_fail_closed"):
        Engine.predict_all()


def test_authority_records_are_bound_to_dedicated_partition_keys():
    champion = valid_champion()
    champion["PK"] = policy.CUTOVER_PK
    result = policy.validate_champion(champion)
    assert result.ok is False
    assert "champion_partition_key_mismatch" in result.errors

    cutover = valid_cutover()
    cutover["PK"] = policy.CHAMPION_PK
    result = policy.validate_cutover(cutover)
    assert result.ok is False
    assert "cutover_partition_key_mismatch" in result.errors


def test_cutover_payload_self_attests_both_authority_partitions():
    cutover = policy.build_cutover_payload(valid_champion()["data"])
    assert cutover["championPartitionKey"] == policy.CHAMPION_PK
    assert cutover["cutoverPartitionKey"] == policy.CUTOVER_PK
    validation = policy.validate_cutover(
        {
            "PK": policy.CUTOVER_PK,
            "SK": policy.CUTOVER_SK,
            "record_type": policy.CUTOVER_RECORD_TYPE,
            "data": cutover,
        }
    )
    assert validation.ok, validation.errors
