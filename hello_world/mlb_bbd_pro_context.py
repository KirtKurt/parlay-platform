from __future__ import annotations

"""Big Balls Data Pro adapter for lock-safe MLB pregame context.

The adapter is intentionally schema-tolerant. It discovers documented MLB GET
operations from the provider's OpenAPI document (or accepts an explicit endpoint
manifest), calls a bounded set of applicable endpoints, fingerprints each
response, and projects useful fields into the existing MLB advanced-context
contract. Raw API keys are never persisted or logged.
"""

import copy
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "MLB-BBD-PRO-CONTEXT-v1"
KEY_ENV_NAMES: Tuple[str, ...] = (
    "BIG_BALLS_DATA_API_KEY",
    "BBD_API_KEY",
    "BIGBALLS_DATA_API_KEY",
    "BIG_BALLS_API_KEY",
)
BASE_URL_ENV_NAMES: Tuple[str, ...] = (
    "BIG_BALLS_DATA_API_BASE_URL",
    "BBD_API_BASE_URL",
    "BIGBALLS_DATA_API_BASE_URL",
)
OPENAPI_URL_ENV_NAMES: Tuple[str, ...] = (
    "BIG_BALLS_DATA_OPENAPI_URL",
    "BBD_OPENAPI_URL",
    "BIGBALLS_DATA_OPENAPI_URL",
)
ENDPOINT_MANIFEST_ENV_NAMES: Tuple[str, ...] = (
    "BIG_BALLS_DATA_MLB_ENDPOINTS_JSON",
    "BBD_MLB_ENDPOINTS_JSON",
)

DEFAULT_OPENAPI_CANDIDATES: Tuple[str, ...] = (
    "https://api.bigballsdata.com/openapi.json",
    "https://api.bigballsdata.com/v1/openapi.json",
    "https://bigballsdata.com/openapi.json",
    "https://bigballsdata.com/api/openapi.json",
)

MAX_CALLS_PER_GAME = max(1, min(16, int(os.environ.get("BBD_MAX_CALLS_PER_GAME", "8"))))
CACHE_SECONDS = max(30, int(os.environ.get("BBD_CONTEXT_CACHE_SECONDS", "300")))
HTTP_TIMEOUT_SECONDS = max(3, min(30, int(os.environ.get("BBD_HTTP_TIMEOUT_SECONDS", "12"))))
MAX_SIGNAL_VALUES = max(20, min(500, int(os.environ.get("BBD_MAX_SIGNAL_VALUES", "160"))))
MAX_PAYLOAD_BYTES = max(10_000, min(1_000_000, int(os.environ.get("BBD_MAX_PAYLOAD_BYTES", "250000"))))

_CATEGORY_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("confirmed_lineups", ("lineup", "batting order", "starting lineup")),
    ("injuries", ("injur", "disabled list", "il report", "scratch", "inactive")),
    ("probable_pitchers", ("probable pitcher", "starting pitcher", "starter")),
    ("bullpen", ("bullpen", "reliever", "relief pitcher")),
    ("team_stats", ("team stat", "team metric", "team season", "team performance")),
    ("player_stats", ("player stat", "pitcher stat", "batter stat", "player performance")),
    ("recent_form", ("recent form", "last games", "rolling", "streak", "form")),
    ("standings", ("standing", "division rank", "league rank")),
    ("odds_intelligence", ("odds", "fair price", "consensus", "line movement", "divergence")),
    ("provider_prediction", ("prediction", "forecast", "win probability", "model edge")),
    ("games", ("schedule", "game", "match", "score")),
)

_SIGNAL_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "confirmed_lineups": (
        "lineup", "battingorder", "batting_order", "starter", "position", "confirmed",
    ),
    "injuries": (
        "injury", "injuries", "status", "il", "disabled", "scratch", "inactive", "return",
    ),
    "probable_pitchers": (
        "probablepitcher", "probable_pitcher", "startingpitcher", "starting_pitcher", "starter",
        "throws", "handedness",
    ),
    "bullpen": (
        "bullpen", "relief", "reliever", "availability", "fatigue", "innings", "pitches",
    ),
    "team_stats": (
        "wins", "losses", "run", "ops", "obp", "slg", "era", "fip", "xfip", "wrc",
        "strikeout", "walk", "home", "away",
    ),
    "player_stats": (
        "avg", "ops", "obp", "slg", "era", "fip", "xfip", "wrc", "strikeout", "walk",
        "velocity", "innings", "pitches", "hits", "runs", "rbi", "home_run",
    ),
    "recent_form": ("last", "recent", "rolling", "streak", "form", "wins", "losses"),
    "standings": ("rank", "standing", "wins", "losses", "pct", "gamesback", "games_back"),
    "odds_intelligence": (
        "odds", "price", "consensus", "fair", "movement", "divergence", "implied", "edge",
    ),
    "provider_prediction": (
        "prediction", "probability", "winner", "edge", "confidence", "forecast",
    ),
    "games": ("game", "match", "date", "start", "home", "away", "score"),
}

_OPENAPI_CACHE: Dict[str, Any] = {"loaded_at": 0.0, "manifest": None, "error": None}
_RESPONSE_CACHE: Dict[str, Tuple[float, Any]] = {}


def _first_env(names: Sequence[str]) -> str:
    for name in names:
        value = str(os.environ.get(name, "")).strip()
        if value:
            return value
    return ""


def api_key() -> str:
    return _first_env(KEY_ENV_NAMES)


def configured_base_url() -> str:
    return _first_env(BASE_URL_ENV_NAMES).rstrip("/")


def configured_openapi_url() -> str:
    return _first_env(OPENAPI_URL_ENV_NAMES)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [
        (key, "REDACTED" if "key" in key.lower() or "token" in key.lower() else value)
        for key, value in query
    ]
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(redacted), parsed.fragment)
    )


def _http_json(
    url: str,
    *,
    key: str = "",
    timeout: int = HTTP_TIMEOUT_SECONDS,
    http_get: Optional[Callable[[urllib.request.Request, int], Any]] = None,
) -> Any:
    headers = {
        "accept": "application/json",
        "user-agent": "inqsi-mlb-bbd-pro/1.0",
    }
    if key:
        # Provider deployments have used API-key and bearer schemes. Sending
        # both common forms is deliberate and no key is ever written to logs.
        headers.update(
            {
                "x-api-key": key,
                "X-API-Key": key,
                "Authorization": f"Bearer {key}",
            }
        )
    request = urllib.request.Request(url, headers=headers)
    if http_get is not None:
        return http_get(request, timeout)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_PAYLOAD_BYTES + 1)
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise RuntimeError("BBD_RESPONSE_EXCEEDS_BOUNDED_PAYLOAD")
        return json.loads(raw.decode("utf-8"))


def _manifest_from_env() -> Optional[Dict[str, Any]]:
    raw = _first_env(ENDPOINT_MANIFEST_ENV_NAMES)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"BBD_ENDPOINT_MANIFEST_INVALID_JSON:{type(exc).__name__}") from exc
    if isinstance(parsed, list):
        parsed = {"operations": parsed}
    if not isinstance(parsed, dict):
        raise RuntimeError("BBD_ENDPOINT_MANIFEST_NOT_OBJECT")
    return parsed


def _operation_category(text: str) -> Optional[str]:
    normalized = " ".join(text.lower().split())
    for category, needles in _CATEGORY_RULES:
        if any(needle in normalized for needle in needles):
            return category
    return None


def _server_url(spec: Dict[str, Any], openapi_url: str) -> str:
    servers = spec.get("servers") if isinstance(spec.get("servers"), list) else []
    for row in servers:
        value = str((row or {}).get("url") or "").strip()
        if value.startswith("http"):
            return value.rstrip("/")
    configured = configured_base_url()
    if configured:
        return configured
    parsed = urllib.parse.urlsplit(openapi_url)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _openapi_operations(spec: Dict[str, Any], openapi_url: str) -> Dict[str, Any]:
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    base = _server_url(spec, openapi_url)
    operations: List[Dict[str, Any]] = []
    for path, path_row in paths.items():
        if not isinstance(path_row, dict):
            continue
        operation = path_row.get("get")
        if not isinstance(operation, dict):
            continue
        material = " ".join(
            [
                str(path),
                str(operation.get("operationId") or ""),
                str(operation.get("summary") or ""),
                str(operation.get("description") or ""),
                " ".join(str(value) for value in operation.get("tags") or []),
            ]
        )
        if not re.search(r"\b(mlb|baseball)\b", material, re.I):
            continue
        category = _operation_category(material) or "games"
        parameters: List[Dict[str, Any]] = []
        for source in (path_row.get("parameters") or [], operation.get("parameters") or []):
            if isinstance(source, dict):
                parameters.append(copy.deepcopy(source))
        operations.append(
            {
                "category": category,
                "method": "GET",
                "path": str(path),
                "urlTemplate": base + "/" + str(path).lstrip("/"),
                "operationId": str(operation.get("operationId") or ""),
                "summary": str(operation.get("summary") or ""),
                "parameters": parameters,
            }
        )
    operations.sort(
        key=lambda row: (
            next((index for index, (name, _) in enumerate(_CATEGORY_RULES) if name == row["category"]), 999),
            row["path"],
        )
    )
    return {
        "version": VERSION,
        "source": "BBD OpenAPI",
        "openapiUrl": _safe_url(openapi_url),
        "baseUrl": base,
        "operations": operations,
        "specFingerprint": _fingerprint(spec),
    }


def discover_manifest(
    *,
    force: bool = False,
    http_get: Optional[Callable[[urllib.request.Request, int], Any]] = None,
) -> Dict[str, Any]:
    now = time.time()
    cached = _OPENAPI_CACHE.get("manifest")
    if not force and cached and now - float(_OPENAPI_CACHE.get("loaded_at") or 0) <= CACHE_SECONDS:
        return copy.deepcopy(cached)

    explicit = _manifest_from_env()
    if explicit is not None:
        explicit.setdefault("version", VERSION)
        explicit.setdefault("source", "explicit endpoint manifest")
        explicit.setdefault("operations", [])
        _OPENAPI_CACHE.update({"loaded_at": now, "manifest": explicit, "error": None})
        return copy.deepcopy(explicit)

    candidates: List[str] = []
    configured = configured_openapi_url()
    if configured:
        candidates.append(configured)
    base = configured_base_url()
    if base:
        candidates.extend((f"{base}/openapi.json", f"{base}/v1/openapi.json"))
    candidates.extend(DEFAULT_OPENAPI_CANDIDATES)

    errors: List[Dict[str, str]] = []
    seen: set[str] = set()
    for url in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            spec = _http_json(url, key=api_key(), http_get=http_get)
            if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
                raise RuntimeError("OPENAPI_PATHS_MISSING")
            manifest = _openapi_operations(spec, url)
            if not manifest.get("operations"):
                raise RuntimeError("OPENAPI_HAS_NO_MLB_GET_OPERATIONS")
            _OPENAPI_CACHE.update({"loaded_at": now, "manifest": manifest, "error": None})
            return copy.deepcopy(manifest)
        except Exception as exc:
            errors.append({"url": _safe_url(url), "error": f"{type(exc).__name__}:{exc}"})

    error = {"status": "DISCOVERY_FAILED", "attempts": errors}
    _OPENAPI_CACHE.update({"loaded_at": now, "manifest": None, "error": error})
    raise RuntimeError("BBD_MLB_OPENAPI_DISCOVERY_FAILED")


def _parse_date(value: Any) -> Optional[str]:
    if not value:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.date().isoformat()
    except Exception:
        return text[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text) else None


def _game_value(game: Dict[str, Any], parameter_name: str) -> Optional[Any]:
    name = re.sub(r"[^a-z0-9]", "", parameter_name.lower())
    aliases: Dict[str, Tuple[str, ...]] = {
        "gameid": ("provider_event_id", "providerEventId", "game_id", "gameId", "id", "official_game_pk", "officialGamePk"),
        "eventid": ("provider_event_id", "providerEventId", "game_id", "gameId", "id", "official_game_pk", "officialGamePk"),
        "matchid": ("provider_event_id", "providerEventId", "game_id", "gameId", "id", "official_game_pk", "officialGamePk"),
        "id": ("provider_event_id", "providerEventId", "game_id", "gameId", "id", "official_game_pk", "officialGamePk"),
        "date": ("official_commence_time", "officialCommenceTime", "commence_time", "commenceTime", "game_date_et", "gameDateEt"),
        "gamedate": ("official_commence_time", "officialCommenceTime", "commence_time", "commenceTime", "game_date_et", "gameDateEt"),
        "startdate": ("official_commence_time", "officialCommenceTime", "commence_time", "commenceTime", "game_date_et", "gameDateEt"),
        "enddate": ("official_commence_time", "officialCommenceTime", "commence_time", "commenceTime", "game_date_et", "gameDateEt"),
        "season": ("season",),
        "league": ("league",),
        "sport": ("provider_sport_key", "sport_key", "sport"),
        "sportkey": ("provider_sport_key", "sport_key", "sport"),
        "hometeam": ("home_team", "homeTeam"),
        "awayteam": ("away_team", "awayTeam"),
        "team": ("home_team", "homeTeam"),
        "teamname": ("home_team", "homeTeam"),
    }
    if name in {"season"}:
        date = _parse_date(
            game.get("official_commence_time")
            or game.get("commence_time")
            or game.get("game_date_et")
        )
        return game.get("season") or (date[:4] if date else None)
    if name in {"league"}:
        return game.get("league") or "MLB"
    if name in {"sport", "sportkey"}:
        return game.get("provider_sport_key") or "baseball_mlb"
    keys = aliases.get(name, ())
    for key in keys:
        value = game.get(key)
        if value not in (None, ""):
            return _parse_date(value) if "date" in name else value
    return None


def _operation_url(operation: Dict[str, Any], game: Dict[str, Any]) -> Optional[str]:
    template = str(operation.get("urlTemplate") or "")
    if not template.startswith("http"):
        return None
    query: List[Tuple[str, str]] = []
    for parameter in operation.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        name = str(parameter.get("name") or "")
        location = str(parameter.get("in") or "query")
        required = parameter.get("required") is True
        value = _game_value(game, name)
        if value in (None, ""):
            schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {}
            value = schema.get("default")
        if value in (None, ""):
            if required:
                return None
            continue
        text = str(value)
        if location == "path":
            template = template.replace("{" + name + "}", urllib.parse.quote(text, safe=""))
        elif location == "query":
            query.append((name, text))
    if re.search(r"\{[^}]+\}", template):
        return None
    if query:
        join = "&" if "?" in template else "?"
        template += join + urllib.parse.urlencode(query)
    return template


def _flatten(value: Any, *, prefix: str = "", out: Optional[List[Tuple[str, Any]]] = None) -> List[Tuple[str, Any]]:
    rows = out if out is not None else []
    if len(rows) >= MAX_SIGNAL_VALUES * 4:
        return rows
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            _flatten(item, prefix=child, out=rows)
    elif isinstance(value, list):
        for index, item in enumerate(value[:100]):
            _flatten(item, prefix=f"{prefix}[{index}]", out=rows)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        rows.append((prefix, value))
    return rows


def _signals(payload: Any, category: str) -> Dict[str, Any]:
    keywords = tuple(re.sub(r"[^a-z0-9]", "", value.lower()) for value in _SIGNAL_KEYWORDS.get(category, ()))
    selected: List[Dict[str, Any]] = []
    for path, value in _flatten(payload):
        normalized = re.sub(r"[^a-z0-9]", "", path.lower())
        if keywords and not any(keyword in normalized for keyword in keywords):
            continue
        selected.append({"path": path, "value": value})
        if len(selected) >= MAX_SIGNAL_VALUES:
            break
    return {
        "category": category,
        "valueCount": len(selected),
        "values": selected,
    }


def _cached_request(url: str, key: str, http_get: Optional[Callable[[urllib.request.Request, int], Any]]) -> Any:
    cache_key = _fingerprint({"url": _safe_url(url), "keyConfigured": bool(key)})
    cached = _RESPONSE_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] <= CACHE_SECONDS:
        return copy.deepcopy(cached[1])
    payload = _http_json(url, key=key, http_get=http_get)
    _RESPONSE_CACHE[cache_key] = (now, copy.deepcopy(payload))
    return payload


def collect_game_context(
    game: Dict[str, Any],
    *,
    as_of_utc: Optional[str] = None,
    http_get: Optional[Callable[[urllib.request.Request, int], Any]] = None,
) -> Dict[str, Any]:
    retrieved_at = as_of_utc or _now_iso()
    key = api_key()
    base: Dict[str, Any] = {
        "version": VERSION,
        "provider": "Big Balls Data Pro",
        "retrievedAtUtc": retrieved_at,
        "keyConfigured": bool(key),
        "sourceStatus": "NOT_CONFIGURED" if not key else "DISCOVERING",
        "gameIdentity": {
            "officialGamePk": game.get("official_game_pk") or game.get("officialGamePk"),
            "providerEventId": game.get("provider_event_id") or game.get("providerEventId") or game.get("id"),
            "homeTeam": game.get("home_team") or game.get("homeTeam"),
            "awayTeam": game.get("away_team") or game.get("awayTeam"),
            "commenceTime": game.get("official_commence_time") or game.get("commence_time") or game.get("commenceTime"),
        },
        "operationsAttempted": 0,
        "operationsSucceeded": 0,
        "categories": {},
        "errors": [],
    }
    if not key:
        return base

    try:
        manifest = discover_manifest(http_get=http_get)
    except Exception as exc:
        base["sourceStatus"] = "DISCOVERY_FAILED"
        base["errors"].append(f"{type(exc).__name__}:{exc}")
        return base

    operations = [row for row in manifest.get("operations") or [] if isinstance(row, dict)]
    selected: List[Dict[str, Any]] = []
    used_categories: set[str] = set()
    # One primary operation per category first, followed by additional distinct
    # operations until the strict per-game call budget is reached.
    for operation in operations:
        category = str(operation.get("category") or "games")
        if category in used_categories:
            continue
        url = _operation_url(operation, game)
        if not url:
            continue
        selected.append({**operation, "resolvedUrl": url})
        used_categories.add(category)
        if len(selected) >= MAX_CALLS_PER_GAME:
            break
    if len(selected) < MAX_CALLS_PER_GAME:
        for operation in operations:
            url = _operation_url(operation, game)
            if not url or any(row["resolvedUrl"] == url for row in selected):
                continue
            selected.append({**operation, "resolvedUrl": url})
            if len(selected) >= MAX_CALLS_PER_GAME:
                break

    base["manifestFingerprint"] = manifest.get("specFingerprint") or _fingerprint(manifest)
    base["openapiUrl"] = manifest.get("openapiUrl")
    if not selected:
        base["sourceStatus"] = "NO_APPLICABLE_OPERATIONS"
        return base

    category_rows: Dict[str, List[Dict[str, Any]]] = {}
    for operation in selected:
        url = str(operation["resolvedUrl"])
        category = str(operation.get("category") or "games")
        base["operationsAttempted"] += 1
        try:
            payload = _cached_request(url, key, http_get)
            row = {
                "operationId": operation.get("operationId"),
                "endpoint": _safe_url(url),
                "retrievedAtUtc": retrieved_at,
                "payloadFingerprint": _fingerprint(payload),
                "signals": _signals(payload, category),
            }
            category_rows.setdefault(category, []).append(row)
            base["operationsSucceeded"] += 1
        except urllib.error.HTTPError as exc:
            base["errors"].append(f"{category}:HTTP_{exc.code}:{_safe_url(url)}")
        except Exception as exc:
            base["errors"].append(f"{category}:{type(exc).__name__}:{exc}")

    base["categories"] = category_rows
    succeeded = int(base["operationsSucceeded"])
    attempted = int(base["operationsAttempted"])
    base["sourceStatus"] = (
        "CONNECTED" if succeeded == attempted and succeeded > 0
        else "PARTIAL" if succeeded > 0
        else "ERROR"
    )
    base["contextFingerprint"] = _fingerprint(
        {
            "version": VERSION,
            "retrievedAtUtc": retrieved_at,
            "gameIdentity": base["gameIdentity"],
            "manifestFingerprint": base.get("manifestFingerprint"),
            "categories": category_rows,
        }
    )
    return base


def _category_values(bbd: Dict[str, Any], category: str) -> List[Dict[str, Any]]:
    values: List[Dict[str, Any]] = []
    for row in (bbd.get("categories") or {}).get(category) or []:
        values.extend(((row.get("signals") or {}).get("values") or []))
    return values[:MAX_SIGNAL_VALUES]


def _normalized_component(
    bbd: Dict[str, Any],
    categories: Iterable[str],
    *,
    dataset: str,
) -> Dict[str, Any]:
    values: List[Dict[str, Any]] = []
    for category in categories:
        values.extend(_category_values(bbd, category))
    status = "CONNECTED" if values else (
        "ERROR" if bbd.get("sourceStatus") == "ERROR" else "MISSING_FROM_PROVIDER"
    )
    return {
        "source": "Big Balls Data Pro",
        "source_status": status,
        "required_for_advanced_eligibility": True,
        "dataset": dataset,
        "values": values[:MAX_SIGNAL_VALUES],
        "sourceProvenance": {
            "provider": "Big Balls Data Pro",
            "retrievedAtUtc": bbd.get("retrievedAtUtc"),
            "contextFingerprint": bbd.get("contextFingerprint"),
            "manifestFingerprint": bbd.get("manifestFingerprint"),
        },
    }


def _usable(component: Any) -> bool:
    if not isinstance(component, dict):
        return False
    return str(component.get("source_status") or component.get("sourceStatus") or "").upper() in {
        "CONNECTED", "CONFIRMED", "AVAILABLE", "OK"
    }


def merge_into_advanced_context(
    current: Dict[str, Any],
    bbd: Dict[str, Any],
) -> Dict[str, Any]:
    merged = copy.deepcopy(current) if isinstance(current, dict) else {}
    projections: Dict[str, Dict[str, Any]] = {
        "confirmed_probable_pitchers": _normalized_component(
            bbd, ("probable_pitchers", "player_stats"), dataset="probable starters and pitcher context"
        ),
        "confirmed_lineups": _normalized_component(
            bbd, ("confirmed_lineups",), dataset="confirmed starting lineups"
        ),
        "injuries_late_scratches_news": _normalized_component(
            bbd, ("injuries", "confirmed_lineups"), dataset="injuries, IL reports and late scratches"
        ),
        "fip_xfip": _normalized_component(
            bbd, ("player_stats", "team_stats"), dataset="pitching FIP/xFIP and related metrics"
        ),
        "wrc_plus": _normalized_component(
            bbd, ("player_stats", "team_stats", "recent_form"), dataset="offensive production and recent form"
        ),
        "bullpen_fatigue": _normalized_component(
            bbd, ("bullpen", "player_stats", "recent_form"), dataset="bullpen usage and availability"
        ),
    }
    for key, supplement in projections.items():
        existing = merged.get(key)
        if _usable(existing):
            if isinstance(existing, dict):
                existing = copy.deepcopy(existing)
                existing["bbdProSupplement"] = supplement
                merged[key] = existing
        elif _usable(supplement):
            merged[key] = supplement
        else:
            # Preserve the original missing/error reason while still attaching
            # auditable BBD evidence and failure telemetry.
            if isinstance(existing, dict):
                existing = copy.deepcopy(existing)
                existing["bbdProSupplement"] = supplement
                merged[key] = existing
            else:
                merged[key] = supplement

    merged["big_balls_data_pro"] = copy.deepcopy(bbd)
    odds_present = bool(
        merged.get("bookmakers")
        or merged.get("odds")
        or merged.get("market_context")
        or merged.get("marketContext")
    )
    official_present = bool(
        merged.get("official_game_pk")
        or merged.get("officialGamePk")
        or merged.get("schedule_authority")
        or merged.get("scheduleAuthority")
    )
    merged["three_api_source_status"] = {
        "version": VERSION,
        "mlbStatsApi": "PRESENT" if official_present else "PRESENT_IN_GAME_ENVELOPE",
        "theOddsApi": "PRESENT" if odds_present else "PRESENT_IN_GAME_ENVELOPE",
        "bigBallsDataPro": bbd.get("sourceStatus"),
        "bigBallsDataContextFingerprint": bbd.get("contextFingerprint"),
    }
    return merged
