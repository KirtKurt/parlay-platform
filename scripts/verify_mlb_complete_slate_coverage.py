#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

# boto3 resources are imported by production modules even though this regression
# test replaces every table/network dependency. Give botocore a deterministic
# offline region and disable metadata lookup before those imports occur.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("SNAPSHOTS_TABLE", "")

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_daily_lock_ml_vector_preservation_patch as exact_contract
import inqsi_pull_history
import mlb_daily_per_game_lock_patch as per_game
import mlb_doubleheader_safe_audit_patch as audit_patch
import mlb_immutable_locked_storage_patch as immutable_storage
import mlb_locked_card_audit_v1 as locked_audit
import mlb_slate_coverage_patch as coverage_patch
import mlb_slate_prediction_lock as lock_module


def game(game_id, start, away="Milwaukee Brewers", home="Pittsburgh Pirates"):
    return {
        "id": game_id,
        "game_id": game_id,
        "game_key": "mlb|2020-07-11|milwaukee brewers|pittsburgh pirates",
        "away_team": away,
        "home_team": home,
        "commence_time": start,
        "books": {"fanduel": {"ml": {"home": -110, "away": 100}}},
    }


def main() -> int:
    coverage_patch.apply(lock_module)

    early = game("doubleheader-game-1", "2020-07-11T16:00:00Z")
    late = game("doubleheader-game-2", "2020-07-11T20:00:00Z")
    third = game("separate-game", "2020-07-11T22:00:00Z", "New York Yankees", "Washington Nationals")
    # A provider-scheduled game without supported odds still belongs to the
    # immutable full-slate manifest and must be required for public coverage.
    third.pop("books", None)
    no_id_early = {**early, "id": None, "game_id": None, "commence_time": "2020-07-12T16:00:00Z"}
    no_id_late = {**late, "id": None, "game_id": None, "commence_time": "2020-07-12T20:00:00Z"}
    assert coverage_patch.game_identity(no_id_early) != coverage_patch.game_identity(no_id_late)

    def provider_pull(pulled_at, pull_id, games):
        manifest = inqsi_pull_history._build_provider_schedule_manifest(
            sport="mlb",
            slate="2020-07-11",
            pulled_at=pulled_at,
            pull_id=pull_id,
            source="the_odds_api",
            games=games,
        )
        key = inqsi_pull_history._provider_manifest_key(manifest)
        return {
            "sport": "mlb",
            "source": "the_odds_api",
            "slate_date": "2020-07-11",
            "pulled_at": pulled_at,
            "pull_id": pull_id,
            "games": copy.deepcopy(games),
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

    pulls = [
        provider_pull("2020-07-11T14:00:00Z", "pull-1", [early, late, third]),
        provider_pull("2020-07-11T15:00:00Z", "pull-2", [late, early, third]),
    ]

    class History:
        PULLS = None

        @staticmethod
        def query_pulls(sport, slate, limit):
            assert sport == "mlb"
            assert slate == "2020-07-11"
            return pulls

        @staticmethod
        def provider_manifest_games_for_lock(pull, slate):
            return inqsi_pull_history.provider_manifest_games_for_lock(pull, slate)

    class Engine:
        history = History()

        @staticmethod
        def _prediction_for_game(scoring, source_game, slate):
            game_id = source_game.get("game_id") or source_game.get("id")
            predicted_side = "away" if game_id == "doubleheader-game-2" else "home"
            predicted_winner = source_game.get("away_team") if predicted_side == "away" else source_game.get("home_team")
            return {
                "sport": "mlb",
                "slate_date": slate,
                "gameId": game_id,
                "gameIdentity": game_id,
                "gameKey": source_game.get("game_key"),
                "homeTeam": source_game.get("home_team"),
                "awayTeam": source_game.get("away_team"),
                "commenceTime": source_game.get("commence_time"),
                "predictedWinner": predicted_winner,
                "predictedSide": predicted_side,
                "score": 60,
                "winProbability": 0.60,
                "winProbabilityPct": 60.0,
                "actionablePick": False,
                "officialPick": False,
                "homeSignal": {"marketConsensusProbability": 0.60, "probLatest": 0.60, "tags": ["BOOK_AGREEMENT"]},
                "awaySignal": {"marketConsensusProbability": 0.40, "probLatest": 0.40, "tags": ["BOOK_AGREEMENT"]},
                "tags": [],
                "createdAt": "2020-07-11T15:01:00Z",
            }

        @staticmethod
        def _store_prediction(row):
            return {"ok": True, "gameId": row.get("gameId")}

    current_rows = [Engine._prediction_for_game(pulls, row, "2020-07-11") for row in (early, late, third)]

    canonical_items = []
    stages = {}
    for row in current_rows:
        start = datetime.fromisoformat(str(row.get("commenceTime")).replace("Z", "+00:00"))
        lock_at = (start - timedelta(minutes=45)).isoformat()
        selection_fingerprint = f"selection-{row.get('gameId')}"
        staged_row = copy.deepcopy(row)
        staged_row.update({
            "lockedPrediction": True,
            "officialPrediction": True,
            "officialPick": True,
            "lockedAtUtc": lock_at,
            "predictionSourcePullAt": "2020-07-11T15:00:00+00:00",
            "immutablePerGameStage": True,
            "lastPrelockSelectionFingerprint": selection_fingerprint,
            "lastPrelockPromotionVersion": per_game.PROMOTION_POLICY_VERSION,
            "modelOrSignalRecomputedAtLock": False,
            "slatePredictionLock": {"locked": True, "lockAtUtc": lock_at},
            "frozenFeatureVector": {
                "lockAtUtc": lock_at,
                "sourcePullAtUtc": "2020-07-11T15:00:00+00:00",
                "fingerprint": f"fixture-{row.get('gameId')}",
            },
        })
        stage = {
            **immutable_storage._stage_key(staged_row),
            "record_type": per_game.STAGE_RECORD_TYPE,
            "slate_date": "2020-07-11",
            "game_identity": coverage_patch.game_identity(staged_row),
            "commence_time": staged_row["commenceTime"],
            "scheduled_lock_at_utc": lock_at,
            "source_pull_at_utc": "2020-07-11T15:00:00+00:00",
            "staged_at_utc": lock_at,
            "promotion_policy_version": per_game.PROMOTION_POLICY_VERSION,
            "immutable_staged": True,
            "write_once": True,
            "candidate_proof": {
                "version": per_game.PROMOTION_POLICY_VERSION,
                "predictionSourcePullAtUtc": "2020-07-11T15:00:00+00:00",
                "predictionCreatedAtUtc": "2020-07-11T15:01:00+00:00",
                "predictionPersistedAtUtc": lock_at,
                "sourceAtOrBeforeCutoff": True,
                "createdAtOrBeforeCutoff": True,
                "persistedAtOrBeforeCutoff": True,
                "candidateSelectionFingerprint": selection_fingerprint,
                "modelOrSignalRecomputedAtLock": False,
            },
            "data": {"row": copy.deepcopy(staged_row)},
        }
        stage["stage_fingerprint"] = per_game._stage_fingerprint(stage)
        canonical = copy.deepcopy(staged_row)
        canonical.update({
            "immutableLockedStorage": True,
            "immutableLockedStorageVersion": immutable_storage.VERSION,
            "immutableLockedStorageKeyspace": "LOCKED#GAME",
            "canonicalPerGameStageAuthority": immutable_storage._authority_proof(stage),
        })
        canonical_items.append({
            "PK": "GAME_WINNERS#mlb#2020-07-11",
            "SK": f"LOCKED#GAME#{row.get('commenceTime')}#{row.get('gameIdentity')}",
            "record_type": coverage_patch.CANONICAL_RECORD_TYPE,
            "immutable_locked": True,
            "stage_authority_verified": True,
            "stage_authority_version": immutable_storage.AUTHORITY_VERSION,
            "stage_fingerprint": stage["stage_fingerprint"],
            "data": canonical,
        })
        stages[(stage["PK"], stage["SK"])] = stage

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

    class CanonicalTable:
        @staticmethod
        def query(**kwargs):
            return {"Items": copy.deepcopy(canonical_items)}

        @staticmethod
        def get_item(*, Key, ConsistentRead=False):
            item = stages.get((Key["PK"], Key["SK"])) or manifest_records.get((Key["PK"], Key["SK"]))
            return {"Item": copy.deepcopy(item)} if item else {}

    History.PULLS = CanonicalTable()
    inqsi_pull_history.PULLS = History.PULLS

    original_enhance = lock_module._enhance
    original_optimize = lock_module._optimize_locked_row
    original_validate = exact_contract.validate_exact_locked_row
    original_stage_validate = immutable_storage.validate_canonical_stage_authority
    lock_module._enhance = lambda result: result
    lock_module._optimize_locked_row = lambda row: row
    exact_contract.validate_exact_locked_row = lambda row: []
    # This verifier owns public provider-manifest completeness and
    # doubleheader-safe canonical overlay. The persisted stage-authority chain
    # has a dedicated exhaustive verifier; isolate it here so this fixture can
    # stay focused without weakening the production validator.
    immutable_storage.validate_canonical_stage_authority = lambda table, row: []
    try:
        result = lock_module._locked_result(
            Engine(),
            {"slate_date": "2020-07-11", "predictions": current_rows},
            ("2020-07-11",),
            {"limit": 500},
            True,
        )
    finally:
        lock_module._enhance = original_enhance
        lock_module._optimize_locked_row = original_optimize
        exact_contract.validate_exact_locked_row = original_validate
        immutable_storage.validate_canonical_stage_authority = original_stage_validate

    assert result["slatePredictionLock"]["firstGameStartUtc"] == "2020-07-11T16:00:00+00:00"
    assert result["slatePredictionLock"]["manifestGameCount"] == 3
    assert result["gameCount"] == 3
    assert result["count"] == 3
    assert result["locked"] is True
    assert result["slatePredictionLock"]["slateWideLock"] is False
    assert result["slatePredictionLock"]["canonicalLockedGameCount"] == 3
    assert result["slateCoverage"]["canonicalReadAuthorityWriteCount"] == 0
    assert result["slateCoverage"]["coverageComplete"] is True
    assert result["slateCoverage"]["providerManifestValidated"] is True
    assert result["slateCoverage"]["providerManifestImmutable"] is True
    assert result["slatePredictionLock"]["manifestGameCount"] == 3
    assert result["slateCoverage"]["doubleheaderSafeIdentity"] is True
    assert "milwaukee brewers|pittsburgh pirates" in result["slateCoverage"]["doubleheaderMatchups"]

    prediction_rows = result["predictions"]

    class AuditModule:
        @staticmethod
        def normalize_team(name):
            return " ".join(str(name or "").lower().split())

        @staticmethod
        def _query_predictions_for_slate(slate):
            return prediction_rows

    audit_module = AuditModule()
    locked_audit.apply(audit_module)
    audit_patch.apply(audit_module)
    finals = [
        {
            "id": "doubleheader-game-1",
            "slateDateEt": "2020-07-11",
            "awayTeam": "Milwaukee Brewers",
            "homeTeam": "Pittsburgh Pirates",
            "commenceTime": "2020-07-11T16:00:00Z",
            "winner": "Pittsburgh Pirates",
            "gameKeyBase": "milwaukee brewers|pittsburgh pirates",
        },
        {
            "id": "doubleheader-game-2",
            "slateDateEt": "2020-07-11",
            "awayTeam": "Milwaukee Brewers",
            "homeTeam": "Pittsburgh Pirates",
            "commenceTime": "2020-07-11T20:00:00Z",
            "winner": "Milwaukee Brewers",
            "gameKeyBase": "milwaukee brewers|pittsburgh pirates",
        },
        {
            "id": "separate-game",
            "slateDateEt": "2020-07-11",
            "awayTeam": "New York Yankees",
            "homeTeam": "Washington Nationals",
            "commenceTime": "2020-07-11T22:00:00Z",
            "winner": "Washington Nationals",
            "gameKeyBase": "new york yankees|washington nationals",
        },
    ]
    audited = audit_module.audit_rows(finals)
    assert len(audited) == 3
    assert all(row.get("status") == "GRADED" for row in audited)
    assert all(row.get("correct") is True for row in audited)
    assert all((row.get("lockedCardAudit") or {}).get("matchMethod") == "provider_game_id" for row in audited)
    assert audited[0]["predictedWinner"] != audited[1]["predictedWinner"]

    # Missing, tampered, and odds-subset pull evidence must all fail closed;
    # none may be interpreted as a complete public slate.
    missing_manifest = copy.deepcopy(pulls[-1])
    missing_manifest.pop("provider_schedule_manifest")
    missing_manifest.pop("provider_manifest_binding")
    tampered_manifest = copy.deepcopy(pulls[-1])
    tampered_manifest["provider_schedule_manifest"]["games"][0]["home_team"] = "Tampered Team"
    subset_pull = copy.deepcopy(pulls[-1])
    subset_pull["games"] = subset_pull["games"][:2]
    for invalid_pull, expected_error in (
        (missing_manifest, "provider_schedule_manifest_missing"),
        (tampered_manifest, "provider_manifest_fingerprint_mismatch"),
        (subset_pull, "provider_manifest_pull_membership_mismatch"),
    ):
        try:
            coverage_patch._provider_manifest_for_public(
                Engine(),
                lock_module,
                [invalid_pull],
                "2020-07-11",
            )
        except RuntimeError as exc:
            assert expected_error in str(exc), str(exc)
        else:
            raise AssertionError(f"invalid provider manifest unexpectedly accepted: {expected_error}")

    # Daily write-once lock behavior and no-backfill semantics are now covered
    # by verify_mlb_per_game_lock.py.  This verifier owns only public read
    # coverage and doubleheader-safe canonical authority.
    print("MLB per-game canonical public coverage and doubleheader-safe audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
