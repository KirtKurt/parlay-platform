from __future__ import annotations

from types import SimpleNamespace

from hello_world import mlb_fundamentals_lock_authority_v1 as authority


def _bridge():
    return SimpleNamespace(_vector=lambda row: row.get("frozenFeatureVector") or {})


def test_uses_existing_locked_card_audit_authority() -> None:
    bridge = _bridge()
    authority.install(bridge)
    row = {"lockedCardAudit": {"lockAtUtc": "2026-09-11T17:35:00+00:00"}}
    assert bridge._lock_at(row) == "2026-09-11T17:35:00+00:00"


def test_uses_existing_last_possible_prediction_gate_authority() -> None:
    bridge = _bridge()
    authority.install(bridge)
    row = {
        "lastPossiblePredictionGate": {
            "lockAtUtc": "2026-09-11T17:35:00+00:00"
        }
    }
    assert bridge._lock_at(row) == "2026-09-11T17:35:00+00:00"


def test_signed_vector_remains_highest_priority() -> None:
    bridge = _bridge()
    authority.install(bridge)
    row = {
        "frozenFeatureVector": {"lockAtUtc": "2026-09-11T17:30:00+00:00"},
        "lockedCardAudit": {"lockAtUtc": "2026-09-11T17:35:00+00:00"},
        "lastPossiblePredictionGate": {"lockAtUtc": "2026-09-11T17:40:00+00:00"},
    }
    assert bridge._lock_at(row) == "2026-09-11T17:30:00+00:00"


def test_missing_lock_authority_stays_missing_and_is_not_derived_from_commence() -> None:
    bridge = _bridge()
    authority.install(bridge)
    row = {"commenceTime": "2026-09-11T18:20:00+00:00"}
    assert bridge._lock_at(row) is None


def test_install_is_idempotent() -> None:
    bridge = _bridge()
    authority.install(bridge)
    first = bridge._lock_at
    authority.install(bridge)
    assert bridge._lock_at is first
