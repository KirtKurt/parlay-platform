#!/usr/bin/env python3
from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

try:
    import boto3  # noqa: F401
except ModuleNotFoundError:
    from types import ModuleType

    boto3_stub = ModuleType("boto3")
    boto3_stub.resource = lambda *_args, **_kwargs: SimpleNamespace(Table=lambda _name: None)
    conditions_stub = ModuleType("boto3.dynamodb.conditions")

    class KeyStub:
        def __init__(self, _name):
            pass

        def eq(self, _value):
            return self

        def begins_with(self, _value):
            return self

        def __and__(self, _other):
            return self

    conditions_stub.Key = KeyStub
    sys.modules["boto3"] = boto3_stub
    sys.modules["boto3.dynamodb"] = ModuleType("boto3.dynamodb")
    sys.modules["boto3.dynamodb.conditions"] = conditions_stub

import mlb_fundamentals_snapshot_v1 as fundamentals
import mlb_daily_per_game_lock_patch as per_game
import mlb_game_winner_engine as winner_engine
import mlb_immutable_locked_storage_patch as immutable_storage
import mlb_ml_clean_cohort_hardening_v1 as hardening
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_dual_model_v1 as dual
import mlb_ml_exact_lock_vector_patch as exact_patch
import mlb_ml_frozen_features as frozen_features
import mlb_official_freeze_bridge as freeze_bridge
import mlb_official_prediction_semantics as semantics
import mlb_slate_coverage_patch as slate_coverage
import mlb_slate_prediction_lock as slate_lock
import mlb_temporal_features_v1 as temporal


def advanced_context():
    return {
        "version": "TEST-ADVANCED-CONTEXT-v1",
        "confirmed_probable_pitchers": {"source_status": "CONNECTED"},
        "fip_xfip": {
            "source_status": "CONNECTED",
            "home_starter_fip": 3.2,
            "away_starter_fip": 4.1,
            "home_starter_xfip": 3.4,
            "away_starter_xfip": 4.0,
        },
        "wrc_plus": {"source_status": "CONNECTED", "home_team_wrc_plus": 112, "away_team_wrc_plus": 96},
        "starter_handedness_splits": {"source_status": "CONNECTED"},
        "bullpen_fatigue": {"source_status": "CONNECTED", "home_bullpen_fatigue_score": 0.2, "away_bullpen_fatigue_score": 0.7},
        "confirmed_lineups": {"source_status": "PARTIAL", "home_lineup_strength_delta": 0.1},
        "weather_wind_roof": {"source_status": "CONNECTED", "wind_out_mph": 7},
        "ballpark_factors": {"source_status": "CONNECTED", "park_factor_runs": 1.04},
        "travel_rest": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "injuries_late_scratches_news": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "public_betting_handle": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "closing_line_value": {"source_status": "SCHEMA_CONNECTED_PENDING_CLOSING_SNAPSHOT"},
    }


def series(source_at: datetime, include_future: bool = True):
    rows = []
    start = source_at - timedelta(hours=3)
    for index in range(13):
        at = start + timedelta(minutes=15 * index)
        home = 0.50 + index * 0.002 + (0.003 if index in {5, 9} else 0.0) - (0.002 if index in {6, 10} else 0.0)
        rows.append({"pulled_at": at.isoformat(), "fair": {"home": home, "away": 1.0 - home}})
    if include_future:
        rows.append({"pulled_at": (source_at + timedelta(minutes=15)).isoformat(), "fair": {"home": 0.99, "away": 0.01}})
    return rows


def pregame_row():
    source = datetime(2026, 7, 13, 17, 55, tzinfo=timezone.utc)
    lock = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc)
    points = series(source)
    home_temporal = temporal.summarize_side(points, "home", cutoff_at=source)
    away_temporal = temporal.summarize_side(points, "away", cutoff_at=source)
    row = {
        "id": "temporal-game-1",
        "gameId": "temporal-game-1",
        "slateDateEt": "2026-07-13",
        "slate_date": "2026-07-13",
        "commenceTime": "2026-07-13T20:00:00+00:00",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "score": 62.0,
        "teamWinProbabilityPct": 56.0,
        "winProbabilityMeaning": "estimated_probability_selected_team_wins_game",
        "probabilitySemanticsFixed": True,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1-test",
        "americanOdds": -125,
        "lockedAmericanOdds": -125,
        "priceBook": "fanduel",
        "priceSource": "real_book",
        "advanced_context": advanced_context(),
        "slatePredictionLock": {"locked": True, "lockAtUtc": lock.isoformat(), "latestScoringPullAt": source.isoformat()},
        "predictionSourcePullAt": source.isoformat(),
        "lockedPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "homeSignal": {
            "marketConsensusProbability": 0.524,
            "probLatest": 0.524,
            "delta": 0.024,
            "bookDivergence": 0.01,
            "reversalCount": 4,
            "americanOdds": -125,
            "priceBook": "fanduel",
            "priceSource": "real_book",
            "tags": ["BOOK_AGREEMENT", "STEAM"],
            "temporalFeatures": home_temporal,
        },
        "awaySignal": {
            "marketConsensusProbability": 0.476,
            "probLatest": 0.476,
            "delta": -0.024,
            "bookDivergence": 0.01,
            "reversalCount": 4,
            "americanOdds": 110,
            "priceBook": "fanduel",
            "priceSource": "real_book",
            "tags": ["BOOK_AGREEMENT"],
            "temporalFeatures": away_temporal,
        },
    }
    row["fundamentalsSnapshot"] = fundamentals.build(row)
    return row


def ddb_round_trip(value):
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("AWS_REGION", "us-east-1")
    os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    os.environ.setdefault("SNAPSHOTS_TABLE", "")
    import inqsi_pull_history
    safe = inqsi_pull_history.ddb_safe(value)
    try:
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

        return TypeDeserializer().deserialize(TypeSerializer().serialize(safe))
    except ModuleNotFoundError:
        return copy.deepcopy(safe)


def actual_locked_wrapper_result():
    import inqsi_pull_history

    now = datetime.now(timezone.utc)
    commence = now + timedelta(minutes=20)
    slate = commence.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    pulls = []
    for index in range(13):
        pulled_at = now - timedelta(minutes=210 - 15 * index)
        home_price = -102 - index * 2
        away_price = -108 + index * 2
        game = {
            "game_id": "actual-lock-game",
            "id": "actual-lock-game",
            "game_key": "mlb|actual-lock-game",
            "home_team": "Actual Home",
            "away_team": "Actual Away",
            "commence_time": commence.isoformat(),
            "provider_sport_key": "baseball_mlb",
            "books": {
                "fanduel": {"ml": {"home": home_price, "away": away_price}},
                "draftkings": {"ml": {"home": home_price - 2, "away": away_price + 2}},
            },
        }
        pulled_at_value = pulled_at.isoformat()
        pull_id = f"pull-{index}"
        manifest = inqsi_pull_history._build_provider_schedule_manifest(
            sport="mlb",
            slate=slate,
            pulled_at=pulled_at_value,
            pull_id=pull_id,
            source="the_odds_api",
            games=[game],
        )
        manifest_key = inqsi_pull_history._provider_manifest_key(manifest)
        pulls.append({
            "sport": "mlb",
            "source": "the_odds_api",
            "slate_date": slate,
            "pulled_at": pulled_at_value,
            "pull_id": pull_id,
            "games": [game],
            "provider_schedule_manifest": manifest,
            "provider_manifest_binding": {
                "version": inqsi_pull_history.PROVIDER_MANIFEST_VERSION,
                "fingerprint": manifest["fingerprint"],
                "gameCount": manifest["gameCount"],
                "pk": manifest_key["PK"],
                "sk": manifest_key["SK"],
                "immutable": True,
                "fullProviderSchedule": True,
            },
        })

    latest_game = pulls[-1]["games"][0]
    source_at = datetime.fromisoformat(pulls[-1]["pulled_at"])
    lock_at = commence - timedelta(minutes=45)
    canonical = winner_engine._prediction_for_game(pulls, latest_game, slate)
    canonical.update({
        "advanced_context": advanced_context(),
        "createdAt": source_at.isoformat(),
        "lockedAtUtc": lock_at.isoformat(),
        "predictionSourcePullAt": source_at.isoformat(),
        "lockedPrediction": True,
        "officialPrediction": True,
        "officialPick": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "immutablePerGameStage": True,
        "lastPrelockSelectionFingerprint": "selection-actual-lock-game",
        "lastPrelockPromotionVersion": per_game.PROMOTION_POLICY_VERSION,
        "modelOrSignalRecomputedAtLock": False,
        "slateCoverage": {"coverageComplete": True},
        "slatePredictionLock": {
            "locked": True,
            "slateWideLock": False,
            "perGameLock": True,
            "lockAtUtc": lock_at.isoformat(),
            "latestScoringPullAt": source_at.isoformat(),
        },
    })
    canonical["teamWinProbabilityPct"] = canonical.get("winProbabilityPct")
    fundamentals.enhance_row(canonical)
    exact_patch.apply(frozen_features)
    freeze_bridge.apply(semantics)
    canonical_result = semantics.enhance_result({
        "ok": True,
        "sport": "mlb",
        "slate_date": slate,
        "slateCoverage": {"coverageComplete": True},
        "slatePredictionLock": canonical["slatePredictionLock"],
        "predictions": [canonical],
    })
    canonical = canonical_result["predictions"][0]
    stage = {
        **immutable_storage._stage_key(canonical),
        "record_type": per_game.STAGE_RECORD_TYPE,
        "slate_date": slate,
        "game_identity": slate_coverage.game_identity(canonical),
        "commence_time": canonical.get("commenceTime"),
        "scheduled_lock_at_utc": lock_at.isoformat(),
        "source_pull_at_utc": source_at.isoformat(),
        "staged_at_utc": now.isoformat(),
        "promotion_policy_version": per_game.PROMOTION_POLICY_VERSION,
        "immutable_staged": True,
        "write_once": True,
        "candidate_proof": {
            "version": per_game.PROMOTION_POLICY_VERSION,
            "predictionSourcePullAtUtc": source_at.isoformat(),
            "predictionCreatedAtUtc": source_at.isoformat(),
            "predictionPersistedAtUtc": lock_at.isoformat(),
            "sourceAtOrBeforeCutoff": True,
            "createdAtOrBeforeCutoff": True,
            "persistedAtOrBeforeCutoff": True,
            "candidateSelectionFingerprint": canonical.get("lastPrelockSelectionFingerprint"),
            "modelOrSignalRecomputedAtLock": False,
        },
        "data": {"row": copy.deepcopy(canonical)},
    }
    stage["stage_fingerprint"] = per_game._stage_fingerprint(stage)
    canonical["immutableLockedStorage"] = True
    canonical["immutableLockedStorageVersion"] = immutable_storage.VERSION
    canonical["immutableLockedStorageKeyspace"] = "LOCKED#GAME"
    canonical["canonicalPerGameStageAuthority"] = immutable_storage._authority_proof(stage)

    canonical_item = {
        "PK": f"GAME_WINNERS#mlb#{slate}",
        "SK": f"LOCKED#GAME#{canonical.get('commenceTime')}#{canonical.get('gameIdentity')}",
        "record_type": slate_coverage.CANONICAL_RECORD_TYPE,
        "immutable_locked": True,
        "stage_authority_verified": True,
        "stage_authority_version": immutable_storage.AUTHORITY_VERSION,
        "stage_fingerprint": stage["stage_fingerprint"],
        "data": copy.deepcopy(canonical),
    }

    # The live predictor deliberately disagrees.  Public authority must return
    # the stored canonical row and must never call _prediction_for_game again.
    live = copy.deepcopy(canonical)
    live.update({
        "predictedWinner": canonical["awayTeam"] if canonical["predictedSide"] == "home" else canonical["homeTeam"],
        "predictedSide": "away" if canonical["predictedSide"] == "home" else "home",
        "lockedPrediction": False,
        "officialPrediction": False,
        "officialPick": False,
    })
    for key in (
        "frozenFeatureVector",
        "frozenFeatureVectorVersion",
        "featureVectorFrozenAtLock",
        "mlFeatureFreeze",
        "immutableLockedStorage",
        "immutablePerGameStage",
    ):
        live.pop(key, None)

    manifest_records = {}
    for pull in pulls:
        manifest = pull["provider_schedule_manifest"]
        key = inqsi_pull_history._provider_manifest_key(manifest)
        manifest_records[(key["PK"], key["SK"])] = {
            **key,
            "record_type": inqsi_pull_history.PROVIDER_MANIFEST_RECORD_TYPE,
            "manifest_fingerprint": manifest["fingerprint"],
            "data": copy.deepcopy(manifest),
        }

    class Table:
        @staticmethod
        def query(**kwargs):
            return {"Items": [copy.deepcopy(canonical_item)]}

        @staticmethod
        def get_item(*, Key, ConsistentRead=False):
            if (Key.get("PK"), Key.get("SK")) == (stage["PK"], stage["SK"]):
                return {"Item": copy.deepcopy(stage)}
            manifest_item = manifest_records.get((Key.get("PK"), Key.get("SK")))
            if manifest_item:
                return {"Item": copy.deepcopy(manifest_item)}
            return {}

    table = Table()
    inqsi_pull_history.PULLS = table

    module = SimpleNamespace(
        history=SimpleNamespace(
            PULLS=table,
            query_pulls=lambda sport, date, limit: copy.deepcopy(pulls),
            provider_manifest_games_for_lock=inqsi_pull_history.provider_manifest_games_for_lock,
        ),
        _today_et=lambda: slate,
        _prediction_for_game=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("canonical public lock must not recompute at cutoff")
        ),
        _store_prediction=lambda row: {"ok": True},
        predict_all=lambda *args, **kwargs: {
            "ok": True,
            "sport": "mlb",
            "slate_date": slate,
            "slateCoverage": {"coverageComplete": True},
            "predictions": [copy.deepcopy(live)],
        },
    )
    slate_coverage.apply(slate_lock)
    slate_lock.apply(module)
    fundamentals.apply(module)
    semantics.apply(module)
    slate_coverage.install_public_authority(module, slate_lock)
    # Temporal/vector semantics are the subject of this verifier. The full
    # persisted stage-authority chain is independently exercised by the
    # immutable-storage verifier, so isolate that validator for this synthetic
    # canonical row while retaining the real provider-manifest validation.
    original_stage_validate = immutable_storage.validate_canonical_stage_authority
    immutable_storage.validate_canonical_stage_authority = lambda table, row: []
    try:
        return module.predict_all(slate, store=False, limit=500)
    finally:
        immutable_storage.validate_canonical_stage_authority = original_stage_validate


def main() -> int:
    source = datetime(2026, 7, 13, 17, 55, tzinfo=timezone.utc)
    summary = temporal.summarize_side(series(source), "home", cutoff_at=source)
    assert summary["asOfUtc"] == source.isoformat()
    assert summary["excludedAfterCutoffCount"] == 1
    assert summary["sourcePointCount"] == 13
    assert summary["horizons"]["15m"]["pullCount"] == 2
    assert summary["horizons"]["60m"]["pullCount"] == 5
    assert summary["horizons"]["180m"]["pullCount"] == 13
    assert summary["horizons"]["full"]["velocityPpHr"] != 0
    assert summary["horizons"]["full"]["reversalCount"] >= 1

    row = pregame_row()
    vector = cohort.freeze_feature_snapshot(row)
    assert vector["version"] == cohort.FEATURE_SNAPSHOT_VERSION
    assert vector["labels"] == {"homeWon": None, "pickCorrect": None}
    assert vector["temporalFeaturesAtOrBeforeLock"] is True
    assert vector["fundamentalMasksAtOrBeforeLock"] is True
    assert vector["temporalSourcePullAtUtc"] <= vector["sourcePullAtUtc"] <= vector["lockAtUtc"]
    assert vector["fundamentalsSnapshotAsOfUtc"] <= vector["sourcePullAtUtc"] <= vector["lockAtUtc"]
    assert vector["features"]["fundamentalFipXfipMissing"] == 0.0
    assert vector["features"]["fundamentalConfirmedLineupsMissing"] == 1.0
    assert vector["features"]["homeVelocityPpHr60m"] != 0.0
    assert vector["features"]["selectedOpponentVelocityPpHr60mDiff"] != 0.0
    assert vector["fingerprint"] == cohort.fingerprint_for_vector(vector)
    assert set(dual.OUTCOME_FEATURES).issubset(vector["features"])
    assert set(dual.RELIABILITY_FEATURES).issubset(vector["features"])
    assert len(dual.OUTCOME_FEATURES) <= dual.MAX_MODEL_FEATURES
    assert len(dual.RELIABILITY_FEATURES) <= dual.MAX_MODEL_FEATURES

    round_tripped = ddb_round_trip({"frozenFeatureVector": vector})["frozenFeatureVector"]
    assert round_tripped["labels"] == {"homeWon": None, "pickCorrect": None}
    assert cohort.fingerprint_for_vector(round_tripped) == vector["fingerprint"]

    settled = copy.deepcopy(row)
    settled.update({
        "status": "GRADED",
        "winner": "Home Club",
        "correct": True,
        "slateCoverage": {"coverageComplete": True},
        "mlFeatureFreeze": {"completeSlateCoverage": True, "trainingEligible": True},
        "lockedCardAudit": {
            "lockedFlag": True,
            "lockAtUtc": vector["lockAtUtc"],
            "explicitSourceAtUtc": vector["sourcePullAtUtc"],
            "preventsLateRows": True,
            "providerGameId": row["gameId"],
        },
        "frozenFeatureVector": vector,
        "frozenFeatureVectorVersion": vector["version"],
    })
    hardening.apply(cohort)
    clean = cohort.build([settled])
    assert clean["cleanRowCount"] == 1, clean
    original_vector = copy.deepcopy(vector)
    records = dual.records_from_clean_rows(clean["cleanRows"])
    assert len(records) == 1
    assert vector == original_vector
    assert records[0]["labelSource"] == "final_settlement_join_not_pregame_feature_vector"

    future_temporal = pregame_row()
    future_temporal["homeSignal"]["temporalFeatures"]["asOfUtc"] = "2026-07-13T18:01:00+00:00"
    leaked = cohort.freeze_feature_snapshot(future_temporal)
    assert leaked["temporalFeaturesAtOrBeforeLock"] is False
    assert leaked["features"]["homeTemporalAvailable"] == 0.0

    future_fundamentals = pregame_row()
    future_fundamentals["fundamentalsSnapshot"]["asOfUtc"] = "2026-07-13T18:01:00+00:00"
    leaked_fundamentals = cohort.freeze_feature_snapshot(future_fundamentals)
    assert leaked_fundamentals["fundamentalMasksAtOrBeforeLock"] is False

    actual = actual_locked_wrapper_result()
    assert actual["slatePredictionLock"]["locked"] is True, actual
    assert actual["slatePredictionLock"]["slateWideLock"] is False
    assert actual["slatePredictionLock"]["perGameLock"] is True
    assert actual["slatePredictionLock"]["canonicalLockedGameCount"] == 1
    assert actual["slatePredictionLock"]["pendingCanonicalGameCount"] == 0
    assert actual["publicPerGameAuthority"]["version"] == slate_coverage.AUTHORITY_VERSION
    assert actual["publicPerGameAuthority"]["recomputedLockedPredictions"] is False
    actual_row = actual["predictions"][0]
    assert actual_row["perGameCanonicalLock"]["canonical"] is True
    assert actual_row["immutableLockedStorage"] is True
    assert actual_row["officialPredictionStatus"] == "OFFICIAL_LOCKED_PREDICTION"
    actual_vector = actual_row["frozenFeatureVector"]
    assert actual_row["fundamentalsSnapshot"]["asOfUtc"]
    assert actual_vector["fundamentalsSnapshotAsOfUtc"] <= actual_vector["sourcePullAtUtc"] <= actual_vector["lockAtUtc"]
    assert actual_vector["temporalSourcePullAtUtc"] <= actual_vector["sourcePullAtUtc"] <= actual_vector["lockAtUtc"]
    assert actual_vector["temporalFeaturesAtOrBeforeLock"] is True
    assert actual_vector["fundamentalMasksAtOrBeforeLock"] is True
    assert actual_row["mlFeatureFreeze"]["exactVectorCreated"] is True, actual_row["mlFeatureFreeze"]

    exact_odds = dual._selected_reliability_test(
        [{"reliabilityProbability": 0.9, "lockedAmericanOdds": 120, "pickCorrect": 1}], 0.7
    )
    assert exact_odds["priceCoveragePct"] == 100.0
    assert exact_odds["exactOddsCoveragePct"] == 100.0

    print(
        "MLB temporal and missingness features verified: lock-bounded multi-horizon summaries, "
        "explicit source masks, v2 vectors with v3 bound fingerprints, DDB null-label preservation, "
        "parsimonious model dimensions, final-only label joins, and exact-odds coverage only."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
