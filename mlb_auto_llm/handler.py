from __future__ import annotations

import copy
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
TARGET_ACCURACY = float(os.environ.get("MLB_AUTO_TARGET_DAILY_ACCURACY", "0.70"))
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


def _odds_core() -> Dict[str, Any]:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY_MISSING")
    base = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
    last_error: Optional[Exception] = None
    for regions in ("us,us2,uk,eu,au", "us,uk,eu,au", "us"):
        params = urllib.parse.urlencode({
            "apiKey": ODDS_API_KEY, "regions": regions, "markets": "h2h,spreads,totals",
            "oddsFormat": "decimal", "dateFormat": "iso",
        })
        try:
            payload, headers = _http_json(f"{base}?{params}", timeout=25)
            if not isinstance(payload, list):
                raise RuntimeError("ODDS_CORE_NOT_LIST")
            return {
                "source": "The Odds API", "regions": regions, "events": payload,
                "quota": {
                    "remaining": headers.get("x-requests-remaining"),
                    "used": headers.get("x-requests-used"),
                    "last": headers.get("x-requests-last"),
                },
            }
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"ODDS_CORE_FAILED:{type(last_error).__name__}")


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
    if not isinstance(payload, dict) or payload.get("error"):
        raise RuntimeError("BBS_RESPONSE_INVALID")
    return payload


def _bbs_payload_rows(payload: Any) -> List[Dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("BBS_MATCHES_NOT_LIST")
    return [copy.deepcopy(row) for row in rows if isinstance(row, dict)]


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
    matched: List[str] = []
    missing: List[str] = []
    for game in official.get("games") or []:
        if not isinstance(game, dict):
            continue
        game_pk = str(game.get("gamePk") or "")
        if _match_event(game, rows, provider="bbs") is not None:
            matched.append(game_pk)
        else:
            missing.append(game_pk)
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
        payload = _bbs_get("/v1/matches", params)
        found = _bbs_payload_rows(payload)
        rows.extend(found)
        queries.append({
            "label": label,
            "params": copy.deepcopy(params),
            "count": len(found),
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

    events = _dedupe_bbs_events(rows)
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
        events = _dedupe_bbs_events(rows)
        matched, missing = _bbs_official_coverage(official, events)
        if len(found) < 200:
            break

    return {
        "source": "Big Balls Sports Data",
        "events": events,
        "meta": {
            "resolver": "official_utc_date_union_v1",
            "officialUtcDates": utc_dates,
            "queries": queries,
            "providerMeta": provider_meta,
            "unfilteredFallbackUsed": fallback_used,
            "expectedOfficialGameCount": len(official.get("games") or []),
            "matchedOfficialGameCount": len(matched),
            "missingOfficialGamePks": missing,
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
    match_id = str(event.get("id") or event.get("match_id") or "").strip()
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
    home = _normalize((game.get("home") or {}).get("name"))
    away = _normalize((game.get("away") or {}).get("name"))
    matches: List[Tuple[int, Dict[str, Any]]] = []
    official_start = _parse(game.get("gameDate"))
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if provider == "odds":
            rh, ra, start_value = row.get("home_team"), row.get("away_team"), row.get("commence_time")
        else:
            rh = _team_name(row.get("home") or row.get("home_team"))
            ra = _team_name(row.get("away") or row.get("away_team"))
            start_value = row.get("kickoff_utc") or row.get("start_time") or row.get("commence_time")
        if _normalize(rh) != home or _normalize(ra) != away:
            continue
        provider_start = _parse(start_value)
        drift = abs(int((provider_start - official_start).total_seconds())) if provider_start and official_start else 999999
        matches.append((drift, row))
    matches.sort(key=lambda item: item[0])
    return copy.deepcopy(matches[0][1]) if matches and matches[0][0] <= 12 * 3600 else None


def _market_consensus(game: Dict[str, Any]) -> Dict[str, Any]:
    home = str((game.get("home") or {}).get("name") or "")
    away = str((game.get("away") or {}).get("name") or "")
    event = game.get("oddsCore") or {}
    home_probs: List[float] = []
    away_probs: List[float] = []
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            prices: Dict[str, float] = {}
            for outcome in market.get("outcomes") or []:
                try:
                    prices[_normalize(outcome.get("name"))] = float(outcome.get("price"))
                except Exception:
                    continue
            hp = prices.get(_normalize(home))
            ap = prices.get(_normalize(away))
            if hp and ap and hp > 1 and ap > 1:
                raw_h, raw_a = 1.0 / hp, 1.0 / ap
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
    market_anchor = 0.72 if aggregate is not None and aggregate < TARGET_ACCURACY else 0.55
    return {
        "targetDailyAccuracy": TARGET_ACCURACY,
        "recentDays": len(rows),
        "recentGradedPicks": graded,
        "recentCorrectPicks": correct,
        "recentAccuracy": round(aggregate, 6) if aggregate is not None else None,
        "marketAnchorWeight": market_anchor,
        "policy": "If recent accuracy is below target, constrain the LLM more tightly to multi-book market consensus while still considering independent baseball context.",
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
        "autonomyState": state,
    }
    prompt = (
        "You are the autonomous MLB winner-selection analyst for Inqsi. Choose exactly one winner for this game. "
        "Use all applicable evidence in the packet from MLB Stats API (official identity/schedule/pitchers), "
        "The Odds API (multi-book prices, movement and all available markets/props), and Big Balls Sports Data Pro "
        "(lineups, odds, stats, form/context and other returned baseball intelligence). Never invent missing data. "
        f"The long-run operational goal is at least {TARGET_ACCURACY:.0%} correct daily picks, but do not fake confidence or claim a guarantee. "
        f"When evidence conflicts, use the marketAnchorWeight={state.get('marketAnchorWeight')} as the default weight on normalized multi-book h2h consensus. "
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
    consensus = game.get("marketConsensus") or {}
    if consensus.get("available"):
        winner = str(consensus.get("marketFavorite"))
        loser = away if winner == home else home
        return {
            "ok": True,
            "authority": "FALLBACK_MARKET_CONSENSUS",
            "modelId": None,
            "winner": winner,
            "loser": loser,
            "probability": float(consensus.get("marketFavoriteProbability") or 0.5),
            "confidence": "FALLBACK",
            "rationale": "All configured Bedrock models failed; used normalized multi-book h2h consensus so the autonomous full card is not silently omitted.",
            "sourceWeights": {"theOddsApiConsensus": 1.0},
            "disagreements": [],
            "llmErrors": errors,
        }
    raise RuntimeError("NO_LLM_OR_MARKET_FALLBACK_AVAILABLE")


def _assemble(slate: str, *, expanded: bool) -> Dict[str, Any]:
    official = _official_schedule(slate)
    odds = _odds_core()
    bbs = _bbs_matches(slate, official)
    games: List[Dict[str, Any]] = []
    for official_game in official.get("games") or []:
        odds_event = _match_event(official_game, odds.get("events") or [], provider="odds")
        bbs_event = _match_event(official_game, bbs.get("events") or [], provider="bbs")
        detailed_odds = _odds_event_markets(str((odds_event or {}).get("id"))) if expanded and (odds_event or {}).get("id") else None
        detailed_bbs = _bbs_event_bundle(bbs_event) if expanded and bbs_event else ({"match": bbs_event} if bbs_event else None)
        row = {
            "gamePk": official_game.get("gamePk"),
            "gameDate": official_game.get("gameDate"),
            "home": copy.deepcopy(official_game.get("home") or {}),
            "away": copy.deepcopy(official_game.get("away") or {}),
            "official": copy.deepcopy(official_game),
            "oddsCore": odds_event,
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
            "mlbStatsApi": {"ok": True, "games": official.get("totalGames")},
            "theOddsApi": {"ok": True, "events": len(odds.get("events") or []), "quota": odds.get("quota")},
            "bigBallsDataPro": {"ok": True, "events": len(bbs.get("events") or [])},
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
