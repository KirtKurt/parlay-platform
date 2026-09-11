"""Past-only, count-based calendar windows with empirical shrinkage."""
from datetime import date as calendar_date, datetime, timedelta
from zoneinfo import ZoneInfo
import math

ET = ZoneInfo("America/New_York")
WINDOWS = (7, 10, 30, 75)


def utc(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must have timezone")
    return result


def day(value):
    return utc(value).astimezone(ET).date()


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def counts(rows, keys):
    valid = [r for r in rows if all(number(r.get(k)) is not None for k in keys)]
    return {k: sum(number(r[k]) for r in valid) for k in keys}, len(valid)


def rate(numerator, denominator):
    return numerator / denominator if denominator else None


def shrink(n, d, prior, strength):
    if prior is None:
        return None  # never learn a cold-start prior from future games
    return (n + strength * prior) / (d + strength)


BAT = ("atBats", "hits", "baseOnBalls", "hitByPitch", "sacFlies", "doubles", "triples", "homeRuns")
PITCH = ("strikeOuts", "baseOnBalls", "battersFaced")


def offense(rows, league):
    total, n = counts(rows, BAT)
    prior = league if isinstance(league, dict) else counts(league, BAT)[0]
    def raw(t):
        pa = t["atBats"] + t["baseOnBalls"] + t["hitByPitch"] + t["sacFlies"]
        on = t["hits"] + t["baseOnBalls"] + t["hitByPitch"]
        tb = t["hits"] + t["doubles"] + 2*t["triples"] + 3*t["homeRuns"]
        return pa, on, tb
    pa, on, tb = raw(total)
    ppa, pon, ptb = raw(prior)
    obp = shrink(on, pa, rate(pon, ppa), 100)
    slg = shrink(tb, total["atBats"], rate(ptb, prior["atBats"]), 100)
    iso = shrink(tb-total["hits"], total["atBats"], rate(ptb-prior["hits"], prior["atBats"]), 100)
    return {"ops": obp+slg if obp is not None and slg is not None else None,
            "iso": iso, "pa": pa, "games": n}


def pitching(rows, league):
    total, n = counts(rows, PITCH)
    prior = league["kbb"] if isinstance(league, dict) else counts(league, PITCH)[0]
    kbb = shrink(total["strikeOuts"]-total["baseOnBalls"], total["battersFaced"],
                 rate(prior["strikeOuts"]-prior["baseOnBalls"], prior["battersFaced"]), 100)
    w, _ = counts(rows, ("outs", "hits", "baseOnBalls"))
    wp = league["whip"] if isinstance(league, dict) else counts(league, ("outs", "hits", "baseOnBalls"))[0]
    whip = shrink(3*(w["hits"]+w["baseOnBalls"]), w["outs"],
                  rate(3*(wp["hits"]+wp["baseOnBalls"]), wp["outs"]), 75)
    return {"k_bb_pct": 100*kbb if kbb is not None else None,
            "whip": whip, "bf": total["battersFaced"], "appearances": n}


def normalize(games):
    rows = []
    for game in games:
        if game.get("gameType") not in ("R", "F", "D", "L", "W"):
            continue
        start, completed = utc(game["startAtUtc"]), utc(game["completedAtUtc"])
        for side in ("home", "away"):
            team = game["teams"][side]
            full = "teamStats" in team
            starters, relief = [], []
            players = []
            if full:
                for player in team.get("players", {}).values():
                    stats = player.get("stats", {}).get("pitching", {})
                    if number(stats.get("battersFaced")) is None or not stats.get("battersFaced"):
                        continue
                    players.append({"id": str(player["person"]["id"]), "stats": stats})
                    (starters if stats.get("gamesStarted") == 1 else relief).append(stats)
                starter_total, _ = counts(starters, PITCH)
                # WHIP needs full boxes, not compact starter summaries.
                if starters and all(all(number(p.get(k)) is not None for k in ("outs", "hits")) for p in starters):
                    starter_total.update({k: sum(number(p[k]) for p in starters) for k in ("outs", "hits")})
                if not starters:
                    starter_total = {}
                usage = {"pitches": sum(number(p["numberOfPitches"]) for p in relief),
                         "outs": sum(number(p["outs"]) for p in relief)} if all(
                             all(number(p.get(k)) is not None for k in ("numberOfPitches", "outs")) for p in relief) else {}
            rows.append({"game_id": str(game["officialGamePk"]), "start": start,
                         "completed": completed, "day": day(game["startAtUtc"]),
                         "team_id": str(team["team"]["id"] if full else team["id"]),
                         "batting": team["teamStats"].get("batting", {}) if full else team.get("batting", {}),
                         "starters": starter_total if full else team.get("priorStarters", {}),
                         "relief": usage if full else team.get("relief", {}), "players": players})
    return rows


class Features:
    def __init__(self, games):
        self.rows = normalize(games)
        self.cache = {}
        self.priors = {}

    def at(self, cutoff, team_id, starter_id=None, *, game_date=None):
        # Conservative same-day exclusion also prevents game-one results from
        # leaking into a doubleheader unless original observations say otherwise.
        date = calendar_date.fromisoformat(game_date) if game_date else day(cutoff)
        eligible = [r for r in self.rows if r["completed"] < utc(cutoff) and r["day"] < date
                    and r["day"].year == date.year]
        # Within one day the eligible set only grows with cutoff. Its length
        # identifies that set without retaining millions of duplicate ID tuples.
        key = (date, len(eligible), str(team_id), starter_id)
        if key in self.cache:
            return self.cache[key]
        team = [r for r in eligible if r["team_id"] == str(team_id)]
        prior_key = (date, len(eligible))
        if prior_key not in self.priors:
            bats = [r["batting"] for r in eligible]
            pitchers = [r["starters"] for r in eligible]
            self.priors[prior_key] = (counts(bats, BAT)[0],
                                      {"kbb": counts(pitchers, PITCH)[0],
                                       "whip": counts(pitchers, ("outs", "hits", "baseOnBalls"))[0]})
        league_bat, league_pitch = self.priors[prior_key]
        result = {"rest_days": (date-max(r["day"] for r in team)).days-1 if team else None,
                  "history_games": len(team)}
        for window in WINDOWS:
            chosen = [r for r in team if r["day"] >= date-timedelta(days=window)]
            for name, value in offense([r["batting"] for r in chosen], league_bat).items():
                result[f"offense_{name}_{window}d"] = value
            for name, value in pitching([r["starters"] for r in chosen], league_pitch).items():
                result[f"team_starter_{name}_{window}d"] = value
            individual = [p["stats"] for r in eligible if r["day"] >= date-timedelta(days=window)
                          for p in r["players"] if p["id"] == starter_id]
            for name, value in pitching(individual, league_pitch).items():
                result[f"starter_{name}_{window}d"] = value if starter_id else None
        for window in (1, 3, 5):
            chosen = [r["relief"] for r in team if r["day"] >= date-timedelta(days=window)]
            valid = all(number(r.get("pitches")) is not None and number(r.get("outs")) is not None for r in chosen)
            for stat in ("pitches", "outs"):
                result[f"bullpen_{stat}_{window}d"] = sum(number(r[stat]) for r in chosen) if team and valid else None
        self.cache[key] = result
        return result
