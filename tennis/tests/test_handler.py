from __future__ import annotations

from datetime import timezone

import pytest

import handler


class FakePipeline:
    def __init__(self):
        self.slot_anchor_utc = None
        self.run_count = 0
        self.config = type(
            "FakeConfig",
            (),
            {"pull_interval_minutes": 15, "slot_lease_seconds": 300},
        )()
        self.store = FakeLeaseStore()

    def run(self, *, slot_anchor_utc=None):
        self.run_count += 1
        self.slot_anchor_utc = slot_anchor_utc
        return {"ok": True, "slot": str(slot_anchor_utc)}


class FakeLeaseStore:
    def __init__(self):
        self.busy = False
        self.acquired = []
        self.released = []
        self.failures = {}
        self.recoveries = []

    def acquire_slot_lease(self, slot_utc, **kwargs):
        self.acquired.append((slot_utc, kwargs))
        return not self.busy

    def release_slot_lease(self, slot_utc, *, owner_id):
        self.released.append((slot_utc, owner_id))
        return True

    def record_invocation_failure(self, slot_utc, **kwargs):
        key = (slot_utc, kwargs["delivery_id"])
        count = int(self.failures.get(key, {}).get("failure_attempt_count") or 0) + 1
        row = {
            **kwargs,
            "failure_attempt_count": count,
            "retry_exhausted": count >= kwargs["max_attempts"],
        }
        self.failures[key] = row
        return row

    def resolve_invocation_failure(self, slot_utc, **kwargs):
        key = (slot_utc, kwargs["delivery_id"])
        row = self.failures.get(key)
        if row is None:
            return None
        self.recoveries.append((slot_utc, kwargs))
        return row


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
    assert pipeline.store.acquired[0][0] == "2026-07-22T10:00:00+00:00"
    assert pipeline.store.released[0][0] == "2026-07-22T10:00:00+00:00"


def test_busy_slot_lease_blocks_pipeline_before_any_provider_work(monkeypatch):
    pipeline = FakePipeline()
    pipeline.store.busy = True
    monkeypatch.setattr(handler, "_pipeline", pipeline)

    with pytest.raises(RuntimeError, match="tennis_slot_lease_busy"):
        handler.lambda_handler(
            {"time": "2026-07-22T10:00:00Z", "sport": "tennis"}, None
        )

    assert pipeline.run_count == 0
    assert pipeline.store.released == []


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
    pipeline = PartialPipeline()
    monkeypatch.setattr(handler, "_pipeline", pipeline)
    monkeypatch.setattr(
        handler, "emit_report_metrics", lambda *args, **kwargs: seen.append(1)
    )
    monkeypatch.setattr(
        handler, "emit_failure_metrics", lambda **kwargs: seen.append(2)
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

    assert seen == [1, 2]
    failure = next(iter(pipeline.store.failures.values()))
    assert failure["failure_attempt_count"] == 1
    assert failure["retry_exhausted"] is False


def test_third_failed_delivery_is_journaled_and_emitted_as_exhausted(monkeypatch):
    class FailedPipeline(FakePipeline):
        def run(self, *, slot_anchor_utc=None):
            raise RuntimeError("provider_request_failed")

    pipeline = FailedPipeline()
    emitted = []
    monkeypatch.setattr(handler, "_pipeline", pipeline)
    monkeypatch.setattr(
        handler, "emit_failure_metrics", lambda **kwargs: emitted.append(kwargs)
    )
    event = {
        "id": "scheduled-delivery-1",
        "time": "2026-07-22T10:00:00Z",
        "sport": "tennis",
    }

    for _ in range(3):
        with pytest.raises(RuntimeError, match="provider_request_failed"):
            handler.lambda_handler(event, None)

    failure = pipeline.store.failures[
        ("2026-07-22T10:00:00+00:00", "scheduled-delivery-1")
    ]
    assert failure["failure_attempt_count"] == 3
    assert failure["retry_exhausted"] is True
    assert emitted[-1]["failure_attempt_count"] == 3
    assert emitted[-1]["retry_exhausted"] is True


def test_successful_retry_marks_prior_failure_recovered(monkeypatch):
    class RecoveringPipeline(FakePipeline):
        def run(self, *, slot_anchor_utc=None):
            self.run_count += 1
            if self.run_count == 1:
                raise RuntimeError("provider_request_failed")
            return {"ok": True, "retry_required": False, "slate_runs": []}

    pipeline = RecoveringPipeline()
    monkeypatch.setattr(handler, "_pipeline", pipeline)
    monkeypatch.setattr(handler, "emit_failure_metrics", lambda **kwargs: kwargs)
    event = {
        "id": "scheduled-delivery-2",
        "time": "2026-07-22T10:00:00Z",
        "sport": "tennis",
    }

    with pytest.raises(RuntimeError, match="provider_request_failed"):
        handler.lambda_handler(event, None)
    report = handler.lambda_handler(event, None)

    assert report["ok"] is True
    assert len(pipeline.store.recoveries) == 1
