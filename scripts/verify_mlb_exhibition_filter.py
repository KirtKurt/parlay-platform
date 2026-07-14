#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.pop("SNAPSHOTS_TABLE", None)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hello_world"))

import inqsi_pull_history as history  # noqa: E402


class FakeTable:
    def __init__(self, pull):
        self.pull = pull

    def query(self, **_kwargs):
        return {"Items": [{"data": self.pull}]}


def game(game_id: str, away: str, home: str, league: str = "MLB"):
    return {
        "game_id": game_id,
        "game_key": f"mlb|2026-07-14|{away.lower()}|{home.lower()}",
        "away_team": away,
        "home_team": home,
        "league": league,
        "commence_time": "2026-07-15T00:01:00Z",
        "books": {"fanduel": {"ml": {"home": -120, "away": 105}}},
    }


def main() -> None:
    all_star = game("all-star", "American League", "National League", "MLB All-Star Game")
    regular = game("regular", "Boston Red Sox", "New York Yankees")
    pull = {
        "pull_id": "test-pull",
        "sport": "mlb",
        "slate_date": "2026-07-14",
        "pulled_at": "2026-07-14T05:00:00Z",
        "games": [all_star, regular],
        "meta": {"architecture": "15_min_pull_history"},
    }

    history.PULLS = FakeTable(pull)
    filtered = history.query_pulls("mlb", "2026-07-14")
    assert len(filtered) == 1
    assert [row["game_id"] for row in filtered[0]["games"]] == ["regular"]
    assert filtered[0]["meta"]["excludedNonModelGameCount"] == 1
    assert "ALL_STAR" in filtered[0]["meta"]["excludedNonModelGamePolicy"]

    history.PULLS = FakeTable(pull)
    untouched = history.query_pulls("nba", "2026-07-14")
    assert len(untouched[0]["games"]) == 2

    assert history.mlb_model_eligible_game(regular) is True
    assert history.mlb_model_eligible_game(all_star) is False
    assert history.mlb_model_eligible_game(game("marker", "Team A", "Team B", "All Star Exhibition")) is False

    print("MLB exhibition filtering verification passed")


if __name__ == "__main__":
    main()
