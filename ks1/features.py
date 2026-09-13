"""Past-only, count-based calendar windows with empirical shrinkage."""
from collections import Counter
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
PITCH_RESULTS = ("outs", "earnedRuns", "runs", "hits", "baseOnBalls")
PITCH_FIP = ("outs", "homeRuns", "baseOnBalls", "hitBatsmen", "strikeOuts")
FIP_CONSTANT = 3.10
PITCH_TYPES = ("FF", "SI", "FC", "SL", "ST", "CU", "KC", "CH", "FS", "SV")
PITCH_BUCKETS = PITCH_TYPES + ("OTHER",)
SWINGING_STRIKES = {"swinging_strike", "swinging_strike_blocked", "foul_tip", "missed_bunt"}
CALLED_STRIKES = {"called_strike"}
SWINGS = SWINGING_STRIKES | {"foul", "foul_bunt", "hit_into_play", "bunt_foul_tip"}
UNAVAILABLE_EXACT = ("xera", "siera", "stuff_plus", "location_plus", "pitching_plus", "active_spin_pct")
PRIOR_WEIGHT_CAP_PITCHES = 300


def statcast_metric_names():
    names = {
        "complete", "pitches", "hard_hit_pct", "barrel_pct", "avg_ev_allowed",
        "xwoba_contact", "xwoba", "xwoba_pa", "swstr_pct", "csw_pct", "velocity",
        "spin", "horizontal_break_in", "vertical_break_in", "extension", "fly_balls",
        *UNAVAILABLE_EXACT,
    }
    for pitch in PITCH_BUCKETS:
        stem = pitch.lower()
        names.update({f"{stem}_{suffix}" for suffix in (
            "mix_pct", "velocity", "spin", "horizontal_break_in", "vertical_break_in",
            "extension", "whiff_per_pitch_pct", "whiff_pct", "xwoba_contact")})
    return tuple(sorted(names))


STATCAST_METRIC_NAMES = statcast_metric_names()
MATCHUP_METRICS = ("pitch_hand_left", "opponent_lhb_pct", "opponent_rhb_pct",
                   "opponent_switch_pct")


def starter_matchup(pitch_hand, bat_sides):
    complete = (pitch_hand in ("L", "R") and isinstance(bat_sides, list)
                and len(bat_sides) == 9 and all(value in ("L", "R", "S") for value in bat_sides))
    return {
        "pitch_hand_left": float(pitch_hand == "L") if complete else None,
        "opponent_lhb_pct": 100*bat_sides.count("L")/9 if complete else None,
        "opponent_rhb_pct": 100*bat_sides.count("R")/9 if complete else None,
        "opponent_switch_pct": 100*bat_sides.count("S")/9 if complete else None,
    }


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
    def complete(keys):
        valid = [r for r in rows if all(number(r.get(k)) is not None for k in keys)]
        return ({k: sum(number(r[k]) for r in valid) for k in keys}, len(valid)) if len(valid) == len(rows) else (None, len(valid))

    results, result_games = complete(PITCH_RESULTS)
    decisions, decision_games = complete(("wins", "losses"))
    fip, fip_games = complete(PITCH_FIP)
    era = 27*results["earnedRuns"]/results["outs"] if results and results["outs"] else None
    ra9 = 27*results["runs"]/results["outs"] if results and results["outs"] else None
    fip_value = ((13*fip["homeRuns"] + 3*(fip["baseOnBalls"]+fip["hitBatsmen"])
                  - 2*fip["strikeOuts"])*3/fip["outs"] + FIP_CONSTANT) if fip and fip["outs"] else None
    k_pct = rate(total["strikeOuts"], total["battersFaced"])
    bb_pct = rate(total["baseOnBalls"], total["battersFaced"])
    return {"era": era, "whip": whip, "ra9": ra9,
            "wins": decisions["wins"] if decisions else None,
            "losses": decisions["losses"] if decisions else None,
            "win_pct": rate(decisions["wins"], decisions["wins"]+decisions["losses"]) if decisions else None,
            "fip": fip_value, "fip_constant": FIP_CONSTANT if fip_value is not None else None,
            "k_pct": 100*k_pct if k_pct is not None else None,
            "bb_pct": 100*bb_pct if bb_pct is not None else None,
            "k_bb_pct": 100*kbb if kbb is not None else None,
            "bf": total["battersFaced"], "appearances": n,
            "outs": fip["outs"] if fip else None,
            "home_runs": fip["homeRuns"] if fip else None,
            "hit_batters": fip["hitBatsmen"] if fip else None,
            "strikeouts": fip["strikeOuts"] if fip else None,
            "walks": fip["baseOnBalls"] if fip else None,
            "result_games": result_games, "decision_games": decision_games, "fip_games": fip_games}


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
                for key in ("earnedRuns", "runs", "homeRuns", "hitBatsmen", "wins", "losses"):
                    if starters and all(number(p.get(key)) is not None for p in starters):
                        starter_total[key] = sum(number(p[key]) for p in starters)
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
    def __init__(self, games, statcast_rows=None, *, statcast_complete=True,
                 prior_statcast_profiles=None, prior_statcast_year=None):
        self.rows = normalize(games)
        self.statcast_rows = list(statcast_rows or [])
        self.statcast_complete = bool(statcast_complete)
        self.prior_statcast_profiles = dict(prior_statcast_profiles or {})
        self.prior_statcast_year = prior_statcast_year
        self.statcast_by_game = {}
        self.statcast_by_pitcher_game = {}
        for row in self.statcast_rows:
            game_id, pitcher_id = str(row.get("game_pk")), str(row.get("pitcher"))
            self.statcast_by_game.setdefault(game_id, []).append(row)
            self.statcast_by_pitcher_game.setdefault((pitcher_id, game_id), []).append(row)
        self.cache = {}
        self.priors = {}

    @staticmethod
    def _finite(value):
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    def statcast(self, starter_id, game_ids, expected_pitches):
        selected = [row for game_id in game_ids
                    for row in self.statcast_by_pitcher_game.get((str(starter_id), str(game_id)), ())]
        complete = bool(starter_id and game_ids and expected_pitches is not None and len(selected) == expected_pitches)
        def average(values):
            return sum(values)/len(values) if values and all(v is not None for v in values) else None
        contacts = [r for r in selected if r.get("type") == "X"]
        speeds = [self._finite(r.get("launch_speed")) for r in contacts]
        barrels = [self._finite(r.get("launch_speed_angle")) for r in contacts]
        descriptions = [str(r.get("description") or "").lower() for r in selected]
        plate_appearances = [r for r in selected if self._finite(r.get("woba_denom")) == 1]
        expected_woba = []
        for row in plate_appearances:
            value = (self._finite(row.get("estimated_woba_using_speedangle"))
                     if row.get("type") == "X" else self._finite(row.get("woba_value")))
            expected_woba.append(value)
        fly_balls = [r for r in contacts if r.get("bb_type") == "fly_ball"]
        result = {"complete": 1.0 if complete else 0.0, "pitches": len(selected) if complete else None,
                  "hard_hit_pct": 100*sum(v >= 95 for v in speeds)/len(speeds) if complete and speeds and all(v is not None for v in speeds) else None,
                  "barrel_pct": 100*sum(v == 6 for v in barrels)/len(barrels) if complete and barrels and all(v is not None for v in barrels) else None,
                  "avg_ev_allowed": average(speeds) if complete else None,
                  "xwoba_contact": average([self._finite(r.get("estimated_woba_using_speedangle")) for r in contacts]) if complete else None,
                  "xwoba": average(expected_woba) if complete and plate_appearances else None,
                  "xwoba_pa": len(plate_appearances) if complete else None,
                  "swstr_pct": 100*sum(d in SWINGING_STRIKES for d in descriptions)/len(selected) if complete and selected else None,
                  "csw_pct": 100*sum(d in SWINGING_STRIKES | CALLED_STRIKES for d in descriptions)/len(selected) if complete and selected else None,
                  "velocity": average([self._finite(r.get("release_speed")) for r in selected]) if complete else None,
                  "spin": average([self._finite(r.get("release_spin_rate")) for r in selected]) if complete else None,
                  "horizontal_break_in": average([12*self._finite(r.get("pfx_x")) if self._finite(r.get("pfx_x")) is not None else None for r in selected]) if complete else None,
                  "vertical_break_in": average([12*self._finite(r.get("pfx_z")) if self._finite(r.get("pfx_z")) is not None else None for r in selected]) if complete else None,
                  "extension": average([self._finite(r.get("release_extension")) for r in selected]) if complete else None,
                  "fly_balls": len(fly_balls) if complete else None}
        result.update({name: None for name in UNAVAILABLE_EXACT})
        counts = Counter(r.get("pitch_type") if r.get("pitch_type") in PITCH_TYPES else "OTHER"
                         for r in selected)
        for pitch in PITCH_BUCKETS:
            group = [r for r in selected if (r.get("pitch_type") == pitch if pitch != "OTHER"
                                              else r.get("pitch_type") not in PITCH_TYPES)]
            desc = [str(r.get("description") or "").lower() for r in group]
            contact = [r for r in group if r.get("type") == "X"]
            swings = sum(d in SWINGS or row.get("type") == "X" for row, d in zip(group, desc))
            result.update({f"{pitch.lower()}_mix_pct": 100*counts[pitch]/len(selected) if complete and selected else None,
                           f"{pitch.lower()}_velocity": average([self._finite(r.get("release_speed")) for r in group]) if complete and group else None,
                           f"{pitch.lower()}_spin": average([self._finite(r.get("release_spin_rate")) for r in group]) if complete and group else None,
                           f"{pitch.lower()}_horizontal_break_in": average([12*self._finite(r.get("pfx_x")) if self._finite(r.get("pfx_x")) is not None else None for r in group]) if complete and group else None,
                           f"{pitch.lower()}_vertical_break_in": average([12*self._finite(r.get("pfx_z")) if self._finite(r.get("pfx_z")) is not None else None for r in group]) if complete and group else None,
                           f"{pitch.lower()}_extension": average([self._finite(r.get("release_extension")) for r in group]) if complete and group else None,
                           f"{pitch.lower()}_whiff_per_pitch_pct": 100*sum(d in SWINGING_STRIKES for d in desc)/len(group) if complete and group else None,
                           f"{pitch.lower()}_whiff_pct": 100*sum(d in SWINGING_STRIKES for d in desc)/swings if complete and swings else None,
                           f"{pitch.lower()}_xwoba_contact": average([self._finite(r.get("estimated_woba_using_speedangle")) for r in contact]) if complete and contact else None})
        return result

    def league_hr_fb(self, eligible):
        if not self.statcast_complete:
            return None
        game_ids = {row["game_id"] for row in eligible}
        balls = [row for game_id in game_ids for row in self.statcast_by_game.get(str(game_id), ())
                 if row.get("bb_type") == "fly_ball"]
        return rate(sum(row.get("events") == "home_run" for row in balls), len(balls))

    @staticmethod
    def xfip(box, statcast, league_hr_fb):
        values = [box.get(name) for name in ("outs", "walks", "hit_batters", "strikeouts")]
        if (any(value is None for value in values) or not box.get("outs")
                or statcast.get("fly_balls") is None or league_hr_fb is None):
            return None
        outs, walks, hit_batters, strikeouts = values
        expected_home_runs = statcast["fly_balls"] * league_hr_fb
        return (13*expected_home_runs + 3*(walks+hit_batters)-2*strikeouts)*3/outs + FIP_CONSTANT

    @staticmethod
    def talent(current, prior, metric):
        now, old = current.get(metric), prior.get(metric)
        now_n, old_n = current.get("pitches"), prior.get("pitches")
        if now is None:
            return old
        if old is None or old_n is None or old_n <= 0:
            return now
        now_weight = now_n if now_n is not None and now_n > 0 else 0
        prior_weight = min(old_n, PRIOR_WEIGHT_CAP_PITCHES)
        return (now*now_weight + old*prior_weight)/(now_weight+prior_weight)

    def at(self, cutoff, team_id, starter_id=None, *, game_date=None):
        # Conservative same-day exclusion also prevents game-one results from
        # leaking into a doubleheader unless original observations say otherwise.
        date = calendar_date.fromisoformat(game_date) if game_date else day(cutoff)
        completed = [r for r in self.rows if r["completed"] < utc(cutoff) and r["day"] < date]
        eligible = [r for r in completed if r["day"].year == date.year]
        prior_year = [r for r in completed if r["day"].year == date.year-1]
        # Within one day the eligible set only grows with cutoff. Its length
        # identifies that set without retaining millions of duplicate ID tuples.
        key = (date, len(eligible), str(team_id), starter_id)
        if key in self.cache:
            return self.cache[key]
        # The accepted model learned history_games/rest from target-season
        # history.  Prior-season rows are retained solely for explicit
        # prior-year and cross-year rolling pitcher fields; they must not
        # silently change an incumbent input at serving time.
        team = [r for r in eligible if r["team_id"] == str(team_id)]
        prior_key = (date, len(eligible))
        if prior_key not in self.priors:
            bats = [r["batting"] for r in eligible]
            pitchers = [r["starters"] for r in eligible]
            self.priors[prior_key] = (counts(bats, BAT)[0],
                                      {"kbb": counts(pitchers, PITCH)[0],
                                       "whip": counts(pitchers, ("outs", "hits", "baseOnBalls"))[0]})
        league_bat, league_pitch = self.priors[prior_key]
        prior_pitchers = [r["starters"] for r in prior_year]
        prior_league_pitch = {"kbb": counts(prior_pitchers, PITCH)[0],
                              "whip": counts(prior_pitchers, ("outs", "hits", "baseOnBalls"))[0]}
        # The retained live Statcast contract proves the trailing 30 complete
        # dates, not every game in the season. Match the league denominator to
        # that proven source window before deriving xFIP.
        league_hr_fb = self.league_hr_fb(
            [r for r in eligible if r["day"] >= date-timedelta(days=30)])
        result = {"rest_days": (date-max(r["day"] for r in team)).days-1 if team else None,
                  "history_games": len(team)}
        for window in WINDOWS:
            chosen = [r for r in team if r["day"] >= date-timedelta(days=window)]
            for name, value in offense([r["batting"] for r in chosen], league_bat).items():
                result[f"offense_{name}_{window}d"] = value
            for name, value in pitching([r["starters"] for r in chosen], league_pitch).items():
                result[f"team_starter_{name}_{window}d"] = value
            appearances = [(r, p["stats"]) for r in completed if r["day"] >= date-timedelta(days=window)
                           for p in r["players"] if p["id"] == starter_id]
            individual = [stats for _, stats in appearances]
            box = pitching(individual, league_pitch)
            for name, value in box.items():
                result[f"starter_{name}_{window}d"] = value if starter_id else None
            ids = {r["game_id"] for r, _ in appearances}
            expected = sum(number(stats.get("numberOfPitches")) for _, stats in appearances) if appearances and all(
                number(stats.get("numberOfPitches")) is not None for _, stats in appearances) else None
            statcast = self.statcast(starter_id, ids, expected)
            statcast["xfip"] = self.xfip(box, statcast, league_hr_fb)
            for name, value in statcast.items():
                result[f"starter_{name}_{window}d"] = value
        starts = [(r["start"], r["game_id"], p["stats"]) for r in completed for p in r["players"]
                  if p["id"] == starter_id and number(p["stats"].get("gamesStarted")) == 1]
        last_three_pairs = sorted(starts, key=lambda item: item[0], reverse=True)[:3]
        last_three_complete = len(last_three_pairs) == 3
        last_three = [stats for _, _, stats in last_three_pairs] if last_three_complete else []
        # A pitcher's season debut can occur after other current-season games.
        # Select the shrinkage baseline from the starts themselves, not the
        # league-wide current-season history.
        prior_only = (last_three_complete and
                      all(start.year == date.year-1 for start, _, _ in last_three_pairs))
        last_three_league = prior_league_pitch if prior_only else league_pitch
        last_three_box = pitching(last_three, last_three_league)
        for name, value in last_three_box.items():
            result[f"starter_{name}_last3"] = value if starter_id and last_three_complete else None
        result["starter_starts_observed_last3"] = float(len(last_three_pairs)) if starter_id else None
        last_three_ids = {str(game_id) for _, game_id, _ in last_three_pairs}
        expected = sum(number(stats.get("numberOfPitches")) for stats in last_three) if last_three and all(
            number(stats.get("numberOfPitches")) is not None for stats in last_three) else None
        last_three_statcast = self.statcast(starter_id, last_three_ids, expected)
        last_three_statcast["xfip"] = self.xfip(last_three_box, last_three_statcast, league_hr_fb)
        for name, value in last_three_statcast.items():
            result[f"starter_{name}_last3"] = value
        prior_pairs = [(r, p["stats"]) for r in prior_year for p in r["players"]
                       if p["id"] == starter_id and number(p["stats"].get("gamesStarted")) == 1]
        prior_box = pitching([stats for _, stats in prior_pairs], prior_league_pitch)
        for name, value in prior_box.items():
            result[f"starter_{name}_prior_year"] = value if starter_id else None
        prior_ids = {r["game_id"] for r, _ in prior_pairs}
        prior_expected = sum(number(stats.get("numberOfPitches")) for _, stats in prior_pairs) if prior_pairs and all(
            number(stats.get("numberOfPitches")) is not None for _, stats in prior_pairs) else None
        retained_prior = (self.prior_statcast_profiles.get(str(starter_id))
                          if self.prior_statcast_year == date.year-1 else None)
        prior_statcast = dict(retained_prior) if retained_prior is not None else self.statcast(
            starter_id, prior_ids, prior_expected)
        prior_statcast["xfip"] = None  # current-year league HR/FB is not a prior-year constant
        for name, value in prior_statcast.items():
            result[f"starter_{name}_prior_year"] = value
        current_30 = {name: result.get(f"starter_{name}_30d") for name in STATCAST_METRIC_NAMES}
        for metric in ("velocity", "spin", "extension", "ff_velocity", "ff_spin"):
            result[f"starter_{metric}_talent"] = self.talent(current_30, prior_statcast, metric)
        result["starter_talent_prior_weight_cap_pitches"] = float(PRIOR_WEIGHT_CAP_PITCHES)
        for window in (1, 3, 5):
            chosen = [r["relief"] for r in team if r["day"] >= date-timedelta(days=window)]
            valid = all(number(r.get("pitches")) is not None and number(r.get("outs")) is not None for r in chosen)
            for stat in ("pitches", "outs"):
                result[f"bullpen_{stat}_{window}d"] = sum(number(r[stat]) for r in chosen) if team and valid else None
        self.cache[key] = result
        return result
