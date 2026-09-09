"""Current pre-lock starter observations, with exact MLB identities and provenance.

This adapter supplies ERA, K-BB%, and handedness under their actual names. It
does not invent FIP, expected statistics, health, or composite model scores.
Historical requests never reconstruct current season totals into past locks.
"""
from __future__ import annotations

import hashlib
import json
import math
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

VERSION = "MLB-STATSAPI-STARTER-CONTEXT-v1-prelock-season-observations"
DATASET = "MLB regular season pitching totals and pitcher handedness; " + VERSION
_CACHE = {}


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _season_metrics(person, season):
    """Use an unambiguous MLB season total; do not add aggregate and team splits."""
    splits = [split for block in person.get("stats", [])
              if block.get("group", {}).get("displayName") == "pitching"
              and block.get("type", {}).get("displayName") == "season"
              for split in block.get("splits", [])
              if str(split.get("season")) == str(season)
              and str(split.get("sport", {}).get("id")) == "1"
              and split.get("gameType") == "R"]
    totals = [split for split in splits if not split.get("team", {}).get("id")]
    selected = totals if totals else splits
    if len(selected) != 1:
        return {}
    stat = selected[0].get("stat") or {}
    era = _number(stat.get("era"))
    strikeouts, walks, faced = [_number(stat.get(key)) for key in (
        "strikeOuts", "baseOnBalls", "battersFaced")]
    valid_counts = all(value is not None and value >= 0 and value.is_integer()
                       for value in (strikeouts, walks, faced))
    rate = (100 * (strikeouts - walks) / faced
            if valid_counts and faced > 0 and strikeouts + walks <= faced else None)
    return {"era": era if era is not None and era >= 0 else None,
            "kMinusBbPct": rate}


def observe(game_date, game, schedule, http_get, *, now=None):
    """Return partial context for one exact official game using one cached batch."""
    clock = now or (lambda: datetime.now(timezone.utc))
    observed_at = clock().astimezone(timezone.utc)
    unavailable = {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
                   "algorithmVersion": VERSION,
                   "note": "Verified current pre-T45 starter observations unavailable."}
    if game_date != observed_at.astimezone(ZoneInfo("America/New_York")).date().isoformat():
        return unavailable, unavailable.copy()
    start = _time(game.get("gameDate"))
    if (not game.get("gamePk") or not start
            or start.astimezone(ZoneInfo("America/New_York")).date().isoformat() != game_date
            or observed_at >= start - timedelta(minutes=45)
            or game.get("status", {}).get("abstractGameState") != "Preview"):
        return unavailable, unavailable.copy()
    identities = {side: game.get("teams", {}).get(side, {}).get("probablePitcher", {}).get("id")
                  for side in ("home", "away")}
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in identities.values()):
        return unavailable, unavailable.copy()
    games = [item for day in schedule.get("payload", {}).get("dates", [])
             for item in day.get("games", [])]
    ids = sorted({team.get("probablePitcher", {}).get("id") for item in games
                  for team in item.get("teams", {}).values()
                  if isinstance(team.get("probablePitcher", {}).get("id"), int)
                  and not isinstance(team.get("probablePitcher", {}).get("id"), bool)
                  and team["probablePitcher"]["id"] > 0})
    if not ids or len(ids) > 60 or not set(identities.values()).issubset(ids):
        return unavailable, unavailable.copy()
    key = (game_date, tuple(ids))
    cached = _CACHE.get(key)
    if not cached or not 0 <= (observed_at - cached["at"]).total_seconds() <= 300:
        params = {"personIds": ",".join(map(str, ids)),
                  "hydrate": f"stats(group=[pitching],type=[season],season={observed_at.year})"}
        endpoint = "https://statsapi.mlb.com/api/v1/people?" + urllib.parse.urlencode(params)
        try:
            payload = http_get(endpoint, timeout=4)
            if not isinstance(payload, dict) or not isinstance(payload.get("people"), list):
                raise ValueError("invalid people response")
            cached = {"payload": payload, "endpoint": endpoint, "ok": True,
                      "fingerprint": hashlib.sha256(json.dumps(payload, sort_keys=True,
                          separators=(",", ":"), default=str).encode()).hexdigest()}
        except Exception:
            cached = {"ok": False}
        cached["at"] = clock().astimezone(timezone.utc)
        _CACHE.clear()
        _CACHE[key] = cached
    if not cached["ok"] or clock().astimezone(timezone.utc) >= start - timedelta(minutes=45):
        return unavailable, unavailable.copy()
    provenance = {"provider": "MLB Stats API", "endpoint": cached["endpoint"],
                  "dataset": DATASET,
                  "retrievedAtUtc": cached["at"].isoformat(),
                  "sourceEffectiveAtUtc": cached["at"].isoformat(),
                  "payloadFingerprint": cached["fingerprint"]}
    common = {"source_status": "PARTIAL", "algorithmVersion": VERSION,
              "game_pk": game["gamePk"], "home_pitcher_id": identities["home"],
              "away_pitcher_id": identities["away"], "sourceProvenance": provenance}
    quality = {**common, "note": "Observed ERA and derived K-BB%; FIP, xFIP, xERA, health, and composites remain unavailable."}
    handedness = {**common, "note": "Observed pitcher handedness; opponent splits, pitch mix, and velocity remain unavailable."}
    for side, person_id in identities.items():
        people = [person for person in cached["payload"]["people"] if person.get("id") == person_id]
        person = people[0] if len(people) == 1 else {}
        metrics = _season_metrics(person, observed_at.year)
        quality[f"{side}_starter_era"] = metrics.get("era")
        quality[f"{side}_starter_k_minus_bb_pct"] = metrics.get("kMinusBbPct")
        hand = person.get("pitchHand", {}).get("code")
        handedness[f"{side}_starter_hand"] = hand if hand in {"L", "R", "S"} else None
    return quality, handedness
