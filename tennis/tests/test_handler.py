from __future__ import annotations

from datetime import timezone

import pytest

import handler


class FakePipeline:
    def __init__(self):
        self.slot_anchor_utc = None

    def run(self, *, slot_anchor_utc=None):
        self.slot_anchor_utc = slot_anchor_utc
        return {"ok": True, "slot": str(slot_anchor_utc)}


def test_standard_eventbridge_time_is_used_as_retry_stable_slot_anchor(monkeypatch):
    pipeline = FakePipeline()
    monkeypatch.setattr(handler, "_pipeline", pipeline)

    report = handler.lambda_handler(
        {
            "source": "aws.events",
            "detail-type": "Scheduled Event",
            "time": "2026-07-22T10:00:00Z",
        },
        None,
    )

    assert report["ok"] is True
    assert pipeline.slot_anchor_utc.astimezone(timezone.utc).isoformat() == (
        "2026-07-22T10:00:00+00:00"
    )


def test_invalid_eventbridge_time_fails_closed(monkeypatch):
    monkeypatch.setattr(handler, "_pipeline", FakePipeline())

    with pytest.raises(RuntimeError, match="invalid_eventbridge_scheduled_time"):
        handler.lambda_handler({"time": "not-a-time"}, None)


def test_scheduled_mode_requires_eventbridge_time_before_pipeline_build(monkeypatch):
    monkeypatch.setattr(handler, "_pipeline", None)

    with pytest.raises(RuntimeError, match="eventbridge_scheduled_time_required"):
        handler.lambda_handler({"sport": "tennis", "mode": "scheduled"}, None)


def test_health_canary_performs_no_pipeline_or_network_work(monkeypatch):
    monkeypatch.setattr(
        handler,
        "_build_pipeline",
        lambda: (_ for _ in ()).throw(AssertionError("must not build pipeline")),
    )

    report = handler.lambda_handler({"sport": "tennis", "mode": "canary_health"}, None)

    assert report["ok"] is True
    assert report["network_calls"] == 0
    assert report["schedule_invoked"] is False


def test_retryable_partial_report_raises_after_metrics_are_emitted(monkeypatch):
    class PartialPipeline(FakePipeline):
        def run(self, *, slot_anchor_utc=None):
            return {
                "ok": False,
                "mode": "RULE_BASED_SHADOW",
                "run_status": "PARTIAL_RETRY_REQUIRED",
                "retry_required": True,
                "slate_runs": [],
            }

    seen = []
    monkeypatch.setattr(handler, "_pipeline", PartialPipeline())
    monkeypatch.setattr(
        handler, "emit_report_metrics", lambda *args, **kwargs: seen.append(1)
    )

    with pytest.raises(RuntimeError, match="tennis_slot_retry_required"):
        handler.lambda_handler(
            {
                "source": "aws.events",
                "detail-type": "Scheduled Event",
                "time": "2026-07-22T10:00:00Z",
            },
            None,
        )

    assert seen == [1]
