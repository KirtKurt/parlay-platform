from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mlb_deploy_cutoff_smoke_policy import historical_lifecycle_acceptance


NOW = datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc)


def _row(game_id: str, start: datetime, status: str, winner=None):
    return {
        "gameId": game_id,
        "gameIdentity": game_id,
        "commenceTime": start.isoformat(),
        "lockStatus": status,
        "officialPredictionStatus": status,
        "predictedWinner": winner,
    }


def test_accepts_complete_historical_lifecycle_after_every_cutoff():
    starts = [NOW - timedelta(hours=2), NOW - timedelta(minutes=30)]
    status_rows = [
        _row("g1", starts[0], "MISSED_LOCK"),
        _row("g2", starts[1], "LOCKED_NO_PREDICTION_DATA"),
    ]
    predictions = {
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "predictions": [dict(row) for row in status_rows],
    }

    assert historical_lifecycle_acceptance(
        predictions,
        status_rows,
        2,
        now=NOW,
    ) is True


def test_rejects_before_the_last_tminus45_cutoff():
    starts = [NOW - timedelta(hours=2), NOW + timedelta(hours=2)]
    status_rows = [
        _row("g1", starts[0], "MISSED_LOCK"),
        _row("g2", starts[1], "OPEN_PRE_LOCK"),
    ]
    predictions = {
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "predictions": [dict(row) for row in status_rows],
    }

    assert historical_lifecycle_acceptance(
        predictions,
        status_rows,
        2,
        now=NOW,
    ) is False


def test_rejects_any_late_fabricated_winner():
    start = NOW - timedelta(hours=2)
    status_rows = [_row("g1", start, "MISSED_LOCK")]
    predictions = {
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "predictions": [_row("g1", start, "MISSED_LOCK", winner="Invented Team")],
    }

    assert historical_lifecycle_acceptance(
        predictions,
        status_rows,
        1,
        now=NOW,
    ) is False


def test_rejects_identity_or_coverage_mismatch():
    start = NOW - timedelta(hours=2)
    status_rows = [_row("g1", start, "MISSED_LOCK")]
    predictions = {
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "predictions": [_row("different", start, "MISSED_LOCK")],
    }

    assert historical_lifecycle_acceptance(
        predictions,
        status_rows,
        1,
        now=NOW,
    ) is False


def test_deploy_workflow_contains_cutoff_policy_after_migration():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )
    assert "historical_lifecycle_acceptance" in workflow
    assert "historical_no_late_backfill" in workflow
    assert "all_tminus45_cutoffs_passed_without_valid_pregame_predictions" in workflow
    assert "No fresh persisted canonical probability-contract predictions or complete post-cutoff lifecycle appeared within 20 minutes" in workflow
    assert "if predictions.get('operationalDefect') is True and not historical_no_late_backfill:" in workflow
