from hello_world.arb_engine import american_to_decimal, scan_all, scan_market


def test_american_to_decimal():
    assert round(american_to_decimal(-110), 4) == 1.9091
    assert american_to_decimal(100) == 2.0
    assert american_to_decimal(150) == 2.5


def test_two_way_arb_detected():
    row = scan_market(
        market_id="demo-h2h",
        event="NYM @ NYY",
        market="h2h",
        quotes=[
            {"outcome": "NYM", "book": "fanduel", "american": 120},
            {"outcome": "NYY", "book": "draftkings", "american": -105},
            {"outcome": "NYM", "book": "betmgm", "american": 105},
        ],
        bankroll=1000,
    )
    assert row is not None
    assert row["arb"] is True
    assert row["best"]["NYM"]["book"] == "fanduel"
    assert row["best"]["NYY"]["american"] == -105
    assert abs(sum(row["stakes"].values()) - 1000) < 0.05
    assert row["guaranteed_profit"] > 0


def test_hold_is_not_arb():
    row = scan_market(
        market_id="hold",
        event="BOS @ NYY",
        market="h2h",
        quotes=[
            {"outcome": "BOS", "book": "a", "american": -130},
            {"outcome": "NYY", "book": "b", "american": 110},
        ],
    )
    assert row is not None
    assert row["arb"] is False
    assert row["sum_implied"] > 1


def test_scan_all_sorts_hits():
    payload = {
        "bankroll": 500,
        "events": [
            {
                "id": "g1",
                "event": "A @ B",
                "market": "h2h",
                "quotes": [
                    {"outcome": "A", "book": "x", "american": 130},
                    {"outcome": "B", "book": "y", "american": -110},
                ],
            },
            {
                "id": "g2",
                "event": "C @ D",
                "market": "h2h",
                "quotes": [
                    {"outcome": "C", "book": "x", "american": -150},
                    {"outcome": "D", "book": "y", "american": 120},
                ],
            },
        ],
    }
    out = scan_all(payload)
    assert out["ok"] is True
    assert out["places_bets"] is False
    assert out["n_arbs"] == 1
    assert out["hits"][0]["event"] == "A @ B"
