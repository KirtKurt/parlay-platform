from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import guard_mlb_v8_shadow_report as guard


def _report(created_at: str, *, run_id: str = "100", marker: str = "value") -> dict:
    return {
        "createdAtUtc": created_at,
        "runId": run_id,
        "authority": "SHADOW_ONLY",
        "marker": marker,
    }


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def test_publishes_when_canonical_report_is_missing(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.json"
    destination = tmp_path / "latest.json"
    value = _report("2026-07-29T01:00:00+00:00")
    _write(incoming, value)

    decision = guard.guarded_update(incoming_path=incoming, destination_path=destination)

    assert decision.publish is True
    assert decision.reason == "canonical_report_missing_or_invalid"
    assert json.loads(destination.read_text()) == value


def test_publishes_newer_report(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.json"
    destination = tmp_path / "latest.json"
    _write(destination, _report("2026-07-29T01:00:00Z", run_id="100", marker="old"))
    newer = _report("2026-07-29T01:00:01+00:00", run_id="101", marker="new")
    _write(incoming, newer)

    decision = guard.guarded_update(incoming_path=incoming, destination_path=destination)

    assert decision.publish is True
    assert decision.reason == "incoming_report_is_newer"
    assert json.loads(destination.read_text()) == newer


def test_skips_stale_report_without_touching_canonical_file(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.json"
    destination = tmp_path / "latest.json"
    current = _report("2026-07-29T01:00:01+00:00", run_id="101", marker="current")
    _write(destination, current)
    original_bytes = destination.read_bytes()
    _write(incoming, _report("2026-07-29T01:00:00+00:00", run_id="100", marker="stale"))

    decision = guard.guarded_update(incoming_path=incoming, destination_path=destination)

    assert decision.publish is False
    assert decision.reason == "incoming_report_is_stale"
    assert destination.read_bytes() == original_bytes


def test_skips_exact_duplicate(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.json"
    destination = tmp_path / "latest.json"
    value = _report("2026-07-29T01:00:00+00:00")
    _write(destination, value)
    _write(incoming, value)

    decision = guard.guarded_update(incoming_path=incoming, destination_path=destination)

    assert decision.publish is False
    assert decision.reason == "incoming_report_is_duplicate"


def test_equal_timestamp_uses_numeric_run_id_as_tie_breaker(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.json"
    destination = tmp_path / "latest.json"
    _write(destination, _report("2026-07-29T01:00:00+00:00", run_id="100", marker="old"))
    higher = _report("2026-07-28T21:00:00-04:00", run_id="101", marker="new")
    _write(incoming, higher)

    decision = guard.guarded_update(incoming_path=incoming, destination_path=destination)

    assert decision.publish is True
    assert decision.reason == "equal_timestamp_higher_run_id"
    assert json.loads(destination.read_text()) == higher


def test_equal_timestamp_incomparable_evidence_fails_closed(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.json"
    destination = tmp_path / "latest.json"
    current = _report("2026-07-29T01:00:00+00:00", run_id="run-a", marker="old")
    _write(destination, current)
    _write(incoming, _report("2026-07-29T01:00:00+00:00", run_id="run-b", marker="new"))

    decision = guard.guarded_update(incoming_path=incoming, destination_path=destination)

    assert decision.publish is False
    assert decision.reason == "equal_timestamp_incomparable_evidence"
    assert json.loads(destination.read_text()) == current


def test_valid_incoming_repairs_corrupt_canonical_report(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.json"
    destination = tmp_path / "latest.json"
    destination.write_text("not-json", encoding="utf-8")
    value = _report("2026-07-29T01:00:00+00:00")
    _write(incoming, value)

    decision = guard.guarded_update(incoming_path=incoming, destination_path=destination)

    assert decision.publish is True
    assert decision.reason == "canonical_report_missing_or_invalid"
    assert json.loads(destination.read_text()) == value


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"createdAtUtc": "not-a-timestamp"},
        {"createdAtUtc": "2026-07-29T01:00:00"},
    ],
)
def test_invalid_incoming_report_is_rejected(tmp_path: Path, value: dict) -> None:
    incoming = tmp_path / "incoming.json"
    destination = tmp_path / "latest.json"
    _write(incoming, value)

    with pytest.raises(guard.ReportGuardError):
        guard.guarded_update(incoming_path=incoming, destination_path=destination)
