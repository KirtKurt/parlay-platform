from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


HELLO_WORLD = Path(__file__).resolve().parents[2] / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_playability_checkpoint_scheduler as scheduler


def _runtime(monkeypatch, runner):
    protected = SimpleNamespace(PER_GAME_LOCK_STATUS={"ok": True})
    daily_lock = SimpleNamespace(run_playability_checkpoints=runner)
    monkeypatch.setattr(
        scheduler,
        "_load_runtime",
        lambda: (protected, daily_lock),
    )
    return daily_lock


def test_module_import_does_not_eagerly_install_protected_runtime():
    assert "mlb_daily_pick_lock" not in scheduler.__dict__
    assert "protected_runtime" not in scheduler.__dict__


def test_handler_accepts_only_exact_forward_playability_event(monkeypatch):
    calls = []

    def run(slate_date=None):
        calls.append(slate_date)
        return {
            "ok": True,
            "sport": "mlb",
            "slateDateEt": slate_date,
            "selectionRewriteAllowed": False,
            "predictionCreationAllowed": False,
            "postStartPredictionCreationAllowed": False,
            "historicalMutationAllowed": False,
            "writeOnce": True,
        }

    _runtime(monkeypatch, run)
    result = scheduler.lambda_handler(
        {
            "sport": "mlb",
            "run": "playability_checkpoint_sweep",
            "auto_ingest": False,
            "slateDateEt": "2026-07-13",
        },
        None,
    )

    assert calls == ["2026-07-13"]
    assert result["ok"] is True
    assert result["selectionRewriteAllowed"] is False
    assert result["predictionCreationAllowed"] is False
    assert result["postStartPredictionCreationAllowed"] is False
    assert result["historicalMutationAllowed"] is False
    assert result["writeOnce"] is True


@pytest.mark.parametrize(
    "event",
    [
        {},
        {
            "sport": "nfl",
            "run": "playability_checkpoint_sweep",
            "auto_ingest": False,
        },
        {
            "sport": "mlb",
            "run": "daily_lock_check",
            "auto_ingest": False,
        },
        {
            "sport": "mlb",
            "run": "playability_checkpoint_sweep",
            "auto_ingest": True,
        },
    ],
)
def test_handler_rejects_non_exact_or_ingesting_event(event):
    with pytest.raises(
        RuntimeError,
        match="MLB_PLAYABILITY_CHECKPOINT_EVENT_CONTRACT_INVALID",
    ):
        scheduler.lambda_handler(event, None)


def test_handler_surfaces_incomplete_due_checkpoint_as_lambda_error(monkeypatch):
    _runtime(
        monkeypatch,
        lambda slate_date=None: {
            "ok": False,
            "failClosed": True,
            "reason": "DUE_PLAYABILITY_CHECKPOINT_CAPTURE_INCOMPLETE",
        },
    )

    with pytest.raises(
        RuntimeError,
        match="MLB_PLAYABILITY_CHECKPOINT_SWEEP_FAILED",
    ):
        scheduler.lambda_handler(
            {
                "sport": "mlb",
                "run": "playability_checkpoint_sweep",
                "auto_ingest": False,
            },
            None,
        )
