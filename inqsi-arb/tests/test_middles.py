import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arb_engine import scan_all, scan_market
import middle_engine
from middle_engine import detect_middles
from provider import normalize_games


def test_total_risk_middle_is_not_labelled_arb():
    events = [{
        "id": "e1|totals|8.5", "event_id": "e1", "event": "A @ B", "market": "totals",
        "quotes": [
            {"outcome": "Over", "name": "Over", "book": "draftkings", "decimal": 1.91, "point": 8.5},
            {"outcome": "Under", "name": "Under", "book": "draftkings", "decimal": 1.91, "point": 8.5},
        ],
    }, {
        "id": "e1|totals|9.5", "event_id": "e1", "event": "A @ B", "market": "totals",
        "quotes": [
            {"outcome": "Over", "name": "Over", "book": "fanduel", "decimal": 1.91, "point": 9.5},
            {"outcome": "Under", "name": "Under", "book": "fanduel", "decimal": 1.91, "point": 9.5},
        ],
    }]
    middles = detect_middles(events, bankroll=100)
    assert middles
    row = middles[0]
    assert row["kind"] == "risk_middle"
    assert row["arb"] is False
    assert row["math_arb"] is False
    assert row["gap"] == 1.0
    assert {leg["book"] for leg in row["legs"]} == {"draftkings", "fanduel"}
