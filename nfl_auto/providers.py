"""Redaction-safe BBD and The Odds API clients used only by nfl_auto."""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping

try:
    import boto3  # type: ignore
except ImportError:  # pragma: no cover - Lambda includes boto3; core tests do not need it.
    boto3 = None  # type: ignore

from .config import (
    BBD_ALLOWED_GAME_TYPES,
    DEFAULT_BBD_TIMEOUT_SECONDS,
    DEFAULT_HTTP_ATTEMPTS,
    DEFAULT_ODDS_TIMEOUT_SECONDS,
    ODDS_MARKETS,
    ODDS_REGIONS,
    SPORT_KEY,
)


class ProviderError(RuntimeError):
    """Base provider exception. Messages deliberately exclude credentials/URLs."""


class AuthenticationError(ProviderError):
    pass


class RateLimitError(ProviderError):
    pass


class TransientProviderError(ProviderError):
    pass


class ContractError(ProviderError):
    pass


@dataclass(frozen=True)
class TransportMeta:
    provider: str
    endpoint: str
    status: int
    rate_limit: str | None = None
    rate_remaining: str | None = None
    rate_reset: str | None = None
    requests_remaining: str | None = None
    requests_used: str | None = None
    requests_last: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "endpoint": self.endpoint,
            "status": self.status,
            "rate_limit": self.rate_limit,
            "rate_remaining": self.rate_remaining,
            "rate_reset": self.rate_reset,
            "requests_remaining": self.requests_remaining,
            "requests_used": self.requests_used,
            "requests_last": self.requests_last,
        }


def resolve_secret(secret_arn: str, *, client: Any = None) -> str:
    if not secret_arn:
        raise AuthenticationError("PROVIDER_SECRET_ARN_MISSING")
    if boto3 is None and client is None:
        raise AuthenticationError("BOTO3_NOT_AVAILABLE_FOR_SECRET_RESOLUTION")
    secrets = client or boto3.client("secretsmanager")
    try:
        response = secrets.get_secret_value(SecretId=secret_arn)
    except Exception:
        raise AuthenticationError("PROVIDER_SECRET_RETRIEVAL_FAILED") from None
    value = response.get("SecretString")
    if not isinstance(value, str) or not value.strip():
        raise AuthenticationError("PROVIDER_SECRET_VALUE_MISSING")
    text = value.strip()
    # Permit a JSON secret while never returning/logging the full object.
    if text.startswith("{"):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            raise AuthenticationError("PROVIDER_SECRET_JSON_INVALID") from None
        for key in ("api_key", "key", "token", "value"):
            candidate = decoded.get(key) if isinstance(decoded, Mapping) else None
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        raise AuthenticationError("PROVIDER_SECRET_JSON_KEY_MISSING")
    return text


class _JsonHttpClient:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        timeout_seconds: int,
        max_attempts: int = DEFAULT_HTTP_ATTEMPTS,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.opener = opener
        self.sleeper = sleeper

    @staticmethod
    def _headers(response: Any) -> dict[str, str]:
        return {str(k).lower(): str(v) for k, v in response.headers.items()}

    def request_json(
        self,
        *,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[Any, TransportMeta]:
        query = urllib.parse.urlencode(
            [(key, value) for key, value in (params or {}).items() if value is not None],
            doseq=True,
        )
        url = f"{self.base_url}{endpoint}" + (f"?{query}" if query else "")
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "inqis-nfl-auto/1.0", **dict(headers or {})},
            method="GET",
        )
        last_code = "NETWORK_UNAVAILABLE"
        for attempt in range(1, self.max_attempts + 1):
            try:
                with self.opener(request, timeout=self.timeout_seconds) as response:
                    status = int(getattr(response, "status", response.getcode()))
                    raw = response.read()
                    response_headers = self._headers(response)
                if status != 200:
                    raise TransientProviderError(f"{self.provider}_UNEXPECTED_HTTP_{status}")
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise ContractError(f"{self.provider}_RESPONSE_NOT_JSON") from None
                meta = TransportMeta(
                    provider=self.provider,
                    endpoint=endpoint,
                    status=status,
                    rate_limit=response_headers.get("x-ratelimit-limit"),
                    rate_remaining=response_headers.get("x-ratelimit-remaining"),
                    rate_reset=response_headers.get("x-ratelimit-reset"),
                    requests_remaining=response_headers.get("x-requests-remaining"),
                    requests_used=response_headers.get("x-requests-used"),
                    requests_last=response_headers.get("x-requests-last"),
                )
                return payload, meta
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise AuthenticationError(f"{self.provider}_AUTH_REJECTED_HTTP_{exc.code}") from None
                if exc.code == 404:
                    raise ContractError(f"{self.provider}_ENDPOINT_NOT_FOUND") from None
                if exc.code == 422:
                    raise ContractError(f"{self.provider}_REQUEST_REJECTED_HTTP_422") from None
                if exc.code == 429:
                    last_code = "RATE_LIMITED"
                    if attempt >= self.max_attempts:
                        raise RateLimitError(f"{self.provider}_RATE_LIMITED") from None
                    retry_after = (exc.headers or {}).get("Retry-After")
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = min(1.0 * (2 ** (attempt - 1)), 15.0)
                    self.sleeper(max(0.0, min(delay, 30.0)))
                    continue
                if 500 <= exc.code <= 599:
                    last_code = f"UPSTREAM_HTTP_{exc.code}"
                else:
                    raise ProviderError(f"{self.provider}_HTTP_{exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                last_code = "NETWORK_UNAVAILABLE"
            if attempt < self.max_attempts:
                self.sleeper(min(0.5 * (2 ** (attempt - 1)) + random.random() * 0.15, 8.0))
        raise TransientProviderError(f"{self.provider}_{last_code}")


class BBDClient:
    """NFL statistics client. BBD is never used as the odds authority here."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        secret_arn: str | None = None,
        secrets_client: Any = None,
        base_url: str = "https://api.bigballsdata.com",
        timeout_seconds: int = DEFAULT_BBD_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_HTTP_ATTEMPTS,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key or resolve_secret(str(secret_arn or ""), client=secrets_client)
        self.http = _JsonHttpClient(
            provider="BBD",
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            opener=opener,
            sleeper=sleeper,
        )

    def _get(self, endpoint: str, params: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], TransportMeta]:
        payload, meta = self.http.request_json(
            endpoint=endpoint,
            params=params,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        if not isinstance(payload, Mapping) or "data" not in payload:
            raise ContractError("BBD_ENVELOPE_INVALID")
        if payload.get("error"):
            error = payload.get("error")
            code = str(error.get("code") or "UNKNOWN") if isinstance(error, Mapping) else "UNKNOWN"
            raise ProviderError(f"BBD_RESPONSE_ERROR_{code[:60]}")
        return dict(payload), meta

    def account(self) -> tuple[dict[str, Any], TransportMeta]:
        payload, meta = self._get("/v1/user/me")
        if not isinstance(payload.get("data"), Mapping):
            raise ContractError("BBD_ACCOUNT_DATA_INVALID")
        return payload, meta

    def list_games_page(
        self,
        *,
        season: int,
        game_type: str,
        week: int | None = None,
        team: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], TransportMeta]:
        normalized_type = str(game_type).upper()
        if normalized_type not in BBD_ALLOWED_GAME_TYPES:
            raise ContractError("BBD_NFL_GAME_TYPE_FORBIDDEN")
        payload, meta = self._get(
            "/v1/nfl/games",
            {
                "season": int(season),
                "type": normalized_type,
                "week": week,
                "team": team,
                "limit": min(max(int(limit), 1), 200),
                "offset": max(int(offset), 0),
            },
        )
        rows = payload.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise ContractError("BBD_NFL_GAMES_DATA_INVALID")
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), Mapping) else {}
        return [dict(row) for row in rows], dict(pagination), meta

    def iter_games(self, *, season: int, game_type: str) -> Iterator[tuple[dict[str, Any], TransportMeta]]:
        offset = 0
        while True:
            rows, pagination, meta = self.list_games_page(
                season=season, game_type=game_type, limit=200, offset=offset
            )
            for row in rows:
                yield row, meta
            offset += len(rows)
            total = int(pagination.get("total") or offset)
            if not rows or offset >= total:
                break

    def list_plays_page(
        self,
        *,
        game_id: str,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], TransportMeta]:
        safe_id = urllib.parse.quote(str(game_id).strip(), safe="_-.")
        if not safe_id:
            raise ContractError("BBD_NFL_GAME_ID_MISSING")
        payload, meta = self._get(
            f"/v1/nfl/games/{safe_id}/plays",
            {"limit": min(max(int(limit), 1), 500), "offset": max(int(offset), 0)},
        )
        rows = payload.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise ContractError("BBD_NFL_PLAYS_DATA_INVALID")
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), Mapping) else {}
        return [dict(row) for row in rows], dict(pagination), meta

    def all_plays(self, *, game_id: str, max_pages: int = 12) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        offset = 0
        pages = 0
        rows: list[dict[str, Any]] = []
        transport: list[dict[str, Any]] = []
        while pages < max_pages:
            page, pagination, meta = self.list_plays_page(game_id=game_id, limit=500, offset=offset)
            rows.extend(page)
            transport.append(meta.to_dict())
            offset += len(page)
            pages += 1
            total = int(pagination.get("total") or offset)
            if not page or offset >= total:
                return rows, transport
        raise ContractError("BBD_NFL_PLAYS_PAGINATION_LIMIT_EXCEEDED")


class OddsApiClient:
    """The Odds API authority for all NFL historical and live market snapshots."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        secret_arn: str | None = None,
        secrets_client: Any = None,
        base_url: str = "https://api.the-odds-api.com",
        timeout_seconds: int = DEFAULT_ODDS_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_HTTP_ATTEMPTS,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key or resolve_secret(str(secret_arn or ""), client=secrets_client)
        self.http = _JsonHttpClient(
            provider="ODDS_API",
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            opener=opener,
            sleeper=sleeper,
        )

    def _get(self, endpoint: str, params: Mapping[str, Any]) -> tuple[Any, TransportMeta]:
        values = {**dict(params), "apiKey": self.api_key}
        return self.http.request_json(endpoint=endpoint, params=values)

    def historical_odds(
        self,
        *,
        snapshot_at: str,
        markets: tuple[str, ...] = ODDS_MARKETS,
        regions: str = ODDS_REGIONS,
    ) -> tuple[dict[str, Any], TransportMeta]:
        payload, meta = self._get(
            f"/v4/historical/sports/{SPORT_KEY}/odds",
            {
                "date": snapshot_at,
                "regions": regions,
                "markets": ",".join(markets),
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
        )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
            raise ContractError("ODDS_API_HISTORICAL_ENVELOPE_INVALID")
        return dict(payload), meta

    def historical_events(self, *, snapshot_at: str) -> tuple[dict[str, Any], TransportMeta]:
        payload, meta = self._get(
            f"/v4/historical/sports/{SPORT_KEY}/events",
            {"date": snapshot_at, "dateFormat": "iso"},
        )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
            raise ContractError("ODDS_API_HISTORICAL_EVENTS_INVALID")
        return dict(payload), meta

    def live_odds(
        self,
        *,
        markets: tuple[str, ...] = ODDS_MARKETS,
        regions: str = ODDS_REGIONS,
    ) -> tuple[list[dict[str, Any]], TransportMeta]:
        payload, meta = self._get(
            f"/v4/sports/{SPORT_KEY}/odds",
            {
                "regions": regions,
                "markets": ",".join(markets),
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
        )
        if not isinstance(payload, list) or any(not isinstance(row, Mapping) for row in payload):
            raise ContractError("ODDS_API_LIVE_ODDS_INVALID")
        return [dict(row) for row in payload], meta

    def scores(self, *, days_from: int = 3) -> tuple[list[dict[str, Any]], TransportMeta]:
        payload, meta = self._get(
            f"/v4/sports/{SPORT_KEY}/scores",
            {"daysFrom": min(max(int(days_from), 1), 3), "dateFormat": "iso"},
        )
        if not isinstance(payload, list) or any(not isinstance(row, Mapping) for row in payload):
            raise ContractError("ODDS_API_SCORES_INVALID")
        return [dict(row) for row in payload], meta
