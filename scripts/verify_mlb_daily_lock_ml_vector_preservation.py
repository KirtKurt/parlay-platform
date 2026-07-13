#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_daily_lock_ml_vector_preservation_patch as patch
import mlb_daily_lock_coverage_patch as coverage_patch
import mlb_ml_clean_cohort_v1 as cohort


def fingerprint(vector: dict) -> str:
    return cohort.fingerprint_for_vector(vector)


def base_compact(row: dict) -> dict:
    return {
        "gameId": row.get("gameId"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "americanOdds": row.get("americanOdds"),
        "priceBook": row.get("priceBook"),
        "priceSource": row.get("priceSource"),
    }


def valid_row() -> dict:
    vector = {
        "version": patch.EXPECTED_VECTOR_VERSION,
        "createdAtUtc": "2026-07-13T15:00:00+00:00",
        "sourcePullAtUtc": "2026-07-13T14:59:00+00:00",
        "lockAtUtc": "2026-07-13T15:00:00+00:00",
        "gameId": "game-1",
        "slateDateEt": "2026-07-13",
        "commenceTime": "2026-07-13T18:00:00+00:00",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "selectedAmericanOdds": -115,
        "selectedPriceBook": "FanDuel",
        "selectedPriceSource": "real_book",
        "features": {"homeMarketProb": 0.55, "awayMarketProb": 0.45},
        "labels": {"homeWon": None, "pickCorrect": None},
        "immutableSource": "locked_prediction_row_pre_game_features",
        "derivedOnceFromImmutableLockedRow": True,
        "fingerprintVersion": cohort.FINGERPRINT_VERSION,
    }
    vector["fingerprint"] = fingerprint(vector)
    return {
        "gameId": "game-1",
        "gameIdentity": "provider:game-1",
        "gameKey": "mlb|2026-07-13|away club|home club",
        "slateDateEt": "2026-07-13",
        "commenceTime": "2026-07-13T18:00:00+00:00",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "americanOdds": -115,
        "lockedAmericanOdds": -115,
        "priceBook": "FanDuel",
        "priceSource": "real_book",
        "teamWinProbabilityPct": 55.0,
        "winProbabilityPct": 55.0,
        "winProbabilityMeaning": "estimated_probability_selected_team_wins_game",
        "probabilitySemanticsFixed": True,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1-test",
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "lockedPrediction": True,
        "lockedAtUtc": vector["lockAtUtc"],
        "predictionSourcePullAt": vector["sourcePullAtUtc"],
        "featureVectorFrozenAtLock": True,
        "frozenFeatureVectorVersion": vector["version"],
        "frozenFeatureVector": vector,
        "mlFeatureFreeze": {
            "exactVectorApplied": True,
            "exactVectorCreated": True,
            "completeSlateCoverage": True,
            "trainingEligible": True,
        },
        "slateCoverage": {"coverageComplete": True},
        # These accidental postgame fields must never be copied into the pregame card.
        "winner": "Home Club",
        "correct": True,
        "success": True,
    }


class FakeTable:
    def __init__(self) -> None:
        self.puts = []

    def put_item(self, *, Item, ConditionExpression=None):
        self.puts.append({
            "Item": copy.deepcopy(Item),
            "ConditionExpression": ConditionExpression,
        })
        return {}


def full_lock_module(prediction: dict) -> SimpleNamespace:
    game = {
        "game_id": "game-1",
        "id": "game-1",
        "game_key": "mlb|2026-07-13|away club|home club",
        "commence_time": "2026-07-13T18:00:00+00:00",
        "home_team": "Home Club",
        "away_team": "Away Club",
    }
    pulls = [
        {
            "pulled_at": "2026-07-13T14:59:00+00:00",
            "pull_id": "pull-1",
            "games": [game],
        }
    ]
    table = FakeTable()
    payload = {
        "ok": True,
        "modelVersion": "test-single-game-model",
        "engine": "test-engine",
        "predictions": [copy.deepcopy(prediction)],
        "promotedCount": 0,
        "storedCount": 1,
        "allGamesPredicted": True,
        "slateCoverage": {"coverageComplete": True},
    }

    def parse_dt(value):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    module = SimpleNamespace(
        _compact_pick=base_compact,
        _lock_response=lambda item: item,
        _get_lock_item=lambda slate: None,
        _pulls_for_date=lambda slate: copy.deepcopy(pulls),
        _parse_dt=parse_dt,
        _game_date_et=lambda row: parse_dt(row.get("commence_time") or row.get("commenceTime")).astimezone(ZoneInfo("America/New_York")).date().isoformat(),
        _first_start_et=lambda rows: min(parse_dt(row.get("commence_time") or row.get("commenceTime")).astimezone(ZoneInfo("America/New_York")) for row in rows),
        _now_utc=lambda: datetime(2026, 7, 13, 17, 16, tzinfo=timezone.utc),
        _latest_pull_age_minutes=lambda rows, now: 2.0,
        _pull_depths=lambda rows, games: {"provider:game-1": 4},
        _sort_picks=lambda rows: rows,
        _today_et=lambda: "2026-07-13",
        _lock_pk=lambda slate: f"LOCKED_PICKS#mlb#{slate}",
        _lock_sk=lambda: "DAILY_LOCK#TMINUS45",
        TABLE=table,
        REQUIRE_ALL_GAMES_FOR_LOCK=True,
        MIN_PULLS_PER_GAME_FOR_LOCK=4,
        MAX_LATEST_PULL_AGE_MINUTES=20,
        LOCK_MINUTES=45,
        LOCK_POLICY="first_mlb_game_minus_45_minutes",
        EASTERN=ZoneInfo("America/New_York"),
        timedelta=timedelta,
        history=SimpleNamespace(ddb_safe=lambda item: copy.deepcopy(item)),
        mlb_game_winner_engine=SimpleNamespace(predict_all=lambda slate, store, limit: copy.deepcopy(payload)),
        ClientError=Exception,
    )
    coverage_patch.apply(module)
    patch.apply(module)
    return module


def main() -> int:
    module = SimpleNamespace(_compact_pick=base_compact)
    status = patch.apply(module)
    assert status["ok"] is True
    assert status["failClosed"] is True

    source = valid_row()
    compact = module._compact_pick(source)
    vector = compact["frozenFeatureVector"]

    assert compact["mlLockVectorStorageVerified"] is True
    assert compact["mlLockVectorStorageErrors"] == []
    assert compact["mlLockVectorStorageVersion"] == patch.VERSION
    assert compact["featureVectorFrozenAtLock"] is True
    assert compact["frozenFeatureVectorVersion"] == patch.EXPECTED_VECTOR_VERSION
    assert vector["fingerprint"] == fingerprint(vector)
    assert vector["labels"] == {"homeWon": None, "pickCorrect": None}
    assert compact["lockedAmericanOdds"] == -115
    assert compact["priceBook"] == "FanDuel"
    assert compact["priceSource"] == "real_book"
    assert compact["teamWinProbabilityPct"] == 55.0
    assert compact["predictionSemanticsVersion"].startswith("MLB-OFFICIAL-PREDICTION-SEMANTICS-")
    for forbidden in ("winner", "correct", "success", "homeWon", "pickCorrect"):
        assert forbidden not in compact

    # Deep-copy proof: later mutation of the source row cannot change the write-once card.
    original_probability = vector["features"]["homeMarketProb"]
    source["frozenFeatureVector"]["features"]["homeMarketProb"] = 0.01
    assert compact["frozenFeatureVector"]["features"]["homeMarketProb"] == original_probability

    # Fail closed when the exact vector is missing.
    missing = valid_row()
    missing.pop("frozenFeatureVector")
    missing.pop("frozenFeatureVectorVersion")
    try:
        module._compact_pick(missing)
    except RuntimeError as exc:
        assert "missing_frozen_feature_vector" in str(exc)
    else:
        raise AssertionError("daily lock accepted a pick without the exact frozen vector")

    # Fail closed when a fingerprint does not match the immutable features.
    altered = valid_row()
    altered["frozenFeatureVector"]["features"]["homeMarketProb"] = 0.99
    try:
        module._compact_pick(altered)
    except RuntimeError as exc:
        assert "frozen_vector_fingerprint_mismatch" in str(exc)
    else:
        raise AssertionError("daily lock accepted a mutated frozen vector")

    # Full production-order proof: the complete-slate patch is installed first by
    # sitecustomize, then the protected Lambda installs this preservation wrapper.
    # The first clean row must survive that exact conversion path unchanged.
    clean_source = valid_row()
    clean_module = full_lock_module(clean_source)
    clean_result = clean_module.run_lock("2026-07-13", force=False)
    assert clean_result["locked"] is True
    assert len(clean_module.TABLE.puts) == 1
    stored_pick = clean_module.TABLE.puts[0]["Item"]["data"]["picks"][0]
    assert stored_pick["mlLockVectorStorageVerified"] is True
    assert stored_pick["frozenFeatureVector"] == clean_source["frozenFeatureVector"]
    assert stored_pick["frozenFeatureVector"]["labels"] == {"homeWon": None, "pickCorrect": None}
    assert clean_module.TABLE.puts[0]["ConditionExpression"] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"

    # Full fail-closed proof: an invalid row aborts card construction before the
    # DynamoDB write call. No partial or vectorless daily card can be persisted.
    invalid_source = valid_row()
    invalid_source.pop("frozenFeatureVector")
    invalid_source.pop("frozenFeatureVectorVersion")
    invalid_module = full_lock_module(invalid_source)
    try:
        invalid_module.run_lock("2026-07-13", force=False)
    except RuntimeError as exc:
        assert "missing_frozen_feature_vector" in str(exc)
    else:
        raise AssertionError("full lock path accepted a vectorless prediction")
    assert invalid_module.TABLE.puts == []

    print(
        "MLB daily lock ML vector preservation verified: exact vector, fingerprint, "
        "timestamps, semantics, selected price, and blank pregame labels survive compaction; "
        "invalid cards fail before storage, and the full complete-slate lock path "
        "cannot reach DynamoDB without the exact vector."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
