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


def _row(game_id: str, start: datetime, status: str, winner=None, locked=False):
    return {
        "gameId": game_id,
        "gameIdentity": game_id,
        "commenceTime": start.isoformat(),
        "lockStatus": status,
        "officialPredictionStatus": status,
        "predictedWinner": winner,
        "lockedPrediction": locked,
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
    assert historical_lifecycle_acceptance(predictions, status_rows, 2, now=NOW)


def test_accepts_status_only_historical_evidence_when_prediction_store_is_empty():
    start = NOW - timedelta(hours=2)
    status_rows = [
        _row("g1", start, "MISSED_NOT_BACKFILLED"),
        _row("g2", start, "LOCKED_NO_PREDICTION_DATA"),
    ]
    predictions = _empty_predictions()
    assert historical_lifecycle_acceptance(predictions, status_rows, 2, now=NOW)
    assert predictions["gameCount"] == 2
    assert predictions["displayStatusCoverageComplete"] is True
    assert predictions["lifecycleCoverageComplete"] is True
    assert predictions["statusOnlyHistoricalProjection"] is True
    assert predictions["statusOnlyHistoricalProjectionPersisted"] is False
    assert predictions["statusAuthoritativeHistoricalProjection"] is True
    assert predictions["statusAuthoritativeHistoricalProjectionPersisted"] is False
    assert predictions["predictions"] == status_rows
    assert predictions["predictions"] is not status_rows
    assert predictions["lockedPredictionCount"] == 0
    assert predictions["officialPredictionCount"] == 0
    assert predictions["lockedStatusCount"] == 2
    assert predictions["noPredictionDataCount"] == 2
    assert predictions["lockStatusComplete"] is True
    assert predictions["canonicalPredictionComplete"] is False


def test_projection_reconciles_stale_empty_endpoint_counters():
    start = NOW - timedelta(hours=2)
    rows = [
        _row("g1", start, "MISSED_LOCK"),
        _row("g2", start, "LOCK_DUE_CANONICAL_MISSING"),
        _row("g3", start, "LOCKED_NO_PREDICTION_DATA"),
    ]
    predictions = _empty_predictions(
        lockedStatusCount=0,
        noPredictionDataCount=0,
        lockStatusComplete=False,
    )
    assert historical_lifecycle_acceptance(predictions, rows, 3, now=NOW)
    assert predictions["lockedStatusCount"] == 3
    assert predictions["noPredictionDataCount"] == 3


def test_projection_is_a_deep_copy():
    start = NOW - timedelta(hours=2)
    rows = [_row("g1", start, "MISSED_NOT_BACKFILLED")]
    predictions = _empty_predictions()
    assert historical_lifecycle_acceptance(predictions, rows, 1, now=NOW)
    predictions["predictions"][0]["lockStatus"] = "CHANGED_IN_TEST"
    assert rows[0]["lockStatus"] == "MISSED_NOT_BACKFILLED"


def test_empty_endpoint_rejects_contradictory_winner_count_claims():
    start = NOW - timedelta(hours=2)
    rows = [_row("g1", start, "MISSED_NOT_BACKFILLED")]
    assert not historical_lifecycle_acceptance(
        _empty_predictions(lockedPredictionCount=1), rows, 1, now=NOW
    )
    assert not historical_lifecycle_acceptance(
        _empty_predictions(officialPredictionCount=1), rows, 1, now=NOW
    )
    assert not historical_lifecycle_acceptance(
        _empty_predictions(canonicalPredictionComplete=True), rows, 1, now=NOW
    )


def test_accepts_stale_unlocked_winners_as_non_authoritative_after_cutoff():
    start = NOW - timedelta(hours=2)
    status_rows = [
        _row("g1", start, "MISSED_NOT_BACKFILLED"),
        _row("g2", start, "LOCKED_NO_PREDICTION_DATA"),
    ]
    predictions = {
        "sport": "mlb",
        "predictions": [
            _row("g1", start, "OPEN_PRE_LOCK", winner="A"),
            _row("g2", start, "OPEN_PRE_LOCK", winner="B"),
        ],
        "lockedPredictionCount": 2,
        "officialPredictionCount": 2,
        "canonicalPredictionComplete": False,
        "operationalDefect": True,
    }
    assert historical_lifecycle_acceptance(predictions, status_rows, 2, now=NOW)
    assert predictions["ignoredNonAuthoritativeWinnerCount"] == 2
    assert predictions["lockedPredictionCount"] == 0
    assert predictions["officialPredictionCount"] == 0
    assert all(not row.get("predictedWinner") for row in predictions["predictions"])


def test_accepts_and_preserves_exact_immutable_locked_winner():
    start = NOW - timedelta(hours=2)
    status_rows = [
        _row("g1", start, "LOCKED", winner="A", locked=True),
        _row("g2", start, "MISSED_NOT_BACKFILLED"),
    ]
    predictions = {
        "sport": "mlb",
        "predictions": [
            _row("g1", start, "LOCKED", winner="A", locked=True),
            _row("g2", start, "OPEN_PRE_LOCK", winner="B"),
        ],
        "lockedPredictionCount": 1,
        "canonicalPredictionComplete": False,
    }
    assert historical_lifecycle_acceptance(predictions, status_rows, 2, now=NOW)
    assert predictions["lockedPredictionCount"] == 1
    assert predictions["noPredictionDataCount"] == 1
    assert predictions["predictions"][0]["predictedWinner"] == "A"
    assert predictions["ignoredNonAuthoritativeWinnerCount"] == 1


def test_rejects_fabricated_or_mismatched_immutable_winner():
    start = NOW - timedelta(hours=2)
    status_rows = [_row("g1", start, "LOCKED", winner="A", locked=True)]
    predictions = {
        "sport": "mlb",
        "predictions": [_row("g1", start, "LOCKED", winner="B", locked=True)],
        "lockedPredictionCount": 1,
        "canonicalPredictionComplete": True,
    }
    assert not historical_lifecycle_acceptance(predictions, status_rows, 1, now=NOW)


def test_rejects_status_winner_without_immutable_lock_marker():
    start = NOW - timedelta(hours=2)
    status_rows = [_row("g1", start, "OPEN_PRE_LOCK", winner="A")]
    assert not historical_lifecycle_acceptance(_empty_predictions(), status_rows, 1, now=NOW)


def test_rejects_before_last_tminus45_cutoff():
    status_rows = [
        _row("g1", NOW - timedelta(hours=2), "MISSED_NOT_BACKFILLED"),
        _row("g2", NOW + timedelta(hours=2), "OPEN_PRE_LOCK"),
    ]
    assert not historical_lifecycle_acceptance(_empty_predictions(), status_rows, 2, now=NOW)


def test_rejects_partial_duplicate_or_identityless_status_authority():
    start = NOW - timedelta(hours=2)
    duplicate = [
        _row("g1", start, "MISSED_NOT_BACKFILLED"),
        _row("g1", start, "LOCKED_NO_PREDICTION_DATA"),
    ]
    assert not historical_lifecycle_acceptance(_empty_predictions(), duplicate, 2, now=NOW)
    assert not historical_lifecycle_acceptance(_empty_predictions(), duplicate[:1], 2, now=NOW)
    identityless = [_row("", start, "MISSED_NOT_BACKFILLED")]
    assert not historical_lifecycle_acceptance(_empty_predictions(), identityless, 1, now=NOW)


def test_workflow_uses_status_authoritative_cutoff_policy():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    policy = (ROOT / "scripts" / "mlb_deploy_cutoff_smoke_policy.py").read_text(encoding="utf-8")
    assert "historical_lifecycle_acceptance" in workflow
    assert "historical_no_late_backfill" in workflow
    assert "all_tminus45_cutoffs_passed_without_valid_pregame_predictions" in workflow
    assert "statusAuthoritativeHistoricalProjection" in policy
    assert "ignoredNonAuthoritativeWinnerCount" in policy
    assert "projected_rows = copy.deepcopy(status)" in policy
