"""Small, observable client for The Odds API v4.

No endpoint response is silently truncated.  Every response retains provider
quota headers so the autonomous controller can diagnose coverage versus quota.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .config import ALL_BOOKMAKER_REGIONS


class OddsApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


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
        max_attempts: int = 4,
        opener: Any = None,
    ) -> None:
        if not api_key:
            raise ValueError("The Odds API key is required")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self._opener = opener or urllib.request.urlopen

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
                if not retryable or attempt >= self.max_attempts:
                    detail = exc.read().decode("utf-8", errors="replace")
                    raise OddsApiError(
                        f"Odds API HTTP {exc.code}: {detail}",
                        status_code=exc.code,
                        retryable=retryable,
                    ) from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if retry_after else min(20.0, 2 ** (attempt - 1))
                time.sleep(delay * (0.75 + random.random() * 0.5))
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
