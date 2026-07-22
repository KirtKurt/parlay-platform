from __future__ import annotations

from storage import InMemoryTennisStore, invocation_failure_key


def test_failure_journal_counts_retries_marks_exhaustion_and_recovery():
    store = InMemoryTennisStore()
    slot = "2026-07-22T10:00:00+00:00"
    delivery = "scheduled-delivery-1"

    for attempt in range(1, 4):
        row = store.record_invocation_failure(
            slot,
            delivery_id=delivery,
            scheduled_at_utc=slot,
            failed_at_utc=f"2026-07-22T10:00:0{attempt}+00:00",
            request_id=f"request-{attempt}",
            error_code="provider_request_failed",
            max_attempts=3,
        )
        assert row["failure_attempt_count"] == attempt
        assert row["retry_exhausted"] is (attempt == 3)

    assert row["failure_status"] == "EXHAUSTED"
    assert row["exhausted_at_utc"] == "2026-07-22T10:00:03+00:00"

    recovered = store.resolve_invocation_failure(
        slot,
        delivery_id=delivery,
        recovered_at_utc="2026-07-22T10:00:04+00:00",
        request_id="request-4",
    )

    assert recovered is not None
    assert recovered["failure_status"] == "RECOVERED"
    assert recovered["failure_attempt_count"] == 3


def test_failure_journal_key_is_tennis_slot_and_delivery_scoped():
    assert invocation_failure_key("2026-07-22T10:00:00+00:00", "event-1") == {
        "PK": "TENNIS#COLLECTOR#FAILURE#SLOT#2026-07-22T10:00:00+00:00",
        "SK": "DELIVERY#event-1",
    }
