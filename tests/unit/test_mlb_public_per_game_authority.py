from __future__ import annotations

import copy
import importlib
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_daily_lock_ml_vector_preservation_patch as exact_contract
import inqsi_pull_history
import mlb_daily_per_game_lock_patch as per_game
import mlb_immutable_locked_storage_patch as immutable_storage
import mlb_slate_coverage_patch as coverage
import mlb_slate_prediction_lock as slate_lock


SLATE = "2026-07-17"


def _game(game_id: str, start: str, away: str, home: str):
    return {
        "game_id": game_id,
        "game_key": f"mlb|{game_id}",
        "commence_time": start,
        "away_team": away,
        "home_team": home,
    }


G1 = _game("game-1", "2026-07-17T23:00:00Z", "Away One", "Home One")
G2 = _game("game-2", "2026-07-18T02:00:00Z", "Away Two", "Home Two")
G1["books"] = {"fanduel": {"ml": {"home": -110, "away": 100}}}


def _provider_pull(
    manifest_games=None,
    raw_games=None,
    pull_id="pull-1",
    pulled_at="2026-07-17T20:00:00Z",
):
    manifest_games = copy.deepcopy(manifest_games if manifest_games is not None else [G1, G2])
    raw_games = copy.deepcopy(raw_games if raw_games is not None else manifest_games)
    manifest = inqsi_pull_history._build_provider_schedule_manifest(
        sport="mlb",
        slate=SLATE,
        pulled_at=pulled_at,
        pull_id=pull_id,
        source="the_odds_api",
        games=manifest_games,
    )
    key = inqsi_pull_history._provider_manifest_key(manifest)
    return {
        "sport": "mlb",
        "source": "the_odds_api",
        "slate_date": SLATE,
        "pulled_at": pulled_at,
        "pull_id": pull_id,
        "games": raw_games,
        "provider_schedule_manifest": manifest,
        "provider_manifest_binding": {
            "version": inqsi_pull_history.PROVIDER_MANIFEST_VERSION,
            "fingerprint": manifest["fingerprint"],
            "gameCount": manifest["gameCount"],
            "pk": key["PK"],
            "sk": key["SK"],
            "immutable": True,
            "fullProviderSchedule": True,
        },
    }


PULLS = [_provider_pull()]


def _live(game, winner, side):
    return {
        "slate_date": SLATE,
        "gameId": game["game_id"],
        "gameIdentity": game["game_id"],
        "gameKey": game["game_key"],
        "commenceTime": game["commence_time"],
        "awayTeam": game["away_team"],
        "homeTeam": game["home_team"],
        "predictedWinner": winner,
        "predictedSide": side,
        "score": 1,
        "winProbability": 0.01,
        # Simulate legacy wrappers having incorrectly declared this row final.
        "lockedPrediction": True,
        "officialPrediction": True,
        "officialPick": True,
        "tags": ["FINAL_LOCKED", "SLATE_LOCKED", "SLATE_WIDE_45_MIN_LOCK_POLICY"],
    }


def _canonical_item(game, winner, side, score):
    start = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
    lock_at = (start - timedelta(minutes=45)).astimezone(timezone.utc).isoformat()
    row = {
        "slate_date": SLATE,
        "gameId": game["game_id"],
        "gameIdentity": game["game_id"],
        "gameKey": game["game_key"],
        "commenceTime": game["commence_time"],
        "awayTeam": game["away_team"],
        "homeTeam": game["home_team"],
        "predictedWinner": winner,
        "predictedSide": side,
        "score": score,
        "winProbability": 0.61,
        "lockedPrediction": True,
        "officialPrediction": True,
        "officialPick": True,
        "lockedAtUtc": lock_at,
        "predictionSourcePullAt": "2026-07-17T20:00:00+00:00",
        "frozenFeatureVector": {"lockAtUtc": lock_at, "fingerprint": f"fingerprint-{game['game_id']}"},
        "tags": ["FINAL_LOCKED", "OFFICIAL_LOCKED_PREDICTION"],
        "immutablePerGameStage": True,
        "lastPrelockSelectionFingerprint": f"selection-{game['game_id']}",
        "lastPrelockPromotionVersion": per_game.PROMOTION_POLICY_VERSION,
        "modelOrSignalRecomputedAtLock": False,
        "slatePredictionLock": {"lockAtUtc": lock_at, "locked": True},
    }
    stage = {
        **immutable_storage._stage_key(row),
        "record_type": per_game.STAGE_RECORD_TYPE,
        "slate_date": SLATE,
        "game_identity": coverage.game_identity(row),
        "commence_time": game["commence_time"],
        "scheduled_lock_at_utc": lock_at,
        "source_pull_at_utc": "2026-07-17T20:00:00+00:00",
        "staged_at_utc": lock_at,
        "promotion_policy_version": per_game.PROMOTION_POLICY_VERSION,
        "immutable_staged": True,
        "write_once": True,
        "candidate_proof": {
            "version": per_game.PROMOTION_POLICY_VERSION,
            "predictionSourcePullAtUtc": "2026-07-17T20:00:00+00:00",
            "predictionCreatedAtUtc": "2026-07-17T20:01:00+00:00",
            "predictionPersistedAtUtc": "2026-07-17T20:02:00+00:00",
            "sourceAtOrBeforeCutoff": True,
            "createdAtOrBeforeCutoff": True,
            "persistedAtOrBeforeCutoff": True,
            "candidateSelectionFingerprint": f"selection-{game['game_id']}",
            "modelOrSignalRecomputedAtLock": False,
        },
        "data": {"row": copy.deepcopy(row)},
    }
    stage["stage_fingerprint"] = per_game._stage_fingerprint(stage)
    canonical_row = copy.deepcopy(row)
    canonical_row.update({
        "immutableLockedStorage": True,
        "immutableLockedStorageVersion": immutable_storage.VERSION,
        "immutableLockedStorageKeyspace": "LOCKED#GAME",
        "canonicalPerGameStageAuthority": immutable_storage._authority_proof(stage),
    })
    return {
        "PK": f"GAME_WINNERS#mlb#{SLATE}",
        "SK": f"LOCKED#GAME#{game['commence_time']}#{game['game_id']}",
        "record_type": coverage.CANONICAL_RECORD_TYPE,
        "immutable_locked": True,
        "stage_authority_verified": True,
        "stage_authority_version": immutable_storage.AUTHORITY_VERSION,
        "stage_fingerprint": stage["stage_fingerprint"],
        "data": canonical_row,
        "_stage": stage,
    }

# The remainder of this file is unchanged except for the clock freeze in
# test_persisted_reader_uses_one_read_scope_without_scoping_writer.
