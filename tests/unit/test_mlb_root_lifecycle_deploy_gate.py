from __future__ import annotations

import json
import sys
from pathlib import Path


UNIT_DIR = Path(__file__).resolve().parent
if str(UNIT_DIR) not in sys.path:
    sys.path.insert(0, str(UNIT_DIR))

import test_mlb_daily_per_game_lock as legacy


def test_root_lifecycle_never_creates_prediction_before_due_or_after_start():
    before = legacy.build_module(
        legacy.EARLY_PULLS,
        "2026-07-13T17:14:00+00:00",
    )
    result = before.run_lock(legacy.SLATE)

    assert result["reason"] == "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER"
    assert legacy.staged_items(before) == []
    assert before.mlb_game_winner_engine.canonical_new_writes == 0

    missed = legacy.build_module(
        [legacy.pull("2026-07-13T17:15:00+00:00", [legacy.G1], "deploy-gate")],
        "2026-07-13T18:01:00+00:00",
    )
    result = missed.run_lock(legacy.SLATE, force=True)

    assert result["reason"] == "MISSED_PER_GAME_LOCK_NOT_BACKFILLED"
    assert result["forceIgnoredForSafety"] is True
    assert legacy.staged_items(missed) == []
    assert missed.mlb_game_winner_engine.canonical_new_writes == 0
    assert missed.mlb_game_winner_engine.prediction_calls == 0


def test_root_lifecycle_missed_lock_diagnostic_is_write_once_and_terminal():
    missed = legacy.build_module(
        [legacy.pull("2026-07-13T17:15:00+00:00", [legacy.G1], "diagnostic-gate")],
        "2026-07-13T18:01:00+00:00",
    )
    result = missed.run_lock(legacy.SLATE, force=True)

    assert result["reason"] == "MISSED_PER_GAME_LOCK_NOT_BACKFILLED"
    diagnostics = legacy.diagnostic_items(missed)
    context = json.dumps(
        {
            "result": result,
            "tableItems": list(missed.TABLE.items.values()),
            "putRequests": missed.TABLE.put_requests,
        },
        default=str,
        indent=2,
        sort_keys=True,
    )
    assert len(diagnostics) == 2, context
    outcome = next(
        item
        for item in diagnostics
        if item["diagnostic_event"] == "ATTEMPT_OUTCOME"
    )
    assert outcome["outcome"] == "MISSED_NOT_BACKFILLED"
    assert outcome["state_at_attempt"] == "MISSED_NOT_BACKFILLED"
    assert outcome["state_after_attempt"] == "MISSED_NOT_BACKFILLED"
    assert outcome["force_requested"] is True

    first_keys = {(item["PK"], item["SK"]) for item in diagnostics}
    repeated = missed.run_lock(legacy.SLATE, force=True)

    assert repeated["reason"] == "MISSED_PER_GAME_LOCK_NOT_BACKFILLED"
    assert {(item["PK"], item["SK"]) for item in legacy.diagnostic_items(missed)} == first_keys
    assert repeated["perGameLockAttemptDiagnostics"]["attemptedGameCount"] == 0
