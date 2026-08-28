from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_result_signals as subject


RULE_ARN = (
    "arn:aws:events:us-east-1:123456789012:"
    "rule/parlay-MLBResultsEvery6Hours-abc"
)


def _provenance():
    return {
        "schema_version": "MLB-RESULT-SIGNAL-PRODUCER-PROOF-v1",
        "authority": "NATIVE_EVENTBRIDGE_SCHEDULE_ENVELOPE",
        "lambda_request_id": "22222222-2222-4222-8222-222222222222",
        "event_id": "11111111-1111-4111-8111-111111111111",
        "event_time_utc": "2026-08-28T01:06:04Z",
        "event_source": "aws.events",
        "detail_type": "Scheduled Event",
        "rule_arn": RULE_ARN,
        "account": "123456789012",
        "region": "us-east-1",
    }


class _Table:
    def __init__(self):
        self.items = []

    def put_item(self, *, Item):
        self.items.append(Item)
        return {}


def test_native_provenance_is_persisted_and_logged_after_summary_put(
    monkeypatch,
    capsys,
):
    table = _Table()
    monkeypatch.setattr(subject, "signal_ledger_tbl", table)
    monkeypatch.setattr(subject, "_outcomes_for_slate", lambda _date: [])
    monkeypatch.setattr(subject, "_movement_row_by_game", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(subject, "_latest_prediction_by_game", lambda _date: {})
    monkeypatch.setattr(
        subject,
        "_now_iso",
        lambda: "2026-08-28T01:06:05+00:00",
    )

    result = subject.build_result_signals(
        "2026-08-27",
        fetch_scores=False,
        store=True,
        producer_provenance=_provenance(),
    )

    assert len(table.items) == 1
    summary = table.items[0]
    assert summary["SK"] == "SUMMARY#2026-08-28T01:06:05+00:00"
    assert summary["created_at"] == "2026-08-28T01:06:05+00:00"
    assert summary["producer_provenance"] == _provenance()
    assert result["summary_key"] == {"PK": summary["PK"], "SK": summary["SK"]}
    logged = json.loads(capsys.readouterr().out)
    assert logged == {
        "event": "MLB_RESULT_SIGNAL_SUMMARY_PERSISTED",
        "schema_version": "MLB-RESULT-SIGNAL-PRODUCER-PROOF-v1",
        "lambda_request_id": _provenance()["lambda_request_id"],
        "event_id": _provenance()["event_id"],
        "rule_arn": RULE_ARN,
        "PK": summary["PK"],
        "SK": summary["SK"],
    }


def test_invalid_provenance_fails_before_fetch_query_or_any_put(monkeypatch):
    table = _Table()
    monkeypatch.setattr(subject, "signal_ledger_tbl", table)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid provenance crossed the mutation boundary")

    monkeypatch.setattr(subject, "pull_mlb_results", forbidden)
    monkeypatch.setattr(subject, "_outcomes_for_slate", forbidden)
    malformed = _provenance()
    malformed.pop("event_id")

    with pytest.raises(ValueError, match="fields mismatch"):
        subject.build_result_signals(
            "2026-08-27",
            fetch_scores=True,
            store=True,
            producer_provenance=malformed,
        )

    assert table.items == []
