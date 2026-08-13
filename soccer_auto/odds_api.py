"""Small, observable client for The Odds API v4.

No endpoint response is silently truncated.  Every response retains provider
quota headers so the autonomous controller can diagnose coverage versus quota.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Mapping, Sequence

import boto3
from botocore.exceptions import ClientError

from .config import ALL_BOOKMAKER_REGIONS

DEFAULT_MAX_ATTEMPTS = 4


class OddsApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class OddsApiRateLimitError(OddsApiError):
    """The distributed soccer request lease could not be acquired safely."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


def provider_safety_config() -> dict[str, Any]:
    """Return bounded, non-secret shared-provider controls for health output."""
    rate = max(1, min(3, int(os.getenv("SOCCER_AUTO_ODDS_RPS_CAP", "3"))))
    return {
        "shared_quota_reserve_percent": max(
            0.0,
            min(100.0, float(os.getenv("SOCCER_AUTO_SHARED_QUOTA_RESERVE_PERCENT", "0"))),
        ),
        "quota_race_buffer_credits": max(
            0,
            int(os.getenv("SOCCER_AUTO_QUOTA_RACE_BUFFER_CREDITS", "2000")),
        ),
        "soccer_request_limit_per_second": rate,
        "burst_capacity": 1,
        "minimum_spacing_ms": int(math.ceil(1000.0 / rate)),
        "distributed_lease": True,
        "fail_closed": True,
    }


class DistributedOddsApiRateLimiter:
    """DynamoDB-backed, globally smoothed request leases for soccer_auto.

    A single compare-and-swap pointer grants one just-in-time request slot at a
    time. This avoids the edge burst possible with fixed one-second counters.
    Every process and Lambda using the isolated SoccerOpsTable sees the same
    pointer; callers wait before competing for a slot they can use immediately.
    """

    _key = {"PK": "RATE_LIMIT", "SK": "ODDS_API_REQUESTS"}

    def __init__(
        self,
        table: Any,
        *,
        requests_per_second: int = 3,
        max_wait_seconds: float = 8.0,
        max_contention_attempts: int = 128,
        clock_ms: Any = None,
        sleeper: Any = None,
    ) -> None:
        if table is None:
            raise OddsApiRateLimitError("soccer Odds API limiter table is unavailable")
        self.table = table
        # Three RPS is the configured soccer ceiling, leaving normal provider
        # capacity for the separately deployed MLB and tennis systems.
        self.requests_per_second = max(1, min(3, int(requests_per_second)))
        self.spacing_ms = int(math.ceil(1000.0 / self.requests_per_second))
        self.max_wait_ms = max(0, int(float(max_wait_seconds) * 1000))
        self.max_contention_attempts = max(1, int(max_contention_attempts))
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleeper or time.sleep

    @classmethod
    def from_environment(cls) -> "DistributedOddsApiRateLimiter":
        table_name = os.getenv("SOCCER_AUTO_OPS_TABLE", "").strip()
        if not table_name:
            raise OddsApiRateLimitError(
                "SOCCER_AUTO_OPS_TABLE is required before any Odds API request"
            )
        try:
            table = boto3.resource("dynamodb").Table(table_name)
        except Exception as exc:
            raise OddsApiRateLimitError(
                "soccer Odds API limiter could not initialize its DynamoDB table"
            ) from exc
        return cls(
            table,
            requests_per_second=int(os.getenv("SOCCER_AUTO_ODDS_RPS_CAP", "3")),
            max_wait_seconds=float(
                os.getenv("SOCCER_AUTO_ODDS_RATE_LIMIT_MAX_WAIT_SECONDS", "8")
            ),
            max_contention_attempts=int(
                os.getenv("SOCCER_AUTO_ODDS_RATE_LIMIT_MAX_CONTENTION_ATTEMPTS", "128")
            ),
        )

    @staticmethod
    def _conditional_failure(exc: BaseException) -> bool:
        return (
            isinstance(exc, ClientError)
            and (exc.response.get("Error") or {}).get("Code")
            == "ConditionalCheckFailedException"
        )

    def _record_block(self, operation: str, reason: str, observed_ms: int) -> None:
        observed = datetime.fromtimestamp(observed_ms / 1000.0, timezone.utc)
        item = {
            "PK": "RATE_LIMIT_GUARD",
            "SK": f"BLOCKED#{observed.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}#{operation[:120]}",
            "entity_type": "SOCCER_DISTRIBUTED_RATE_LIMIT_BLOCK",
            "operation": operation[:500],
            "reason": reason,
            "configured_rps": self.requests_per_second,
            "minimum_spacing_ms": self.spacing_ms,
            "observed_at": observed.isoformat().replace("+00:00", "Z"),
            "expires_at": int((observed + timedelta(days=30)).timestamp()),
        }
        try:
            self.table.put_item(Item=item)
        except Exception:
            # If DynamoDB itself is unavailable, fail closed and emit the
            # bounded record to Lambda logs without hiding the original cause.
            print(json.dumps(item, sort_keys=True))

    def record_provider_429(
        self,
        *,
        operation: str,
        attempt: int,
        retry_after: float | None,
    ) -> None:
        """Persist non-secret evidence for every provider 429 response.

        This is deliberately best effort: telemetry persistence must never
        hide the provider response or weaken the fail-closed request limiter.
        The operation is the URL path only; query parameters and credentials
        are never written.
        """
        observed = datetime.now(timezone.utc)
        observed_at = observed.isoformat().replace("+00:00", "Z")
        item = {
            "PK": "PROVIDER_429",
            "SK": f"OBSERVED#{observed_at}#{uuid.uuid4().hex[:12]}",
            "entity_type": "SOCCER_ODDS_API_PROVIDER_429",
            "operation": str(operation)[:500],
            "attempt": int(attempt),
            "retry_after": None if retry_after is None else str(retry_after),
            "observed_at": observed_at,
            "expires_at": int((observed + timedelta(days=30)).timestamp()),
        }
        try:
            self.table.put_item(Item=item)
        except Exception:
            print(json.dumps(item, sort_keys=True))

    def acquire(self, *, operation: str, attempt: int) -> int:
        """Wait, then atomically claim one just-in-time provider permit.

        Future permits are deliberately not reserved. A caller that wakes late
        competes again using its actual wake time, so a delayed Lambda cannot
        replay a stale slot alongside callers that already advanced the lease.
        """
        started_ms = int(self._clock_ms())
        deadline_ms = started_ms + self.max_wait_ms
        last_error: BaseException | None = None
        for contention_attempt in range(self.max_contention_attempts):
            observed_ms = int(self._clock_ms())
            if observed_ms > deadline_ms:
                break
            try:
                current = self.table.get_item(
                    Key=self._key,
                    ConsistentRead=True,
                ).get("Item") or {}
                current_next = current.get("next_allowed_ms")
                current_next_ms = int(current_next) if current_next is not None else None
                if current_next_ms is not None and current_next_ms > deadline_ms:
                    reason = "DISTRIBUTED_RATE_LIMIT_WAIT_EXCEEDED"
                    self._record_block(operation, reason, observed_ms)
                    raise OddsApiRateLimitError(
                        f"{reason}: soccer provider slot exceeds the bounded acquisition deadline"
                    )
                if current_next_ms is not None and current_next_ms > observed_ms:
                    # Do not own the future timestamp. Wake at its boundary and
                    # compete again, using the actual clock after the sleep.
                    self._sleep(max(0.001, (current_next_ms - observed_ms) / 1000.0))
                    continue
                slot_ms = observed_ms
                next_allowed_ms = slot_ms + self.spacing_ms
                condition = (
                    "attribute_not_exists(next_allowed_ms)"
                    if current_next_ms is None
                    else "next_allowed_ms=:expected"
                )
                values: dict[str, Any] = {
                    ":entity": "SOCCER_DISTRIBUTED_RATE_LIMIT_STATE",
                    ":next": next_allowed_ms,
                    ":slot": slot_ms,
                    ":operation": operation[:500],
                    ":attempt": int(attempt),
                    ":rps": self.requests_per_second,
                    ":spacing": self.spacing_ms,
                    ":burst": 1,
                    ":updated": datetime.fromtimestamp(
                        observed_ms / 1000.0, timezone.utc
                    ).isoformat().replace("+00:00", "Z"),
                }
                if current_next_ms is not None:
                    values[":expected"] = current_next_ms
                self.table.update_item(
                    Key=self._key,
                    UpdateExpression=(
                        "SET entity_type=:entity, next_allowed_ms=:next, "
                        "last_granted_slot_ms=:slot, "
                        "last_operation=:operation, last_provider_attempt=:attempt, "
                        "configured_rps=:rps, minimum_spacing_ms=:spacing, "
                        "burst_capacity=:burst, updated_at=:updated"
                    ),
                    ConditionExpression=condition,
                    ExpressionAttributeValues=values,
                )
                return slot_ms
            except OddsApiRateLimitError:
                raise
            except Exception as exc:
                last_error = exc
                # Conditional failures are ordinary cross-Lambda contention.
                # Other DynamoDB errors get the same small bounded retry loop;
                # none can fall through to provider I/O.
                remaining_ms = deadline_ms - int(self._clock_ms())
                if remaining_ms <= 0:
                    break
                delay = min(
                    0.2,
                    max(0.001, remaining_ms / 1000.0),
                    0.005 * (2 ** min(contention_attempt, 5)),
                )
                self._sleep(delay)
        observed_ms = int(self._clock_ms())
        reason = (
            "DISTRIBUTED_RATE_LIMIT_CONTENTION"
            if self._conditional_failure(last_error or RuntimeError())
            else "DISTRIBUTED_RATE_LIMIT_UNAVAILABLE"
        )
        self._record_block(operation, reason, observed_ms)
        raise OddsApiRateLimitError(reason) from last_error


def _bounded_retry_after(headers: Any, *, max_seconds: float = 20.0) -> float | None:
    """Parse Retry-After without retrying early or sleeping beyond our bound."""
    raw = headers.get("Retry-After") if headers else None
    if raw is None:
        return None
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(str(raw))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            delay = max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError) as exc:
            raise OddsApiError(
                "Odds API returned an invalid Retry-After value",
                status_code=429,
                retryable=True,
            ) from exc
    if not math.isfinite(delay) or delay < 0:
        raise OddsApiError(
            "Odds API returned an invalid Retry-After delay",
            status_code=429,
            retryable=True,
        )
    if delay > max_seconds:
        raise OddsApiError(
            f"Odds API Retry-After exceeds bounded {max_seconds:g}s wait",
            status_code=429,
            retryable=True,
        )
    return delay


@dataclass(frozen=True)
class ApiResponse:
    data: Any
    status: int
    request_url: str
    quota_remaining: int | None = None
    quota_used: int | None = None
    quota_last: int | None = None


def _header_int(headers: Mapping[str, str], name: str) -> int | None:
    value = headers.get(name)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def chunks(values: Sequence[str], size: int) -> Iterable[tuple[str, ...]]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for offset in range(0, len(values), size):
        yield tuple(values[offset : offset + size])


class OddsApiClient:
    base_url = "https://api.the-odds-api.com/v4"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: int = 30,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        opener: Any = None,
        limiter: Any = None,
    ) -> None:
        if not api_key:
            raise ValueError("The Odds API key is required")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self._opener = opener or urllib.request.urlopen
        self._limiter = (
            limiter
            if limiter is not None
            else DistributedOddsApiRateLimiter.from_environment()
        )

    def _url(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        query: dict[str, Any] = {"apiKey": self.api_key}
        for key, value in (params or {}).items():
            if value is None:
                continue
            if isinstance(value, (tuple, list, set)):
                value = ",".join(str(item) for item in value)
            query[key] = value
        return f"{self.base_url}/{path.lstrip('/')}?{urllib.parse.urlencode(query)}"

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> ApiResponse:
        url = self._url(path, params)
        request = urllib.request.Request(url, headers={"accept": "application/json"})
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            # A retry is another provider request and therefore owns another
            # distributed slot. Limiter failures never reach provider I/O.
            self._limiter.acquire(operation=path, attempt=attempt)
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8")
                    headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                    return ApiResponse(
                        data=json.loads(body or "null"),
                        status=int(getattr(response, "status", 200)),
                        request_url=url,
                        quota_remaining=_header_int(headers, "x-requests-remaining"),
                        quota_used=_header_int(headers, "x-requests-used"),
                        quota_last=_header_int(headers, "x-requests-last"),
                    )
            except urllib.error.HTTPError as exc:
                last_error = exc
                retryable = exc.code == 429 or 500 <= exc.code < 600
                retry_after: float | None = None
                retry_after_error: OddsApiError | None = None
                if retryable and (exc.code == 429 or attempt < self.max_attempts):
                    try:
                        retry_after = _bounded_retry_after(exc.headers)
                    except OddsApiError as parse_error:
                        retry_after_error = parse_error
                if exc.code == 429:
                    recorder = getattr(self._limiter, "record_provider_429", None)
                    if callable(recorder):
                        try:
                            recorder(
                                operation=path,
                                attempt=attempt,
                                retry_after=retry_after,
                            )
                        except Exception as telemetry_error:
                            print(
                                json.dumps(
                                    {
                                        "level": "ERROR",
                                        "system": "soccer_auto",
                                        "event": "provider_429_telemetry_write_failed",
                                        "operation": path[:500],
                                        "attempt": attempt,
                                        "error": str(telemetry_error)[:500],
                                    },
                                    sort_keys=True,
                                )
                            )
                if retry_after_error is not None and attempt < self.max_attempts:
                    raise retry_after_error from exc
                if not retryable or attempt >= self.max_attempts:
                    detail = exc.read().decode("utf-8", errors="replace")
                    raise OddsApiError(
                        f"Odds API HTTP {exc.code}: {detail}",
                        status_code=exc.code,
                        retryable=retryable,
                    ) from exc
                if retry_after is not None:
                    # Retry-After is a provider minimum; never jitter below it.
                    time.sleep(retry_after)
                else:
                    delay = min(20.0, 2 ** (attempt - 1))
                    time.sleep(delay * (1.0 + random.random() * 0.25))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    raise OddsApiError(f"Odds API request failed: {exc}", retryable=True) from exc
                time.sleep(min(20.0, 2 ** (attempt - 1)) * (0.75 + random.random() * 0.5))
        raise OddsApiError(f"Odds API request failed: {last_error}", retryable=True)

    def sports(self, *, include_inactive: bool = True) -> ApiResponse:
        return self.get("sports", {"all": str(include_inactive).lower()})

    def events(self, sport_key: str) -> ApiResponse:
        return self.get(f"sports/{sport_key}/events", {"dateFormat": "iso"})

    def odds(
        self,
        sport_key: str,
        markets: Sequence[str],
        *,
        regions: Sequence[str] = ALL_BOOKMAKER_REGIONS,
        bookmakers: Sequence[str] | None = None,
        commence_from: str | None = None,
        commence_to: str | None = None,
    ) -> ApiResponse:
        return self.get(
            f"sports/{sport_key}/odds",
            {
                "regions": None if bookmakers else regions,
                "bookmakers": bookmakers,
                "markets": markets,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "commenceTimeFrom": commence_from,
                "commenceTimeTo": commence_to,
                "includeLinks": "true",
                "includeSids": "true",
                "includeBetLimits": "true",
                "includeRotationNumbers": "true",
                "includeMultipliers": "true",
            },
        )

    def scores(self, sport_key: str, *, days_from: int = 3) -> ApiResponse:
        if not 1 <= days_from <= 3:
            raise ValueError("The Odds API scores endpoint supports daysFrom 1 through 3")
        return self.get(
            f"sports/{sport_key}/scores",
            {"daysFrom": days_from, "dateFormat": "iso"},
        )

    def event_markets(
        self,
        sport_key: str,
        event_id: str,
        *,
        regions: Sequence[str] = ALL_BOOKMAKER_REGIONS,
        bookmakers: Sequence[str] | None = None,
    ) -> ApiResponse:
        return self.get(
            f"sports/{sport_key}/events/{event_id}/markets",
            {
                "regions": None if bookmakers else regions,
                "bookmakers": bookmakers,
                "dateFormat": "iso",
            },
        )

    def event_odds(
        self,
        sport_key: str,
        event_id: str,
        markets: Sequence[str],
        *,
        regions: Sequence[str] = ALL_BOOKMAKER_REGIONS,
        bookmakers: Sequence[str] | None = None,
    ) -> ApiResponse:
        return self.get(
            f"sports/{sport_key}/events/{event_id}/odds",
            {
                "regions": None if bookmakers else regions,
                "bookmakers": bookmakers,
                "markets": markets,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "includeLinks": "true",
                "includeSids": "true",
                "includeBetLimits": "true",
                "includeRotationNumbers": "true",
                "includeMultipliers": "true",
            },
        )

    def historical_odds(
        self,
        sport_key: str,
        snapshot_at: str,
        markets: Sequence[str],
        *,
        regions: Sequence[str] = ALL_BOOKMAKER_REGIONS,
        bookmakers: Sequence[str] | None = None,
    ) -> ApiResponse:
        return self.get(
            f"historical/sports/{sport_key}/odds",
            {
                "date": snapshot_at,
                "regions": None if bookmakers else regions,
                "bookmakers": bookmakers,
                "markets": markets,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "includeLinks": "true",
                "includeSids": "true",
                "includeBetLimits": "true",
                "includeRotationNumbers": "true",
                "includeMultipliers": "true",
            },
        )

    def historical_events(self, sport_key: str, snapshot_at: str) -> ApiResponse:
        return self.get(
            f"historical/sports/{sport_key}/events",
            {"date": snapshot_at, "dateFormat": "iso"},
        )

    def historical_event_odds(
        self,
        sport_key: str,
        event_id: str,
        snapshot_at: str,
        markets: Sequence[str],
        *,
        regions: Sequence[str] = ALL_BOOKMAKER_REGIONS,
        bookmakers: Sequence[str] | None = None,
    ) -> ApiResponse:
        return self.get(
            f"historical/sports/{sport_key}/events/{event_id}/odds",
            {
                "date": snapshot_at,
                "regions": None if bookmakers else regions,
                "bookmakers": bookmakers,
                "markets": markets,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "includeLinks": "true",
                "includeSids": "true",
                "includeBetLimits": "true",
                "includeRotationNumbers": "true",
                "includeMultipliers": "true",
            },
        )
