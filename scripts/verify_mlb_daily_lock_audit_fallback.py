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
import mlb_doubleheader_safe_audit_patch as doubleheader
import mlb_daily_lock_audit_fallback_patch as fallback


class FakeTable:
    def get_item(self, Key, ConsistentRead=False):
        assert Key == {
            "PK": "LOCKED_PICKS#mlb#2026-07-11",
            "SK": "DAILY_LOCK#TMINUS45",
        }
        return {
            "Item": {
                "PK": Key["PK"],
                "SK": Key["SK"],
                "locked": True,
                "locked_at": "2026-07-11T15:21:20+00:00",
                "latest_pull_at": "2026-07-11T15:15:25+00:00",
                "first_game_start_utc": "2026-07-11T16:06:00+00:00",
                "game_count": 1,
                "prediction_count": 1,
                "all_games_predicted": True,
                "data": {
                    "picks": [{
                        "gameId": "db5e5f99da28e0ea52c06c7694fb5ad1",
                        "gameIdentity": "db5e5f99da28e0ea52c06c7694fb5ad1",
                        "awayTeam": "Toronto Blue Jays",
                        "homeTeam": "San Diego Padres",
                        "commenceTime": "2026-07-12T00:41:00Z",
                        "predictedWinner": "Toronto Blue Jays",
                        "predictedSide": "away",
                        "americanOdds": -104,
                        "priceBook": "fanduel",
                        "priceSource": "real_book",
                        "winProbabilityPct": 52.0,
                        "frozenFeatureVector": {
                            "version": "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v1-home-away-outcome",
                            "gameId": "db5e5f99da28e0ea52c06c7694fb5ad1",
                            "lockAtUtc": "2026-07-11T15:21:00+00:00",
                            "sourcePullAtUtc": "2026-07-11T15:15:00+00:00",
                            "features": {"homeMarketProb": 0.48, "awayMarketProb": 0.52},
                            "labels": {"homeWon": None, "pickCorrect": None},
                            "fingerprint": "test-fingerprint",
                        },
                        "tags": ["BOOK_AGREEMENT", "PICKEM", "POSITIVE_MOVE"],
                    }]
                },
            }
        }


class History:
    PULLS = FakeTable()


class AuditModule:
    history = History()

    @staticmethod
    def normalize_team(value):
        return " ".join(str(value or "").lower().split())

    @staticmethod
    def _query_predictions_for_slate(_slate):
        # This is the unsafe live row that must remain rejected.
        return [{
            "gameId": "db5e5f99da28e0ea52c06c7694fb5ad1",
            "awayTeam": "Toronto Blue Jays",
            "homeTeam": "San Diego Padres",
            "commenceTime": "2026-07-12T00:41:00Z",
            "predictedWinner": "San Diego Padres",
            "predictedSide": "home",
            "createdAt": "2026-07-12T03:50:36+00:00",
            "tags": ["BOOK_DIVERGENCE", "FAVORITE"],
        }]


def main() -> int:
    module = AuditModule()
    base.apply(module)
    doubleheader.apply(module)
    fallback.apply(module)

    final = {
        "id": "db5e5f99da28e0ea52c06c7694fb5ad1",
        "slateDateEt": "2026-07-11",
        "awayTeam": "Toronto Blue Jays",
        "homeTeam": "San Diego Padres",
        "commenceTime": "2026-07-12T00:41:00Z",
        "winner": "San Diego Padres",
    }
    rows = module.audit_rows([final])
    assert len(rows) == 1, rows
    row = rows[0]
    assert row.get("status") == "GRADED", row
    assert row.get("predictedWinner") == "Toronto Blue Jays", row
    assert row.get("correct") is False, row
    assert row.get("lockedAmericanOdds") == -104, row
    assert row.get("priceBook") == "fanduel", row
    assert "IMMUTABLE_DAILY_LOCK_FALLBACK" in set(row.get("tags") or []), row
    audit = row.get("lockedCardAudit") or {}
    assert audit.get("matchMethod") == "provider_game_id", audit
    assert audit.get("authoritySource") == "immutable_daily_locked_card", audit
    assert audit.get("writeOnceCard") is True, audit
    assert audit.get("lockAtUtc") == "2026-07-11T15:21:00+00:00", audit
    assert audit.get("explicitSourceAtUtc") == "2026-07-11T15:15:00+00:00", audit
    fallback_proof = row.get("immutableDailyLockFallback") or {}
    assert fallback_proof.get("cardStoredAtUtc") == "2026-07-11T15:21:20+00:00", fallback_proof
    assert fallback_proof.get("cardLatestPullAtUtc") == "2026-07-11T15:15:25+00:00", fallback_proof
    print("MLB immutable daily lock settlement fallback verified: unsafe live row rejected, write-once card accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
