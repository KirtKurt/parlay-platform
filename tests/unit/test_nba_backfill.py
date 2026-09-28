import json
from pathlib import Path

from nba_intelligence.backfill import devig_home, replay_row, replay_slate, sort_chronological
from nba_intelligence.contract import filter_fundamental_features

FIXTURE = Path(__file__).resolve().parents[2] / "nba_intelligence" / "fixtures" / "chronological_shadow_slate.json"


def test_odds_tokens_cannot_be_used_as_fundamentals():
    kept = filter_fundamental_features(["net_rating", "moneyline_implied", "rest_days"])
    assert kept == ("net_rating", "rest_days")


def test_devig_does_not_become_p_fundamental_without_basketball_features():
    row = replay_row(
        {
            "event_id": "x",
            "tip_utc": "2025-11-01T00:00:00Z",
            "home": "NYK",
            "away": "BOS",
            "home_american": -200,
            "away_american": 170,
            "fundamental_features": [],
        }
    )
    assert row["fundamentals_available"] is False
    assert row["p_fundamental_status"] == "UNAVAILABLE"
    assert row["p_market"] == devig_home(-200, 170)
    assert row["authority_changed"] is False


def test_fixture_slate_stays_chronological_and_does_not_call_live_odds():
    raw = json.loads(FIXTURE.read_text())
    out = replay_slate(raw)
    assert out["order"] == ["nba-fix-1", "nba-fix-2", "nba-fix-3"]
    assert out["live_odds_called"] is False
    assert out["authority_changed"] is False
    assert out["fundamentals_available_count"] == 2
    assert out["events"][1]["fundamentals_available"] is False
    assert out["ablation"]["assumed_market_helps"] is False


def test_out_of_order_rows_are_sorted_not_silently_shuffled_by_label():
    raw = json.loads(FIXTURE.read_text())
    reversed_rows = list(reversed(raw))
    ordered = sort_chronological(reversed_rows)
    assert [row["event_id"] for row in ordered] == ["nba-fix-1", "nba-fix-2", "nba-fix-3"]
