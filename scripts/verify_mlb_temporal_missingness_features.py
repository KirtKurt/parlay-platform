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
import mlb_game_winner_engine as winner_engine
import mlb_ml_clean_cohort_hardening_v1 as hardening
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_dual_model_v1 as dual
import mlb_ml_exact_lock_vector_patch as exact_patch
import mlb_ml_frozen_features as frozen_features
import mlb_official_freeze_bridge as freeze_bridge
import mlb_official_prediction_semantics as semantics
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
        pulls.append({"pulled_at": pulled_at.isoformat(), "pull_id": f"pull-{index}", "games": [game]})

    def prediction_for_game(scoring_pulls, latest_game, slate_date):
        row = winner_engine._prediction_for_game(scoring_pulls, latest_game, slate_date)
        row["advanced_context"] = advanced_context()
        return row

    module = SimpleNamespace(
        history=SimpleNamespace(query_pulls=lambda sport, date, limit: copy.deepcopy(pulls)),
        _today_et=lambda: slate,
        _prediction_for_game=prediction_for_game,
        _store_prediction=lambda row: {"ok": True},
        predict_all=lambda *args, **kwargs: {
            "ok": True,
            "sport": "mlb",
            "slate_date": slate,
            "slateCoverage": {"coverageComplete": True},
            "predictions": [],
        },
    )
    slate_lock.apply(module)
    fundamentals.apply(module)
    exact_patch.apply(frozen_features)
    freeze_bridge.apply(semantics)
    semantics.apply(module)
    return module.predict_all(slate, store=False, limit=500)


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
    actual_row = actual["predictions"][0]
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
