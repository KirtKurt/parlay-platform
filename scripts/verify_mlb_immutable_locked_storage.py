#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_immutable_locked_storage_patch as patch
import mlb_daily_per_game_lock_patch as per_game
import inqsi_pull_history as history_contract
import mlb_ml_clean_cohort_v1 as cohort
import mlb_slate_coverage_patch as coverage
from mlb_ml_feature_test_fixtures import attach_lock_safe_features


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, *, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and key in self.items:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        self.items[key] = copy.deepcopy(Item)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_item(self, *, Key, ConsistentRead=False):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item else {}


class FakeHistory:
    def __init__(self):
        self.PULLS = FakeTable()

    @staticmethod
    def ddb_safe(value):
        return value


def locked_row(base, *, game_id="provider-123", winner="Home Team"):
    commence = datetime.fromisoformat(str(base["commenceTime"]).replace("Z", "+00:00"))
    lock_at = commence.astimezone(timezone.utc) - timedelta(minutes=45)
    source_at = lock_at - timedelta(minutes=5)
    row = {
        **base,
        "gameId": game_id,
        "gameIdentity": game_id,
        "homeTeam": "Home Team",
        "awayTeam": "Away Team",
        "predictedWinner": winner,
        "predictedSide": "home" if winner == "Home Team" else "away",
        "americanOdds": -120,
        "lockedAmericanOdds": -120,
        "priceBook": "fanduel",
        "priceSource": "real_book",
        "teamWinProbabilityPct": 55.0,
        "probabilitySemanticsFixed": True,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1-test",
        "officialPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "lockedPrediction": True,
        "immutablePerGameStage": True,
        "lastPrelockSelectionFingerprint": f"selection-{game_id}",
        "lastPrelockPromotionVersion": per_game.PROMOTION_POLICY_VERSION,
        "modelOrSignalRecomputedAtLock": False,
        "createdAt": (source_at + timedelta(seconds=1)).isoformat(),
        "predictionSourcePullAt": source_at.isoformat(),
        "predictionSourcePullId": f"pull-{game_id}",
        "lockedAtUtc": lock_at.isoformat(),
        "featureVectorFrozenAtLock": True,
        "tags": ["SLATE_LOCKED", "OFFICIAL_LOCKED_PREDICTION"],
        "slatePredictionLock": {
            "locked": True,
            "lockAtUtc": lock_at.isoformat(),
            "latestScoringPullAt": source_at.isoformat(),
        },
        "slateCoverage": {"coverageComplete": True},
        "mlFeatureFreeze": {
            "exactVectorCreated": True,
            "completeSlateCoverage": True,
            "trainingEligible": True,
        },
    }
    attach_lock_safe_features(row)
    vector = cohort.freeze_feature_snapshot(row)
    row["frozenFeatureVector"] = vector
    row["frozenFeatureVectorVersion"] = vector["version"]
    row["lastPrelockSelectionFingerprint"] = per_game._payload_fingerprint(
        per_game._selection_material(row)
    )
    return row


def seed_stage(history, row):
    lock_at = (row.get("slatePredictionLock") or {}).get("lockAtUtc")
    source_at = (row.get("slatePredictionLock") or {}).get("latestScoringPullAt")
    selection = row.get("lastPrelockSelectionFingerprint")
    cutoff = datetime.fromisoformat(str(lock_at).replace("Z", "+00:00"))
    source_dt = datetime.fromisoformat(str(source_at).replace("Z", "+00:00"))
    staged_at = cutoff + timedelta(seconds=per_game.CUTOFF_STABILIZATION_SECONDS)
    created_at = source_dt + timedelta(seconds=1)
    persisted_at = source_dt + timedelta(seconds=2)
    raw_identity = str(row.get("gameIdentity") or row.get("gameId"))
    canonical_identity = coverage.game_identity(row)
    pull_id = f"pull-{raw_identity}"
    game = {
        "game_id": raw_identity,
        "game_key": row.get("gameKey") or f"mlb|{raw_identity}",
        "commence_time": row["commenceTime"],
        "home_team": row["homeTeam"],
        "away_team": row["awayTeam"],
        "books": {"fanduel": {"ml": {"home": -120, "away": 110}}},
    }
    pull = {
        "sport": "mlb",
        "source": "the_odds_api",
        "slate_date": row["slate_date"],
        "pulled_at": source_dt.isoformat(),
        "pull_id": pull_id,
        "games": [copy.deepcopy(game)],
    }
    history.PULLS.put_item(Item={
        "PK": f"PULLS#mlb#{row['slate_date']}",
        "SK": f"PULL#{source_dt.isoformat()}#{pull_id}",
        "record_type": "pull_run",
        "data": copy.deepcopy(pull),
    })

    manifest = history_contract._build_provider_schedule_manifest(
        sport="mlb",
        slate=row["slate_date"],
        pulled_at=source_dt.isoformat(),
        pull_id=pull_id,
        source="the_odds_api",
        games=[game],
    )
    manifest_key = history_contract._provider_manifest_key(manifest)
    history.PULLS.put_item(Item={
        **manifest_key,
        "record_type": history_contract.PROVIDER_MANIFEST_RECORD_TYPE,
        "manifest_fingerprint": manifest["fingerprint"],
        "write_once": True,
        "data": copy.deepcopy(manifest),
    })

    candidate = copy.deepcopy(row)
    candidate["createdAt"] = created_at.isoformat()
    candidate["predictionSourcePullAt"] = source_dt.isoformat()
    candidate["predictionSourcePullId"] = pull_id
    candidate["lastPrelockSelectionFingerprint"] = selection
    live_pk = f"GAME_WINNERS#mlb#{row['slate_date']}"
    live_sk = f"GAME#{candidate['commenceTime']}#{raw_identity}"
    snapshot = {
        "PK": live_pk,
        "SK": f"PREGAME#GAME#{raw_identity}#PERSISTED#{persisted_at.isoformat()}#CREATED#{created_at.isoformat()}#fixture",
        "record_type": per_game.PREGAME_SNAPSHOT_RECORD_TYPE,
        "snapshot_version": per_game.PREGAME_SNAPSHOT_VERSION,
        "slate_date": row["slate_date"],
        "game_id": row.get("gameId"),
        "game_identity": raw_identity,
        "commence_time": candidate["commenceTime"],
        "prediction_created_at_utc": created_at.isoformat(),
        "prediction_persisted_at_utc": persisted_at.isoformat(),
        "prediction_persistence_proof_type": per_game.PREGAME_PERSISTENCE_PROOF_TYPE,
        "prediction_persistence_write_pk": live_pk,
        "prediction_persistence_write_sk": live_sk,
        "prediction_payload_fingerprint": per_game._payload_fingerprint(candidate),
        "prediction_source_pull_at_utc": source_dt.isoformat(),
        "prediction_source_pull_id": pull_id,
        "immutable_pregame": True,
        "write_once": True,
        "data": candidate,
    }
    history.PULLS.put_item(Item=snapshot)
    candidate_proof = {
        "version": per_game.PROMOTION_POLICY_VERSION,
        "pk": snapshot["PK"],
        "sk": snapshot["SK"],
        "recordType": per_game.PREGAME_SNAPSHOT_RECORD_TYPE,
        "snapshotVersion": per_game.PREGAME_SNAPSHOT_VERSION,
        "predictionSourcePullAtUtc": source_dt.isoformat(),
        "predictionSourcePullId": pull_id,
        "predictionCreatedAtUtc": created_at.isoformat(),
        "predictionPersistedAtUtc": persisted_at.isoformat(),
        "persistenceProofType": per_game.PREGAME_PERSISTENCE_PROOF_TYPE,
        "persistenceWritePk": live_pk,
        "persistenceWriteSk": live_sk,
        "predictionPayloadFingerprint": per_game._payload_fingerprint(candidate),
        "candidateSnapshotFingerprint": per_game._payload_fingerprint(snapshot),
        "sourceAtOrBeforeCutoff": True,
        "createdAtOrBeforeCutoff": True,
        "persistedAtOrBeforeCutoff": True,
        "candidateRowFingerprint": per_game._payload_fingerprint(candidate),
        "candidateSelectionFingerprint": selection,
        "candidateVectorFingerprint": (candidate.get("frozenFeatureVector") or {}).get("fingerprint"),
        "promotionRule": "last_valid_persisted_prediction_at_or_before_own_tminus45_becomes_final_lock",
        "modelOrSignalRecomputedAtLock": False,
    }
    manifest_games = list(manifest.get("games") or [])
    stage = {
        **patch._stage_key(row),
        "record_type": per_game.STAGE_RECORD_TYPE,
        "slate_date": row["slate_date"],
        "model_version": per_game.VERSION,
        "lock_policy": per_game.LOCK_POLICY,
        "game_identity": canonical_identity,
        "commence_time": row["commenceTime"],
        "scheduled_lock_at_utc": lock_at,
        "source_pull_at_utc": source_at,
        "source_pull_id": pull_id,
        "pull_depth": 1,
        "staged_at_utc": staged_at.isoformat(),
        "promotion_policy_version": per_game.PROMOTION_POLICY_VERSION,
        "immutable_staged": True,
        "write_once": True,
        "source_window": {
            "version": per_game.SOURCE_WINDOW_VERSION,
            "scheduledCutoffAtUtc": lock_at,
            "closedAtUtc": staged_at.isoformat(),
            "stabilizationSeconds": per_game.CUTOFF_STABILIZATION_SECONDS,
            "pulls": [{
                "pullId": pull_id,
                "pulledAtUtc": source_dt.isoformat(),
                "gameSnapshotFingerprint": per_game._game_snapshot_fingerprint(game),
            }],
        },
        "candidate_proof": candidate_proof,
        "provider_manifest_authority": {
            "version": history_contract.PROVIDER_MANIFEST_VERSION,
            "recordType": history_contract.PROVIDER_MANIFEST_RECORD_TYPE,
            "pk": manifest_key["PK"],
            "sk": manifest_key["SK"],
            "fingerprint": manifest["fingerprint"],
            "slateDate": row["slate_date"],
            "observedAtUtc": manifest.get("observedAtUtc"),
            "pullId": pull_id,
            "gameCount": len(manifest_games),
            "gameIdentities": [history_contract.provider_game_identity("mlb", game) for game in manifest_games],
            "canonicalGameIdentities": [coverage.game_identity(game) for game in manifest_games],
            "immutable": True,
            "writeOnce": True,
            "fullProviderSchedule": True,
            "consistentReadVerified": True,
        },
        "manifest_game_count": len(manifest_games),
        "data": {
            "row": copy.deepcopy(row),
            "manifestGameIdentities": [coverage.game_identity(game) for game in manifest_games],
        },
        "created_at": staged_at.isoformat(),
    }
    stage["stage_fingerprint"] = per_game._stage_fingerprint(stage)
    history.PULLS.put_item(
        Item=stage,
        ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
    )
    return stage


def main() -> int:
    history = FakeHistory()
    module = SimpleNamespace(history=history)

    def original_store(row):
        item = {
            "PK": f"GAME_WINNERS#mlb#{row['slate_date']}",
            "SK": f"GAME#{row['commenceTime']}#{row['gameIdentity']}",
            "data": copy.deepcopy(row),
        }
        history.PULLS.put_item(Item=item)
        return {"ok": True, "pk": item["PK"], "sk": item["SK"]}

    module._store_prediction = original_store
    patch.apply(module)

    base = {
        "slate_date": "2026-07-12",
        "gameId": "provider-123",
        "gameIdentity": "provider-123",
        "commenceTime": "2026-07-12T20:00:00Z",
        "predictedWinner": "Home Team",
        "createdAt": "2026-07-12T18:00:00Z",
    }

    live = {
        **base,
        # Display overlays historically used this on visible pre-lock picks;
        # without an actual lock flag it must remain mutable.
        "officialPrediction": True,
        "officialPredictionStatus": "PRE_LOCK_PLATFORM_PREDICTION",
    }
    live_result = module._store_prediction(live)
    assert live_result["storageClass"] == "LIVE_MUTABLE"

    # Legacy/generic locked output has no canonical authority and is suppressed
    # before either the mutable or LOCKED#GAME store can run.
    unauthorized = {
        **base,
        "lockedPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "slatePredictionLock": {"locked": True},
    }
    before_unauthorized = copy.deepcopy(history.PULLS.items)
    unauthorized_result = module._store_prediction(unauthorized)
    assert unauthorized_result == {
        "ok": False,
        "stored": False,
        "suppressed": True,
        "error": patch.UNAUTHORIZED_LOCKED_WRITE,
        "storageClass": "LOCKED_REJECTED",
        "canonicalWriteAuthorized": False,
        "requiredAuthority": "verified immutable T-minus-45 stage record",
        "version": patch.VERSION,
    }
    assert history.PULLS.items == before_unauthorized

    # A self-consistent stage is not authority unless its immutable provider
    # manifest, bound pulls, and exact pre-lock candidate snapshot all exist.
    self_certified = locked_row(
        {**base, "commenceTime": "2026-07-12T20:30:00Z"},
        game_id="provider-self-certified",
    )
    self_stage = {
        **patch._stage_key(self_certified),
        "record_type": per_game.STAGE_RECORD_TYPE,
        "slate_date": self_certified["slate_date"],
        "model_version": per_game.VERSION,
        "lock_policy": per_game.LOCK_POLICY,
        "promotion_policy_version": per_game.PROMOTION_POLICY_VERSION,
        "immutable_staged": True,
        "write_once": True,
        "game_identity": coverage.game_identity(self_certified),
        "commence_time": self_certified["commenceTime"],
        "scheduled_lock_at_utc": self_certified["lockedAtUtc"],
        "staged_at_utc": (
            datetime.fromisoformat(self_certified["lockedAtUtc"])
            + timedelta(seconds=per_game.CUTOFF_STABILIZATION_SECONDS)
        ).isoformat(),
        "candidate_proof": {
            "version": per_game.PROMOTION_POLICY_VERSION,
            "candidateSelectionFingerprint": self_certified["lastPrelockSelectionFingerprint"],
            "modelOrSignalRecomputedAtLock": False,
            "sourceAtOrBeforeCutoff": True,
            "createdAtOrBeforeCutoff": True,
            "persistedAtOrBeforeCutoff": True,
        },
        "data": {
            "row": copy.deepcopy(self_certified),
            "manifestGameIdentities": [coverage.game_identity(self_certified)],
        },
    }
    self_stage["created_at"] = self_stage["staged_at_utc"]
    self_stage["stage_fingerprint"] = per_game._stage_fingerprint(self_stage)
    history.PULLS.put_item(Item=self_stage)
    self_result = module._store_prediction(self_certified)
    assert self_result["ok"] is False
    assert "provider_manifest_authority_missing" in self_result["authorityErrors"]
    assert "candidate_snapshot_key_missing" in self_result["authorityErrors"]

    def assert_chain_tamper_rejected(row, mutate, expected_fragment):
        stage = seed_stage(history, row)
        mutate(stage)
        result = module._store_prediction(row)
        assert result["ok"] is False, result
        assert any(expected_fragment in error for error in result["authorityErrors"]), result

    snapshot_tamper = locked_row(
        {**base, "commenceTime": "2026-07-12T21:30:00Z"},
        game_id="provider-snapshot-chain-tamper",
    )
    assert_chain_tamper_rejected(
        snapshot_tamper,
        lambda stage: history.PULLS.items[
            (stage["candidate_proof"]["pk"], stage["candidate_proof"]["sk"])
        ]["data"].update({"predictedWinner": "Changed After Stage"}),
        "candidate_snapshot",
    )

    pull_tamper = locked_row(
        {**base, "commenceTime": "2026-07-12T22:30:00Z"},
        game_id="provider-pull-chain-tamper",
    )
    assert_chain_tamper_rejected(
        pull_tamper,
        lambda stage: history.PULLS.items[
            (
                f"PULLS#mlb#{stage['slate_date']}",
                f"PULL#{stage['source_pull_at_utc']}#{stage['source_pull_id']}",
            )
        ]["data"]["games"][0]["books"]["fanduel"]["ml"].update({"home": -999}),
        "bound_source_window_game_fingerprint_mismatch",
    )

    manifest_tamper = locked_row(
        {**base, "commenceTime": "2026-07-12T23:30:00Z"},
        game_id="provider-manifest-chain-tamper",
    )
    assert_chain_tamper_rejected(
        manifest_tamper,
        lambda stage: history.PULLS.items[
            (
                stage["provider_manifest_authority"]["pk"],
                stage["provider_manifest_authority"]["sk"],
            )
        ]["data"]["games"][0].update({"home_team": "Changed After Stage"}),
        "immutable_provider_manifest_readback_mismatch",
    )

    # An authorized per-game stage still fails closed until the exact
    # fingerprinted ML vector exists.
    missing_vector = locked_row(base)
    missing_vector.pop("frozenFeatureVector", None)
    missing_vector.pop("frozenFeatureVectorVersion", None)
    seed_stage(history, missing_vector)
    before_missing = copy.deepcopy(history.PULLS.items)
    try:
        module._store_prediction(missing_vector)
    except RuntimeError as exc:
        assert "missing_frozen_feature_vector" in str(exc)
    else:
        raise AssertionError("locked storage accepted a vectorless row")
    assert history.PULLS.items == before_missing

    locked = locked_row(base)
    locked_stage_row = copy.deepcopy(locked)
    # Replace the vectorless fixture stage with the exact valid staged row.
    history.PULLS.items.pop((patch._stage_key(locked)["PK"], patch._stage_key(locked)["SK"]))
    seed_stage(history, locked)
    locked_result = module._store_prediction(locked)
    assert locked_result["storageClass"] == "LOCKED_IMMUTABLE"
    assert locked_result["created"] is True

    live_later = {**base, "predictedWinner": "Away Team", "createdAt": "2026-07-12T22:00:00Z"}
    module._store_prediction(live_later)

    locked_key = (
        "GAME_WINNERS#mlb#2026-07-12",
        "LOCKED#GAME#2026-07-12T20:00:00Z#provider-123",
    )
    live_key = (
        "GAME_WINNERS#mlb#2026-07-12",
        "GAME#2026-07-12T20:00:00Z#provider-123",
    )
    assert history.PULLS.items[locked_key]["data"]["predictedWinner"] == "Home Team"
    assert history.PULLS.items[live_key]["data"]["predictedWinner"] == "Away Team"

    repeated = module._store_prediction(copy.deepcopy(locked_stage_row))
    assert repeated["immutableExisting"] is True
    assert repeated["exactVectorVerified"] is True

    changed_lock = locked_row(base, winner="Away Team")
    changed_result = module._store_prediction(changed_lock)
    assert changed_result["ok"] is False
    assert changed_result["error"] == patch.UNAUTHORIZED_LOCKED_WRITE
    assert "canonical_payload_not_exact_stage_row" in changed_result["authorityErrors"]
    assert history.PULLS.items[locked_key]["data"]["predictedWinner"] == "Home Team"

    tampered = locked_row({**base, "commenceTime": "2026-07-12T21:00:00Z"}, game_id="provider-tampered")
    tampered["frozenFeatureVector"]["features"]["homeMarketProb"] = 0.99
    seed_stage(history, tampered)
    before_tampered = copy.deepcopy(history.PULLS.items)
    try:
        module._store_prediction(tampered)
    except RuntimeError as exc:
        assert "frozen_vector_fingerprint_mismatch" in str(exc)
    else:
        raise AssertionError("locked storage accepted a tampered fingerprint")
    assert history.PULLS.items == before_tampered

    # A legacy/vectorless row already occupying the write-once key is never
    # treated as success or repaired after outcomes are known.
    legacy = locked_row({**base, "commenceTime": "2026-07-12T22:00:00Z"}, game_id="provider-legacy")
    seed_stage(history, legacy)
    legacy_key = (
        "GAME_WINNERS#mlb#2026-07-12",
        "LOCKED#GAME#2026-07-12T22:00:00Z#provider-legacy",
    )
    history.PULLS.items[legacy_key] = {
        "PK": legacy_key[0],
        "SK": legacy_key[1],
        "data": {"gameId": "provider-legacy", "lockedPrediction": True},
    }
    try:
        module._store_prediction(legacy)
    except RuntimeError as exc:
        assert "existing_collision" in str(exc)
    else:
        raise AssertionError("vectorless existing collision was accepted")

    canonical_items = [
        item for item in history.PULLS.items.values()
        if str(item.get("SK") or "").startswith("LOCKED#GAME#")
    ]
    assert len(canonical_items) == 2
    print(
        "MLB immutable locked storage verified: live rows remain mutable; legacy locked writes are suppressed; "
        "only immutable per-game stages may enter LOCKED#GAME; authorized stages require an exact vector; and "
        "tampered, changed, or vectorless collisions fail closed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
