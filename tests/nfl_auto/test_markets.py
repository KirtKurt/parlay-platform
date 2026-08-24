from nfl_auto.markets import snapshot_consensus


def event() -> dict:
    books = []
    for index, home_price in enumerate((1.80, 1.83, 1.85, 1.88)):
        books.append(
            {
                "key": f"book{index}",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Buffalo Bills", "price": home_price},
                            {"name": "Miami Dolphins", "price": 2.10 + index * 0.01},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Buffalo Bills", "price": 1.91, "point": -3.5},
                            {"name": "Miami Dolphins", "price": 1.91, "point": 3.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": 1.91, "point": 47.5},
                            {"name": "Under", "price": 1.91, "point": 47.5},
                        ],
                    },
                ],
            }
        )
    return {
        "home_team": "Buffalo Bills",
        "away_team": "Miami Dolphins",
        "bookmakers": books,
    }


def test_consensus_devigs_and_counts_books() -> None:
    result = snapshot_consensus(event())
    assert result["moneyline"]["bookmaker_count"] == 4
    assert 0.50 < result["moneyline"]["home_probability"] < 0.60
    assert result["spread"]["home_line"] == -3.5
    assert result["spread"]["home_probability"] == 0.5
    assert result["total"]["total_line"] == 47.5
    assert result["total"]["over_probability"] == 0.5
