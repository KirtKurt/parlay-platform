from __future__ import annotations

from storage import InMemoryTennisStore, slot_lease_key


def test_slot_lease_is_single_owner_releasable_and_expiry_recoverable():
    store = InMemoryTennisStore()
    slot = "2026-07-22T10:00:00+00:00"

    assert store.acquire_slot_lease(
        slot,
        owner_id="owner-a",
        acquired_at_utc=slot,
        now_epoch=100,
        lease_seconds=300,
    )
    assert not store.acquire_slot_lease(
        slot,
        owner_id="owner-b",
        acquired_at_utc=slot,
        now_epoch=200,
        lease_seconds=300,
    )
    assert not store.release_slot_lease(slot, owner_id="owner-b")
    assert store.release_slot_lease(slot, owner_id="owner-a")

    assert store.acquire_slot_lease(
        slot,
        owner_id="owner-b",
        acquired_at_utc=slot,
        now_epoch=401,
        lease_seconds=300,
    )


def test_slot_lease_key_is_tennis_only_and_slot_scoped():
    assert slot_lease_key("2026-07-22T10:00:00+00:00") == {
        "PK": "TENNIS#COLLECTOR#LEASE",
        "SK": "SLOT#2026-07-22T10:00:00+00:00",
    }
