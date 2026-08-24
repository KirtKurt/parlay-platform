from nfl_auto.config import Settings
from nfl_auto.historical import historical_quota_admitted


class Store:
    def __init__(self, state):
        self._state = state

    def state_get(self, pk, sk="CURRENT"):
        return self._state


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
        shared_quota_reserve_percent=20,
        quota_race_buffer_credits=2000,
    )


def test_historical_backfill_stops_at_shared_quota_reserve() -> None:
    admitted, quota = historical_quota_admitted(
        Store(
            {
                "last_transport": {
                    "requests_remaining": "15000",
                    "requests_used": "85000",
                }
            }
        ),
        settings(),
    )
    assert not admitted
    assert quota["reserve"] == 22000


def test_historical_backfill_runs_above_reserve() -> None:
    admitted, quota = historical_quota_admitted(
        Store(
            {
                "last_transport": {
                    "requests_remaining": "50000",
                    "requests_used": "50000",
                }
            }
        ),
        settings(),
    )
    assert admitted
    assert quota["reserve"] == 22000
