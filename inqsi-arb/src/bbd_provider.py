"""Optional Big Balls Data (BBD) context adapter for Inqsi ARB.

The Odds API remains the authoritative sportsbook-price source. BBD is used only
for event identity/status/context when explicitly enabled and authenticated.
Missing credentials or entitlements never fabricate context and never replace
odds data. Callers can fail closed when a particular context field is required.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_BASE_URL = "https://api.bigballsdata.com"
USER_AGENT = "inqsi-arb-bbd/1.0"


class BBDError(RuntimeError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    """Never forward a BBD bearer token through a provider redirect."""

    def http_error_302(self, req, fp, code, msg, headers):
        fp.close()
        raise BBDError("BBD_REDIRECT_NOT_ALLOWED")

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


@dataclass(frozen=True)
class BBDStatus:
    enabled: bool
    configured: bool
    ok: bool
    reason: str = ""
    base_url: str = DEFAULT_BASE_URL
    auth_status: Optional[int] = None
    sports_status: Optional[int] = None
    sports_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def enabled() -> bool:
    return os.environ.get("ARB_BBD_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def api_key() -> str:
    return (
        os.environ.get("BBD_API_KEY")
        or os.environ.get("BIG_BALLS_DATA_API_KEY")
        or os.environ.get("BIGBALLS_DATA_API_KEY")
        or ""
    ).strip()


def base_url() -> str:
    return (os.environ.get("BBD_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _validated_base_url() -> str:
    """Reject unsafe configuration before constructing an authenticated request."""
    url = base_url()
    try:
        parsed = urlsplit(url)
        port = parsed.port  # Validate malformed and out-of-range ports.
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in url
            or "#" in url
            or "\\" in url
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
            or port == 0
        ):
            raise ValueError("unsafe base URL")
        # Validate the literal authority urllib will use. Reject encoded hosts
        # rather than decoding to a different destination; IDNs must use ASCII
        # punycode. DNS lookup is deliberately not part of this syntax check.
        host = parsed.hostname
        if not parsed.netloc.isascii() or "%" in parsed.netloc:
            raise ValueError("invalid authority")
        if parsed.netloc.startswith("["):
            if not re.fullmatch(r"\[[0-9A-Fa-f:.]+\](?::[0-9]+)?", parsed.netloc):
                raise ValueError("invalid IPv6 authority")
            ipaddress.IPv6Address(host)
        else:
            if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", parsed.netloc):
                raise ValueError("invalid DNS authority")
            if re.fullmatch(r"[0-9.]+", host):
                ipaddress.IPv4Address(host)
            else:
                dns_name = host[:-1] if host.endswith(".") else host
                if len(dns_name) > 253 or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in dns_name.split(".")
                ):
                    raise ValueError("invalid DNS hostname")
    except ValueError as exc:
        raise BBDError("BBD_BASE_URL_INVALID") from exc
    return url


def _unique_json_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """Reject ambiguous provider fields at every nesting level."""
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BBDError("BBD_RESPONSE_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise BBDError("BBD_RESPONSE_JSON_INVALID")


def _request(path: str, *, params: Optional[Dict[str, Any]] = None, timeout: int = 20,
             optional: bool = False) -> Tuple[int, Dict[str, str], Any]:
    key = api_key()
    if not key:
        raise BBDError("BBD_API_KEY_NOT_CONFIGURED")
    url = f"{_validated_base_url()}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None and str(v) != ""}
        if clean:
            url += "?" + urlencode(clean, doseq=True)
    request = Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with build_opener(_RejectRedirects()).open(request, timeout=timeout) as response:
            raw = response.read()
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            ) if raw else {}
            return int(response.status), dict(response.headers.items()), payload
    except HTTPError as exc:
        raw = exc.read()
        if optional and exc.code in {401, 403, 404}:
            return int(exc.code), dict(exc.headers.items()), {}
        detail = raw.decode("utf-8", "replace")[:500]
        raise BBDError(f"BBD_HTTP_{exc.code}: {detail}") from exc
    except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BBDError(f"BBD_REQUEST_FAILED: {type(exc).__name__}") from exc


def _items(payload: Any) -> List[Dict[str, Any]]:
    """Accept recognized collection envelopes without silently discarding bad rows."""
    if isinstance(payload, list):
        if not all(isinstance(row, dict) for row in payload):
            raise BBDError("BBD_COLLECTION_SCHEMA_INVALID")
        return payload
    if not isinstance(payload, dict):
        raise BBDError("BBD_COLLECTION_SCHEMA_INVALID")
    # Provider error envelopes must not masquerade as successful empty data.
    if payload.get("error") is not None:
        raise BBDError("BBD_COLLECTION_SCHEMA_INVALID")
    # Count present keys, including null/malformed candidates: selecting the
    # first usable collection would silently hide conflicting provider data.
    keys = [key for key in ("data", "sports", "matches", "events", "results") if key in payload]
    if len(keys) != 1:
        raise BBDError("BBD_COLLECTION_SCHEMA_INVALID")
    value = payload[keys[0]]
    if isinstance(value, list):
        return _items(value)
    if isinstance(value, dict):
        if value.get("error") is not None:
            raise BBDError("BBD_COLLECTION_SCHEMA_INVALID")
        nested_keys = [key for key in ("sports", "matches", "events", "results", "items") if key in value]
        if len(nested_keys) == 1 and isinstance(value[nested_keys[0]], list):
            return _items(value[nested_keys[0]])
    raise BBDError("BBD_COLLECTION_SCHEMA_INVALID")


def health() -> Dict[str, Any]:
    if not enabled():
        return BBDStatus(
            enabled=False,
            configured=bool(api_key()),
            ok=True,
            reason="DISABLED_BY_CONFIG",
            base_url=base_url(),
        ).to_dict()
    if not api_key():
        return BBDStatus(
            enabled=True,
            configured=False,
            ok=False,
            reason="BBD_API_KEY_NOT_CONFIGURED",
            base_url=base_url(),
        ).to_dict()
    auth_status: Optional[int] = None
    sports_status: Optional[int] = None
    try:
        auth_status, _, _ = _request("/v1/user/me", optional=True)
        sports_status, _, sports = _request("/v1/sports", optional=True)
        ok = auth_status == 200 and sports_status == 200
        # A known access failure takes precedence over collection diagnostics.
        rows = _items(sports) if ok else []
        return BBDStatus(
            enabled=True,
            configured=True,
            ok=ok,
            reason="" if ok else "BBD_AUTH_OR_DISCOVERY_FAILED",
            base_url=base_url(),
            auth_status=auth_status,
            sports_status=sports_status,
            sports_count=len(rows) if ok else None,
        ).to_dict()
    except BBDError as exc:
        return BBDStatus(
            enabled=True,
            configured=True,
            ok=False,
            reason=(
                "BBD_AUTH_OR_DISCOVERY_FAILED"
                if auth_status is not None and auth_status != 200
                else str(exc).split(":", 1)[0]
            ),
            base_url=base_url(),
            auth_status=auth_status,
            sports_status=sports_status,
        ).to_dict()


def sports() -> Dict[str, Any]:
    if not enabled():
        return {"ok": False, "enabled": False, "reason": "DISABLED_BY_CONFIG", "sports": []}
    if not api_key():
        return {"ok": False, "enabled": True, "reason": "BBD_API_KEY_NOT_CONFIGURED", "sports": []}
    try:
        status, headers, payload = _request("/v1/sports")
        rows = _items(payload)
        return {
            "ok": status == 200,
            "enabled": True,
            "status": status,
            "sports": rows,
            "count": len(rows),
            "provider": "big_balls_data",
            "base_url": base_url(),
            "request_id": headers.get("x-request-id") or headers.get("X-Request-Id"),
        }
    except BBDError as exc:
        return {"ok": False, "enabled": True, "reason": str(exc).split(":", 1)[0], "sports": []}


def events(*, sport: Optional[str] = None, league: Optional[str] = None,
           status: Optional[str] = None) -> Dict[str, Any]:
    """Return BBD event/match context without any sportsbook prices.

    Endpoint shape is kept behind this adapter because BBD schemas/entitlements
    may vary. The current integration uses /v1/matches, matching existing repo
    integrations. Unsupported/auth failures are returned as explicit evidence.
    """
    if not enabled():
        return {"ok": False, "enabled": False, "reason": "DISABLED_BY_CONFIG", "events": []}
    if not api_key():
        return {"ok": False, "enabled": True, "reason": "BBD_API_KEY_NOT_CONFIGURED", "events": []}
    try:
        code, headers, payload = _request(
            "/v1/matches",
            params={"sport": sport, "league": league, "status": status},
            optional=True,
        )
        if code != 200:
            return {
                "ok": False,
                "enabled": True,
                "status": code,
                "reason": "BBD_MATCH_ENDPOINT_UNAVAILABLE_OR_UNENTITLED",
                "events": [],
            }
        rows = _items(payload)
        normalized: List[Dict[str, Any]] = []
        for row in rows:
            home = row.get("home") or row.get("home_team")
            away = row.get("away") or row.get("away_team")
            normalized.append({
                "bbd_event_id": str(row.get("id") or row.get("match_id") or row.get("event_id") or "") or None,
                "sport": row.get("sport") or sport,
                "league": row.get("league") or row.get("competition") or league,
                "status": row.get("status"),
                "start_time": row.get("kickoff") or row.get("commence_time") or row.get("start_time") or row.get("date"),
                "home": home,
                "away": away,
                "venue": row.get("venue"),
                "source": "big_balls_data",
                "raw": row,
            })
        return {
            "ok": True,
            "enabled": True,
            "status": code,
            "events": normalized,
            "count": len(normalized),
            "provider": "big_balls_data",
            "request_id": headers.get("x-request-id") or headers.get("X-Request-Id"),
        }
    except BBDError as exc:
        return {"ok": False, "enabled": True, "reason": str(exc).split(":", 1)[0], "events": []}
