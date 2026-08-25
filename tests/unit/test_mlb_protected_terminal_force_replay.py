from __future__ import annotations

import sys
from pathlib import Path


UNIT_DIR = Path(__file__).resolve().parent
if str(UNIT_DIR) not in sys.path:
    sys.path.insert(0, str(UNIT_DIR))

import test_mlb_prospective_status_lifecycle_repair as lifecycle
import mlb_prospective_row_repair as repair


def test_forced_scheduled_reconciliation_writes_only_no_prediction_terminal():
    module = lifecycle.FakeModule()
    repair.install_prospective_row_repair(module, lifecycle.FakePatch)

    result = module.run_lock(
        slate_date=lifecycle.SLATE,
        force=True,
        scheduled=True,
    )

    assert module.original_calls == 1
    assert module.outcome is not None
    assert module.outcome["lock_status"] == "LOCKED_NO_PREDICTION_DATA"
    assert module.outcome["locked_prediction"] is False
    assert module.outcome["training_eligible"] is False
    assert result["ok"] is True
    assert result["reason"] == "PROVEN_NO_PREDICTION_TERMINALS_RECONCILED"
    assert result["postStartPredictionCreationAllowed"] is False
    assert result["durableNoPredictionTerminalReconciledCount"] == 1
    assert result["canonicalPredictionComplete"] is False
    assert result["lockStatusComplete"] is True


def test_manual_force_probe_remains_fail_closed():
    module = lifecycle.FakeModule()
    repair.install_prospective_row_repair(module, lifecycle.FakePatch)

    result = module.run_lock(
        slate_date=lifecycle.SLATE,
        force=True,
        scheduled=False,
    )

    assert module.outcome is None
    assert result["ok"] is False
    assert result["reason"] == "MISSED_PER_GAME_LOCK_NOT_BACKFILLED"
    assert result["failClosed"] is True
