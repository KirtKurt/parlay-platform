from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal

import pytest

from hello_world import mlb_historical_state_integrity_v1 as integrity


class Handler:
    END_DATE = "2026-12-31"
    VERSION = "handler-v1"

    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.write_count = 0

    def _migrate_state(self, state):
        return copy.deepcopy(dict(state))

    def _load_state(self):
        return copy.deepcopy(self.state)

    def _save_state(self, state):
        self.write_count += 1
        value = copy.deepcopy(dict(state))
        value["version"] = self.VERSION
        value["revision"] = int(value.get("revision") or 0) + 1
        value["updatedAtUtc"] = f"write-{self.write_count}"
        self.state = value
        return copy.deepcopy(value)


class Base:
    def __init__(self, handler, *, advance=False):
        self.optimizer_handler = handler
        self.advance = advance
        self.append_calls = 0

    def _append_authorized_range_extension(self):
        self.append_calls += 1
        if self.advance:
            state = self.optimizer_handler._load_state()
            assert state["phase"] == "DATA_RANGE_EXHAUSTED"
            state["phase"] = "BACKFILLING"
            state["endDate"] = "2026-07-28"
            state["currentDate"] = "2026-07-28"
            self.optimizer_handler._save_state(state)


def state():
    return {
        "version": "handler-v1",
        "revision": 7,
        "updatedAtUtc": "old",
        "phase": "BACKFILLING",
        "endDate": "2026-07-27",
        "currentDate": "2026-07-28",
        "lastError": None,
    }


def test_identical_state_write_is_suppressed():
    handler = Handler(state())
    base = Base(handler)
    integrity.install(handler, base)

    result = handler._save_state(handler._load_state())

    assert handler.write_count == 0
    assert result["revision"] == 7
    assert result["updatedAtUtc"] == "old"


def test_mapping_none_values_match_dynamodb_omission_semantics():
    persisted = state()
    persisted.pop("lastError")
    persisted["nested"] = {"required": "value"}
    handler = Handler(persisted)
    base = Base(handler)
    integrity.install(handler, base)
    candidate = handler._load_state()
    candidate["lastError"] = None
    candidate["nested"]["optional"] = None

    result = handler._save_state(candidate)

    assert handler.write_count == 0
    assert result["revision"] == 7
    assert "lastError" not in result
    assert "optional" not in result["nested"]


def test_decimal_and_float_round_trip_are_semantically_identical():
    persisted = state()
    persisted["metrics"] = {
        "integral": Decimal("4.0"),
        "ratio": Decimal("0.125"),
    }
    handler = Handler(persisted)
    base = Base(handler)
    integrity.install(handler, base)
    candidate = handler._load_state()
    candidate["metrics"] = {"integral": 4.0, "ratio": 0.125}

    result = handler._save_state(candidate)

    assert handler.write_count == 0
    assert result["revision"] == 7


def test_none_inside_list_remains_material_like_dynamodb_null():
    persisted = state()
    persisted["values"] = [1]
    handler = Handler(persisted)
    base = Base(handler)
    integrity.install(handler, base)
    candidate = handler._load_state()
    candidate["values"] = [1, None]

    result = handler._save_state(candidate)

    assert handler.write_count == 1
    assert result["values"] == [1, None]
    assert result["revision"] == 8


def test_nonfinite_numeric_state_fails_closed():
    handler = Handler(state())
    base = Base(handler)
    integrity.install(handler, base)
    candidate = handler._load_state()
    candidate["badMetric"] = float("nan")

    with pytest.raises(
        ValueError, match="historical state contains non-finite float"
    ):
        handler._save_state(candidate)

    assert handler.write_count == 0


def test_schema_version_change_writes_once():
    old = state()
    old["version"] = "handler-v0"
    handler = Handler(old)
    base = Base(handler)
    integrity.install(handler, base)

    first = handler._save_state(handler._load_state())
    second = handler._save_state(handler._load_state())

    assert first["version"] == "handler-v1"
    assert first["revision"] == 8
    assert second["revision"] == 8
    assert handler.write_count == 1


def test_material_state_change_still_writes():
    handler = Handler(state())
    base = Base(handler)
    integrity.install(handler, base)
    changed = handler._load_state()
    changed["eligibleGameCount"] = 4006

    result = handler._save_state(changed)

    assert handler.write_count == 1
    assert result["eligibleGameCount"] == 4006
    assert result["revision"] == 8


def test_cursor_beyond_settled_range_enters_nonblocking_wait(monkeypatch):
    handler = Handler(state())
    base = Base(handler)
    monkeypatch.setattr(
        integrity.incremental_range_extension,
        "settled_horizon",
        lambda: date(2026, 7, 27),
    )
    integrity.install(handler, base)

    base._append_authorized_range_extension()
    first_revision = handler.state["revision"]
    base._append_authorized_range_extension()

    assert handler.state["phase"] == integrity.WAITING_PHASE
    assert handler.state["lastError"] is None
    assert handler.state["rangeExtensionNextRetryDate"] == "2026-07-28"
    assert handler.state["settledHorizonWait"]["blockingError"] is False
    assert (
        handler.state["settledHorizonWait"]["version"]
        == integrity.WAITING_PROOF_VERSION
    )
    assert handler.state["revision"] == first_revision
    assert handler.write_count == 1


def test_waiting_state_with_persisted_none_omission_remains_idempotent(
    monkeypatch,
):
    waiting = state()
    waiting.pop("lastError")
    waiting["phase"] = integrity.WAITING_PHASE
    waiting["rangeExtensionNextRetryDate"] = "2026-07-28"
    waiting["settledHorizonWait"] = {
        "version": integrity.WAITING_PROOF_VERSION,
        "authorizedThroughDate": "2026-07-27",
        "settledHorizonDate": "2026-07-27",
        "configuredCeilingDate": "2026-12-31",
        "nextEligibleSlateDate": "2026-07-28",
        "blockingError": False,
    }
    handler = Handler(waiting)
    base = Base(handler)
    monkeypatch.setattr(
        integrity.incremental_range_extension,
        "settled_horizon",
        lambda: date(2026, 7, 27),
    )
    integrity.install(handler, base)

    base._append_authorized_range_extension()
    base._append_authorized_range_extension()

    assert handler.write_count == 0
    assert handler.state["revision"] == 7
    assert "lastError" not in handler.state


def test_waiting_state_resumes_when_horizon_advances(monkeypatch):
    waiting = state()
    waiting["phase"] = integrity.WAITING_PHASE
    waiting["settledHorizonWait"] = {"blockingError": False}
    handler = Handler(waiting)
    base = Base(handler, advance=True)
    monkeypatch.setattr(
        integrity.incremental_range_extension,
        "settled_horizon",
        lambda: date(2026, 7, 28),
    )
    integrity.install(handler, base)

    base._append_authorized_range_extension()

    assert base.append_calls == 1
    assert handler.state["phase"] == "BACKFILLING"
    assert handler.state["endDate"] == "2026-07-28"
    assert "settledHorizonWait" not in handler.state
