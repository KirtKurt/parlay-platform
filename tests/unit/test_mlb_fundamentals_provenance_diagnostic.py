from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from hello_world import mlb_fundamentals_snapshot_v2 as snapshot_v2


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "mlb_fundamentals_provenance_diagnostic",
    SCRIPTS / "mlb_fundamentals_provenance_diagnostic.py",
)
assert SPEC and SPEC.loader
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)


def _row(*, lock_at: str, retrieved_at: str) -> dict:
    context = {
        context_name: {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "reason": "fixture source unavailable",
        }
        for _output, context_name, _fields in snapshot_v2.GROUP_SPECS
    }
    context["travel_rest"] = {
        "source_status": "CONNECTED",
        "home_rest_days": 2,
        "away_rest_days": 1,
        "sourceProvenance": {
            "provider": "fixture",
            "dataset": "rest",
            "retrievedAtUtc": retrieved_at,
            "sourceEffectiveAtUtc": "2026-07-22T11:58:00+00:00",
            "payloadFingerprint": "rest-fixture",
        },
    }
    row = {
        "gameId": "fixture-game",
        "homeTeam": "Home",
        "awayTeam": "Away",
        "commenceTime": "2026-07-22T17:05:00+00:00",
        "predictionSourcePullAt": "2026-07-22T12:00:00+00:00",
        "predictionSourcePullId": "fixture-pull",
        "lockedAtUtc": lock_at,
        "advanced_context": context,
    }
    row["fundamentalsSnapshotV2"] = snapshot_v2.build(
        row,
        captured_at_utc="2026-07-22T12:00:00+00:00",
    )
    snapshot_v2.enhance_row(row)
    return row


def test_valid_provenance_boundary_reports_safe_without_changing_contract() -> None:
    result = SUBJECT.diagnose_row(
        _row(
            lock_at="2026-07-22T16:20:00+00:00",
            retrieved_at="2026-07-22T11:59:30+00:00",
        ),
        persisted_at="2026-07-22T12:01:00+00:00",
    )

    assert result["contractSafe"] is True
    assert result["violations"] == []


def test_persistence_after_lock_is_reason_coded_but_not_relaxed() -> None:
    result = SUBJECT.diagnose_row(
        _row(
            lock_at="2026-07-22T12:00:30+00:00",
            retrieved_at="2026-07-22T11:59:30+00:00",
        ),
        persisted_at="2026-07-22T12:01:00+00:00",
    )

    assert result["contractSafe"] is False
    assert "prediction_persisted_after_lock" in result["violations"]


def test_source_retrieved_after_persistence_is_reason_coded_and_stays_blocked() -> None:
    result = SUBJECT.diagnose_row(
        _row(
            lock_at="2026-07-22T16:20:00+00:00",
            retrieved_at="2026-07-22T12:02:00+00:00",
        ),
        persisted_at="2026-07-22T12:01:00+00:00",
    )

    assert result["contractSafe"] is False
    assert (
        "group:travel_rest:retrieved_after_prediction_persistence"
        in result["violations"]
    )
