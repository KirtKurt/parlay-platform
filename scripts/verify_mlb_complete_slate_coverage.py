#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_daily_lock_coverage_patch as daily_lock_patch
import mlb_daily_pick_lock as daily_lock
import mlb_doubleheader_safe_audit_patch as audit_patch
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
    no_id_early = {**early, "id": None, "game_id": None, "commence_time": "2020-07-12T16:00:00Z"}
    no_id_late = {**late, "id": None, "game_id": None, "commence_time": "2020-07-12T20:00:00Z"}
    assert coverage_patch.game_identity(no_id_early) != coverage_patch.game_identity(no_id_late)

    pulls = [
        {"pulled_at": "2020-07-11T14:00:00Z", "pull_id": "pull-1", "games": [early, late, third]},
        {"pulled_at": "2020-07-11T15:00:00Z", "pull_id": "pull-2", "games": [late, early, third]},
    ]

    class History:
        @staticmethod
        def query_pulls(sport, slate, limit):
            assert sport == "mlb"
            assert slate == "2020-07-11"
            return pulls

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

    original_enhance = lock_module._enhance
    original_optimize = lock_module._optimize_locked_row
    lock_module._enhance = lambda result: result
    lock_module._optimize_locked_row = lambda row: row
    try:
        result = lock_module._locked_result(Engine(), {"slate_date": "2020-07-11"}, ("2020-07-11",), {"limit": 500}, True)
    finally:
        lock_module._enhance = original_enhance
        lock_module._optimize_locked_row = original_optimize

    assert result["slatePredictionLock"]["firstGameStartUtc"] == "2020-07-11T16:00:00+00:00"
    assert result["slatePredictionLock"]["manifestGameCount"] == 3
    assert result["gameCount"] == 3
    assert result["count"] == 3
    assert result["storedCount"] == 3
    assert result["slateCoverage"]["coverageComplete"] is True
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

    daily_lock_patch.apply(daily_lock)

    class FakeTable:
        def __init__(self):
            self.item = None

        def put_item(self, Item, ConditionExpression=None):
            self.item = Item
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    fake_table = FakeTable()
    original_table = daily_lock.TABLE
    original_get = daily_lock._get_lock_item
    original_pulls = daily_lock._pulls_for_date
    original_now = daily_lock._now_utc
    original_min_depth = daily_lock.MIN_PULLS_PER_GAME_FOR_LOCK
    original_predict_all = daily_lock.mlb_game_winner_engine.predict_all
    daily_lock.TABLE = fake_table
    daily_lock._get_lock_item = lambda slate: None
    daily_lock._pulls_for_date = lambda slate: pulls
    daily_lock._now_utc = lambda: datetime(2020, 7, 11, 15, 15, tzinfo=timezone.utc)
    daily_lock.MIN_PULLS_PER_GAME_FOR_LOCK = 2
    daily_lock.mlb_game_winner_engine.predict_all = lambda slate, store, limit: result
    try:
        daily_result = daily_lock.run_lock("2020-07-11", force=False)
        assert daily_result["locked"] is True
        assert daily_result["lock"]["manifestGameCount"] == 3
        assert daily_result["lock"]["predictionCount"] == 3
        assert daily_result["lock"]["coverageComplete"] is True
        assert daily_result["lock"]["doubleheaderSafeIdentity"] is True
        assert len(daily_result["lock"]["picks"]) == 3
        assert fake_table.item["manifest_game_count"] == 3
        assert fake_table.item["coverage_complete"] is True

        daily_lock._now_utc = lambda: datetime(2020, 7, 11, 17, 0, tzinfo=timezone.utc)
        late_result = daily_lock.run_lock("2020-07-11", force=True)
        assert late_result["locked"] is False
        assert late_result["reason"] == "MISSED_FULL_SLATE_LOCK_WINDOW_NOT_BACKFILLED"
    finally:
        daily_lock.TABLE = original_table
        daily_lock._get_lock_item = original_get
        daily_lock._pulls_for_date = original_pulls
        daily_lock._now_utc = original_now
        daily_lock.MIN_PULLS_PER_GAME_FOR_LOCK = original_min_depth
        daily_lock.mlb_game_winner_engine.predict_all = original_predict_all

    print("MLB complete-slate coverage, AWS daily lock, no-backfill policy, and doubleheader-safe audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
