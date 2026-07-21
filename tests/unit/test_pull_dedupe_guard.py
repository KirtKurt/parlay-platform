#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import pull_dedupe_guard


class ConditionalFailure(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
        super().__init__("conditional failure")


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, *, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if key in self.items and ConditionExpression:
            raise ConditionalFailure()
        self.items[key] = dict(Item)
        return {}

    def delete_item(self, *, Key):
        self.items.pop((Key["PK"], Key["SK"]), None)
        return {}


def _history(table):
    calls = []
    stored = []
    module = SimpleNamespace()
    module.PULLS = table
    module.sport_key = lambda value: str(value or "").lower()
    module.slate_date = lambda value: "2026-07-20"
    module.query_pulls = lambda sport, slate, limit: calls.append(limit) or list(stored)

    def original(body):
        stored.append(dict(body))
        return {"ok": True, "stored": {"pk": "pk", "sk": "sk", "pull_id": "id"}, "pull": body}

    module.store_pull = original
    module.calls = calls
    module.stored = stored
    return module


def main() -> int:
    table = FakeTable()
    history = _history(table)
    pull_dedupe_guard.apply(history)

    first = history.store_pull({
        "sport": "mlb",
        "slate_date": "2026-07-20",
        "pulled_at": "2026-07-20T16:01:00+00:00",
        "games": [{"id": "a"}],
    })
    assert first.get("ok") is True and first.get("deduped") is not True
    assert first.get("slotClaim", {}).get("claimed") is True
    assert len(history.stored) == 1

    duplicate = history.store_pull({
        "sport": "mlb",
        "slate_date": "2026-07-20",
        "pulled_at": "2026-07-20T16:14:59+00:00",
        "games": [{"id": "a"}],
    })
    assert duplicate.get("deduped") is True
    assert len(history.stored) == 1

    next_slot = history.store_pull({
        "sport": "mlb",
        "slate_date": "2026-07-20",
        "pulled_at": "2026-07-20T16:15:00+00:00",
        "games": [{"id": "a"}],
    })
    assert next_slot.get("ok") is True and next_slot.get("deduped") is not True
    assert len(history.stored) == 2

    # When atomic slot markers are unavailable, the fallback must inspect the
    # latest row, not the first three rows of the day.
    fallback = _history(None)
    fallback.stored.extend([
        {"pulled_at": "2026-07-20T05:00:00+00:00"},
        {"pulled_at": "2026-07-20T16:31:00+00:00"},
    ])
    pull_dedupe_guard.apply(fallback)
    result = fallback.store_pull({
        "sport": "mlb",
        "slate_date": "2026-07-20",
        "pulled_at": "2026-07-20T16:44:00+00:00",
        "games": [{"id": "a"}],
    })
    assert result.get("deduped") is True
    assert fallback.calls == [500], fallback.calls
    assert len(fallback.stored) == 2

    print("Atomic MLB quarter-hour pull-slot dedupe verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
