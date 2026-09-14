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


def finite(value):
    """Return any finite numeric value, including signed model features."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def pitcher_context(values):
    """V8-compatible current-season pitcher summary for history and inference."""
    def first(*names):
        return next((value for name in names if (value := finite(values.get(name))) is not None), None)

    return {
        "quality": first("starter_context_quality"),
        "recent_form": first("starter_context_recent_form"),
        # The official V8 game-log producer does not manufacture velocity.
        # It remains available in KS1's separately named Statcast features.
        "velocity": first("starter_context_velocity"),
        "command": first("starter_context_command"),
        "expected_innings": first("starter_expected_innings_last5"),
    }


CONTEXT_REQUIRED_COUNTS = ("outs", "earnedRuns", "homeRuns", "baseOnBalls",
                           "hitBatsmen", "strikeOuts", "battersFaced")


def complete_context_counts(stats):
    return (all(number(stats.get(key)) is not None for key in CONTEXT_REQUIRED_COUNTS)
            and number(stats.get("battersFaced")) > 0)


CONTEXT_BASES = {"current_season_pitcher": 0.0, "prior_year_pitcher": 1.0,
                 "current_season_league_prior": 2.0, "prior_year_league_prior": 3.0}


def context_history(completed, pitcher_id, year):
    """Select an explicit prior when an identified pitcher has no season history.

    Callers supply only games completed before the cutoff and on earlier days.
    An incomplete observed pitching line is never replaced by a prior.
    """
    if pitcher_id is None:
        return [], None
    for season, basis in ((year, "current_season_pitcher"), (year-1, "prior_year_pitcher")):
        entries = [(r, p["stats"]) for r in completed if r["day"].year == season
                   for p in r.get("context_players", r["players"]) if p["id"] == str(pitcher_id)]
        if entries:
            return entries, basis
    for season, basis in ((year, "current_season_league_prior"),
                          (year-1, "prior_year_league_prior")):
        entries = [(r, p["stats"]) for r in completed if r["day"].year == season
                   for p in r.get("context_players", r["players"])
                   if number(p["stats"].get("gamesStarted")) == 1
                   and complete_context_counts(p["stats"])]
        if entries:
            return entries, basis
    return [], None


def summarize_context(entries, basis):
    ordered = sorted(entries, key=lambda x: (x[0]["start"], x[0]["game_id"]))
    starts = [x for x in ordered if number(x[1].get("gamesStarted")) == 1]
    ordered = starts or ordered
    season = official_context_pitching([stats for _, stats in ordered])
    league_prior = basis in ("current_season_league_prior", "prior_year_league_prior")
    recent = season if league_prior else official_context_pitching([stats for _, stats in ordered[-3:]])
    outs = [number(stats.get("outs")) for _, stats in (ordered if league_prior else ordered[-5:])]
    return {"quality": season["quality"], "command": season["command"],
            "recent_form": recent["command"] if recent["command"] is not None else recent["quality"],
            "velocity": None,
            "expected_innings": round(sum(outs)/(3*len(outs)), 3)
            if outs and all(x is not None for x in outs) else None}


def official_context_pitching(rows):
    """Mirror V8's unshrunk official game-log summary contract."""
    if not rows:
        return {"quality": None, "command": None}
    required = CONTEXT_REQUIRED_COUNTS
    if any(not complete_context_counts(row) for row in rows):
        return {"quality": None, "command": None}
    total = {key: sum(number(row[key]) for row in rows) for key in required}
    if not total["outs"]:
        return {"quality": None, "command": None}
    fip = ((13*total["homeRuns"] + 3*(total["baseOnBalls"]+total["hitBatsmen"])
            - 2*total["strikeOuts"])*3/total["outs"] + FIP_CONSTANT)
    command = (100*(total["strikeOuts"]-total["baseOnBalls"])/total["battersFaced"]
               if total["battersFaced"] else None)
    return {"quality": round(-fip, 4),
            "command": round(command, 4) if command is not None else None}


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
BATTER_RESULTS = BAT + ("strikeOuts",)
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
LINEUP_SLOT_WEIGHTS = (1.00, .98, .96, .94, .92, .90, .88, .86, .84)


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
            players, batters, context_players = [], [], []
            if full:
                for player in team.get("players", {}).values():
                    stats = player.get("stats", {}).get("pitching", {})
                    # A nonempty official pitching line is evidence of an
                    # appearance even when BF is missing or zero. Retain it
                    # for context so incomplete history cannot become a prior.
                    # Keep legacy team/rolling feature membership unchanged.
                    if stats:
                        context_players.append({"id": str(player["person"]["id"]), "stats": stats})
                    if number(stats.get("battersFaced")) is not None and stats.get("battersFaced"):
                        players.append({"id": str(player["person"]["id"]), "stats": stats})
                        (starters if stats.get("gamesStarted") == 1 else relief).append(stats)
                    batting_stats = player.get("stats", {}).get("batting", {})
                    if any(number(batting_stats.get(key)) is not None for key in BATTER_RESULTS):
                        batters.append({"id": str(player["person"]["id"]), "stats": batting_stats})
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
                         "relief": usage if full else team.get("relief", {}),
                         "players": players, "batters": batters, "context_players": context_players})
    return rows


class Features:
    def __init__(self, games, statcast_rows=None, *, statcast_complete=True,
                 prior_statcast_profiles=None, prior_statcast_year=None,
                 statcast_retained_dates=None):
        self.rows = normalize(games)
        self.statcast_rows = list(statcast_rows or [])
        self.statcast_complete = bool(statcast_complete)
        self.statcast_retained_dates = (None if statcast_retained_dates is None
                                       else set(statcast_retained_dates))
        self.prior_statcast_profiles = dict(prior_statcast_profiles or {})
        self.prior_statcast_year = prior_statcast_year
        self.statcast_by_game = {}
        self.statcast_by_pitcher_game = {}
        self.statcast_by_batter_game = {}
        for row in self.statcast_rows:
            game_id, pitcher_id = str(row.get("game_pk")), str(row.get("pitcher"))
            self.statcast_by_game.setdefault(game_id, []).append(row)
            self.statcast_by_pitcher_game.setdefault((pitcher_id, game_id), []).append(row)
            self.statcast_by_batter_game.setdefault((str(row.get("batter")), game_id), []).append(row)
        self.cache = {}
        self.priors = {}
        # Preserve the original row/player order while avoiding a full league
        # scan for each of nine batters and each roster reliever in every game.
        self.batter_history = {}
        self.reliever_history = {}
        self.bullpen_priors = {}
        for row_number, row in enumerate(self.rows):
            for batter in row["batters"]:
                self.batter_history.setdefault(batter["id"], []).append((row, batter["stats"]))
            for player_number, player in enumerate(row["players"]):
                if number(player["stats"].get("gamesStarted")) != 1:
                    self.reliever_history.setdefault(player["id"], []).append(
                        ((row_number, player_number), row, player["stats"]))

    def team_statcast_window_complete(self, target, window):
        """Archive completeness alone cannot prove compacted rows are loaded."""
        return self.statcast_complete and (
            self.statcast_retained_dates is None or all(
                (target-timedelta(days=age)).isoformat() in self.statcast_retained_dates
                for age in range(1, window+1)))

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

    def bullpen_roster_at(self, cutoff, team_id, roster_ids, *, game_date=None):
        """Summarize only listed relievers from strictly earlier games.

        Availability-named model fields are workload estimates, never confirmed
        availability. A player with no retained earlier appearance is UNKNOWN.
        """
        target = calendar_date.fromisoformat(game_date) if game_date else day(cutoff)
        roster = {str(value) for value in roster_ids}
        # Apply the current, point-in-time roster identity after selecting the
        # history.  A traded/claimed reliever's earlier appearances remain
        # relevant even when they were recorded for another club.
        cutoff_at = utc(cutoff)
        completed = [r for r in self.rows if r["completed"] < cutoff_at
                     and r["day"] < target]
        eligible = [r for r in completed if r["day"].year == target.year]
        # For a fixed target date, increasing cutoff can only add completed
        # rows. Equal lengths therefore identify the same eligible population.
        prior_key = (target, len(eligible))
        if prior_key not in self.bullpen_priors:
            league_rows = [p["stats"] for r in eligible for p in r["players"]]
            self.bullpen_priors[prior_key] = {
                "kbb": counts(league_rows, PITCH)[0],
                "whip": counts(league_rows, ("outs", "hits", "baseOnBalls"))[0]}
        league = self.bullpen_priors[prior_key]
        indexed = [entry for pid in roster for entry in self.reliever_history.get(pid, ())
                   if entry[1]["completed"] < cutoff_at and entry[1]["day"] < target]
        # The pooled sums must retain original accumulation order, including
        # appearances before a trade and interleaved opposing-team records.
        appearances = [(r, stats) for _, r, stats in sorted(indexed, key=lambda entry: entry[0])]
        result = {"bullpen_context_roster_count": float(len(roster))}
        for window in (7, 15, 30):
            pairs = [(r, stats) for r, stats in appearances
                     if r["day"] >= target-timedelta(days=window)]
            box = pitching([stats for _, stats in pairs], league)
            for metric in ("era", "whip", "ra9", "wins", "losses", "fip",
                           "k_pct", "bb_pct", "k_bb_pct", "appearances"):
                result[f"bullpen_context_{metric}_{window}d"] = box.get(metric)
            game_ids = {r["game_id"] for r, _ in pairs}
            expected = (sum(number(stats.get("numberOfPitches")) for _, stats in pairs)
                        if pairs and all(number(stats.get("numberOfPitches")) is not None
                                         for _, stats in pairs) else None)
            statcast = self.statcast(None, set(), None)
            relief_identities = {(r["game_id"], p["id"])
                                 for r, stats in pairs for p in r["players"]
                                 if p["id"] in roster and p["stats"] is stats
                                 and number(p["stats"].get("gamesStarted")) != 1}
            selected = [row for game_id, pitcher_id in relief_identities
                        for row in self.statcast_by_pitcher_game.get((pitcher_id, game_id), ())]
            if (self.team_statcast_window_complete(target, window)
                    and expected is not None and selected and len(selected) == expected):
                descriptions = [str(row.get("description") or "").lower() for row in selected]
                contacts = [row for row in selected if row.get("type") == "X"]
                speeds = [self._finite(row.get("launch_speed")) for row in contacts]
                barrels = [self._finite(row.get("launch_speed_angle")) for row in contacts]
                pas = [row for row in selected if self._finite(row.get("woba_denom")) == 1]
                expected_woba = [(self._finite(row.get("estimated_woba_using_speedangle"))
                                  if row.get("type") == "X" else self._finite(row.get("woba_value")))
                                 for row in pas]
                release = [self._finite(row.get("release_speed")) for row in selected]
                statcast.update({
                    "swstr_pct": 100*sum(d in SWINGING_STRIKES for d in descriptions)/len(selected),
                    "csw_pct": 100*sum(d in SWINGING_STRIKES | CALLED_STRIKES for d in descriptions)/len(selected),
                    "xwoba": (sum(expected_woba)/len(expected_woba)
                              if expected_woba and all(v is not None for v in expected_woba) else None),
                    "barrel_pct": (100*sum(v == 6 for v in barrels)/len(barrels)
                                   if barrels and all(v is not None for v in barrels) else None),
                    "hard_hit_pct": (100*sum(v >= 95 for v in speeds)/len(speeds)
                                     if speeds and all(v is not None for v in speeds) else None),
                    "avg_ev_allowed": (sum(speeds)/len(speeds)
                                       if speeds and all(v is not None for v in speeds) else None),
                    "velocity": (sum(release)/len(release)
                                 if release and all(v is not None for v in release) else None),
                })
            for metric in ("swstr_pct", "csw_pct", "xwoba", "barrel_pct",
                           "hard_hit_pct", "avg_ev_allowed", "velocity"):
                result[f"bullpen_context_{metric}_{window}d"] = statcast.get(metric)
        states = {"AVAILABLE": 0, "LIMITED": 0, "LIKELY_UNAVAILABLE": 0, "UNKNOWN": 0}
        reliever_profiles, state_by_pitcher = [], {}
        workload_score = 0.0
        workload_complete = bool(roster)
        for pid in roster:
            recent = [(r, stats) for r, stats in appearances if any(
                p["id"] == pid and p["stats"] is stats for p in r["players"])]
            by_age = {age: [stats for r, stats in recent if (target-r["day"]).days == age]
                      for age in range(1, 8)}
            # A retained prior appearance establishes the pitcher's identity.
            # Complete history plus no use in the last seven days is positive
            # evidence of rest, not an unknown workload state.
            known = bool(recent)
            usage1 = [s for s in by_age[1]]
            usage3 = [s for age in range(1, 4) for s in by_age[age]]
            counts1 = [number(s.get("numberOfPitches")) for s in usage1]
            counts3 = [number(s.get("numberOfPitches")) for s in usage3]
            pitches1 = sum(counts1) if all(v is not None for v in counts1) else None
            pitches3 = sum(counts3) if all(v is not None for v in counts3) else None
            consecutive = 0
            for age in range(1, 8):
                if by_age[age]: consecutive += 1
                else: break
            counts_known = known and pitches1 is not None and pitches3 is not None
            workload_complete = workload_complete and counts_known
            if known and counts_known:
                workload_score += pitches1 + .35*max(0, pitches3-pitches1)
            state = ("UNKNOWN" if not known or not counts_known else "LIKELY_UNAVAILABLE"
                     if pitches1 >= 30 or consecutive >= 3 else "LIMITED"
                     if pitches1 >= 20 or pitches3 >= 45 or consecutive >= 2 else "AVAILABLE")
            states[state] += 1
            state_by_pitcher[pid] = state
            pitcher_profile = {"player_id": pid, "availability_state": state,
                               "consecutive_usage_days": consecutive,
                               "workload": {}}
            for days in (1, 3, 5, 7):
                selected_usage = [stats for r, stats in recent
                                  if 1 <= (target-r["day"]).days <= days]
                pitcher_profile["workload"][str(days)+"d"] = {
                    "pitches": (sum(number(stats.get("numberOfPitches")) for stats in selected_usage)
                                if selected_usage and all(number(stats.get("numberOfPitches")) is not None
                                                          for stats in selected_usage) else None),
                    "batters_faced": (sum(number(stats.get("battersFaced")) for stats in selected_usage)
                                      if selected_usage and all(number(stats.get("battersFaced")) is not None
                                                                for stats in selected_usage) else None),
                    "outs": (sum(number(stats.get("outs")) for stats in selected_usage)
                             if selected_usage and all(number(stats.get("outs")) is not None
                                                       for stats in selected_usage) else None)}
            pitcher_profile["windows"] = {}
            for days in (7, 15, 30):
                chosen = [(r, stats) for r, stats in recent
                          if 1 <= (target-r["day"]).days <= days]
                box_values = pitching([stats for _, stats in chosen], league)
                game_ids = {r["game_id"] for r, _ in chosen}
                expected = (sum(number(stats.get("numberOfPitches")) for _, stats in chosen)
                            if chosen and all(number(stats.get("numberOfPitches")) is not None
                                              for _, stats in chosen) else None)
                statcast_values = self.statcast(pid, game_ids, expected)
                pitcher_profile["windows"][str(days)+"d"] = {
                    **{key: box_values.get(key) for key in ("era", "whip", "ra9", "wins", "losses",
                                                           "fip", "k_pct", "bb_pct", "k_bb_pct",
                                                           "appearances")},
                    **{key: statcast_values.get(key) for key in ("swstr_pct", "csw_pct", "xwoba",
                                                                 "barrel_pct", "hard_hit_pct",
                                                                 "avg_ev_allowed", "velocity", "spin",
                                                                 "horizontal_break_in", "vertical_break_in",
                                                                 "extension", "active_spin_pct", "stuff_plus",
                                                                 "location_plus", "pitching_plus")},
                    "pitch_arsenal": {pitch: {key: statcast_values.get(
                        pitch.lower()+"_"+key) for key in ("mix_pct", "velocity", "spin",
                                                           "horizontal_break_in", "vertical_break_in",
                                                           "extension", "whiff_pct", "xwoba_contact")}
                                      for pitch in PITCH_BUCKETS}}
            reliever_profiles.append(pitcher_profile)
        for state, count in states.items():
            result["bullpen_context_"+state.lower()+"_count"] = float(count)
        result["bullpen_context_fatigue_score"] = workload_score if workload_complete else None
        result["bullpen_context_depth"] = float(len(roster)-states["UNKNOWN"])
        result["bullpen_context_availability_method"] = "strict_prior_workload_v1"
        result["bullpen_context_high_leverage_quality"] = None
        result["bullpen_context_platoon_coverage"] = None
        result["bullpen_context_quality"] = (-result["bullpen_context_fip_30d"]
                                             if result.get("bullpen_context_fip_30d") is not None else None)
        result["bullpen_context_command"] = result.get("bullpen_context_k_bb_pct_30d")
        prior_30 = [(r, stats) for r, stats in appearances
                    if r["day"] >= target-timedelta(days=30)]
        outs = [number(stats.get("outs")) for _, stats in prior_30]
        games = {r["game_id"] for r, _ in prior_30}
        result["bullpen_context_expected_innings"] = (
            sum(outs)/(3*len(games)) if games and all(value is not None for value in outs) else None)
        available_stats = [stats for r, stats in prior_30
                           if any(p["id"] in state_by_pitcher
                                  and state_by_pitcher[p["id"]] == "AVAILABLE"
                                  and p["stats"] is stats for p in r["players"])]
        available_box = pitching(available_stats, league)
        result["bullpen_context_available_quality"] = (
            -available_box["fip"] if available_box.get("fip") is not None else None)
        result["bullpen_context_early_exit_quality"] = result["bullpen_context_available_quality"]
        result["_reliever_profiles"] = sorted(reliever_profiles, key=lambda item: item["player_id"])
        return result

    def lineup_batters_at(self, cutoff, lineup_ids, opposing_starter_id=None,
                          opposing_hand=None, *, game_date=None):
        """Return batter-level prior boxes/Statcast and lineup aggregates."""
        target = calendar_date.fromisoformat(game_date) if game_date else day(cutoff)
        cutoff_at = utc(cutoff)
        completed = [r for r in self.rows if r["completed"] < cutoff_at and r["day"] < target]
        recent_game_ids = {r["game_id"] for r in completed
                           if r["day"] >= target-timedelta(days=30)}
        ids = [str(value) for value in lineup_ids]
        starter_rows = [row for game_id in self.statcast_by_pitcher_game
                        if game_id[0] == str(opposing_starter_id)
                        and game_id[1] in recent_game_ids
                        for row in self.statcast_by_pitcher_game[game_id]]
        starter_mix = Counter(row.get("pitch_type") for row in starter_rows
                              if row.get("pitch_type") in PITCH_TYPES)
        starter_total = sum(starter_mix.values())

        def box(rows):
            if not rows or any(any(number(row.get(key)) is None for key in BATTER_RESULTS) for row in rows):
                return {key: None for key in ("pa", "ops", "obp", "slg", "iso", "k_pct", "bb_pct", "k_bb_pct")}
            total = {key: sum(number(row[key]) for row in rows) for key in BATTER_RESULTS}
            pa = total["atBats"]+total["baseOnBalls"]+total["hitByPitch"]+total["sacFlies"]
            on = total["hits"]+total["baseOnBalls"]+total["hitByPitch"]
            bases = total["hits"]+total["doubles"]+2*total["triples"]+3*total["homeRuns"]
            obp, slg = rate(on, pa), rate(bases, total["atBats"])
            iso = rate(bases-total["hits"], total["atBats"])
            return {"pa": pa, "ops": obp+slg if obp is not None and slg is not None else None,
                    "obp": obp, "slg": slg, "iso": iso,
                    "k_pct": 100*rate(total["strikeOuts"], pa) if pa else None,
                    "bb_pct": 100*rate(total["baseOnBalls"], pa) if pa else None,
                    "k_bb_pct": 100*rate(total["strikeOuts"]-total["baseOnBalls"], pa) if pa else None}

        def expected_woba(row):
            return (self._finite(row.get("estimated_woba_using_speedangle"))
                    if row.get("type") == "X" else self._finite(row.get("woba_value")))

        profiles = []
        for slot, pid in enumerate(ids, 1):
            pairs = [(r, stats) for r, stats in self.batter_history.get(pid, ())
                     if r["completed"] < cutoff_at and r["day"] < target]
            profile = {"player_id": pid, "slot": slot, "windows": {}}
            for window in (7, 30):
                chosen = [(r, stats) for r, stats in pairs
                          if r["day"] >= target-timedelta(days=window)]
                summary = box([stats for _, stats in chosen])
                pitch_rows = [pitch for r, _ in chosen
                              for pitch in self.statcast_by_batter_game.get((pid, r["game_id"]), ())]
                contacts = [pitch for pitch in pitch_rows if pitch.get("type") == "X"]
                speeds = [self._finite(pitch.get("launch_speed")) for pitch in contacts]
                barrels = [self._finite(pitch.get("launch_speed_angle")) for pitch in contacts]
                swings = [pitch for pitch in pitch_rows if (str(pitch.get("description") or "").lower() in SWINGS
                                                            or pitch.get("type") == "X")]
                contact_swings = [pitch for pitch in swings
                                  if str(pitch.get("description") or "").lower() not in SWINGING_STRIKES]
                expected = [expected_woba(pitch) for pitch in pitch_rows
                            if self._finite(pitch.get("woba_denom")) == 1]
                actual = [self._finite(pitch.get("woba_value")) for pitch in pitch_rows
                          if self._finite(pitch.get("woba_denom")) == 1]
                descriptions = [str(pitch.get("description") or "").lower() for pitch in pitch_rows]
                complete = self.team_statcast_window_complete(target, window)
                summary.update({
                    "woba": sum(actual)/len(actual) if complete and actual and all(v is not None for v in actual) else None,
                    "xwoba": sum(expected)/len(expected) if complete and expected and all(v is not None for v in expected) else None,
                    "barrel_pct": 100*sum(v == 6 for v in barrels)/len(barrels) if complete and barrels and all(v is not None for v in barrels) else None,
                    "hard_hit_pct": 100*sum(v >= 95 for v in speeds)/len(speeds) if complete and speeds and all(v is not None for v in speeds) else None,
                    "avg_exit_velocity": sum(speeds)/len(speeds) if complete and speeds and all(v is not None for v in speeds) else None,
                    "contact_pct": 100*len(contact_swings)/len(swings) if complete and swings else None,
                    "swstr_pct": 100*sum(value in SWINGING_STRIKES for value in descriptions)/len(pitch_rows) if complete and pitch_rows else None,
                    "csw_pct": 100*sum(value in SWINGING_STRIKES | CALLED_STRIKES for value in descriptions)/len(pitch_rows) if complete and pitch_rows else None,
                })
                versus = [pitch for pitch in pitch_rows if opposing_hand in ("L", "R")
                          and pitch.get("p_throws") == opposing_hand
                          and self._finite(pitch.get("woba_denom")) == 1]
                versus_values = [expected_woba(pitch) for pitch in versus]
                summary["platoon_xwoba"] = (sum(versus_values)/len(versus_values)
                                             if complete and versus_values and all(v is not None for v in versus_values)
                                             else None)
                by_type = {}
                for pitch_type in PITCH_TYPES:
                    group = [pitch for pitch in pitch_rows if pitch.get("pitch_type") == pitch_type]
                    values = [expected_woba(pitch) for pitch in group
                              if self._finite(pitch.get("woba_denom")) == 1]
                    actual_values = [self._finite(pitch.get("woba_value")) for pitch in group
                                     if self._finite(pitch.get("woba_denom")) == 1]
                    swings_by_type = [pitch for pitch in group
                                      if str(pitch.get("description") or "").lower() in SWINGS
                                      or pitch.get("type") == "X"]
                    by_type[pitch_type] = {
                        "xwoba": sum(values)/len(values) if complete and values and all(v is not None for v in values) else None,
                        "woba": sum(actual_values)/len(actual_values) if complete and actual_values and all(v is not None for v in actual_values) else None,
                        "whiff_pct": (100*sum(str(pitch.get("description") or "").lower() in SWINGING_STRIKES
                                               for pitch in swings_by_type)/len(swings_by_type)
                                      if complete and swings_by_type else None)}
                supported_mix = [(count, by_type.get(pitch_type, {}).get("xwoba"))
                                 for pitch_type, count in starter_mix.items()
                                 if by_type.get(pitch_type, {}).get("xwoba") is not None]
                supported_pitches = sum(count for count, _ in supported_mix)
                summary["pitch_type_matchup_xwoba"] = (
                    sum(count*value for count, value in supported_mix)/starter_total
                    if (complete and self.team_statcast_window_complete(target, 30)
                        and starter_total > 0 and starter_total == len(starter_rows)
                        and supported_pitches == starter_total) else None)
                summary["pitch_type_xwoba"] = by_type
                profile["windows"][str(window)+"d"] = summary
            season = [stats for r, stats in pairs if r["day"].year == target.year]
            profile["windows"]["season"] = box(season)
            prior = [stats for r, stats in pairs if r["day"].year == target.year-1]
            profile["windows"]["prior_year"] = box(prior)
            recent_box, prior_box = profile["windows"]["30d"], profile["windows"]["prior_year"]
            profile["talent"] = {}
            for metric in ("ops", "obp", "slg", "iso", "k_pct", "bb_pct", "k_bb_pct"):
                now, old = recent_box.get(metric), prior_box.get(metric)
                now_n, old_n = recent_box.get("pa") or 0, min(prior_box.get("pa") or 0, 300)
                profile["talent"][metric] = (
                    (now*now_n+old*old_n)/(now_n+old_n) if now is not None and old is not None and now_n+old_n
                    else now if now is not None else old)
            profiles.append(profile)

        metrics = ("ops", "obp", "slg", "iso", "k_pct", "bb_pct", "k_bb_pct",
                   "woba", "xwoba", "barrel_pct", "hard_hit_pct", "avg_exit_velocity",
                   "contact_pct", "swstr_pct", "csw_pct", "platoon_xwoba",
                   "pitch_type_matchup_xwoba")
        features = {}
        for window in ("7d", "30d"):
            for metric in metrics:
                values = [(LINEUP_SLOT_WEIGHTS[p["slot"]-1], p["windows"][window].get(metric)) for p in profiles
                          if p["windows"][window].get(metric) is not None]
                features[f"lineup_{metric}_{window}"] = (
                    sum(weight*value for weight, value in values)/sum(weight for weight, _ in values)
                    if len(values) == len(profiles) == 9 else None)
        for metric in ("ops", "obp", "slg", "iso", "k_pct", "bb_pct", "k_bb_pct"):
            for label, getter in (("prior_year", lambda p: p["windows"]["prior_year"].get(metric)),
                                  ("talent", lambda p: p["talent"].get(metric))):
                values = [(LINEUP_SLOT_WEIGHTS[p["slot"]-1], getter(p)) for p in profiles
                          if getter(p) is not None]
                features[f"lineup_{metric}_{label}"] = (
                    sum(weight*value for weight, value in values)/sum(weight for weight, _ in values)
                    if len(values) == len(profiles) == 9 else None)
        return profiles, features

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
        context_entries, context_basis = context_history(completed, starter_id, date.year)
        context_values = summarize_context(context_entries, context_basis)
        result["starter_expected_innings_last5"] = context_values["expected_innings"]
        result.update({
            "starter_context_quality": context_values["quality"],
            "starter_context_command": context_values["command"],
            "starter_context_recent_form": context_values["recent_form"],
            "starter_context_velocity": None,
            "starter_context_basis_code": CONTEXT_BASES.get(context_basis),
        })
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
        result.update({"pitcher_context_"+name: value
                       for name, value in pitcher_context(result).items()})
        self.cache[key] = result
        return result
