"""Small, redaction-safe client for Big Balls Sports Data shadow capture."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

import boto3

DEFAULT_BASE_URL = "https://api.bigballsdata.com"
DEFAULT_TIMEOUT_SECONDS = 4
DEFAULT_MAX_ATTEMPTS = 1
ALLOWED_MLB_RESOURCES = {
    "pitchers", "bullpens", "lineups", "injuries", "team_context", "weather", "park"
}
_SAFE_QUERY_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class BBSClientError(RuntimeError):
    """A deliberately redacted provider failure."""


class BBSAuthenticationError(BBSClientError):
    pass


class BBSTransientError(BBSClientError):
    pass


def _secret_value(secret_arn: str, *, secrets_client: Any = None) -> str:
    client = secrets_client or boto3.client("secretsmanager")
    try:
        result = client.get_secret_value(SecretId=secret_arn)
    except Exception:
        raise BBSClientError("BBS_SECRET_RETRIEVAL_FAILED") from None
    value = result.get("SecretString")
    if not isinstance(value, str) or not value.strip():
        raise BBSClientError("BBS_SECRET_VALUE_MISSING")
    return value.strip()


def resolve_api_key(api_key: Optional[str] = None, *, secret_arn: Optional[str] = None, secrets_client: Any = None) -> str:
    direct = api_key or os.environ.get("BBS_API_KEY")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    arn = secret_arn or os.environ.get("BBS_API_SECRET_ARN")
    if not arn:
        raise BBSClientError("BBS_CREDENTIAL_NOT_CONFIGURED")
    return _secret_value(str(arn), secrets_client=secrets_client)


def _historical_as_of_param() -> str:
    value = str(os.environ.get("BBS_HISTORICAL_AS_OF_PARAM", "as_of") or "as_of").strip()
    if not _SAFE_QUERY_NAME.fullmatch(value):
        raise BBSClientError("BBS_HISTORICAL_AS_OF_PARAM_INVALID")
    return value


class BigBallsDataClient:
    def __init__(self, *, api_key: Optional[str] = None, secret_arn: Optional[str] = None,
                 secrets_client: Any = None, base_url: str = DEFAULT_BASE_URL,
                 timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 opener: Callable[..., Any] = urllib.request.urlopen,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self._api_key = resolve_api_key(api_key, secret_arn=secret_arn, secrets_client=secrets_client)
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._max_attempts = max(1, int(max_attempts))
        self._opener = opener
        self._sleeper = sleeper

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self._base_url!r}, timeout_seconds={self._timeout_seconds}, credential=<redacted>)"

    @staticmethod
    def _validated_envelope(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise BBSClientError("BBS_RESPONSE_NOT_OBJECT")
        if not {"data", "meta", "error"}.issubset(payload):
            raise BBSClientError("BBS_RESPONSE_ENVELOPE_INCOMPLETE")
        if payload.get("error") is not None:
            raise BBSClientError("BBS_RESPONSE_REPORTED_ERROR")
        if not isinstance(payload.get("meta"), dict):
            raise BBSClientError("BBS_RESPONSE_META_INVALID")
        return payload

    def _request(self, path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, str]]:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{self._base_url}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "inqsi-mlb-bbs-shadow/2.1",
        }, method="GET")
        last_error = "BBS_REQUEST_FAILED"
        for attempt in range(1, self._max_attempts + 1):
            try:
                with self._opener(request, timeout=self._timeout_seconds) as response:
                    status = int(getattr(response, "status", response.getcode()))
                    headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                    raw = response.read()
                if status != 200:
                    raise BBSTransientError(f"BBS_UNEXPECTED_HTTP_{status}")
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise BBSClientError("BBS_RESPONSE_NOT_JSON") from None
                return self._validated_envelope(payload), headers
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise BBSAuthenticationError(f"BBS_AUTH_REJECTED_HTTP_{exc.code}") from None
                if exc.code == 404:
                    raise BBSClientError("BBS_RESOURCE_NOT_FOUND") from None
                if exc.code == 429:
                    raise BBSTransientError("BBS_RATE_LIMITED") from None
                if 500 <= exc.code <= 599:
                    last_error = f"BBS_UPSTREAM_HTTP_{exc.code}"
                    if attempt < self._max_attempts:
                        retry_after = (exc.headers or {}).get("Retry-After")
                        try:
                            delay = min(max(float(retry_after), 0.0), 5.0)
                        except (TypeError, ValueError):
                            delay = min(0.25 * (2 ** (attempt - 1)), 2.0)
                        self._sleeper(delay)
                        continue
                    break
                raise BBSClientError(f"BBS_HTTP_{exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                last_error = "BBS_NETWORK_UNAVAILABLE"
                if attempt < self._max_attempts:
                    self._sleeper(min(0.25 * (2 ** (attempt - 1)), 2.0))
                    continue
                break
        raise BBSTransientError(last_error)

    @staticmethod
    def _transport(headers: Dict[str, str], *, requested_date: Optional[str], requested_as_of: Optional[str]) -> Dict[str, Any]:
        return {
            "rateLimit": headers.get("x-ratelimit-limit"),
            "rateRemaining": headers.get("x-ratelimit-remaining"),
            "rateReset": headers.get("x-ratelimit-reset"),
            "requestedDate": requested_date,
            "requestedAsOfUtc": requested_as_of,
        }

    def account(self) -> Dict[str, Any]:
        payload, _ = self._request("/v1/user/me")
        if not isinstance(payload.get("data"), dict):
            raise BBSClientError("BBS_ACCOUNT_DATA_INVALID")
        return payload

    def list_mlb_matches(self, game_date: str, *, limit: int = 50, as_of: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "sport": "baseball", "league": "mlb", "date": game_date,
            "limit": min(max(int(limit), 1), 200),
        }
        if as_of:
            params[_historical_as_of_param()] = as_of
        payload, headers = self._request("/v1/matches", params)
        if not isinstance(payload.get("data"), list):
            raise BBSClientError("BBS_MLB_MATCH_DATA_INVALID")
        out = dict(payload)
        out["_transport"] = self._transport(headers, requested_date=game_date, requested_as_of=as_of)
        return out

    def get_mlb_match_resource(
        self,
        match_id: str,
        resource: str,
        *,
        game_date: Optional[str] = None,
        as_of: Optional[str] = None,
    ) -> Dict[str, Any]:
        name = str(resource).strip().lower()
        if name not in ALLOWED_MLB_RESOURCES:
            raise BBSClientError("BBS_MLB_RESOURCE_UNSUPPORTED")
        safe_id = urllib.parse.quote(str(match_id).strip(), safe="")
        if not safe_id:
            raise BBSClientError("BBS_MATCH_ID_MISSING")
        env_name = f"BBS_MLB_{name.upper()}_PATH"
        template = os.environ.get(env_name, "/v1/matches/{match_id}/{resource}")
        path = template.format(match_id=safe_id, resource=urllib.parse.quote(name, safe=""))
        params: Dict[str, Any] = {"sport": "baseball", "league": "mlb", "date": game_date}
        if as_of:
            params[_historical_as_of_param()] = as_of
        payload, headers = self._request(path, params)
        if not isinstance(payload.get("data"), (dict, list)):
            raise BBSClientError(f"BBS_MLB_{name.upper()}_DATA_INVALID")
        out = dict(payload)
        out["_transport"] = self._transport(headers, requested_date=game_date, requested_as_of=as_of)
        return out
