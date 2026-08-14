from __future__ import annotations

import unittest

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.storage import SoccerStore  # noqa: E402


class Ops:
    def __init__(self):
        self.rows = []

    def put_item(self, *, Item, **kwargs):
        self.rows.append(Item)

    def query(self, **kwargs):
        return {"Items": list(self.rows)}


class InventoryUnionTests(unittest.TestCase):
    def test_parallel_market_observations_union_without_read_modify_write(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = Ops()
        event_key = "EVENT#soccer_test#event-1"
        store.put_market_inventory(
            event_key,
            {"book": {"title": "Book", "regions": ["us"], "markets": ["h2h"]}},
            "2026-08-14T04:00:00.000001Z",
        )
        store.put_market_inventory(
            event_key,
            {"book": {"title": "Book", "regions": ["uk"], "markets": ["totals"]}},
            "2026-08-14T04:00:00.000002Z",
        )
        union = store.cumulative_market_inventory(
            event_key,
            observed_at="2026-08-14T05:00:00Z",
        )
        self.assertEqual(union["book"]["markets"], ["h2h", "totals"])
        self.assertEqual(union["book"]["regions"], ["uk", "us"])

    def test_pair_ages_out_when_it_is_not_reobserved(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = Ops()
        event_key = "EVENT#soccer_test#event-1"
        store.put_market_inventory(
            event_key,
            {"old_book": {"title": "Old", "regions": ["uk"], "markets": ["totals"]}},
            "2026-08-13T03:59:59Z",
        )
        store.put_market_inventory(
            event_key,
            {"current_book": {"title": "Current", "regions": ["uk"], "markets": ["h2h"]}},
            "2026-08-14T04:00:00Z",
        )
        union = store.cumulative_market_inventory(
            event_key,
            observed_at="2026-08-14T04:00:00Z",
        )
        self.assertEqual(set(union), {"current_book"})


if __name__ == "__main__":
    unittest.main()
