import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from margin_engine import (
    analyze_events,
    analyze_snapshots,
    book_market_hold,
    line_bucket,
)


def test_standard_minus_110_hold():
    row = book_market_hold(
        [
            {"outcome": "A", "book": "draftkings", "american": -110},
            {"outcome": "B", "book": "draftkings", "american": -110},
        ],
        book="draftkings",
        event="A @ B",
        market="h2h",
        sport="baseball_mlb",
    )
    assert row is not None
    assert row["n_outcomes"] == 2
    assert row["sum_implied"] == 1.04761905
    assert row["hold_pct"] == 4.7619
    assert row["margin_pct"] == -4.5455
    assert row["negative_hold"] is False


def test_plus_110_is_negative_hold_not_verified_arb():
    row = book_market_hold(
        [
            {"outcome": "A", "book": "pinnacle", "american": 110},
            {"outcome": "B", "book": "pinnacle", "american": 110},
        ],
        book="pinnacle",
        event="A @ B",
        market="h2h",
    )
    assert row is not None
    assert row["hold_pct"] == -4.7619
    assert row["margin_pct"] == 5.0
    assert row["negative_hold"] is True


def test_one_sided_book_is_skipped():
    row = book_market_hold(
        [{"outcome": "A", "book": "fanduel", "american": -150}],
        book="fanduel",
        event="A @ B",
        market="h2h",
        expected_outcomes=["A", "B"],
    )
    assert row is None


def test_three_way_missing_draw_is_skipped():
    row = book_market_hold(
        [
            {"outcome": "A", "book": "bet365", "decimal": 2.4},
            {"outcome": "B", "book": "bet365", "decimal": 3.2},
        ],
        book="bet365",
        event="A @ B",
        market="h2h",
        expected_outcomes=["A", "Draw", "B"],
    )
    assert row is None


def test_line_buckets_do_not_mix_spreads():
    assert line_bucket(-1.5) == line_bucket(1.5)
    assert line_bucket(-1.5) != line_bucket(-2.5)
    events = [
        {
            "event": "A @ B",
            "market": "spreads",
            "sport": "americanfootball_nfl",
            "quotes": [
                {"outcome": "A", "book": "draftkings", "american": -110, "point": -1.5},
                {"outcome": "B", "book": "draftkings", "american": -110, "point": 1.5},
                {"outcome": "A", "book": "draftkings", "american": -130, "point": -2.5},
                {"outcome": "B", "book": "draftkings", "american": 110, "point": 2.5},
            ],
        }
    ]
    report = analyze_events(events, sport="americanfootball_nfl")
    assert report["n_complete"] == 2
    points = sorted(row["point_bucket"][1] for row in report["tightest"] + report["juiciest"])
    assert points == [1.5, 2.5]


def test_analyze_events_ranks_sharp_book_ahead_of_juice():
    events = [
        {
            "event": "A @ B",
            "market": "h2h",
            "sport": "baseball_mlb",
            "expected_outcomes": ["A", "B"],
            "quotes": [
                {"outcome": "A", "book": "pinnacle", "american": -105},
                {"outcome": "B", "book": "pinnacle", "american": -105},
                {"outcome": "A", "book": "betmgm", "american": -115},
                {"outcome": "B", "book": "betmgm", "american": -105},
                {"outcome": "A", "book": "draftkings", "american": -110},
                {"outcome": "B", "book": "draftkings", "american": -110},
            ],
        }
    ]
    report = analyze_events(events, sport="baseball_mlb")
    books = [row["book"] for row in report["books"]]
    assert books[0] == "pinnacle"
    assert books[-1] == "betmgm" or report["books"][-1]["mean_hold_pct"] >= report["books"][0]["mean_hold_pct"]
    assert report["books"][0]["mean_hold_pct"] < report["books"][-1]["mean_hold_pct"]
    assert report["n_complete"] == 3
    assert report["n_negative_hold"] == 0
    assert report["market_families"][0]["market"] == "h2h"


def test_analyze_snapshots_combines_sports():
    report = analyze_snapshots(
        [
            {
                "sport": "baseball_mlb",
                "events": [{
                    "event": "NYY @ BOS",
                    "market": "h2h",
                    "quotes": [
                        {"outcome": "NYY", "book": "fanduel", "american": -110},
                        {"outcome": "BOS", "book": "fanduel", "american": -110},
                    ],
                }],
            },
            {
                "sport": "basketball_nba",
                "events": [{
                    "event": "LAL @ BOS",
                    "market": "h2h",
                    "quotes": [
                        {"outcome": "LAL", "book": "fanduel", "american": -110},
                        {"outcome": "BOS", "book": "fanduel", "american": -110},
                    ],
                }],
            },
        ]
    )
    assert report["n_complete"] == 2
    assert report["books"][0]["book"] == "fanduel"
    assert report["books"][0]["n_markets"] == 2
    assert set(report["sports"]) == {"baseball_mlb", "basketball_nba"}
