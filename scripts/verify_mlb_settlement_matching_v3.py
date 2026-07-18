#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import mlb_locked_card_audit_v1 as base
import mlb_doubleheader_safe_audit_patch as patch


class DummyAudit:
    CANONICAL_LOCK_AUTHORITY_VERSION = (
        "MLB-ROLLING-AUDIT-CANONICAL-LOCK-AUTHORITY-v1"
    )

    @staticmethod
    def normalize_team(value):
        return " ".join(str(value or "").lower().split())

    @staticmethod
    def _query_predictions_for_slate(_slate):
        return PREDICTIONS


LOCK = {
    "locked": True,
    "lockAtUtc": "2026-07-12T16:00:00Z",
    "latestScoringPullAt": "2026-07-12T15:55:00Z",
}


def prediction(game_id, away, home, start, winner, *, id_field="providerGameId"):
    slate = "2026-07-12"
    row = {
        "slateDateEt": slate,
        "awayTeam": away,
        "homeTeam": home,
        "commenceTime": start,
        "predictedWinner": winner,
        "predictedSide": "home" if winner == home else "away",
        "slatePredictionLock": dict(LOCK),
        "tags": ["SLATE_LOCKED", "OFFICIAL_LOCKED_PREDICTION"],
        "officialPrediction": True,
        "actionablePick": False,
        "accuracyTargetEligible": False,
        "recommendationStatus": "OFFICIAL_PREDICTION_NOT_PLAYABLE",
        "canonicalLockAuthority": {
            "version": DummyAudit.CANONICAL_LOCK_AUTHORITY_VERSION,
            "verified": True,
            "consistentRead": True,
            "immutableLocked": True,
            "stageAuthorityVerified": True,
            "persistedStageAuthorityValidated": True,
            "exactLockVectorValidated": True,
            "legacyOrDailyCardFallbackUsed": False,
            "sourcePk": f"GAME_WINNERS#mlb#{slate}",
            "sourceSk": f"LOCKED#GAME#{start}#{game_id}",
            "recordType": "mlb_immutable_locked_single_game_prediction",
        },
    }
    row[id_field] = game_id
    return row


PREDICTIONS = [
    prediction("alt-provider-id", "Away A", "Home A", "2026-07-12T18:00:00Z", "Home A"),
    prediction("drift-id", "Away B", "Home B", "2026-07-12T19:00:00Z", "Away B", id_field="event_id"),
    prediction("dh-1", "Milwaukee Brewers", "Pittsburgh Pirates", "2026-07-12T17:00:00Z", "Milwaukee Brewers"),
    prediction("dh-2", "Milwaukee Brewers", "Pittsburgh Pirates", "2026-07-12T21:00:00Z", "Pittsburgh Pirates"),
]

FINALS = [
    {
        "provider_game_id": "alt-provider-id",
        "awayTeam": "Away A", "homeTeam": "Home A",
        "commenceTime": "2026-07-12T18:03:00Z", "winner": "Home A", "slateDateEt": "2026-07-12",
    },
    {
        "awayTeam": "Away B", "homeTeam": "Home B",
        "commenceTime": "2026-07-12T19:07:00Z", "winner": "Away B", "slateDateEt": "2026-07-12",
    },
    {
        "gameId": "dh-1",
        "awayTeam": "Milwaukee Brewers", "homeTeam": "Pittsburgh Pirates",
        "commenceTime": "2026-07-12T17:06:00Z", "winner": "Milwaukee Brewers", "slateDateEt": "2026-07-12",
    },
    {
        "gameId": "dh-2",
        "awayTeam": "Milwaukee Brewers", "homeTeam": "Pittsburgh Pirates",
        "commenceTime": "2026-07-12T21:08:00Z", "winner": "Pittsburgh Pirates", "slateDateEt": "2026-07-12",
    },
    {
        "gameId": "different-final-id",
        "awayTeam": "Milwaukee Brewers", "homeTeam": "Pittsburgh Pirates",
        "commenceTime": "2026-07-12T17:02:00Z", "winner": "Milwaukee Brewers", "slateDateEt": "2026-07-12",
    },
]


def main():
    for name in ("_INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_APPLIED", "_INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_V3_APPLIED"):
        if hasattr(DummyAudit, name):
            delattr(DummyAudit, name)
    patch.apply(DummyAudit)
    rows = DummyAudit.audit_rows(FINALS)
    assert len(rows) == 5, rows
    assert [row.get("status") for row in rows] == [
        "GRADED", "MISSING_CANONICAL_LOCK", "GRADED", "GRADED", "MISSING_CANONICAL_LOCK",
    ], rows
    assert rows[0].get("correct") is True, rows
    assert rows[2].get("correct") is True, rows
    assert rows[3].get("correct") is True, rows
    methods = [row.get("lockedCardAudit", {}).get("matchMethod") for row in rows]
    assert methods[0] == "exact_provider_game_id_and_teams", methods
    assert methods[2] == "exact_provider_game_id_and_teams", methods
    assert methods[3] == "exact_provider_game_id_and_teams", methods
    assert rows[2]["predictedWinner"] == "Milwaukee Brewers", rows[2]
    assert rows[3]["predictedWinner"] == "Pittsburgh Pirates", rows[3]
    assert rows[1]["lockedCardAudit"]["missingReason"] == "no_exact_canonical_provider_game_id_match", rows[1]
    assert rows[4]["lockedCardAudit"]["missingReason"] == "no_exact_canonical_provider_game_id_match", rows[4]
    print("MLB settlement matching verified: exact canonical provider IDs grade; fuzzy joins fail closed; doubleheaders remain distinct")


if __name__ == "__main__":
    main()
