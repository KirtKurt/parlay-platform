"""Big Balls Data client for soccer context. Prices stay on The Odds API."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from soccer_auto.kss1_identity import SPORT_SLUG, is_bbd_uuid

DEFAULT_BASE = "https://api.bigballsdata.com"
DEFAULT_TIMEOUT = 12


class BbdError(RuntimeError):
    pass


class BbdClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_BASE,
        opener: Callable[..., Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        if not token:
            raise BbdError("BBD token missing")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def _get(self, path: str, query: dict[str, str] | None = None) -> dict[str, Any]:
        qs = ""
        if query:
            qs = "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in query.items())
        url = f"{self.base_url}{path}{qs}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            raise BbdError(f"BBD HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise BbdError(f"BBD transport error for {path}") from exc
        if status >= 400:
            raise BbdError(f"BBD HTTP {status} for {path}")
        payload = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise BbdError("BBD payload must be an object")
        return payload

    def list_matches(self, league: str, *, limit: int = 50) -> list[dict[str, Any]]:
        payload = self._get(
            "/v1/matches",
            {"sport": SPORT_SLUG, "league": league, "limit": str(limit)},
        )
        rows = payload.get("data") or payload.get("matches") or []
        if not isinstance(rows, list):
            raise BbdError("BBD matches list malformed")
        return rows

    def match_stats(self, match_id: str) -> dict[str, Any]:
        if not is_bbd_uuid(match_id):
            raise BbdError("BBD match id must be a UUID")
        return self._get(f"/v1/stored/matches/{match_id}/stats")

    def match_lineups(self, match_id: str) -> dict[str, Any]:
        if not is_bbd_uuid(match_id):
            raise BbdError("BBD match id must be a UUID")
        return self._get(f"/v1/stored/matches/{match_id}/lineups")

    def match_events(self, match_id: str) -> dict[str, Any]:
        if not is_bbd_uuid(match_id):
            raise BbdError("BBD match id must be a UUID")
        return self._get(f"/v1/matches/{match_id}/events", {"sport": SPORT_SLUG})


def extract_match_xg(stats_payload: dict[str, Any]) -> dict[str, Any]:
    data = stats_payload.get("data") if isinstance(stats_payload.get("data"), dict) else stats_payload
    home = _first_float(data, ("home_xg", "xg_home", "home.xg"))
    away = _first_float(data, ("away_xg", "xg_away", "away.xg"))
    return {
        "has_xg": home is not None and away is not None,
        "xg_home": home,
        "xg_away": away,
    }


def _first_float(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if "." in key:
            current: Any = payload
            for part in key.split("."):
                if not isinstance(current, dict) or part not in current:
                    current = None
                    break
                current = current[part]
            value = current
        else:
            value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
