"""Canonical identifiers, timestamps, hashing, and strict team reconciliation."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .config import parse_utc

_TEAM_ALIASES = {
    "arizona cardinals": "ARI", "ari": "ARI",
    "atlanta falcons": "ATL", "atl": "ATL",
    "baltimore ravens": "BAL", "bal": "BAL",
    "buffalo bills": "BUF", "buf": "BUF",
    "carolina panthers": "CAR", "car": "CAR",
    "chicago bears": "CHI", "chi": "CHI",
    "cincinnati bengals": "CIN", "cin": "CIN",
    "cleveland browns": "CLE", "cle": "CLE",
    "dallas cowboys": "DAL", "dal": "DAL",
    "denver broncos": "DEN", "den": "DEN",
    "detroit lions": "DET", "det": "DET",
    "green bay packers": "GB", "gb": "GB", "gnb": "GB",
    "houston texans": "HOU", "hou": "HOU",
    "indianapolis colts": "IND", "ind": "IND",
    "jacksonville jaguars": "JAX", "jax": "JAX", "jac": "JAX",
    "kansas city chiefs": "KC", "kc": "KC", "kan": "KC",
    "las vegas raiders": "LV", "lv": "LV", "lvr": "LV",
    "oakland raiders": "LV", "oak": "LV",
    "los angeles chargers": "LAC", "lac": "LAC", "sd": "LAC",
    "san diego chargers": "LAC",
    "los angeles rams": "LAR", "lar": "LAR", "stl": "LAR",
    "st. louis rams": "LAR", "st louis rams": "LAR",
    "miami dolphins": "MIA", "mia": "MIA",
    "minnesota vikings": "MIN", "min": "MIN",
    "new england patriots": "NE", "ne": "NE", "nwe": "NE",
    "new orleans saints": "NO", "no": "NO", "nor": "NO",
    "new york giants": "NYG", "nyg": "NYG",
    "new york jets": "NYJ", "nyj": "NYJ",
    "philadelphia eagles": "PHI", "phi": "PHI",
    "pittsburgh steelers": "PIT", "pit": "PIT",
    "seattle seahawks": "SEA", "sea": "SEA",
    "san francisco 49ers": "SF", "sf": "SF", "sfo": "SF",
    "tampa bay buccaneers": "TB", "tb": "TB", "tam": "TB",
    "tennessee titans": "TEN", "ten": "TEN",
    "washington commanders": "WAS", "was": "WAS", "wsh": "WAS",
    "washington football team": "WAS", "washington redskins": "WAS",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def iso_utc(value: str | datetime) -> str:
    return parse_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_team(value: Any) -> str:
    if isinstance(value, Mapping):
        value = (
            value.get("short_name")
            or value.get("abbreviation")
            or value.get("abbr")
            or value.get("name")
        )
    text = re.sub(r"\s+", " ", str(value or "").strip()).lower()
    if text in _TEAM_ALIASES:
        return _TEAM_ALIASES[text]
    upper = text.upper().replace(".", "")
    if upper in set(_TEAM_ALIASES.values()):
        return upper
    raise ValueError(f"NFL_TEAM_UNRECOGNIZED:{str(value)[:80]}")


def game_id(season: int, week: int, away_team: Any, home_team: Any) -> str:
    return f"{int(season)}_{int(week):02d}_{normalize_team(away_team)}_{normalize_team(home_team)}"


def strict_event_match(
    *,
    home_team: Any,
    away_team: Any,
    commence_time: str | datetime,
    odds_event: Mapping[str, Any],
    tolerance_seconds: int = 1800,
) -> bool:
    try:
        if normalize_team(odds_event.get("home_team")) != normalize_team(home_team):
            return False
        if normalize_team(odds_event.get("away_team")) != normalize_team(away_team):
            return False
        expected = parse_utc(commence_time)
        actual = parse_utc(str(odds_event.get("commence_time") or ""))
    except (ValueError, TypeError):
        return False
    return abs((actual - expected).total_seconds()) <= tolerance_seconds


def first_present(row: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None and value != "":
            return value
    return default


def parse_bbd_kickoff(row: Mapping[str, Any]) -> str:
    direct = first_present(
        row,
        "kickoff_utc",
        "commence_time",
        "start_time",
        "start_time_utc",
        "kickoff",
        "game_time_utc",
    )
    if direct:
        return iso_utc(str(direct))

    # nflverse schedules commonly expose gameday + gametime in US/Eastern.
    # BBD's current canonical surface exposes kickoff_utc; this fallback is
    # intentionally rejected unless an explicit UTC offset is present.
    game_date = first_present(row, "gameday", "game_date", "date")
    game_time = first_present(row, "gametime", "game_time", "time")
    if game_date and game_time:
        combined = f"{game_date}T{game_time}"
        if re.search(r"(?:Z|[+-]\d\d:?\d\d)$", combined):
            return iso_utc(combined)
        raise ValueError("BBD_KICKOFF_TIMEZONE_MISSING")
    raise ValueError("BBD_KICKOFF_MISSING")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
