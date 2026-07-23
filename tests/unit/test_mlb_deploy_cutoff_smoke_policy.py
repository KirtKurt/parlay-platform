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


def _empty_predictions(**overrides):
    payload = {
        "sport": "mlb",
        "predictions": [],
        "lockedPredictionCount": 0,
        "officialPredictionCount": 0,
        "canonicalPredictionComplete": False,
        "operationalDefect": True,
    }
    payload.update(overrides)
    return payload


def test_accepts_complete_historical_lifecycle_after_every_cutoff():
    starts = [NOW - timedelta(hours=2), NOW - timedelta(minutes=30)]
    status_rows = [
        _row("g1", starts[0], "MISSED_NOT_BACKFILLED"),
        _row("g2", starts[1], "LOCKED_NO_PREDICTION_DATA"),
    ]
    predictions = {
        "sport": "mlb",
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


def test_accepts_status_only_historical_evidence_when_prediction_store_is_empty():
    starts = [NOW - timedelta(hours=3), NOW - timedelta(hours=1)]
    status_rows = [
        _row("g1", starts[0], "MISSED_NOT_BACKFILLED"),
        _row("g2", starts[1], "LOCKED_NO_PREDICTION_DATA"),
    ]
    predictions = _empty_predictions()

    assert historical_lifecycle_acceptance(
        predictions,
        status_rows,
        2,
        now=NOW,
    ) is True
    assert predictions["gameCount"] == 2
    assert predictions["displayStatusCoverageComplete"] is True
    assert predictions["lifecycleCoverageComplete"] is True
    assert predictions["statusOnlyHistoricalProjection"] is True
    assert predictions["statusOnlyHistoricalProjectionPersisted"] is False
    assert predictions["predictions"] == status_rows
    assert predictions["predictions"] is not status_rows
    assert all(row.get("predictedWinner") in (None, "") for row in predictions["predictions"])


def test_projection_is_a_deep_copy_of_public_status_rows():
    start = NOW - timedelta(hours=2)
    status_rows = [_row("g1", start, "MISSED_NOT_BACKFILLED")]
    predictions = _empty_predictions()

    assert historical_lifecycle_acceptance(predictions, status_rows, 1, now=NOW) is True
    predictions["predictions"][0]["lockStatus"] = "CHANGED_IN_TEST"
    assert status_rows[0]["lockStatus"] == "MISSED_NOT_BACKFILLED"


def test_rejects_status_only_evidence_with_any_winner_count_claim():
    start = NOW - timedelta(hours=2)
    status_rows = [_row("g1", start, "MISSED_NOT_BACKFILLED")]

    assert historical_lifecycle_acceptance(
        _empty_predictions(lockedPredictionCount=1),
        status_rows,
        1,
        now=NOW,
    ) is False
    assert historical_lifecycle_acceptance(
        _empty_predictions(officialPredictionCount=1),
        status_rows,
        1,
        now=NOW,
    ) is False
    assert historical_lifecycle_acceptance(
        _empty_predictions(canonicalPredictionComplete=True),
        status_rows,
        1,
        now=NOW,
    ) is False


def test_rejects_status_only_evidence_if_status_endpoint_contains_a_winner():
    start = NOW - timedelta(hours=2)
    status_rows = [
        _row("g1", start, "MISSED_NOT_BACKFILLED", winner="Invented Team")
    ]

    assert historical_lifecycle_acceptance(
        _empty_predictions(),
        status_rows,
        1,
        now=NOW,
    ) is False


def test_rejects_before_the_last_tminus45_cutoff():
    starts = [NOW - timedelta(hours=2), NOW + timedelta(hours=2)]
    status_rows = [
        _row("g1", starts[0], "MISSED_NOT_BACKFILLED"),
        _row("g2", starts[1], "OPEN_PRE_LOCK"),
    ]

    assert historical_lifecycle_acceptance(
        _empty_predictions(),
        status_rows,
        2,
        now=NOW,
    ) is False


def test_rejects_any_late_fabricated_winner():
    start = NOW - timedelta(hours=2)
    status_rows = [_row("g1", start, "MISSED_NOT_BACKFILLED")]
    predictions = {
        "sport": "mlb",
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "predictions": [
            _row("g1", start, "MISSED_NOT_BACKFILLED", winner="Invented Team")
        ],
    }

    assert historical_lifecycle_acceptance(
        predictions,
        status_rows,
        1,
        now=NOW,
    ) is False


def test_rejects_partial_prediction_rows_or_identity_mismatch():
    start = NOW - timedelta(hours=2)
    status_rows = [
        _row("g1", start, "MISSED_NOT_BACKFILLED"),
        _row("g2", start, "LOCKED_NO_PREDICTION_DATA"),
    ]
    partial = {
        "sport": "mlb",
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "predictions": [_row("g1", start, "MISSED_NOT_BACKFILLED")],
    }
    mismatched = {
        "sport": "mlb",
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "predictions": [
            _row("g1", start, "MISSED_NOT_BACKFILLED"),
            _row("different", start, "LOCKED_NO_PREDICTION_DATA"),
        ],
    }

    assert historical_lifecycle_acceptance(partial, status_rows, 2, now=NOW) is False
    assert historical_lifecycle_acceptance(mismatched, status_rows, 2, now=NOW) is False


def test_deploy_workflow_uses_cutoff_policy_and_policy_marks_projection():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )
    policy = (ROOT / "scripts" / "mlb_deploy_cutoff_smoke_policy.py").read_text(
        encoding="utf-8"
    )

    assert "historical_lifecycle_acceptance" in workflow
    assert "historical_no_late_backfill" in workflow
    assert "all_tminus45_cutoffs_passed_without_valid_pregame_predictions" in workflow
    assert "No fresh persisted canonical probability-contract predictions or complete post-cutoff lifecycle appeared within 20 minutes" in workflow
    assert "if predictions.get('operationalDefect') is True and not historical_no_late_backfill:" in workflow

    assert '"MISSED_NOT_BACKFILLED"' in policy
    assert '"statusOnlyHistoricalProjection": True' in policy
    assert '"statusOnlyHistoricalProjectionPersisted": False' in policy
    assert '"predictions": copy.deepcopy(status)' in policy
