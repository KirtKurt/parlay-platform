from datetime import datetime, timezone

import pytest

from nfl_auto.config import Settings
from nfl_auto.features import parse_bbd_game


def settings() -> Settings:
    return Settings(
        odds_secret_arn="odds",
        bbd_secret_arn="bbd",
        state_table="state",
        games_table="games",
        odds_table="odds-table",
        features_table="features",
        predictions_table="predictions",
        models_table="models",
        ops_table="ops",
        raw_bucket="raw",
        artifact_bucket="artifacts",
        aws_region="us-east-1",
    )


def test_live_collection_is_date_gated() -> None:
    cfg = settings()
    assert not cfg.live_collection_allowed(datetime(2026, 9, 9, 3, 59, 59, tzinfo=timezone.utc))
    assert cfg.live_collection_allowed(datetime(2026, 9, 9, 4, 0, 0, tzinfo=timezone.utc))


def test_preseason_game_is_rejected() -> None:
    with pytest.raises(ValueError, match="GAME_TYPE_NOT_TRAINING_ELIGIBLE"):
        parse_bbd_game(
            {
                "game_id": "2025_01_PRE_TEST",
                "season": 2025,
                "week": 1,
                "game_type": "PRE",
                "kickoff_utc": "2025-08-10T00:00:00Z",
                "home_team": "BUF",
                "away_team": "NYG",
                "home_score": 20,
                "away_score": 17,
            }
        )


def test_regular_and_postseason_games_are_eligible() -> None:
    for game_type in ("REG", "POST"):
        game = parse_bbd_game(
            {
                "game_id": f"2025_01_BUF_NYG_{game_type}",
                "season": 2025,
                "week": 1,
                "game_type": game_type,
                "kickoff_utc": "2025-09-10T00:00:00Z",
                "home_team": "New York Giants",
                "away_team": "Buffalo Bills",
                "home_score": 20,
                "away_score": 17,
            }
        )
        assert game.game_type == game_type
        assert game.home_team == "NYG"
        assert game.away_team == "BUF"
