from __future__ import annotations

import json
from decimal import Decimal

import soccer_auto.collector as collector


class _Ops:
    def __init__(self, item):
        self.item = item

    def get_item(self, **kwargs):
        assert kwargs["Key"] == {"PK": "QUOTA_STATE", "SK": "LATEST"}
        assert kwargs["ConsistentRead"] is True
        return {"Item": self.item}


class _QuotaStore:
    def __init__(self, item):
        self.ops = _Ops(item)


def test_provider_quota_snapshot_is_read_only_and_blocks_zero_or_negative_remaining():
    zero = collector._provider_quota_snapshot(
        _QuotaStore(
            {
                "remaining": Decimal("0"),
                "used": Decimal("5000000"),
                "observed_at": "2026-08-15T00:00:00Z",
            }
        )
    )
    assert zero == {
        "known": True,
        "exhausted": True,
        "remaining": 0,
        "used": 5000000,
        "observed_at": "2026-08-15T00:00:00Z",
    }

    negative = collector._provider_quota_snapshot(
        _QuotaStore({"remaining": Decimal("-37"), "used": Decimal("5000037")})
    )
    assert negative["exhausted"] is True
    assert negative["remaining"] == -37


def test_provider_quota_snapshot_fails_open_when_observation_is_missing():
    snapshot = collector._provider_quota_snapshot(_QuotaStore({}))
    assert snapshot["known"] is False
    assert snapshot["exhausted"] is False


class _WorkerStore:
    def __init__(self):
        self.enqueued = []
        self.marked = []

    def enqueue(self, job, **kwargs):
        self.enqueued.append((job, kwargs))

    def mark_dispatched(
        self,
        event_key,
        observed_at,
        *,
        schedule_revision,
        schedule_identity_value,
    ):
        self.marked.append(
            {
                "event_key": event_key,
                "observed_at": observed_at,
                "schedule_revision": schedule_revision,
                "schedule_identity": schedule_identity_value,
            }
        )
        return True


def test_worker_retires_quota_blocked_discovery_and_rearms_dispatch(monkeypatch):
    store = _WorkerStore()
    monkeypatch.setattr(collector, "SoccerStore", lambda: store)
    monkeypatch.setattr(collector, "_client", lambda: object())

    def _deferred(job, *, store, client):
        raise collector.ProviderBudgetDeferred(
            {
                "event_key": "event-1",
                "deferred": True,
                "reason": "SHARED_SUBSCRIPTION_RESERVE_REACHED",
                "external_capacity": True,
            }
        )

    monkeypatch.setattr(collector, "process_job", _deferred)
    job = {
        "version": collector.JOB_VERSION,
        "action": "DISCOVER_EVENT",
        "cadence_seconds": 300,
        "event": {
            "event_key": "event-1",
            "event_id": "provider-1",
            "sport_key": "soccer_epl",
            "commence_time": "2026-08-15T04:00:00Z",
            "home_team": "A",
            "away_team": "B",
            "schedule_revision": 3,
            "schedule_identity": "stable-identity",
        },
    }
    result = collector.worker_handler(
        {
            "Records": [
                {
                    "messageId": "m1",
                    "body": json.dumps(job),
                    "attributes": {"ApproximateReceiveCount": "8"},
                }
            ]
        },
        None,
    )

    assert result["batchItemFailures"] == []
    assert store.enqueued == []
    assert len(store.marked) == 1
    assert store.marked[0]["event_key"] == "event-1"
    assert store.marked[0]["schedule_revision"] == 3
    processed = result["processed"][0]
    assert processed["retry_via_dispatcher"] is True
    assert processed["retry_reenqueued"] is False
    assert processed["dispatch_rearmed"] is True
