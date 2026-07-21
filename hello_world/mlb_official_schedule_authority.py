from __future__ import annotations

import copy
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import product
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo


VERSION = "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date"
SOURCE = "MLB Stats API exact-date schedule"
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
EASTERN = ZoneInfo("America/New_York")
MAX_CROSSWALK_DRIFT_SECONDS = 12 * 60 * 60


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def normalize_team(value: Any) -> str:
    text = " ".join(str(value or "").lower().strip().split())
    aliases = {
        "a's": "athletics",
        "az diamondbacks": "arizona diamondbacks",
        "chi cubs": "chicago cubs",
        "chi white sox": "chicago white sox",
        "la angels": "los angeles angels",
        "la dodgers": "los angeles dodgers",
        "ny mets": "new york mets",
        "ny yankees": "new york yankees",
        "oakland a's": "athletics",
        "oakland athletics": "athletics",
        "sd padres": "san diego padres",
        "sf giants": "san francisco giants",
        "tb rays": "tampa bay rays",
    }
    return aliases.get(text, text)


def exact_date_schedule_url(slate_date: str) -> str:
    query = urllib.parse.urlencode(
        {
            "sportId": "1",
            "startDate": slate_date,
            "endDate": slate_date,
        }
    )
    return f"{SCHEDULE_URL}?{query}"


def _http_get_json(url: str, timeout: int = 12) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "inqsi-mlb-official-schedule/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _official_game(raw: Dict[str, Any], slate_date: str) -> Dict[str, Any]:
    game_pk = raw.get("gamePk")
    start = parse_dt(raw.get("gameDate"))
    teams = raw.get("teams") or {}
    home = ((teams.get("home") or {}).get("team") or {}).get("name")
    away = ((teams.get("away") or {}).get("team") or {}).get("name")
    if game_pk in (None, ""):
        raise RuntimeError("MLB_OFFICIAL_SCHEDULE_GAME_PK_MISSING")
    if start is None:
        raise RuntimeError(f"MLB_OFFICIAL_SCHEDULE_START_INVALID:{game_pk}")
    if not home or not away:
        raise RuntimeError(f"MLB_OFFICIAL_SCHEDULE_TEAM_IDENTITY_MISSING:{game_pk}")
    canonical_id = f"mlb_statsapi:{game_pk}"
    game_key = (
        f"mlb|{slate_date}|{normalize_team(away)}|{normalize_team(home)}"
        f"|statsapi:{game_pk}"
    )
    return {
        "id": canonical_id,
        "game_id": canonical_id,
        "game_key": game_key,
        "official_game_pk": str(game_pk),
        "official_game_id": canonical_id,
        "official_commence_time": start.isoformat(),
        "commence_time": start.isoformat(),
        "home_team": str(home),
        "away_team": str(away),
        "game_date_et": slate_date,
        "official_game_type": str(raw.get("gameType") or "UNKNOWN"),
        "official_game_number": int(raw.get("gameNumber") or 1),
        "official_double_header": str(raw.get("doubleHeader") or "N"),
        "official_status": copy.deepcopy(raw.get("status") or {}),
        "league": "MLB",
        "level": "pro",
        "gender": "men",
        "provider_sport_key": "baseball_mlb",
        "schedule_authority": SOURCE,
        "schedule_authority_version": VERSION,
    }


def validate_exact_date_schedule(payload: Any, slate_date: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("MLB_OFFICIAL_SCHEDULE_PAYLOAD_NOT_OBJECT")
    total_games = payload.get("totalGames")
    dates = payload.get("dates")
    if isinstance(total_games, bool) or not isinstance(total_games, int) or total_games < 0:
        raise RuntimeError("MLB_OFFICIAL_SCHEDULE_TOTAL_GAMES_INVALID")
    if not isinstance(dates, list):
        raise RuntimeError("MLB_OFFICIAL_SCHEDULE_DATES_INVALID")

    raw_games: List[Dict[str, Any]] = []
    for date_row in dates:
        if not isinstance(date_row, dict):
            raise RuntimeError("MLB_OFFICIAL_SCHEDULE_DATE_ROW_INVALID")
        if str(date_row.get("date") or "") != slate_date:
            raise RuntimeError(
                f"MLB_OFFICIAL_SCHEDULE_WRONG_DATE:{date_row.get('date')}:{slate_date}"
            )
        row_games = date_row.get("games")
        if not isinstance(row_games, list):
            raise RuntimeError("MLB_OFFICIAL_SCHEDULE_GAMES_INVALID")
        raw_games.extend(row_games)
    if len(raw_games) != total_games:
        raise RuntimeError(
            f"MLB_OFFICIAL_SCHEDULE_COUNT_MISMATCH:{total_games}:{len(raw_games)}"
        )

    games = [_official_game(game, slate_date) for game in raw_games]
    official_ids = [game["official_game_pk"] for game in games]
    if len(set(official_ids)) != len(official_ids):
        raise RuntimeError("MLB_OFFICIAL_SCHEDULE_DUPLICATE_GAME_PK")
    return {
        "version": VERSION,
        "source": SOURCE,
        "sourceUrl": exact_date_schedule_url(slate_date),
        "slateDate": slate_date,
        "verified": True,
        "officialGameCount": len(games),
        "officialGameIds": official_ids,
        "games": sorted(
            games,
            key=lambda game: (
                str(game.get("official_commence_time") or ""),
                str(game.get("official_game_pk") or ""),
            ),
        ),
    }


def fetch_exact_date_schedule(
    slate_date: str,
    *,
    timeout: int = 12,
    http_get: Optional[Callable[[str, int], Any]] = None,
) -> Dict[str, Any]:
    url = exact_date_schedule_url(slate_date)
    getter = http_get or (lambda target, seconds: _http_get_json(target, seconds))
    payload = getter(url, timeout)
    return validate_exact_date_schedule(payload, slate_date)


def _provider_date(game: Dict[str, Any]) -> Optional[str]:
    start = parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return start.astimezone(EASTERN).date().isoformat() if start else None


def _team_pair(game: Dict[str, Any]) -> Tuple[str, str]:
    return (
        normalize_team(game.get("away_team") or game.get("awayTeam")),
        normalize_team(game.get("home_team") or game.get("homeTeam")),
    )


def _crosswalk(
    official_games: Iterable[Dict[str, Any]],
    provider_games: Iterable[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    official = list(official_games or [])
    provider = list(provider_games or [])
    matches: Dict[str, Dict[str, Any]] = {}
    used_provider_ids: set[str] = set()

    pairs = [
        (
            abs(int((provider_start - official_start).total_seconds())),
            str(official_game.get("official_game_pk") or ""),
            str(provider_game.get("id") or provider_game.get("game_id") or ""),
            official_game,
            provider_game,
        )
        for official_game, provider_game in product(official, provider)
        if _team_pair(official_game) == _team_pair(provider_game)
        for official_start in [parse_dt(official_game.get("official_commence_time"))]
        for provider_start in [parse_dt(provider_game.get("commence_time"))]
        if official_start is not None and provider_start is not None
    ]
    pairs.sort(key=lambda item: item[:3])
    used_official_ids: set[str] = set()
    for drift, official_id, provider_id, official_game, provider_game in pairs:
        if drift > MAX_CROSSWALK_DRIFT_SECONDS:
            continue
        if official_id in used_official_ids or provider_id in used_provider_ids:
            continue
        used_official_ids.add(official_id)
        used_provider_ids.add(provider_id)
        matches[official_id] = provider_game

    unmatched = sorted(
        str(game.get("id") or game.get("game_id") or "")
        for game in provider
        if str(game.get("id") or game.get("game_id") or "") not in used_provider_ids
    )
    return matches, unmatched


def _proof_fingerprint(proof: Dict[str, Any]) -> str:
    material = {
        str(key): value
        for key, value in proof.items()
        if key != "fingerprint"
    }
    return hashlib.sha256(
        json.dumps(
            _proof_json_value(material),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def _proof_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _proof_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_proof_json_value(item) for item in value]
    return value


def _without_none(value: Any) -> Any:
    """Match DynamoDB serialization so proof fingerprints survive readback."""
    if isinstance(value, dict):
        return {
            str(key): _without_none(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_without_none(item) for item in value]
    return value


def reconcile_official_schedule(
    schedule: Dict[str, Any],
    provider_games: Iterable[Dict[str, Any]],
    *,
    observed_at_utc: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if schedule.get("verified") is not True:
        raise RuntimeError("MLB_OFFICIAL_SCHEDULE_NOT_VERIFIED")
    slate_date = str(schedule.get("slateDate") or "")
    official_games = [copy.deepcopy(game) for game in schedule.get("games") or []]
    provider_for_date = [
        copy.deepcopy(game)
        for game in provider_games or []
        if _provider_date(game) == slate_date
    ]
    matches, unmatched_provider_ids = _crosswalk(official_games, provider_for_date)

    reconciled: List[Dict[str, Any]] = []
    proof_games: List[Dict[str, Any]] = []
    missing_provider: List[str] = []
    drift_values: List[int] = []
    for official in official_games:
        official_id = str(official["official_game_pk"])
        provider = matches.get(official_id)
        official_start = parse_dt(official.get("official_commence_time"))
        provider_start = parse_dt((provider or {}).get("commence_time"))
        if official_start is None:
            raise RuntimeError(f"MLB_OFFICIAL_SCHEDULE_START_INVALID:{official_id}")
        drift = (
            int((provider_start - official_start).total_seconds())
            if provider_start is not None
            else None
        )
        if drift is not None:
            drift_values.append(drift)
        # The official schedule owns both roster membership and start time.
        # Provider time is retained only as crosswalk/audit evidence; allowing
        # it to change the cutoff would put the lock lifecycle back under the
        # mutable odds feed that this authority is intended to replace.
        canonical_start = official_start
        provider_id = str((provider or {}).get("id") or (provider or {}).get("game_id") or "") or None
        if provider is None:
            missing_provider.append(official_id)

        row = copy.deepcopy(official)
        canonical_game_id = provider_id or str(official["official_game_id"])
        canonical_game_key = (
            str(provider.get("game_key"))
            if provider and provider.get("game_key")
            else f"mlb|{slate_date}|{str(official['away_team']).lower()}|{str(official['home_team']).lower()}"
            if provider_id
            else str(official["game_key"])
        )
        row.update(
            {
                # Preserve established market/pregame identity for matched
                # games. Stats API identity is used only when no provider event
                # exists, while official_game_pk binds the crosswalk.
                "id": canonical_game_id,
                "game_id": canonical_game_id,
                "game_key": canonical_game_key,
                "commence_time": canonical_start.isoformat(),
                "provider_event_id": provider_id,
                "provider_commence_time": provider_start.isoformat() if provider_start else None,
                "provider_start_drift_seconds": drift,
                "canonical_start_time_source": "MLB_STATS_API_EXACT_DATE",
                "bookmakers": copy.deepcopy((provider or {}).get("bookmakers") or []),
                "_provider_event_roster": provider is not None,
                "_provider_odds_payload": bool(
                    provider and provider.get("_provider_odds_payload") is True
                ),
                "_odds_exact_id_match": bool(
                    provider and provider.get("_odds_exact_id_match") is True
                ),
                "_official_schedule_authority": True,
            }
        )
        reconciled.append(row)
        proof_games.append(
            {
                "officialGamePk": official_id,
                "canonicalGameId": canonical_game_id,
                "homeTeam": row["home_team"],
                "awayTeam": row["away_team"],
                "officialStartUtc": official_start.isoformat(),
                "providerEventId": provider_id,
                "providerStartUtc": provider_start.isoformat() if provider_start else None,
                "canonicalStartUtc": canonical_start.isoformat(),
                "providerStartDriftSeconds": drift,
            }
        )

    proof: Dict[str, Any] = {
        "version": VERSION,
        "source": SOURCE,
        "sourceUrl": schedule.get("sourceUrl") or exact_date_schedule_url(slate_date),
        "slateDate": slate_date,
        "observedAtUtc": observed_at_utc,
        "verified": True,
        "authoritativeRoster": True,
        "authoritativeStartTimes": True,
        "providerStartTimeDiagnosticOnly": True,
        "canonicalStartTimePolicy": "MLB_STATS_API_EXACT_DATE_ONLY",
        "officialGameCount": len(reconciled),
        "officialGameIds": sorted(str(game["official_game_pk"]) for game in reconciled),
        "canonicalGameIds": sorted(str(game["game_id"]) for game in reconciled),
        "providerEventCountForSlate": len(provider_for_date),
        "providerMatchedGameCount": len(matches),
        "missingProviderEventOfficialGameIds": sorted(missing_provider),
        "unmatchedProviderEventIds": unmatched_provider_ids,
        "providerStartDriftSeconds": sorted(drift_values),
        "maximumAbsoluteProviderStartDriftSeconds": max(
            (abs(value) for value in drift_values),
            default=None,
        ),
        "exactTeamAndNearestStartCrosswalk": True,
        "games": sorted(proof_games, key=lambda game: game["officialGamePk"]),
    }
    proof = _without_none(proof)
    proof["fingerprint"] = _proof_fingerprint(proof)
    return (
        sorted(
            reconciled,
            key=lambda game: (
                str(game.get("commence_time") or ""),
                str(game.get("official_game_pk") or ""),
            ),
        ),
        proof,
    )


def validate_authority_proof(
    proof: Any,
    games: Iterable[Dict[str, Any]],
) -> List[str]:
    errors: List[str] = []
    if not isinstance(proof, dict):
        return ["official_schedule_authority_missing"]
    if proof.get("version") != VERSION:
        errors.append("official_schedule_authority_version_mismatch")
    if proof.get("source") != SOURCE:
        errors.append("official_schedule_authority_source_mismatch")
    if str(proof.get("slateDate") or "") == "":
        errors.append("official_schedule_authority_slate_missing")
    if parse_dt(proof.get("observedAtUtc")) is None:
        errors.append("official_schedule_authority_observed_at_invalid")
    for field in ("verified", "authoritativeRoster", "authoritativeStartTimes"):
        if proof.get(field) is not True:
            errors.append(f"official_schedule_authority_{field}_missing")
    if proof.get("providerStartTimeDiagnosticOnly") is not True:
        errors.append("official_schedule_authority_provider_time_not_diagnostic")
    if proof.get("canonicalStartTimePolicy") != "MLB_STATS_API_EXACT_DATE_ONLY":
        errors.append("official_schedule_authority_start_policy_mismatch")
    if str(proof.get("fingerprint") or "") != _proof_fingerprint(proof):
        errors.append("official_schedule_authority_fingerprint_mismatch")

    rows = list(games or [])
    official_ids = sorted(str(row.get("official_game_pk") or "") for row in rows)
    canonical_ids = sorted(str(row.get("game_id") or row.get("id") or "") for row in rows)
    if any(not value for value in official_ids):
        errors.append("official_schedule_game_pk_missing")
    if len(set(official_ids)) != len(official_ids):
        errors.append("official_schedule_duplicate_game_pk")
    if len(set(canonical_ids)) != len(canonical_ids):
        errors.append("official_schedule_duplicate_canonical_game_id")
    try:
        count = int(proof.get("officialGameCount"))
    except Exception:
        count = -1
    if count != len(rows):
        errors.append("official_schedule_authority_game_count_mismatch")
    if list(proof.get("officialGameIds") or []) != official_ids:
        errors.append("official_schedule_authority_game_ids_mismatch")
    if list(proof.get("canonicalGameIds") or []) != canonical_ids:
        errors.append("official_schedule_authority_canonical_ids_mismatch")

    proof_by_id = {
        str(item.get("officialGamePk") or ""): item
        for item in proof.get("games") or []
        if isinstance(item, dict)
    }
    if set(proof_by_id) != set(official_ids):
        errors.append("official_schedule_authority_game_proof_membership_mismatch")
    for row in rows:
        official_id = str(row.get("official_game_pk") or "")
        item = proof_by_id.get(official_id) or {}
        canonical_start = parse_dt(item.get("canonicalStartUtc"))
        row_start = parse_dt(row.get("commence_time") or row.get("commenceTime"))
        official_start = parse_dt(item.get("officialStartUtc"))
        if canonical_start is None or row_start != canonical_start:
            errors.append(f"official_schedule_authority_start_mismatch:{official_id}")
        if official_start is None or canonical_start != official_start:
            errors.append(
                f"official_schedule_authority_canonical_start_not_official:{official_id}"
            )
        if str(item.get("canonicalGameId") or "") != str(row.get("game_id") or row.get("id") or ""):
            errors.append(f"official_schedule_authority_identity_mismatch:{official_id}")
        if str(row.get("official_game_id") or "") != f"mlb_statsapi:{official_id}":
            errors.append(f"official_schedule_authority_official_identity_mismatch:{official_id}")
        if row.get("canonical_start_time_source") != "MLB_STATS_API_EXACT_DATE":
            errors.append(f"official_schedule_authority_row_start_source_mismatch:{official_id}")
        if normalize_team(item.get("homeTeam")) != normalize_team(row.get("home_team")):
            errors.append(f"official_schedule_authority_home_team_mismatch:{official_id}")
        if normalize_team(item.get("awayTeam")) != normalize_team(row.get("away_team")):
            errors.append(f"official_schedule_authority_away_team_mismatch:{official_id}")
    return sorted(set(errors))


def slate_dates(start_date: str, days_ahead: int) -> List[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=EASTERN)
    return [
        (start + timedelta(days=offset)).date().isoformat()
        for offset in range(max(0, int(days_ahead)) + 1)
    ]
