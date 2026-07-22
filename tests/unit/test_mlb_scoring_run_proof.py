from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))

import mlb_scoring_run_proof as proof


class ConditionalFailure(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, *, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if key in self.items and ConditionExpression:
            raise ConditionalFailure()
        self.items[key] = deepcopy(Item)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_item(self, *, Key, ConsistentRead=False):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": deepcopy(item)} if item else {}

    def query(self, **kwargs):
        # The test table contains one date partition, so a descending key sort is enough.
        items = sorted(self.items.values(), key=lambda row: row["SK"], reverse=True)
        return {"Items": deepcopy(items[: kwargs.get("Limit", 1)])}


def _prediction(game_id: str, home: str, away: str, winner: str, side: str):
    return {
        "gameId": game_id,
        "gameIdentity": game_id,
        "gameKey": f"mlb|2026-07-22|{away.lower()}|{home.lower()}",
        "homeTeam": home,
        "awayTeam": away,
        "commenceTime": "2026-07-22T23:00:00+00:00",
        "predictedWinner": winner,
        "predictedSide": side,
        "score": 71.25,
        "actionablePick": True,
        "officialPrediction": False,
        "displayPrediction": True,
        "winnerStackV2": {
            "components": {
                "market": {"score": 68.0},
                "movement": {"score": 76.0},
                "fundamentals": {"score": 55.0, "applied": True, "mode": "TIMESTAMPED_FUNDAMENTALS_V2"},
            },
            "weights": {"market": 0.56, "movement": 0.25, "fundamentals": 0.19},
            "calibration": {"calibratedProbability": 0.64},
        },
        "mlOverlay": {"applied": True, "authority": False, "score": 52.0, "version": "shadow-v2"},
        "tags": ["BOOK_AGREEMENT"],
    }


def _payload():
    predictions = [
        _prediction("g1", "Yankees", "Pirates", "Yankees", "home"),
        _prediction("g2", "Red Sox", "Orioles", "Red Sox", "home"),
    ]
    return {
        "ok": True,
        "live_pull_ok": True,
        "fallback_used": False,
        "count": 2,
        "asof": "2026-07-22T19:45:09+00:00",
        "run": "hot_pull_audited",
        "providerScheduleManifestComplete": True,
        "provider_schedule_manifests": [
            {
                "game_date_et": "2026-07-22",
                "gameCount": 2,
                "fingerprint": "manifest-1",
                "ok": True,
            }
        ],
        "canonical_pull_history": [
            {
                "game_date_et": "2026-07-22",
                "ok": True,
                "canonicalPullId": "pull-1",
                "canonicalSlotStartUtc": "2026-07-22T19:45:00+00:00",
            }
        ],
        "hot_movement_features": [
            {"game_date_et": "2026-07-22", "ok": True, "stored": 2}
        ],
        "hot_side_predictions": [
            {
                "game_date_et": "2026-07-22",
                "ok": True,
                "count": 2,
                "individual_prediction_count": 2,
            }
        ],
        "game_winner_predictions": [
            {
                "game_date_et": "2026-07-22",
                "ok": True,
                "count": 2,
                "gameCount": 2,
                "allGamesPredicted": True,
                "preLockStorageComplete": True,
                "preLockStorageCandidateCount": 2,
                "preLockStoredCount": 2,
                "predictions": predictions,
            }
        ],
    }


def test_complete_response_creates_pass_proof_with_components():
    table = FakeTable()
    response = {"statusCode": 200, "body": json.dumps(_payload())}

    updated = proof.attach_and_store(response, event={}, table=table)
    body = json.loads(updated["body"])

    assert updated["statusCode"] == 200
    assert body["ok"] is True
    assert body["scoringProofComplete"] is True
    run_proof = body["scoring_proofs"][0]
    assert run_proof["status"] == "PASS"
    assert run_proof["expectedGameCount"] == 2
    assert run_proof["stageCounts"]["marketComponents"] == 2
    assert run_proof["stageCounts"]["movementComponents"] == 2
    assert run_proof["stageCounts"]["fundamentalsComponents"] == 2
    assert run_proof["fundamentals"]["appliedCount"] == 2
    assert run_proof["ml"]["appliedCount"] == 2
    assert run_proof["ml"]["productionAuthorityCount"] == 0
    assert run_proof["componentRows"][0]["scores"]["market"] == 68.0
    assert run_proof["storage"]["deduped"] is False


def test_count_mismatch_fails_closed_after_persisting_diagnostic_proof():
    table = FakeTable()
    payload = _payload()
    payload["game_winner_predictions"][0]["gameCount"] = 1
    payload["game_winner_predictions"][0]["count"] = 1
    payload["game_winner_predictions"][0]["predictions"] = payload["game_winner_predictions"][0]["predictions"][:1]
    response = {"statusCode": 200, "body": json.dumps(payload)}

    updated = proof.attach_and_store(response, event={}, table=table)
    body = json.loads(updated["body"])

    assert updated["statusCode"] == 500
    assert body["ok"] is False
    assert body["error"] == "MLB_SCORING_RUN_PROOF_FAILED"
    assert body["scoringProofComplete"] is False
    blockers = body["scoring_proofs"][0]["blockers"]
    assert any(value.startswith("winner_count_mismatch") for value in blockers)
    assert any(value.startswith("component_row_count_mismatch") for value in blockers)
    assert len(table.items) == 1


def test_same_slot_is_idempotent_when_fingerprint_matches():
    table = FakeTable()
    payload = _payload()
    first = proof.build_proof(payload, "2026-07-22")
    first_store = proof.store_proof(first, table=table)
    second_store = proof.store_proof(first, table=table)

    assert first_store["deduped"] is False
    assert second_store["deduped"] is True
    assert len(table.items) == 1


def test_latest_proof_reads_most_recent_persisted_record():
    table = FakeTable()
    payload = _payload()
    first = proof.build_proof(payload, "2026-07-22")
    proof.store_proof(first, table=table)

    status = proof.latest_proof("2026-07-22", table=table)

    assert status["ok"] is True
    assert status["proof"]["proofFingerprint"] == first["proofFingerprint"]
    assert status["proof"]["status"] == "PASS"
