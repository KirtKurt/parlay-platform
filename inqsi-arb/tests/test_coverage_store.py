import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import coverage_store


class FakeTable:
    def __init__(self):
        self.rows = {}

    def get_item(self, Key, ConsistentRead=False):
        return {"Item": self.rows.get((Key["pk"], Key["sk"]))} if (Key["pk"], Key["sk"]) in self.rows else {}

    def put_item(self, Item):
        self.rows[(Item["pk"], Item["sk"])] = Item
        return {}


def test_identity_is_deterministic_and_keeps_variants_separate():
    base = {"provider": "the_odds_api", "sport": "baseball_mlb", "book": "draftkings", "market": "h2h", "jurisdiction": "ny"}
    assert coverage_store.coverage_id(base) == coverage_store.coverage_id(dict(base))
    other = {**base, "jurisdiction": "nj"}
    assert coverage_store.coverage_id(base) != coverage_store.coverage_id(other)


def test_unknown_dimensions_are_explicit():
    row = coverage_store.normalize({"provider": "the_odds_api", "sport": "baseball_mlb"}, now_ms=100)
    assert row["provider_support"] == "UNKNOWN"
    assert row["settlement_verification"] == "UNKNOWN"
    assert row["first_observed_at_ms"] == 100


def test_invalid_dimension_is_rejected():
    try:
        coverage_store.normalize({"provider": "x", "sport": "y", "freshness": "PERFECT"})
    except ValueError as exc:
        assert "freshness" in str(exc)
    else:
        raise AssertionError("invalid state should fail")


def test_put_preserves_first_observation():
    table = FakeTable()
    row = {
        "provider": "the_odds_api",
        "sport": "baseball_mlb",
        "book": "draftkings",
        "market_family": "winner",
        "provider_support": "SUPPORTED",
        "subscription_access": "ENTITLED",
        "current_offering": "AVAILABLE",
        "ingestion_health": "HEALTHY",
        "parser_support": "IMPLEMENTED",
        "settlement_verification": "VERIFIED",
        "freshness": "FRESH",
    }
    first = coverage_store.put(row, table=table, now_ms=100)
    second = coverage_store.put({**row, "freshness": "STALE", "last_observed_at_ms": 200}, table=table, now_ms=200)
    assert first["first_observed_at_ms"] == 100
    assert second["first_observed_at_ms"] == 100
    assert second["last_observed_at_ms"] == 200
    assert second["freshness"] == "STALE"
