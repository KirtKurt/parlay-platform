import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
MODULE_PATH = ROOT / "ops" / "coverage_snapshot.py"
spec = importlib.util.spec_from_file_location("coverage_snapshot", MODULE_PATH)
coverage_snapshot = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
sys.modules["coverage_snapshot"] = coverage_snapshot
spec.loader.exec_module(coverage_snapshot)


def test_build_rows_keeps_support_and_verification_separate():
    catalog = {"sports": [{"key": "baseball_mlb", "title": "MLB", "group": "Baseball", "active": True}]}
    sportsbooks = {
        "complete": True,
        "regions": "us,us2",
        "markets": "h2h,spreads,totals",
        "sportsbooks": [{"key": "draftkings", "title": "DraftKings", "sports": ["baseball_mlb"]}],
    }
    rules = {
        "rules": [{
            "reviewed": True,
            "book": "draftkings",
            "sport": "baseball",
            "jurisdiction": "ny",
            "market_family": "winner",
            "source": "https://example.test/rules",
            "version": "1",
            "settlement_profile": "mlb-v1",
        }]
    }
    rows = coverage_snapshot.build_rows(catalog, sportsbooks, rules, now_ms=123)
    sport_rows = [r for r in rows if r.get("book", "*") == "*"]
    book_rows = [r for r in rows if r.get("book") == "draftkings" and r.get("market_family", "*") == "*"]
    rule_rows = [r for r in rows if r.get("market_family") == "winner"]
    assert len(sport_rows) == 1 and sport_rows[0]["provider_support"] == "SUPPORTED"
    assert len(book_rows) == 1 and book_rows[0]["settlement_verification"] == "RULES_PENDING"
    assert len(rule_rows) == 1 and rule_rows[0]["settlement_verification"] == "VERIFIED"
    assert rule_rows[0]["jurisdiction"] == "ny"
