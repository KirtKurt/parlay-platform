from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _non_negative_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 0:
        raise RuntimeError(f"{name} must be non-negative")
    return value


@dataclass(frozen=True)
class TennisConfig:
    odds_api_key: str
    snapshots_table: str
    signals_table: str
    archive_bucket: str = ""
    pull_interval_minutes: int = 15
    lead_hours: int = 8
    slate_timezone: str = "America/New_York"
    regions: str = "us"
    markets: str = "h2h"
    odds_format: str = "american"
    include_doubles: bool = False
    discovery_horizon_hours: int = 48
    provider_timeout_seconds: int = 12
    max_parallel_requests: int = 4
    min_signal_pulls: int = 2
    min_common_books: int = 2
    min_publish_pulls: int = 12
    min_publish_books: int = 3
    max_book_price_age_seconds: int = 900
    max_book_future_skew_seconds: int = 60
    quota_protected_reserve: int = 250
    quota_daily_budget: int = 20_000
    require_quota_headers: bool = True
    slot_lease_seconds: int = 300
    metrics_namespace: str = "Inqsi/TennisCollector"
    model_state: str = "RULE_BASED_SHADOW"

    @classmethod
    def from_env(cls) -> "TennisConfig":
        interval = _positive_int("TENNIS_PULL_INTERVAL_MINUTES", 15)
        if interval != 15:
            raise RuntimeError("TENNIS_PULL_INTERVAL_MINUTES must remain 15 for v0")
        return cls(
            odds_api_key=os.environ.get("ODDS_API_KEY", "").strip(),
            snapshots_table=os.environ.get("TENNIS_SNAPSHOTS_TABLE", "").strip(),
            signals_table=os.environ.get("TENNIS_SIGNALS_TABLE", "").strip(),
            archive_bucket=os.environ.get("TENNIS_ARCHIVE_BUCKET", "").strip(),
            pull_interval_minutes=interval,
            lead_hours=_positive_int("TENNIS_PULL_LEAD_HOURS", 8),
            slate_timezone=os.environ.get(
                "TENNIS_SLATE_TIMEZONE", "America/New_York"
            ).strip(),
            regions=os.environ.get("TENNIS_ODDS_REGIONS", "us").strip(),
            markets=os.environ.get("TENNIS_ODDS_MARKETS", "h2h").strip(),
            odds_format=os.environ.get("TENNIS_ODDS_FORMAT", "american").strip(),
            include_doubles=_boolean("TENNIS_INCLUDE_DOUBLES", False),
            discovery_horizon_hours=_positive_int("TENNIS_DISCOVERY_HORIZON_HOURS", 48),
            provider_timeout_seconds=_positive_int(
                "TENNIS_PROVIDER_TIMEOUT_SECONDS", 12
            ),
            max_parallel_requests=_positive_int("TENNIS_MAX_PARALLEL_REQUESTS", 4),
            min_signal_pulls=_positive_int("TENNIS_MIN_SIGNAL_PULLS", 2),
            min_common_books=_positive_int("TENNIS_MIN_COMMON_BOOKS", 2),
            min_publish_pulls=_positive_int("TENNIS_MIN_PUBLISH_PULLS", 12),
            min_publish_books=_positive_int("TENNIS_MIN_PUBLISH_BOOKS", 3),
            max_book_price_age_seconds=_positive_int(
                "TENNIS_MAX_BOOK_PRICE_AGE_SECONDS", 900
            ),
            max_book_future_skew_seconds=_non_negative_int(
                "TENNIS_MAX_BOOK_FUTURE_SKEW_SECONDS", 60
            ),
            quota_protected_reserve=_non_negative_int(
                "TENNIS_QUOTA_PROTECTED_RESERVE", 250
            ),
            quota_daily_budget=_positive_int("TENNIS_QUOTA_DAILY_BUDGET", 20_000),
            require_quota_headers=_boolean("TENNIS_REQUIRE_QUOTA_HEADERS", True),
            slot_lease_seconds=_positive_int("TENNIS_SLOT_LEASE_SECONDS", 300),
            metrics_namespace=os.environ.get(
                "TENNIS_METRICS_NAMESPACE", "Inqsi/TennisCollector"
            ).strip(),
            model_state=os.environ.get(
                "TENNIS_MODEL_STATE", "RULE_BASED_SHADOW"
            ).strip(),
        )

    def validate_runtime(self) -> None:
        if not self.odds_api_key:
            raise RuntimeError("ODDS_API_KEY is required")
        if not self.snapshots_table:
            raise RuntimeError("TENNIS_SNAPSHOTS_TABLE is required")
        if not self.signals_table:
            raise RuntimeError("TENNIS_SIGNALS_TABLE is required")
        if not self.archive_bucket:
            raise RuntimeError("TENNIS_ARCHIVE_BUCKET is required")
        if self.lead_hours != 8:
            raise RuntimeError("TENNIS_PULL_LEAD_HOURS must remain 8 for v0")
        if self.provider_timeout_seconds > 30:
            raise RuntimeError("TENNIS_PROVIDER_TIMEOUT_SECONDS must be at most 30")
        if self.max_parallel_requests > 8:
            raise RuntimeError("TENNIS_MAX_PARALLEL_REQUESTS must be at most 8")
        if self.max_book_price_age_seconds > 3600:
            raise RuntimeError("TENNIS_MAX_BOOK_PRICE_AGE_SECONDS must be at most 3600")
        if self.max_book_future_skew_seconds > 300:
            raise RuntimeError(
                "TENNIS_MAX_BOOK_FUTURE_SKEW_SECONDS must be at most 300"
            )
        if self.quota_daily_budget <= self.quota_protected_reserve:
            raise RuntimeError(
                "TENNIS_QUOTA_DAILY_BUDGET must exceed TENNIS_QUOTA_PROTECTED_RESERVE"
            )
        if not 241 <= self.slot_lease_seconds <= 840:
            raise RuntimeError("TENNIS_SLOT_LEASE_SECONDS must be between 241 and 840")
        if not self.metrics_namespace:
            raise RuntimeError("TENNIS_METRICS_NAMESPACE is required")
        if self.markets != "h2h":
            raise RuntimeError("Tennis v0 only accepts the pre-match h2h market")
        if self.odds_format != "american":
            raise RuntimeError("Tennis v0 signal math requires american odds")
        if self.model_state != "RULE_BASED_SHADOW":
            raise RuntimeError("Tennis v0 must remain RULE_BASED_SHADOW")
