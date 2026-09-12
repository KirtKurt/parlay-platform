"""Odds API \u2194 Big Balls Data identity graph.

Refuse silent fuzzy joins. Ambiguous names go to a review queue.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

SPORT_SLUG = "football"

ODDS_KEY_TO_BBD_LEAGUE = {
    "soccer_epl": ("epl", "A"),
    "soccer_spain_la_liga": ("laliga", "A"),
    "soccer_germany_bundesliga": ("bundesliga", "A"),
    "soccer_italy_serie_a": ("serie-a", "A"),
    "soccer_france_ligue_one": ("ligue-1", "A"),
    "soccer_usa_mls": ("mls", "A"),
    "soccer_uefa_champs_league": ("ucl", "A"),
    "soccer_uefa_europa_league": ("uel", "A"),
    "soccer_uefa_europa_conference_league": ("uecl", "B"),
    "soccer_efl_champ": ("championship", "B"),
    "soccer_netherlands_eredivisie": ("eredivisie", "B"),
    "soccer_portugal_primeira_liga": ("primeira-liga", "B"),
    "soccer_fifa_world_cup": ("world-cup", "A"),
}

KNOCKOUT_KEYS = frozenset(
    {
        "soccer_fa_cup",
        "soccer_england_efl_cup",
        "soccer_france_coupe_de_france",
        "soccer_germany_dfb_pokal",
        "soccer_italy_coppa_italia",
        "soccer_spain_copa_del_rey",
        "soccer_uefa_champs_league_qualification",
    }
)

FRIENDLY_HINTS = ("friendly", "friendlies")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    aliases = {
        "man utd": "manchester united",
        "man united": "manchester united",
        "man city": "manchester city",
        "spurs": "tottenham hotspur",
        "wolves": "wolverhampton wanderers",
        "psg": "paris saint germain",
        "inter": "internazionale",
        "ath madrid": "atletico madrid",
        "atletico de madrid": "atletico madrid",
    }
    compact = " ".join(text.split())
    return aliases.get(compact, compact)


def is_bbd_uuid(value: str) -> bool:
    return bool(UUID_RE.match(str(value or "").strip()))


def classify_competition(sport_key: str) -> dict[str, Any]:
    key = str(sport_key or "")
    league, tier = ODDS_KEY_TO_BBD_LEAGUE.get(key, (None, "C"))
    stage = "league"
    if key in KNOCKOUT_KEYS or "qualification" in key:
        stage = "knockout"
        if tier == "C":
            tier = "Q"
    lowered = key.lower()
    if any(hint in lowered for hint in FRIENDLY_HINTS):
        stage = "friendly"
        tier = "Q"
    if "women" in lowered or "womens" in lowered:
        stage = "women"
    return {
        "odds_sport_key": key,
        "bbd_league": league,
        "bbd_sport": SPORT_SLUG,
        "tier": tier,
        "stage": stage,
        "goals_model_eligible": tier in {"A", "B"} and stage == "league",
        "publish_ou_btts": tier == "A" and stage == "league",
    }


def map_event(
    *,
    odds_event_id: str,
    sport_key: str,
    home_team: str,
    away_team: str,
    commence_time: str,
    bbd_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    competition = classify_competition(sport_key)
    home_n = normalize_name(home_team)
    away_n = normalize_name(away_team)
    candidates = []
    for row in bbd_matches or []:
        bbd_id = str(row.get("id") or "")
        if not is_bbd_uuid(bbd_id):
            continue
        if normalize_name(row.get("home")) == home_n and normalize_name(row.get("away")) == away_n:
            if not commence_time or not row.get("kickoff_utc") or _same_kickoff(commence_time, row["kickoff_utc"]):
                candidates.append(bbd_id)
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        status = "mapped"
        bbd_match_id = unique[0]
        review_reason = None
    elif len(unique) == 0:
        status = "unmapped"
        bbd_match_id = None
        review_reason = "NO_BBD_CANDIDATE"
    else:
        status = "review"
        bbd_match_id = None
        review_reason = "AMBIGUOUS_BBD_CANDIDATES"
    return {
        "odds_event_id": odds_event_id,
        "home_normalized": home_n,
        "away_normalized": away_n,
        "commence_time": commence_time,
        "bbd_match_id": bbd_match_id,
        "status": status,
        "review_reason": review_reason,
        "candidate_count": len(unique),
        **competition,
    }


def _same_kickoff(left: str, right: str) -> bool:
    def clip(value: str) -> str:
        return str(value).replace("Z", "+00:00")[:16]

    return clip(left) == clip(right)
