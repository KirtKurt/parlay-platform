from __future__ import annotations

import copy
from datetime import datetime, timezone
from types import SimpleNamespace

from hello_world import mlb_historical_incremental_range_extension_v1 as patch


class Handler:
    END_DATE = "2026-12-31"
    MAX_CREDITS = 300000
    QUOTA_RESERVE = 100
    ESTIMATED_CREDITS_PER_HISTORICAL_REQUEST = 10

    class OrchestrationError(RuntimeError):
        pass

    class optimizer:
        @staticmethod
        def _parse_dt(value):
            return value

        @staticmethod
        def build_snapshot_grid(day, starts):
            return SimpleNamespace(
                timestamps_utc=[f"{day}T01:00:00Z", f"{day}T01:15:00Z"],
                first_game_start_utc=starts[0],
                last_game_start_utc=starts[-1],
            )

    def __init__(self, state, finals):
        self.state = copy.deepcopy(state)
        self.finals = finals
        self.calls = []

    def _load_state(self):
        return copy.deepcopy(self.state)

    def _save_state(self, value):
        self.state = copy.deepcopy(value)
        return value

    def _load_or_fetch_finals(self, day):
        self.calls.append(day)
        value = self.finals[day]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value), {}

    def _quota_status(self):
        return {"x-requests-remaining": 1_000_000}

    @staticmethod
    def _now_iso():
        return "2026-07-28T03:40:00+00:00"

    @staticmethod
    def _plan_fingerprint(plan):
        return f"fp-{plan['endDate']}-{len(plan['slates'])}"


class Base:
    COMPETITIVE_GAME_TYPES = frozenset({"R", "F", "D", "L", "W"})

    def __init__(self, handler):
        self.optimizer_handler = handler

    @staticmethod
    def _truthy(_name):
        return True

    @staticmethod
    def _competitive_extension_start():
        from datetime import date

        return date.fromisoformat("2026-03-25")

    @staticmethod
    def _recalculate_plan(plan):
        plan["plannedThroughDate"] = plan["slates"][-1]["slateDateEt"]
        plan["plannedOfficialGames"] = sum(row["officialGameCount"] for row in plan["slates"])
        plan["maximumAuthorizedOfficialGames"] = plan["plannedOfficialGames"]
        plan["plannedCompleteSlateDays"] = len(plan["slates"])
        plan["historicalRequestCount"] = sum(row["historicalRequestCount"] for row in plan["slates"])
        plan["estimatedCredits"] = sum(row["estimatedCredits"] for row in plan["slates"])
        plan["slateLedgerDigest"] = "ledger"


def state():
    return {
        "phase": "DATA_RANGE_EXHAUSTED",
        "endDate": "2026-07-24",
        "currentDate": "2026-07-25",
        "currentSlotIndex": 0,
        "paidBackfillAuthorized": True,
        "creditsConsumed": 254580,
        "maximumCredits": 300000,
        "plan": {
            "endDate": "2026-07-24",
            "slates": [
                {
                    "slateDateEt": "2026-07-24",
                    "officialGameCount": 1,
                    "historicalRequestCount": 2,
                    "estimatedCredits": 20,
                }
            ],
        },
    }


def test_settled_horizon_uses_eastern_previous_date():
    now = datetime(2026, 7, 28, 3, 43, tzinfo=timezone.utc)
    assert patch.settled_horizon(now).isoformat() == "2026-07-26"


def test_extension_stops_at_settled_horizon_not_future_ceiling(monkeypatch):
    finals = {
        "2026-07-25": {
            "officialGameCount": 1,
            "games": [{"gameDate": "2026-07-25T20:00:00Z"}],
        },
        "2026-07-26": {"officialGameCount": 0, "games": []},
    }
    handler = Handler(state(), finals)
    base = Base(handler)
    monkeypatch.setattr(patch, "settled_horizon", lambda: __import__("datetime").date(2026, 7, 26))

    patch.install(base)
    base._append_authorized_range_extension()

    assert handler.calls == ["2026-07-25", "2026-07-26"]
    assert handler.state["endDate"] == "2026-07-26"
    assert handler.state["phase"] == "BACKFILLING"
    assert handler.state["lastError"] is None
    assert handler.state["rangeExtension"]["configuredCeilingDate"] == "2026-12-31"
    assert handler.state["rangeExtension"]["appendedSlateCount"] == 1
    assert handler.state["plan"]["slates"][-1]["slateDateEt"] == "2026-07-25"


def test_unsettled_boundary_is_deferred_without_discarding_proven_date(monkeypatch):
    finals = {
        "2026-07-25": {
            "officialGameCount": 1,
            "games": [{"gameDate": "2026-07-25T20:00:00Z"}],
        },
        "2026-07-26": RuntimeError("official slate is not fully final"),
    }
    handler = Handler(state(), finals)
    base = Base(handler)
    monkeypatch.setattr(patch, "settled_horizon", lambda: __import__("datetime").date(2026, 7, 27))

    patch.install(base)
    base._append_authorized_range_extension()

    assert handler.calls == ["2026-07-25", "2026-07-26"]
    assert handler.state["endDate"] == "2026-07-25"
    assert handler.state["phase"] == "BACKFILLING"
    assert handler.state["lastError"] is None
    assert handler.state["rangeExtensionNextRetryDate"] == "2026-07-26"
    assert handler.state["rangeExtensionDeferredDates"][0]["classification"] == "NOT_YET_PROVABLY_SETTLED"


def test_waiting_phase_resumes_directly_when_horizon_advances(monkeypatch):
    waiting = state()
    waiting["phase"] = patch.WAITING_PHASE
    waiting["settledHorizonWait"] = {
        "authorizedThroughDate": "2026-07-24",
        "settledHorizonDate": "2026-07-24",
        "nextEligibleSlateDate": "2026-07-25",
        "blockingError": False,
    }
    finals = {
        "2026-07-25": {
            "officialGameCount": 1,
            "games": [{"gameDate": "2026-07-25T20:00:00Z"}],
        }
    }
    handler = Handler(waiting, finals)
    base = Base(handler)
    monkeypatch.setattr(
        patch,
        "settled_horizon",
        lambda: __import__("datetime").date(2026, 7, 25),
    )

    patch.install(base)
    base._append_authorized_range_extension()

    assert handler.calls == ["2026-07-25"]
    assert handler.state["endDate"] == "2026-07-25"
    assert handler.state["phase"] == "BACKFILLING"
    assert handler.state["lastError"] is None
    assert handler.state["rangeExtension"]["version"] == patch.VERSION


def test_waiting_phase_does_not_cross_unsettled_horizon(monkeypatch):
    waiting = state()
    waiting["phase"] = patch.WAITING_PHASE
    handler = Handler(waiting, {})
    base = Base(handler)
    monkeypatch.setattr(
        patch,
        "settled_horizon",
        lambda: __import__("datetime").date(2026, 7, 24),
    )

    patch.install(base)
    base._append_authorized_range_extension()

    assert handler.calls == []
    assert handler.state == waiting
