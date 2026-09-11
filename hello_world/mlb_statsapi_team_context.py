"""Pregame batting orders and prior-day relief workload, observed before T-45.

Workload is usage, not a claim that a reliever is available. Season OPS and
current bullpen roster identities are observed only while the exact game is in
Preview; history is never backfilled.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json

from mlb_statsapi_starter_context import _time, _number
from mlb_batter_observations_v1 import VERSION as BATTING_OBSERVATION_VERSION, season_observation

VERSION = "MLB-STATSAPI-TEAM-CONTEXT-v1-prelock-observations"
ET = ZoneInfo("America/New_York")
_CACHE = {}


def _fetch(endpoint, http_get, clock):
    at = clock().astimezone(timezone.utc)
    cached = _CACHE.get(endpoint)
    if cached and 0 <= (at - cached["at"]).total_seconds() <= 300:
        return cached
    try:
        payload = http_get(endpoint, timeout=4)
        if not isinstance(payload, dict):
            raise ValueError("invalid response")
        result = {"payload": payload, "ok": True,
                  "fingerprint": hashlib.sha256(json.dumps(payload, sort_keys=True,
                     separators=(",", ":")).encode()).hexdigest()}
    except Exception:
        result = {"ok": False}
    result.update(at=clock().astimezone(timezone.utc), endpoint=endpoint)
    if len(_CACHE) >= 128:
        _CACHE.clear()
    _CACHE[endpoint] = result
    return result


def _metadata(receipts):
    return {"provider": "MLB Stats API", "dataset": VERSION,
            "endpoint": ";".join(r["endpoint"] for r in receipts),
            "retrievedAtUtc": max(r["at"] for r in receipts).isoformat(),
            "sourceEffectiveAtUtc": max(r["at"] for r in receipts).isoformat(),
            "payloadFingerprint": hashlib.sha256(json.dumps(
                [(r["endpoint"], r["fingerprint"]) for r in receipts],
                separators=(",", ":")).encode()).hexdigest()}


def _id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _lineup(team):
    order = team.get("battingOrder")
    if not isinstance(order, list) or len(order) != 9 or not all(_id(x) for x in order) or len(set(order)) != 9:
        return None
    ops = []
    season_batting = []
    for slot, identity in enumerate(order, 1):
        player = (team.get("players") or {}).get("ID" + str(identity)) or {}
        if (player.get("person", {}).get("id") != identity
                or str(player.get("battingOrder")) != str(slot * 100)
                or player.get("gameStatus", {}).get("isSubstitute") is not False):
            return None
        stat = player.get("seasonStats", {}).get("batting", {})
        value, appearances = _number(stat.get("ops")), _number(stat.get("plateAppearances"))
        if value is not None and 0 <= value <= 5 and appearances is not None and appearances > 0:
            ops.append(value)
        season_batting.append(season_observation(identity, slot, stat))
    return {"order": order, "meanOps": sum(ops)/9 if len(ops) == 9 else None,
            "seasonBatting": season_batting}


def _bullpen_roster(team):
    """Return identity-verified current bullpen IDs without availability inference."""
    roster = team.get("bullpen")
    players = team.get("players") or {}
    if (not isinstance(roster, list) or not roster or not all(_id(x) for x in roster)
            or len(set(roster)) != len(roster)):
        return None
    for identity in roster:
        player = players.get("ID" + str(identity)) or {}
        if player.get("person", {}).get("id") != identity:
            return None
    return list(roster)


def _relief(team):
    ids = team.get("pitchers")
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids) or not all(_id(x) for x in ids):
        raise ValueError("pitcher identities missing")
    pitches, outs, starters = 0, 0, 0
    for identity in ids:
        player = (team.get("players") or {}).get("ID" + str(identity)) or {}
        stat = player.get("stats", {}).get("pitching", {})
        started = _number(stat.get("gamesStarted"))
        if player.get("person", {}).get("id") != identity or started not in (0, 1):
            raise ValueError("pitcher role unavailable")
        starters += int(started)
        if started == 0:
            values = [_number(stat.get(k)) for k in ("numberOfPitches", "outs")]
            if any(v is None or v < 0 or not v.is_integer() for v in values):
                raise ValueError("relief workload incomplete")
            pitches += int(values[0]); outs += int(values[1])
    if starters != 1:
        raise ValueError("ambiguous starter")
    return {"pitches": pitches, "outs": outs}


def observe(game_date, game, history, http_get, *, now=None):
    clock = now or (lambda: datetime.now(timezone.utc))
    at = clock().astimezone(timezone.utc)
    missing = {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED", "algorithmVersion": VERSION,
               "note": "Verified pre-T45 official team observations unavailable."}
    start = _time(game.get("gameDate"))
    ids = {side: game.get("teams", {}).get(side, {}).get("team", {}).get("id") for side in ("home", "away")}
    if (not _id(game.get("gamePk")) or not start or not all(_id(v) for v in ids.values())
            or game_date != at.astimezone(ET).date().isoformat()
            or game_date != start.astimezone(ET).date().isoformat()
            or at >= start - timedelta(minutes=45)
            or game.get("status", {}).get("abstractGameState") != "Preview"):
        return missing, missing.copy()
    common = {"source_status": "PARTIAL", "algorithmVersion": VERSION,
              "game_pk": game["gamePk"], **{side+"_team_id": identity for side, identity in ids.items()}}
    lineup, bullpen = missing.copy(), missing.copy()
    observed_bullpen_rosters = None
    roster_receipt = None
    receipt = _fetch(f"https://statsapi.mlb.com/api/v1.1/game/{game['gamePk']}/feed/live", http_get, clock)
    if receipt["ok"]:
        payload = receipt["payload"]
        data = payload.get("gameData", {})
        teams = payload.get("liveData", {}).get("boxscore", {}).get("teams", {})
        if (data.get("game", {}).get("pk") == game["gamePk"]
                and data.get("status", {}).get("abstractGameState") == "Preview"
                and _time(data.get("datetime", {}).get("dateTime")) == start
                and all(teams.get(side, {}).get("team", {}).get("id") == ids[side] for side in ids)):
            lineup = {**common, "sourceProvenance": _metadata([receipt]),
                      "lineupSeasonBattingVersion": BATTING_OBSERVATION_VERSION,
                      "note": "Official pregame batting orders and mean season OPS; no wRC+ or injury clearance inferred."}
            complete_lineups = True
            for side in ids:
                observed = _lineup(teams[side])
                if observed is None:
                    complete_lineups = False
                lineup[side+"_lineup_confirmed"] = True if observed else None
                lineup[side+"_batting_order"] = observed["order"] if observed else None
                lineup[side+"_lineup_mean_ops"] = observed["meanOps"] if observed else None
                lineup[side+"_lineup_season_batting"] = observed["seasonBatting"] if observed else None
            # CONNECTED means the exact official Preview feed proved both
            # nine-player batting orders before T-45. Optional OPS may remain
            # unavailable without inventing strength or injury information.
            if complete_lineups:
                lineup["source_status"] = "CONNECTED"
            rosters = {side: _bullpen_roster(teams[side]) for side in ids}
            if all(rosters.values()):
                observed_bullpen_rosters = rosters
                roster_receipt = receipt
    # Prior ET calendar days only. Same-day doubleheaders and suspended games
    # remain excluded; incomplete history never becomes zero workload.
    history = history() if callable(history) else history
    lower = (at.astimezone(ET).date() - timedelta(days=5)).isoformat()
    history_games = [g for d in history.get("payload", {}).get("dates", []) for g in d.get("games", [])]
    history_ids = [g.get("gamePk") for g in history_games]
    history_complete = (history.get("payload", {}).get("totalGames") == len(history_games)
                        and all(_id(pk) for pk in history_ids) and len(set(history_ids)) == len(history_ids))
    if (history.get("ok") is True and history_complete and str(history.get("historyStartDateEt", "9999")) <= lower
            and str(history.get("historyEndDateEt", "")) >= game_date):
        prior = []
        for day in history.get("payload", {}).get("dates", []):
            if lower <= str(day.get("date")) < game_date:
                for old in day.get("games", []):
                    if any(t.get("team", {}).get("id") in ids.values() for t in old.get("teams", {}).values()):
                        prior.append((day["date"], old))
        # At most two teams x five days x doubleheaders. A malformed schedule
        # cannot cause an unbounded provider fan-out.
        if len(prior) <= 20 and all(_id(g.get("gamePk")) and g.get("status", {}).get("abstractGameState") == "Final" for _, g in prior):
            endpoints = sorted({f"https://statsapi.mlb.com/api/v1/game/{g['gamePk']}/boxscore" for _, g in prior})
            with ThreadPoolExecutor(max_workers=4) as pool:
                receipts = list(pool.map(lambda url: _fetch(url, http_get, clock), endpoints))
            if all(r["ok"] for r in receipts):
                boxes = {r["endpoint"]: r["payload"] for r in receipts}
                try:
                    result = {}
                    for side, identity in ids.items():
                        usage = {str(n)+"d": {"pitches": 0, "outs": 0} for n in (1, 3, 5)}
                        for date, old in prior:
                            old_sides = [s for s, t in old["teams"].items() if t.get("team", {}).get("id") == identity]
                            if not old_sides:
                                continue
                            box = boxes[f"https://statsapi.mlb.com/api/v1/game/{old['gamePk']}/boxscore"]
                            team = box.get("teams", {}).get(old_sides[0], {})
                            if len(old_sides) != 1 or team.get("team", {}).get("id") != identity:
                                raise ValueError("historical team identity mismatch")
                            values = _relief(team)
                            age = (at.astimezone(ET).date() - datetime.fromisoformat(date).date()).days
                            for n in (1, 3, 5):
                                if age <= n:
                                    for key in values: usage[str(n)+"d"][key] += values[key]
                        result[side+"_reliever_usage_1d_3d_5d"] = usage
                    history_receipt = {"endpoint": history["endpoint"], "at": _time(history["retrievedAtUtc"]),
                                       "fingerprint": history["payloadFingerprint"]}
                    if not history_receipt["at"] or not history_receipt["fingerprint"]:
                        raise ValueError("history provenance missing")
                    bullpen = {**common, **result, "sourceProvenance": _metadata([history_receipt, *receipts]),
                               "note": "Relief pitches and outs over prior 1/3/5 ET calendar days; excludes same-day games. Availability and fatigue scores are not inferred."}
                    if observed_bullpen_rosters and roster_receipt:
                        bullpen.update({
                            "bullpenRosterObservationStatus": "OBSERVED_ROSTER_ONLY",
                            "bullpenRosterSourceProvenance": _metadata([roster_receipt]),
                            "home_bullpen_roster_player_ids": observed_bullpen_rosters["home"],
                            "away_bullpen_roster_player_ids": observed_bullpen_rosters["away"],
                        })
                except (KeyError, TypeError, ValueError):
                    pass
    if clock().astimezone(timezone.utc) >= start - timedelta(minutes=45):
        return missing, missing.copy()
    return lineup, bullpen
