"""Configuration and hard safety boundaries for the isolated NFL pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

SPORT_KEY: Final = "americanfootball_nfl"
BBD_ALLOWED_GAME_TYPES: Final = ("REG", "POST")
BBD_FORBIDDEN_GAME_TYPES: Final = ("PRE",)
HISTORICAL_SEASONS: Final = tuple(range(2020, 2026))
LIVE_SEASON: Final = 2026

# The live collector is allowed to start at 00:00 America/New_York on the date
# of the first 2026 regular-season game. Publication remains locked to T-10.
LIVE_COLLECTION_START_UTC: Final = "2026-09-09T04:00:00Z"
PUBLIC_DECISION_HORIZON_MINUTES: Final = 10
HISTORICAL_SNAPSHOT_HORIZONS_MINUTES: Final = (1440, 60, 10)
ODDS_REGIONS: Final = "us"
ODDS_MARKETS: Final = ("h2h", "spreads", "totals")
TARGETS: Final = ("moneyline_home_win", "spread_home_cover", "total_over")

DEFAULT_MIN_BOOKMAKERS: Final = 3
DEFAULT_MIN_TRAINING_ROWS: Final = 650
DEFAULT_MIN_VALIDATION_ROWS: Final = 180
DEFAULT_MIN_AUDIT_ROWS: Final = 180
DEFAULT_MAX_AUDIT_ECE: Final = 0.07
DEFAULT_ODDS_TIMEOUT_SECONDS: Final = 15
DEFAULT_BBD_TIMEOUT_SECONDS: Final = 20
DEFAULT_HTTP_ATTEMPTS: Final = 4
DEFAULT_BBD_GAMES_PER_TICK: Final = 1
DEFAULT_ODDS_GAMES_PER_TICK: Final = 1


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    odds_secret_arn: str
    bbd_secret_arn: str
    state_table: str
    games_table: str
    odds_table: str
    features_table: str
    predictions_table: str
    models_table: str
    ops_table: str
    raw_bucket: str
    artifact_bucket: str
    aws_region: str
    live_collection_start_utc: str = LIVE_COLLECTION_START_UTC
    historical_backfill_enabled: bool = True
    min_bookmakers: int = DEFAULT_MIN_BOOKMAKERS
    min_training_rows: int = DEFAULT_MIN_TRAINING_ROWS
    min_validation_rows: int = DEFAULT_MIN_VALIDATION_ROWS
    min_audit_rows: int = DEFAULT_MIN_AUDIT_ROWS
    min_live_rows_for_adaptation: int = 144
    min_live_validation_rows: int = 48
    min_live_audit_rows: int = 48
    max_audit_ece: float = DEFAULT_MAX_AUDIT_ECE
    shared_quota_reserve_percent: float = 20.0
    quota_race_buffer_credits: int = 2000
    llm_model_id: str = "us.amazon.nova-2-lite-v1:0"

    @classmethod
    def from_env(cls) -> "Settings":
        required = {
            "odds_secret_arn": "NFL_AUTO_ODDS_SECRET_ARN",
            "bbd_secret_arn": "NFL_AUTO_BBD_SECRET_ARN",
            "state_table": "NFL_AUTO_STATE_TABLE",
            "games_table": "NFL_AUTO_GAMES_TABLE",
            "odds_table": "NFL_AUTO_ODDS_TABLE",
            "features_table": "NFL_AUTO_FEATURES_TABLE",
            "predictions_table": "NFL_AUTO_PREDICTIONS_TABLE",
            "models_table": "NFL_AUTO_MODELS_TABLE",
            "ops_table": "NFL_AUTO_OPS_TABLE",
            "raw_bucket": "NFL_AUTO_RAW_BUCKET",
            "artifact_bucket": "NFL_AUTO_ARTIFACT_BUCKET",
        }
        values: dict[str, str] = {}
        missing: list[str] = []
        for field, env_name in required.items():
            value = os.getenv(env_name, "").strip()
            if not value:
                missing.append(env_name)
            values[field] = value
        if missing:
            raise RuntimeError("NFL_AUTO_CONFIGURATION_MISSING:" + ",".join(sorted(missing)))
        return cls(
            **values,
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            live_collection_start_utc=os.getenv(
                "NFL_AUTO_LIVE_COLLECTION_START_UTC", LIVE_COLLECTION_START_UTC
            ),
            historical_backfill_enabled=env_bool(
                "NFL_AUTO_HISTORICAL_BACKFILL_ENABLED", True
            ),
            min_bookmakers=int(
                os.getenv("NFL_AUTO_MIN_BOOKMAKERS", str(DEFAULT_MIN_BOOKMAKERS))
            ),
            min_training_rows=int(
                os.getenv("NFL_AUTO_MIN_TRAINING_ROWS", str(DEFAULT_MIN_TRAINING_ROWS))
            ),
            min_validation_rows=int(
                os.getenv("NFL_AUTO_MIN_VALIDATION_ROWS", str(DEFAULT_MIN_VALIDATION_ROWS))
            ),
            min_audit_rows=int(
                os.getenv("NFL_AUTO_MIN_AUDIT_ROWS", str(DEFAULT_MIN_AUDIT_ROWS))
            ),
            min_live_rows_for_adaptation=int(
                os.getenv("NFL_AUTO_MIN_LIVE_ROWS_FOR_ADAPTATION", "144")
            ),
            min_live_validation_rows=int(
                os.getenv("NFL_AUTO_MIN_LIVE_VALIDATION_ROWS", "48")
            ),
            min_live_audit_rows=int(
                os.getenv("NFL_AUTO_MIN_LIVE_AUDIT_ROWS", "48")
            ),
            max_audit_ece=float(
                os.getenv("NFL_AUTO_MAX_AUDIT_ECE", str(DEFAULT_MAX_AUDIT_ECE))
            ),
            shared_quota_reserve_percent=float(
                os.getenv("NFL_AUTO_SHARED_QUOTA_RESERVE_PERCENT", "20")
            ),
            quota_race_buffer_credits=int(
                os.getenv("NFL_AUTO_QUOTA_RACE_BUFFER_CREDITS", "2000")
            ),
            llm_model_id=os.getenv(
                "NFL_AUTO_LLM_MODEL_ID", "us.amazon.nova-2-lite-v1:0"
            ),
        )

    def live_collection_allowed(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return current >= parse_utc(self.live_collection_start_utc)
