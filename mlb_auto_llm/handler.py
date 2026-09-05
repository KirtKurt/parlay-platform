from __future__ import annotations

import copy
import functools
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import boto3

VERSION = "MLB-AUTO-LLM-v1-three-source-autonomous"
ET = ZoneInfo("America/New_York")
TABLE_NAME = os.environ.get("MLB_AUTO_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
BBS_SECRET_ARN = os.environ.get("BBS_API_SECRET_ARN", "")
TARGET_ACCURACY = float(os.environ.get("MLB_AUTO_TARGET_DAILY_ACCURACY", "0.80"))
CARD_LEAD_MINUTES = int(os.environ.get("MLB_AUTO_CARD_LEAD_MINUTES_BEFORE_SECOND_GAME", "45"))
FIRST_GAME_SAFETY_MINUTES = int(os.environ.get("MLB_AUTO_FIRST_GAME_SAFETY_MINUTES", "10"))
FINAL_WINDOW_MINUTES = int(os.environ.get("MLB_AUTO_FINAL_COLLECTION_WINDOW_MINUTES", "20"))
BBS_BASE_URL = os.environ.get("BBS_BASE_URL", "https://api.bigballsdata.com").rstrip("/")
BEDROCK_MODELS = [
    item.strip()
    for item in os.environ.get(
        "MLB_AUTO_BEDROCK_MODELS",
        "us.amazon.nova-2-lite-v1:0,us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0",
    ).split(",")
    if item.strip()
]

DDB = boto3.resource("dynamodb")
TABLE = DDB.Table(TABLE_NAME) if TABLE_NAME else None
SECRETS = boto3.client("secretsmanager")
BEDROCK = boto3.client("bedrock-runtime")

ODDS_MARKETS = [
    "h2h", "spreads", "totals", "alternate_spreads", "alternate_totals", "team_totals",
    "h2h_1st_1_innings", "spreads_1st_1_innings", "totals_1st_1_innings",
    "h2h_1st_3_innings", "spreads_1st_3_innings", "totals_1st_3_innings",
    "h2h_1st_5_innings", "spreads_1st_5_innings", "totals_1st_5_innings",
    "h2h_1st_7_innings", "spreads_1st_7_innings", "totals_1st_7_innings",
    "batter_home_runs", "batter_hits", "batter_total_bases", "batter_rbis",
    "batter_runs_scored", "batter_hits_runs_rbis", "batter_singles", "batter_doubles",
    "batter_triples", "batter_walks", "batter_strikeouts", "batter_stolen_bases",
    "batter_fantasy_score", "pitcher_strikeouts", "pitcher_hits_allowed", "pitcher_walks",
    "pitcher_earned_runs", "pitcher_outs", "pitcher_to_record_a_win",
]

BBS_MATCH_FIELDS = "scores,odds,lineups,stats,events"
ODDS_MATCH_MAX_DRIFT_SECONDS = 12 * 3600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _ddb(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(round(value, 8)))
    if isinstance(value, dict):
        return {str(k): _ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_ddb(v) for v in value if v is not None]
    return value


def _http_json(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: int = 20) -> Tuple[Any, Dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": "inqsi-mlb-auto-llm/1.0", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return payload, {str(k).lower(): str(v) for k, v in response.headers.items()}


def _normalize(name: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
    aliases = {
        "athletics": "athletics", "oakland athletics": "athletics", "a s": "athletics",
        "la dodgers": "los angeles dodgers", "la angels": "los angeles angels",
        "ny yankees": "new york yankees", "ny mets": "new york mets",
        "chi cubs": "chicago cubs", "chi white sox": "chicago white sox",
        "tb rays": "tampa bay rays", "sf giants": "san francisco giants",
        "sd padres": "san diego padres", "az diamondbacks": "arizona diamondbacks",
    }
    return aliases.get(text, text)


def _team_name(container: Any) -> str:
    if isinstance(container, dict):
        for key in ("name", "team_name", "display_name", "short_name"):
            if container.get(key):
                return str(container[key])
    return str(container or "")


# MLB_AUTO_PACKET_STORAGE_GZIP_CHUNKED_V1
def _packet_item_too_large(exc: BaseException) -> bool:
    """Recognize only the real DynamoDB oversized-item failure."""
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) or {}
    code = str(error.get("Code", ""))
    message = " ".join(str(error.get("Message", "")).lower().split())
    status = int((response.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode") or 0)
    return status == 413 or (
        code == "ValidationException"
        and "item size has exceeded the maximum allowed size" in message
    )


def _put(pk: str, sk: str, data: Dict[str, Any], *, condition: Optional[str] = None) -> bool:
    if TABLE is None:
        raise RuntimeError("MLB_AUTO_TABLE_NOT_CONFIGURED")
    kwargs: Dict[str, Any] = {"Item": {"PK": pk, "SK": sk, "data": _ddb(data), "updatedAtUtc": _iso(_now())}}
    if condition:
        kwargs["ConditionExpression"] = condition
    try:
        TABLE.put_item(**kwargs)
        return True
    except Exception as exc:
        code = str((getattr(exc, "response", {}) or {}).get("Error", {}).get("Code", ""))
        if code == "ConditionalCheckFailedException":
            return False
        if not str(pk).startswith("PACKET#") or not _packet_item_too_large(exc):
            raise

        # DynamoDB has a hard 400 KB item limit. Full expanded provider packets can
        # exceed it. Persist the exact packet losslessly as deterministic gzip chunks
        # instead of dropping fields or silently failing the production cycle.
        import gzip
        import hashlib
        raw = json.dumps(_plain(data), separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
        digest = hashlib.sha256(raw).hexdigest()
        chunk_size = 240000
        chunks = [compressed[i:i + chunk_size] for i in range(0, len(compressed), chunk_size)] or [b""]
        for index, chunk in enumerate(chunks):
            TABLE.put_item(Item={
                "PK": pk,
                "SK": f"{sk}#CHUNK#{index:04d}",
                "data": {
                    "storageEncoding": "gzip-json-chunk-v1",
                    "chunkIndex": index,
                    "chunkCount": len(chunks),
                    "payload": chunk,
                },
                "updatedAtUtc": _iso(_now()),
            })
        manifest = {
            "storageEncoding": "gzip-json-chunked-v1",
            "chunkCount": len(chunks),
            "compressedBytes": len(compressed),
            "uncompressedBytes": len(raw),
            "sha256": digest,
        }
        manifest_kwargs: Dict[str, Any] = {"Item": {"PK": pk, "SK": sk, "data": manifest, "updatedAtUtc": _iso(_now())}}
        if condition:
            manifest_kwargs["ConditionExpression"] = condition
        try:
            TABLE.put_item(**manifest_kwargs)
        except Exception as manifest_exc:
            if str((getattr(manifest_exc, "response", {}) or {}).get("Error", {}).get("Code", "")) == "ConditionalCheckFailedException":
                return False
            raise
        return True


def _get(pk: str, sk: str) -> Optional[Dict[str, Any]]:
    if TABLE is None:
        return None
    result = TABLE.get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True)
    item = result.get("Item") if isinstance(result, dict) else None
    stored = (item or {}).get("data")
    plain = _plain(stored) if isinstance(stored, dict) else None
    if not isinstance(plain, dict):
        return None
    if plain.get("storageEncoding") != "gzip-json-chunked-v1":
        return plain

    import gzip
    import hashlib
    count = int(plain.get("chunkCount") or 0)
    if count <= 0:
        raise RuntimeError("MLB_AUTO_PACKET_CHUNK_MANIFEST_INVALID")
    parts = []
    for index in range(count):
        child = TABLE.get_item(Key={"PK": pk, "SK": f"{sk}#CHUNK#{index:04d}"}, ConsistentRead=True).get("Item") or {}
        child_data = child.get("data") or {}
        payload = child_data.get("payload")
        if payload is None:
            raise RuntimeError(f"MLB_AUTO_PACKET_CHUNK_MISSING:{index}")
        parts.append(bytes(payload))
    raw = gzip.decompress(b"".join(parts))
    if hashlib.sha256(raw).hexdigest() != str(plain.get("sha256") or ""):
        raise RuntimeError("MLB_AUTO_PACKET_CHUNK_SHA256_MISMATCH")
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else None


def _official_schedule(slate: str) -> Dict[str, Any]:
    params = urllib.parse.urlencode({
        "sportId": "1",
        "date": datetime.strptime(slate, "%Y-%m-%d").strftime("%m/%d/%Y"),
        "hydrate": "probablePitcher,venue,team,linescore",
    })
    url = f"https://statsapi.mlb.com/api/v1/schedule?{params}"
    payload, _ = _http_json(url)
    games: List[Dict[str, Any]] = []
    for date_row in payload.get("dates") or []:
        for raw in date_row.get("games") or []:
            teams = raw.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            games.append({
                "gamePk": str(raw.get("gamePk") or ""),
                "officialDate": raw.get("officialDate"),
                "gameDate": raw.get("gameDate"),
                "gameType": raw.get("gameType"),
                "gameNumber": raw.get("gameNumber"),
                "doubleHeader": raw.get("doubleHeader"),
                "status": copy.deepcopy(raw.get("status") or {}),
                "venue": copy.deepcopy(raw.get("venue") or {}),
                "home": {
                    "name": _team_name(home.get("team")),
                    "id": (home.get("team") or {}).get("id"),
                    "leagueRecord": copy.deepcopy(home.get("leagueRecord") or {}),
                    "probablePitcher": copy.deepcopy(home.get("probablePitcher") or {}),
                    "score": home.get("score"),
                },
                "away": {
                    "name": _team_name(away.get("team")),
                    "id": (away.get("team") or {}).get("id"),
                    "leagueRecord": copy.deepcopy(away.get("leagueRecord") or {}),
                    "probablePitcher": copy.deepcopy(away.get("probablePitcher") or {}),
                    "score": away.get("score"),
                },
                "linescore": copy.deepcopy(raw.get("linescore") or {}),
            })
    games.sort(key=lambda row: (str(row.get("gameDate") or ""), str(row.get("gamePk") or "")))
    return {"source": "MLB Stats API", "url": url, "totalGames": len(games), "games": games}


def _odds_api_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _odds_slate_bounds(slate: str) -> Tuple[str, str]:
    """Return the exact inclusive UTC interval for one Eastern slate date."""

    slate_date = datetime.strptime(slate, "%Y-%m-%d").date()
    start_et = datetime.combine(slate_date, datetime.min.time(), tzinfo=ET)
    next_start_et = datetime.combine(
        slate_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=ET,
    )
    end_et = next_start_et - timedelta(seconds=1)
    return _odds_api_timestamp(start_et), _odds_api_timestamp(end_et)


def _bounded_odds_rows(
    rows: Any,
    start_utc: str,
    end_utc: str,
) -> List[Dict[str, Any]]:
    """Enforce the provider's inclusive bounds locally as a second guard."""

    start = _parse(start_utc)
    end = _parse(end_utc)
    if start is None or end is None:
        raise RuntimeError("ODDS_SLATE_BOUNDS_INVALID")
    bounded: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        commence = _parse(row.get("commence_time") or row.get("commenceTime"))
        if commence is None or commence < start or commence > end:
            continue
        bounded.append(copy.deepcopy(row))
    return bounded


def _validated_odds_event_rows(payload: Any, *, label: str) -> List[Dict[str, Any]]:
    if not isinstance(payload, list):
        raise RuntimeError(f"{label}_NOT_LIST")
    rows: List[Dict[str, Any]] = []
    seen_ids = set()
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise RuntimeError(f"{label}_ROW_NOT_OBJECT:{index}")
        event_id = str(row.get("id") or "").strip()
        home = _normalize(row.get("home_team") or row.get("homeTeam"))
        away = _normalize(row.get("away_team") or row.get("awayTeam"))
        start = _parse(row.get("commence_time") or row.get("commenceTime"))
        if not event_id or not home or not away or home == away or start is None:
            raise RuntimeError(f"{label}_ROW_IDENTITY_INVALID:{index}")
        if event_id in seen_ids:
            raise RuntimeError(f"{label}_DUPLICATE_EVENT_ID:{event_id}")
        seen_ids.add(event_id)
        rows.append(copy.deepcopy(row))
    return rows


def _odds_core(slate: str) -> Dict[str, Any]:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY_MISSING")
    api_base = "https://api.the-odds-api.com/v4/sports/baseball_mlb"
    start_utc, end_utc = _odds_slate_bounds(slate)
    bounds = {
        "commenceTimeFrom": start_utc,
        "commenceTimeTo": end_utc,
    }

    # /events is quota-free and distinguishes a healthy provider/catalog
    # integration from a fixture whose sportsbooks have not posted lines yet.
    catalog_params = urllib.parse.urlencode(
        {
            "apiKey": ODDS_API_KEY,
            "dateFormat": "iso",
            **bounds,
        }
    )
    catalog_payload: List[Dict[str, Any]] = []
    catalog_request_ok = False
    catalog_error: Optional[str] = None
    try:
        raw_catalog, _ = _http_json(
            f"{api_base}/events?{catalog_params}",
            timeout=25,
        )
        catalog_payload = _validated_odds_event_rows(
            raw_catalog,
            label="ODDS_EVENTS",
        )
        catalog_request_ok = True
    except Exception as exc:
        # The quota-free catalogue improves diagnostics but is not price
        # authority.  A schema-valid /odds response carries sufficient exact
        # identity/time data and must remain usable when /events is transiently
        # unavailable.
        catalog_error = type(exc).__name__

    last_error: Optional[Exception] = None
    for regions in ("us,us2,uk,eu,au", "us,uk,eu,au", "us"):
        params = urllib.parse.urlencode({
            "apiKey": ODDS_API_KEY,
            "regions": regions,
            "markets": "h2h,spreads,totals",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
            **bounds,
        })
        try:
            payload, headers = _http_json(
                f"{api_base}/odds?{params}",
                timeout=25,
            )
            price_rows = _validated_odds_event_rows(
                payload,
                label="ODDS_CORE",
            )
            return {
                "source": "The Odds API",
                "regions": regions,
                "events": _bounded_odds_rows(
                    price_rows,
                    start_utc,
                    end_utc,
                ),
                "catalogEvents": _bounded_odds_rows(
                    catalog_payload,
                    start_utc,
                    end_utc,
                ),
                "slateDateEt": slate,
                "slateBoundsUtc": {
                    "fromInclusive": start_utc,
                    "toInclusive": end_utc,
                },
                "oddsRequestOk": True,
                "catalogRequestOk": catalog_request_ok,
                "catalogError": catalog_error,
                "quota": {
                    "remaining": headers.get("x-requests-remaining"),
                    "used": headers.get("x-requests-used"),
                    "last": headers.get("x-requests-last"),
                },
            }
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"ODDS_CORE_FAILED:{type(last_error).__name__}")


def _odds_exact_h2h_pairs(
    event: Any,
    home: Any,
    away: Any,
) -> List[Tuple[float, float]]:
    """Return at most one validated (home, away) decimal pair per book."""

    if not isinstance(event, dict) or not str(event.get("id") or "").strip():
        return []
    expected_home = _normalize(home)
    expected_away = _normalize(away)
    if not expected_home or not expected_away or expected_home == expected_away:
        return []
    if (
        _normalize(event.get("home_team") or event.get("homeTeam"))
        != expected_home
        or _normalize(event.get("away_team") or event.get("awayTeam"))
        != expected_away
    ):
        return []
    pairs: List[Tuple[float, float]] = []
    for bookmaker in event.get("bookmakers") or []:
        if not isinstance(bookmaker, dict):
            continue
        for market in bookmaker.get("markets") or []:
            if not isinstance(market, dict) or market.get("key") != "h2h":
                continue
            prices: Dict[str, float] = {}
            for outcome in market.get("outcomes") or []:
                if not isinstance(outcome, dict):
                    continue
                price = outcome.get("price")
                if isinstance(price, bool):
                    continue
                try:
                    numeric = float(price)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(numeric) or numeric <= 1.0:
                    continue
                prices[_normalize(outcome.get("name"))] = numeric
            if expected_home in prices and expected_away in prices:
                pairs.append((prices[expected_home], prices[expected_away]))
                break
    return pairs


def _odds_has_exact_h2h(event: Any, home: Any, away: Any) -> bool:
    """Require one bookmaker with usable prices for both exact MLB teams."""

    return bool(_odds_exact_h2h_pairs(event, home, away))


def _odds_match_drift_seconds(
    game: Dict[str, Any],
    event: Dict[str, Any],
) -> Optional[int]:
    if not str(event.get("id") or "").strip():
        return None
    home = _normalize((game.get("home") or {}).get("name"))
    away = _normalize((game.get("away") or {}).get("name"))
    if (
        _normalize(event.get("home_team") or event.get("homeTeam")) != home
        or _normalize(event.get("away_team") or event.get("awayTeam")) != away
    ):
        return None
    official_start = _parse(game.get("gameDate"))
    provider_start = _parse(
        event.get("commence_time") or event.get("commenceTime")
    )
    if official_start is None or provider_start is None:
        return None
    if official_start.astimezone(ET).date() != provider_start.astimezone(ET).date():
        return None
    drift = abs(int((provider_start - official_start).total_seconds()))
    return drift if drift <= ODDS_MATCH_MAX_DRIFT_SECONDS else None


def _assign_odds_events(
    official_games: Iterable[Dict[str, Any]],
    rows: Iterable[Dict[str, Any]],
    *,
    require_h2h: bool,
) -> Dict[str, Dict[str, Any]]:
    """Assign unique ordered-team events at maximum coverage/minimum drift."""

    games_by_pair: Dict[Tuple[str, str], List[Tuple[int, Dict[str, Any]]]] = {}
    for index, game in enumerate(official_games or []):
        if not isinstance(game, dict) or not str(game.get("gamePk") or ""):
            continue
        pair = (
            _normalize((game.get("home") or {}).get("name")),
            _normalize((game.get("away") or {}).get("name")),
        )
        if not all(pair):
            continue
        games_by_pair.setdefault(pair, []).append((index, game))

    events_by_pair: Dict[Tuple[str, str], List[Tuple[int, Dict[str, Any]]]] = {}
    seen_event_ids = set()
    for index, event in enumerate(rows or []):
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id or event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        pair = (
            _normalize(event.get("home_team") or event.get("homeTeam")),
            _normalize(event.get("away_team") or event.get("awayTeam")),
        )
        if not all(pair):
            continue
        events_by_pair.setdefault(pair, []).append((index, event))

    assigned: Dict[str, Dict[str, Any]] = {}
    for pair, indexed_games in games_by_pair.items():
        indexed_events = events_by_pair.get(pair) or []
        if not indexed_events:
            continue
        indexed_games.sort(
            key=lambda item: (
                _parse(item[1].get("gameDate"))
                or datetime.max.replace(tzinfo=timezone.utc),
                str(item[1].get("gamePk") or ""),
                item[0],
            )
        )
        indexed_events.sort(
            key=lambda item: (
                _parse(
                    item[1].get("commence_time")
                    or item[1].get("commenceTime")
                )
                or datetime.max.replace(tzinfo=timezone.utc),
                str(item[1].get("id") or ""),
                item[0],
            )
        )
        games = [item[1] for item in indexed_games]
        events = [item[1] for item in indexed_events]
        drift_by_edge: Dict[Tuple[int, int], int] = {}
        for game_index, game in enumerate(games):
            for event_index, event in enumerate(events):
                drift = _odds_match_drift_seconds(game, event)
                if drift is None:
                    continue
                if require_h2h and not _odds_has_exact_h2h(
                    event,
                    (game.get("home") or {}).get("name"),
                    (game.get("away") or {}).get("name"),
                ):
                    continue
                drift_by_edge[(game_index, event_index)] = drift

        def choice_key(
            value: Tuple[int, int, Tuple[Optional[int], ...]],
        ) -> Tuple[int, int, Tuple[int, ...]]:
            count, drift, choices = value
            sentinel = len(events) + 1
            return (
                -count,
                drift,
                tuple(sentinel if item is None else item for item in choices),
            )

        @functools.lru_cache(maxsize=None)
        def solve(
            game_index: int,
            used_mask: int,
        ) -> Tuple[int, int, Tuple[Optional[int], ...]]:
            if game_index >= len(games):
                return 0, 0, ()
            tail = solve(game_index + 1, used_mask)
            best = (tail[0], tail[1], (None,) + tail[2])
            for event_index in range(len(events)):
                bit = 1 << event_index
                edge = (game_index, event_index)
                if used_mask & bit or edge not in drift_by_edge:
                    continue
                tail = solve(game_index + 1, used_mask | bit)
                candidate = (
                    tail[0] + 1,
                    tail[1] + drift_by_edge[edge],
                    (event_index,) + tail[2],
                )
                if choice_key(candidate) < choice_key(best):
                    best = candidate
            return best

        _, _, choices = solve(0, 0)
        for game, event_index in zip(games, choices):
            if event_index is not None:
                assigned[str(game.get("gamePk"))] = copy.deepcopy(
                    events[event_index]
                )
    return assigned


def _odds_catalog_identity(
    event: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(event, dict):
        return None
    return {
        key: copy.deepcopy(event.get(key))
        for key in (
            "id",
            "sport_key",
            "sport_title",
            "commence_time",
            "home_team",
            "away_team",
        )
        if event.get(key) is not None
    }


def _odds_event_markets(event_id: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"eventId": event_id, "markets": {}, "errors": {}}
    # One market per request is intentionally conservative: an unsupported key
    # cannot suppress all other available MLB markets on the account.
    for market in ODDS_MARKETS:
        params = urllib.parse.urlencode({
            "apiKey": ODDS_API_KEY, "regions": "us", "markets": market,
            "oddsFormat": "decimal", "dateFormat": "iso",
        })
        url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{urllib.parse.quote(event_id)}/odds?{params}"
        try:
            payload, headers = _http_json(url, timeout=12)
            if isinstance(payload, dict):
                out["markets"][market] = payload
                out["quota"] = {
                    "remaining": headers.get("x-requests-remaining"),
                    "used": headers.get("x-requests-used"),
                    "last": headers.get("x-requests-last"),
                }
        except urllib.error.HTTPError as exc:
            out["errors"][market] = f"HTTP_{exc.code}"
        except Exception as exc:
            out["errors"][market] = type(exc).__name__
    return out


def _bbs_key() -> str:
    if not BBS_SECRET_ARN:
        raise RuntimeError("BBS_API_SECRET_ARN_MISSING")
    response = SECRETS.get_secret_value(SecretId=BBS_SECRET_ARN)
    value = str(response.get("SecretString") or "").strip()
    if not value:
        raise RuntimeError("BBS_API_KEY_MISSING")
    return value


def _bbs_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"{BBS_BASE_URL}{path}" + (f"?{query}" if query else "")
    payload, _ = _http_json(url, headers={"Authorization": f"Bearer {_bbs_key()}"}, timeout=15)
    if not isinstance(payload, dict):
        raise RuntimeError("BBS_RESPONSE_INVALID:NOT_OBJECT")
    if payload.get("error") is not None:
        raise RuntimeError("BBS_RESPONSE_INVALID:REPORTED_ERROR")
    required = {"data", "meta", "error"}
    if required.issubset(payload):
        if not isinstance(payload.get("meta"), dict):
            raise RuntimeError("BBS_RESPONSE_INVALID:META_NOT_OBJECT")
        return payload
    # The provider's dedicated stored-match catalogue uses the separately
    # observed `{data, pagination}` contract. Scope this exception to that
    # exact endpoint and synthesize explicit provenance for downstream audit.
    if (
        path == "/v1/stored/matches"
        and isinstance(payload.get("data"), list)
        and isinstance(payload.get("pagination"), dict)
    ):
        value = copy.deepcopy(payload)
        value["meta"] = {
            "source": "stored-catalogue",
            "catalogueContract": "data_plus_pagination",
        }
        value.setdefault("error", None)
        return value
    keys = ",".join(sorted(str(key) for key in payload)[:12])
    raise RuntimeError(f"BBS_RESPONSE_INVALID:ENVELOPE_KEYS[{keys}]")


def _bbs_shape(value: Any) -> str:
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        keys = ",".join(sorted(str(key) for key in value)[:12])
        return f"object[{keys}]"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _bbs_payload_rows(payload: Any) -> List[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data
    if not isinstance(rows, list):
        meta = payload.get("meta") if isinstance(payload, dict) else None
        diagnostic = {
            "dataShape": _bbs_shape(data),
            "scoresShape": _bbs_shape(data.get("scores"))
            if isinstance(data, dict) and "scores" in data
            else "missing",
            "metaKeys": sorted(str(key) for key in meta)[:12]
            if isinstance(meta, dict)
            else [],
            "metaShape": _bbs_shape(meta),
            "topLevelKeys": sorted(str(key) for key in payload)[:12]
            if isinstance(payload, dict)
            else [],
        }
        raise RuntimeError(
            "BBS_MATCHES_NOT_LIST:"
            + json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
        )
    invalid = [index for index, row in enumerate(rows) if not isinstance(row, dict)]
    if invalid:
        raise RuntimeError(
            "BBS_MATCH_ROW_NOT_OBJECT:"
            + json.dumps(
                {"indexes": invalid[:12], "rowCount": len(rows)},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return copy.deepcopy(rows)


def _bbs_is_scores_field_envelope(payload: Any) -> bool:
    data = payload.get("data") if isinstance(payload, dict) else None
    scores = data.get("scores") if isinstance(data, dict) else None
    return (
        isinstance(data, dict)
        and set(data) == {"scores"}
        and isinstance(scores, dict)
        and isinstance(scores.get("value"), list)
    )


def _bbs_event_identity(row: Dict[str, Any]) -> str:
    for key in (
        "id", "match_id", "matchId", "event_id", "eventId",
        "fixture_id", "fixtureId", "game_id", "gameId", "uuid",
    ):
        if row.get(key):
            return f"id:{row[key]}"
    home = _team_name(row.get("home") or row.get("home_team") or row.get("homeTeam"))
    away = _team_name(row.get("away") or row.get("away_team") or row.get("awayTeam"))
    start = (
        row.get("kickoff_utc")
        or row.get("start_time")
        or row.get("startTime")
        or row.get("commence_time")
        or row.get("commenceTime")
        or row.get("scheduled_at")
        or row.get("scheduledAt")
        or row.get("game_date")
        or row.get("gameDate")
        or row.get("date")
    )
    return "fallback:" + json.dumps(
        [_normalize(home), _normalize(away), str(start or "")],
        separators=(",", ":"),
    )


def _bbs_provider_event_id(row: Dict[str, Any]) -> str:
    for key in (
        "id", "match_id", "matchId", "event_id", "eventId",
        "fixture_id", "fixtureId", "game_id", "gameId", "uuid",
    ):
        if row.get(key):
            return str(row[key]).strip()
    return ""


def _bbs_provider_team_name(row: Dict[str, Any], side: str) -> str:
    for key in (
        side,
        side + "_team",
        side + "Team",
        side + "_team_name",
        side + "TeamName",
        side + "_name",
        side + "Name",
    ):
        if row.get(key) is None:
            continue
        value = row.get(key)
        if isinstance(value, dict) and isinstance(value.get("team"), dict):
            value = value.get("team")
        return _team_name(value)
    return ""


def _bbs_provider_start(row: Dict[str, Any]) -> Any:
    for key in (
        "kickoff_utc",
        "start_time",
        "startTime",
        "commence_time",
        "commenceTime",
        "scheduled_at",
        "scheduledAt",
        "scheduled",
        "game_date",
        "gameDate",
        "date",
    ):
        if not row.get(key):
            continue
        value = row.get(key)
        if isinstance(value, dict):
            value = value.get("utc") or value.get("dateTime") or value.get("value")
        return value
    return None


def _canonical_bbs_event(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _bbs_provider_event_id(row),
        "home_team": _bbs_provider_team_name(row, "home"),
        "away_team": _bbs_provider_team_name(row, "away"),
        "commence_time": _bbs_provider_start(row),
    }


def _bbs_event_on_slate(row: Dict[str, Any], slate: str) -> bool:
    start = _parse(_bbs_provider_start(row))
    return bool(
        _bbs_provider_event_id(row)
        and start is not None
        and start.astimezone(ET).date().isoformat() == slate
    )


def _bbs_match_drift_seconds(
    game: Dict[str, Any],
    event: Dict[str, Any],
) -> Optional[int]:
    return _odds_match_drift_seconds(game, _canonical_bbs_event(event))


def _assign_bbs_events(
    official_games: Iterable[Dict[str, Any]],
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Use the same exact, unique minimum-drift assignment as Odds."""

    originals: Dict[str, Dict[str, Any]] = {}
    canonical: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        normalized = _canonical_bbs_event(row)
        event_id = str(normalized.get("id") or "")
        if not event_id or event_id in originals:
            continue
        originals[event_id] = row
        canonical.append(normalized)
    assignments = _assign_odds_events(
        official_games,
        canonical,
        require_h2h=False,
    )
    return {
        game_pk: copy.deepcopy(originals[str(event.get("id"))])
        for game_pk, event in assignments.items()
        if str(event.get("id") or "") in originals
    }


def _dedupe_bbs_events(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        identity = _bbs_event_identity(row)
        if identity in seen:
            continue
        seen.add(identity)
        events.append(copy.deepcopy(row))
    return events


def _official_utc_dates(slate: str, official: Dict[str, Any]) -> List[str]:
    values = set()
    for game in official.get("games") or []:
        if not isinstance(game, dict):
            continue
        start = _parse(game.get("gameDate"))
        if start is not None:
            values.add(start.date().isoformat())
    return sorted(values or {slate})


def _bbs_official_coverage(
    official: Dict[str, Any], rows: Iterable[Dict[str, Any]]
) -> Tuple[List[str], List[str]]:
    games = [
        game for game in official.get("games") or [] if isinstance(game, dict)
    ]
    assignments = _assign_bbs_events(games, rows)
    matched = [
        str(game.get("gamePk") or "")
        for game in games
        if str(game.get("gamePk") or "") in assignments
    ]
    missing = [
        str(game.get("gamePk") or "")
        for game in games
        if str(game.get("gamePk") or "") not in assignments
    ]
    return matched, missing


def _bbs_matches(
    slate: str, official: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    official = official if isinstance(official, dict) else {"games": []}
    utc_dates = _official_utc_dates(slate, official)
    rows: List[Dict[str, Any]] = []
    queries: List[Dict[str, Any]] = []
    provider_meta: List[Dict[str, Any]] = []

    def collect(label: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        response_path = "/v1/matches"
        payload = _bbs_get(response_path, params)
        live_envelope_quarantined = None
        if _bbs_is_scores_field_envelope(payload):
            # This value contains score records, not match catalogue rows with
            # team/start identity. Never award it BBS matchup coverage. Resolve
            # identities through BBS's dedicated stored-match catalogue.
            live_envelope_quarantined = "data.scores.value"
            response_path = "/v1/stored/matches"
            payload = _bbs_get(response_path, params)
        found = _bbs_payload_rows(payload)
        rows.extend(found)
        queries.append({
            "label": label,
            "params": copy.deepcopy(params),
            "count": len(found),
            "responsePath": response_path,
            "responseEnvelope": "data.array",
            "liveEnvelopeQuarantined": live_envelope_quarantined,
        })
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if isinstance(meta, dict):
            provider_meta.append(copy.deepcopy(meta))
        return found

    # BBD's documented date filter is UTC. An Eastern MLB slate can therefore
    # span two UTC dates, so use the official MLB gameDate values as the query
    # authority rather than treating the ET slate date as a UTC date.
    for date_value in utc_dates:
        collect(
            "official_utc_date",
            {
                "sport": "baseball",
                "league": "mlb",
                "date": date_value,
                "limit": 200,
                "offset": 0,
            },
        )

    events = [
        row
        for row in _dedupe_bbs_events(rows)
        if _bbs_event_on_slate(row, slate)
    ]
    matched, missing = _bbs_official_coverage(official, events)
    fallback_used = False

    # The documented unfiltered league view is a bounded recovery path for a
    # delayed/misdated provider row. A row is admitted only after the existing
    # official team/start-time crosswalk succeeds.
    for offset in (0, 200, 400):
        if not missing:
            break
        fallback_used = True
        found = collect(
            "league_unfiltered_offset",
            {
                "sport": "baseball",
                "league": "mlb",
                "limit": 200,
                "offset": offset,
            },
        )
        events = [
            row
            for row in _dedupe_bbs_events(rows)
            if _bbs_event_on_slate(row, slate)
        ]
        matched, missing = _bbs_official_coverage(official, events)
        if len(found) < 200:
            break

    assignments = _assign_bbs_events(official.get("games") or [], events)
    return {
        "source": "Big Balls Sports Data",
        "events": events,
        "assignments": assignments,
        "meta": {
            "resolver": "official_utc_date_union_exact_et_unique_v2",
            "officialUtcDates": utc_dates,
            "queries": queries,
            "providerMeta": provider_meta,
            "storedCatalogueFallbackUsed": any(
                query.get("responsePath") == "/v1/stored/matches"
                for query in queries
            ),
            "unfilteredFallbackUsed": fallback_used,
            "expectedOfficialGameCount": len(official.get("games") or []),
            "matchedOfficialGameCount": len(assignments),
            "missingOfficialGamePks": sorted(
                {
                    str(game.get("gamePk") or "")
                    for game in official.get("games") or []
                    if isinstance(game, dict)
                }
                - set(assignments)
            ),
        },
    }


def _safe_bbs(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        value = _bbs_get(path, params)
        return {"ok": True, "data": value.get("data"), "meta": value.get("meta") or {}}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP_{exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def _bbs_event_bundle(event: Dict[str, Any]) -> Dict[str, Any]:
    match_id = _bbs_provider_event_id(event)
    if not match_id:
        return {"ok": False, "error": "BBS_MATCH_ID_MISSING"}
    quoted = urllib.parse.quote(match_id, safe="")
    return {
        "ok": True,
        "match": copy.deepcopy(event),
        "detail": _safe_bbs(f"/v1/matches/{quoted}", {"sport": "baseball", "fields": BBS_MATCH_FIELDS}),
        "odds": _safe_bbs(f"/v1/matches/{quoted}/odds", {"sport": "baseball"}),
        "statistics": _safe_bbs(f"/v1/matches/{quoted}/statistics", {"sport": "baseball"}),
        "lineups": _safe_bbs(f"/v1/stored/matches/{quoted}/lineups"),
    }


def _match_event(game: Dict[str, Any], rows: Iterable[Dict[str, Any]], *, provider: str) -> Optional[Dict[str, Any]]:
    matches: List[Tuple[int, Dict[str, Any]]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if provider == "odds":
            drift = _odds_match_drift_seconds(game, row)
        else:
            drift = _bbs_match_drift_seconds(game, row)
        if drift is not None:
            matches.append((drift, row))
    matches.sort(key=lambda item: item[0])
    return copy.deepcopy(matches[0][1]) if matches else None


def _market_consensus(game: Dict[str, Any]) -> Dict[str, Any]:
    home = str((game.get("home") or {}).get("name") or "")
    away = str((game.get("away") or {}).get("name") or "")
    event = game.get("oddsCore") or {}
    home_probs: List[float] = []
    away_probs: List[float] = []
    for home_price, away_price in _odds_exact_h2h_pairs(event, home, away):
        raw_h, raw_a = 1.0 / home_price, 1.0 / away_price
        total = raw_h + raw_a
        home_probs.append(raw_h / total)
        away_probs.append(raw_a / total)
    home_p = sum(home_probs) / len(home_probs) if home_probs else None
    away_p = sum(away_probs) / len(away_probs) if away_probs else None
    if home_p is None or away_p is None:
        return {"available": False, "bookCount": 0}
    winner = home if home_p >= away_p else away
    return {
        "available": True,
        "bookCount": len(home_probs),
        "homeProbability": round(home_p, 6),
        "awayProbability": round(away_p, 6),
        "marketFavorite": winner,
        "marketFavoriteProbability": round(max(home_p, away_p), 6),
    }


def _deadline(schedule: Dict[str, Any]) -> Dict[str, Any]:
    starts = [_parse(row.get("gameDate")) for row in schedule.get("games") or []]
    starts = sorted(dt for dt in starts if dt is not None)
    if not starts:
        return {"publishDeadlineUtc": None, "reason": "NO_GAMES"}
    first = starts[0]
    second = starts[1] if len(starts) > 1 else starts[0]
    requested = second - timedelta(minutes=CARD_LEAD_MINUTES)
    first_safety = first - timedelta(minutes=FIRST_GAME_SAFETY_MINUTES)
    actual = min(requested, first_safety)
    return {
        "firstGameUtc": _iso(first),
        "secondGameUtc": _iso(second),
        "requestedSecondGameLeadMinutes": CARD_LEAD_MINUTES,
        "requestedDeadlineUtc": _iso(requested),
        "firstGameSafetyDeadlineUtc": _iso(first_safety),
        "publishDeadlineUtc": _iso(actual),
    }


def _recent_accuracy_state() -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    today = _now().astimezone(ET).date()
    for days in range(1, 15):
        slate = (today - timedelta(days=days)).isoformat()
        audit = _get(f"AUDIT#{slate}", "FINAL")
        if audit and int(audit.get("graded") or 0) > 0:
            rows.append(audit)
    graded = sum(int(row.get("graded") or 0) for row in rows)
    correct = sum(int(row.get("correct") or 0) for row in rows)
    aggregate = correct / graded if graded else None
    return {
        "targetDailyAccuracy": TARGET_ACCURACY,
        "targetRole": "long_term_goal_not_a_decision_weight_or_advancement_gate",
        "recentDays": len(rows),
        "recentGradedPicks": graded,
        "recentCorrectPicks": correct,
        "recentAccuracy": round(aggregate, 6) if aggregate is not None else None,
        "decisionWeights": {
            "liveBaseballContext": 0.40,
            "historicalModelFindings": 0.30,
            "moneylineMovement": 0.20,
            "currentMarketLevel": 0.10,
        },
        "marketFavoriteFallbackAllowed": False,
        "policy": (
            "Recent accuracy is diagnostic only. Decisions use immutable pregame "
            "live context, historical model findings, moneyline movement, and the "
            "current market level; missing evidence fails closed."
        ),
    }


def _compact_for_llm(value: Any, limit: int = 30000) -> Any:
    text = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), default=str)
    if len(text) <= limit:
        return json.loads(text)
    return {"truncated": True, "jsonPrefix": text[:limit]}


def _extract_json(text: str) -> Dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}


def _bedrock_decision(game: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    home = str((game.get("home") or {}).get("name") or "")
    away = str((game.get("away") or {}).get("name") or "")
    packet = {
        "officialMlb": game.get("official"),
        "theOddsApi": {
            "core": game.get("oddsCore"),
            "expandedMarkets": game.get("oddsExpanded"),
            "consensus": game.get("marketConsensus"),
        },
        "bigBallsDataPro": game.get("bbs"),
        "decisionEvidence": game.get("decisionEvidence"),
        "autonomyState": state,
    }
    prompt = (
        "You are the autonomous MLB winner-selection analyst for Inqsi. Choose exactly one winner for this game. "
        "Use all applicable evidence in the packet from MLB Stats API (official identity/schedule/pitchers), "
        "The Odds API (multi-book prices, movement and all available markets/props), and Big Balls Sports Data Pro "
        "(lineups, odds, stats, form/context and other returned baseball intelligence). Never invent missing data. "
        f"The long-run operational goal is at least {TARGET_ACCURACY:.0%} correct daily picks, but do not fake confidence or claim a guarantee. "
        "Use the fixed decisionEvidence weights for live baseball context, "
        "historical model findings, pregame moneyline movement, and current "
        "market level. Recent accuracy is diagnostic only. Never default to the "
        "market favorite. "
        "Return ONLY JSON with keys winner, loser, probability, confidence, rationale, source_weights, disagreements. "
        f"winner must be exactly {home!r} or {away!r}; loser must be the other team; probability must be between 0.50 and 0.95.\n"
        "DATA=" + json.dumps(_compact_for_llm(packet), separators=(",", ":"), default=str)
    )
    errors: List[Dict[str, str]] = []
    for model_id in BEDROCK_MODELS:
        try:
            response = BEDROCK.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 900, "temperature": 0.15, "topP": 0.9},
            )
            blocks = (((response.get("output") or {}).get("message") or {}).get("content") or [])
            text = "\n".join(str(block.get("text") or "") for block in blocks if isinstance(block, dict))
            parsed = _extract_json(text)
            winner = str(parsed.get("winner") or "")
            if winner not in {home, away}:
                raise RuntimeError("LLM_WINNER_NOT_EXACT_TEAM")
            loser = away if winner == home else home
            probability = float(parsed.get("probability") or 0.5)
            probability = min(max(probability, 0.50), 0.95)
            return {
                "ok": True,
                "authority": "BEDROCK_LLM",
                "modelId": model_id,
                "winner": winner,
                "loser": loser,
                "probability": round(probability, 6),
                "confidence": str(parsed.get("confidence") or "MODEL"),
                "rationale": parsed.get("rationale"),
                "sourceWeights": parsed.get("source_weights") or {},
                "disagreements": parsed.get("disagreements") or [],
                "errorsBeforeSuccess": errors,
            }
        except Exception as exc:
            errors.append({"modelId": model_id, "error": type(exc).__name__})
    raise RuntimeError(
        "BEDROCK_DECISION_UNAVAILABLE_NO_MARKET_FAVORITE_FALLBACK:"
        + json.dumps(errors, sort_keys=True, separators=(",", ":"))
    )


def _assemble(slate: str, *, expanded: bool) -> Dict[str, Any]:
    official = _official_schedule(slate)
    odds = _odds_core(slate)
    bbs = _bbs_matches(slate, official)
    official_games = [
        row for row in official.get("games") or [] if isinstance(row, dict)
    ]
    odds_assignments = _assign_odds_events(
        official_games,
        odds.get("events") or [],
        require_h2h=True,
    )
    catalog_assignments = _assign_odds_events(
        official_games,
        odds.get("catalogEvents") or [],
        require_h2h=False,
    )
    bbs_assignments = (
        bbs.get("assignments")
        if isinstance(bbs.get("assignments"), dict)
        else _assign_bbs_events(official_games, bbs.get("events") or [])
    )
    games: List[Dict[str, Any]] = []
    for official_game in official_games:
        game_pk = str(official_game.get("gamePk") or "")
        odds_event = odds_assignments.get(game_pk)
        catalog_event = catalog_assignments.get(game_pk)
        bbs_event = bbs_assignments.get(game_pk)
        detailed_odds = _odds_event_markets(str((odds_event or {}).get("id"))) if expanded and (odds_event or {}).get("id") else None
        detailed_bbs = _bbs_event_bundle(bbs_event) if expanded and bbs_event else ({"match": bbs_event} if bbs_event else None)
        row = {
            "gamePk": official_game.get("gamePk"),
            "gameDate": official_game.get("gameDate"),
            "home": copy.deepcopy(official_game.get("home") or {}),
            "away": copy.deepcopy(official_game.get("away") or {}),
            "official": copy.deepcopy(official_game),
            "oddsCore": odds_event,
            # /events is an identity catalogue, never moneyline evidence.
            "oddsCatalogEvent": _odds_catalog_identity(catalog_event),
            "oddsExpanded": detailed_odds,
            "bbs": detailed_bbs,
        }
        row["marketConsensus"] = _market_consensus(row)
        games.append(row)
    return {
        "version": VERSION,
        "slateDateEt": slate,
        "retrievedAtUtc": _iso(_now()),
        "expanded": expanded,
        "deadline": _deadline(official),
        "sourceStatus": {
            "mlbStatsApi": {
                "ok": True,
                "integrationOk": True,
                "games": official.get("totalGames"),
            },
            "theOddsApi": {
                "ok": len(odds_assignments) == len(official_games),
                "integrationOk": odds.get("oddsRequestOk") is True,
                "lineReadinessComplete": len(odds_assignments)
                == len(official_games),
                "catalogCoverageComplete": len(catalog_assignments)
                == len(official_games),
                "oddsRequestOk": odds.get("oddsRequestOk") is True,
                "catalogRequestOk": odds.get("catalogRequestOk") is True,
                "events": len(odds.get("events") or []),
                "catalogEvents": len(odds.get("catalogEvents") or []),
                "slateBoundsUtc": copy.deepcopy(
                    odds.get("slateBoundsUtc") or {}
                ),
                "catalogMatchedGames": len(catalog_assignments),
                "catalogMissingGamePks": sorted(
                    {
                        str(game.get("gamePk") or "")
                        for game in official_games
                    }
                    - set(catalog_assignments)
                ),
                "lineReadyGames": len(odds_assignments),
                "lineMissingGamePks": sorted(
                    {
                        str(game.get("gamePk") or "")
                        for game in official_games
                    }
                    - set(odds_assignments)
                ),
                "catalogOnlyIsMoneylineEvidence": False,
                "catalogError": odds.get("catalogError"),
                "quota": odds.get("quota"),
            },
            "bigBallsDataPro": {
                "ok": True,
                "integrationOk": True,
                "events": len(bbs.get("events") or []),
            },
        },
        "games": games,
    }


def _build_card(packet: Dict[str, Any]) -> Dict[str, Any]:
    state = _recent_accuracy_state()
    picks: List[Dict[str, Any]] = []
    for game in packet.get("games") or []:
        decision = _bedrock_decision(game, state)
        picks.append({
            "gamePk": game.get("gamePk"),
            "gameDate": game.get("gameDate"),
            "homeTeam": (game.get("home") or {}).get("name"),
            "awayTeam": (game.get("away") or {}).get("name"),
            "predictedWinner": decision.get("winner"),
            "predictedLoser": decision.get("loser"),
            "probability": decision.get("probability"),
            "decisionAuthority": decision.get("authority"),
            "llmModelId": decision.get("modelId"),
            "confidence": decision.get("confidence"),
            "rationale": decision.get("rationale"),
            "sourceWeights": decision.get("sourceWeights"),
            "disagreements": decision.get("disagreements"),
            "sourcePresence": {
                "mlbStatsApi": bool(game.get("official")),
                "theOddsApi": bool(game.get("oddsCore")),
                "theOddsApiExpanded": bool(game.get("oddsExpanded")),
                "bigBallsDataPro": bool(game.get("bbs")),
            },
        })
    return {
        "version": VERSION,
        "authority": "MLB_AUTO_LLM_PRIMARY",
        "slateDateEt": packet.get("slateDateEt"),
        "publishedAtUtc": _iso(_now()),
        "deadline": packet.get("deadline"),
        "targetDailyAccuracy": TARGET_ACCURACY,
        "targetIsGoalNotGuarantee": True,
        "autonomyState": state,
        "gameCount": len(picks),
        "llmPickCount": sum(row.get("decisionAuthority") == "BEDROCK_LLM" for row in picks),
        "fallbackPickCount": sum(row.get("decisionAuthority") != "BEDROCK_LLM" for row in picks),
        "picks": picks,
        "sourceStatus": packet.get("sourceStatus"),
    }


def _settle(slate: str) -> Optional[Dict[str, Any]]:
    card = _get(f"CARD#{slate}", "FINAL")
    if not card:
        return None
    schedule = _official_schedule(slate)
    by_pk = {str(row.get("gamePk")): row for row in schedule.get("games") or []}
    graded = correct = 0
    results: List[Dict[str, Any]] = []
    for pick in card.get("picks") or []:
        official = by_pk.get(str(pick.get("gamePk")))
        status = ((official or {}).get("status") or {}).get("abstractGameState")
        if str(status or "").upper() != "FINAL":
            continue
        home_score = ((official or {}).get("home") or {}).get("score")
        away_score = ((official or {}).get("away") or {}).get("score")
        if home_score is None or away_score is None or int(home_score) == int(away_score):
            continue
        winner = ((official or {}).get("home") or {}).get("name") if int(home_score) > int(away_score) else ((official or {}).get("away") or {}).get("name")
        is_correct = _normalize(winner) == _normalize(pick.get("predictedWinner"))
        graded += 1
        correct += int(is_correct)
        results.append({"gamePk": pick.get("gamePk"), "officialWinner": winner, "predictedWinner": pick.get("predictedWinner"), "correct": is_correct})
    audit = {
        "version": VERSION,
        "slateDateEt": slate,
        "auditedAtUtc": _iso(_now()),
        "graded": graded,
        "correct": correct,
        "accuracy": round(correct / graded, 6) if graded else None,
        "target": TARGET_ACCURACY,
        "targetMet": bool(graded and correct / graded >= TARGET_ACCURACY),
        "results": results,
    }
    _put(f"AUDIT#{slate}", "FINAL", audit)
    return audit


def _run(event: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    slate = str(event.get("slate_date") or now.astimezone(ET).date().isoformat())
    # Always settle the prior slate first; this is the self-learning feedback signal.
    yesterday = (now.astimezone(ET).date() - timedelta(days=1)).isoformat()
    try:
        _settle(yesterday)
    except Exception:
        pass
    schedule = _official_schedule(slate)
    deadline = _deadline(schedule)
    deadline_dt = _parse(deadline.get("publishDeadlineUtc"))
    existing = _get(f"CARD#{slate}", "FINAL")
    if existing:
        return {"ok": True, "status": "CARD_ALREADY_PUBLISHED", "card": existing, "audit": _settle(slate)}
    if not schedule.get("games"):
        return {"ok": True, "status": "NO_GAMES", "slateDateEt": slate, "deadline": deadline}
    if deadline_dt is None:
        raise RuntimeError("PUBLISH_DEADLINE_UNAVAILABLE")
    final_window_start = deadline_dt - timedelta(minutes=FINAL_WINDOW_MINUTES)
    force = bool(event.get("force_publish"))
    if not force and now < final_window_start:
        packet = _assemble(slate, expanded=False)
        _put(f"PACKET#{slate}", f"DISCOVERY#{_iso(now)}", packet)
        return {
            "ok": True, "status": "COLLECTING", "slateDateEt": slate,
            "deadline": deadline, "nextFinalWindowAtUtc": _iso(final_window_start),
            "sourceStatus": packet.get("sourceStatus"),
        }
    packet = _assemble(slate, expanded=True)
    _put(f"PACKET#{slate}", "FINAL_INPUT", packet)
    card = _build_card(packet)
    card["deadlineMet"] = bool(now <= deadline_dt)
    card["secondsBeforeDeadline"] = int((deadline_dt - now).total_seconds())
    inserted = _put(f"CARD#{slate}", "FINAL", card, condition="attribute_not_exists(PK)")
    if not inserted:
        card = _get(f"CARD#{slate}", "FINAL") or card
    return {"ok": True, "status": "CARD_PUBLISHED" if inserted else "CARD_ALREADY_PUBLISHED", "card": card}


def _status(slate: Optional[str] = None) -> Dict[str, Any]:
    slate = slate or _now().astimezone(ET).date().isoformat()
    card = _get(f"CARD#{slate}", "FINAL")
    audit = _get(f"AUDIT#{slate}", "FINAL")
    try:
        schedule = _official_schedule(slate)
        deadline = _deadline(schedule)
        schedule_ok = True
    except Exception as exc:
        schedule, deadline, schedule_ok = {}, {}, False
    return {
        "ok": True,
        "service": "mlb-auto-llm",
        "version": VERSION,
        "slateDateEt": slate,
        "fullyAutonomous": True,
        "threeSourcePolicy": ["MLB Stats API", "The Odds API", "Big Balls Sports Data Pro"],
        "bedrockModels": BEDROCK_MODELS,
        "targetDailyAccuracy": TARGET_ACCURACY,
        "targetIsGoalNotGuarantee": True,
        "cardLeadMinutesBeforeSecondGame": CARD_LEAD_MINUTES,
        "scheduleOk": schedule_ok,
        "scheduledGames": len(schedule.get("games") or []),
        "deadline": deadline,
        "cardPublished": card is not None,
        "card": card,
        "audit": audit,
        "autonomyState": _recent_accuracy_state(),
    }


def _response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(_plain(body), separators=(",", ":"), default=str)}


def lambda_handler(event: Any, context: Any) -> Any:
    event = event if isinstance(event, dict) else {}
    path = str(event.get("rawPath") or event.get("path") or "")
    method = str(((event.get("requestContext") or {}).get("http") or {}).get("method") or event.get("httpMethod") or "").upper()
    try:
        if method == "GET" and path.endswith("/status"):
            params = event.get("queryStringParameters") or {}
            return _response(200, _status(params.get("date")))
        if method == "GET" and path.endswith("/today"):
            today = _now().astimezone(ET).date().isoformat()
            return _response(200, {"ok": True, "card": _get(f"CARD#{today}", "FINAL"), "status": _status(today)})
        if method == "POST" and path.endswith("/run"):
            raw = event.get("body")
            body = json.loads(raw) if isinstance(raw, str) and raw else {}
            return _response(200, _run(body if isinstance(body, dict) else {}))
        return _run(event)
    except Exception as exc:
        error = {"ok": False, "service": "mlb-auto-llm", "version": VERSION, "errorType": type(exc).__name__, "error": str(exc)[:500]}
        if method:
            return _response(500, error)
        raise
