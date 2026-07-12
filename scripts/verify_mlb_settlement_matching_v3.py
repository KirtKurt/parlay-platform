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
    row = {
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
        "gameId": "different-final-id-1",
        "awayTeam": "Milwaukee Brewers", "homeTeam": "Pittsburgh Pirates",
        "commenceTime": "2026-07-12T17:06:00Z", "winner": "Milwaukee Brewers", "slateDateEt": "2026-07-12",
    },
    {
        "gameId": "different-final-id-2",
        "awayTeam": "Milwaukee Brewers", "homeTeam": "Pittsburgh Pirates",
        "commenceTime": "2026-07-12T21:08:00Z", "winner": "Pittsburgh Pirates", "slateDateEt": "2026-07-12",
    },
]


def main():
    for name in ("_INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_APPLIED", "_INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_V3_APPLIED"):
        if hasattr(DummyAudit, name):
            delattr(DummyAudit, name)
    patch.apply(DummyAudit)
    rows = DummyAudit.audit_rows(FINALS)
    assert len(rows) == 4, rows
    assert all(row.get("status") == "GRADED" for row in rows), rows
    assert all(row.get("correct") is True for row in rows), rows
    methods = [row.get("lockedCardAudit", {}).get("matchMethod") for row in rows]
    assert methods[0] == "provider_game_id", methods
    assert rows[0].get("lockedCardAudit", {}).get("providerAliasAware") is True, rows[0]
    assert methods[1] == "teams_and_nearest_commence_time", methods
    assert methods[2] == "teams_and_nearest_commence_time", methods
    assert methods[3] == "teams_and_nearest_commence_time", methods
    assert rows[2]["predictedWinner"] == "Milwaukee Brewers", rows[2]
    assert rows[3]["predictedWinner"] == "Pittsburgh Pirates", rows[3]
    print("MLB settlement matching v3 verified: provider aliases, time drift, and doubleheaders remain distinct")


if __name__ == "__main__":
    main()
