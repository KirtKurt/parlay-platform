import json

import pytest

from ks1.reporting_evidence import selected_moneylines


def row():
    return {
        "odds_event_id": "g1",
        "as_of": "2026-09-22T15:33:51Z",
        "commence_time": "2026-09-23T00:05:00Z",
        "home_team": "Texas Rangers",
        "away_team": "New York Mets",
    }


def quote(base, *, commence_time=None, home_price=-133, away_price=120):
    timestamp = "2026-09-22T15:30:00Z"
    payload = {
        "id": "g1",
        "commence_time": commence_time or base["commence_time"],
        "home_team": base["home_team"],
        "away_team": base["away_team"],
        "bookmakers": [{
            "key": "draftkings",
            "last_update": timestamp,
            "markets": [{
                "key": "h2h",
                "last_update": timestamp,
                "outcomes": [
                    {"name": base["home_team"], "price": home_price},
                    {"name": base["away_team"], "price": away_price},
                ],
            }],
        }],
    }
    return {"event_id": "g1", "as_of": "2026-09-22T15:31:00Z", "payload_json": json.dumps(payload)}


@pytest.mark.parametrize("bad_price", [50, None, "not-a-number"])
def test_retained_moneyline_requires_valid_prices_on_both_sides(bad_price):
    base = row()
    book = selected_moneylines(base, "home", [quote(base, away_price=bad_price)])["books"]["draftkings"]
    assert book == {"status": "UNAVAILABLE", "price": None}


def test_retained_moneyline_accepts_serving_start_tolerance_boundary():
    base = row()
    result = selected_moneylines(base, "home", [quote(base, commence_time="2026-09-23T00:06:30Z")])
    assert result["books"]["draftkings"]["status"] == "RETAINED_PRE_PREDICTION_QUOTE"
    assert result["books"]["draftkings"]["price"] == -133


def test_retained_moneyline_rejects_event_outside_serving_start_tolerance():
    base = row()
    result = selected_moneylines(base, "home", [quote(base, commence_time="2026-09-23T00:06:31Z")])
    assert result["status"] == "EVENT_TIME_UNVERIFIED"
    assert result["books"]["draftkings"] == {"status": "UNAVAILABLE", "price": None}
