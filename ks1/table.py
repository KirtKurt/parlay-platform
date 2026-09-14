"""One row per official game; final-box identities are audit labels, never features."""
from collections import defaultdict
from datetime import date as calendar_date, timedelta
import hashlib
import json
import statistics

import numpy as np
import pyarrow as pa

from ks1.features import Features, day, number, starter_matchup, utc
from ks1.historical_starters import published_starter_index
from ks1.inventory import encode
from ks1.passive_context import MODEL_FEATURES as LINEUP_BULLPEN_FEATURES, published_profile_index

VERSION = "KS1-game-table-v1"


def index(rows, key):
    result = {}
    for row in rows:
        pk = str(row[key])
        if pk in result and encode(result[pk]) != encode(row):
            raise ValueError(f"conflicting duplicate {key}: {pk}")
        result[pk] = row
    return result


def team_identity(team):
    value = team.get("team", team)
    return str(value["id"]), value["name"]


def score_pair(home, away):
    values = [number(home), number(away)]
    return tuple(int(v) for v in values) if all(v is not None and v.is_integer() for v in values) else None


def american(value):
    try:
        value = float(value)
        if not np.isfinite(value) or abs(value) < 100:
            return None
        return -value/(100-value) if value < 0 else 100/(100+value)
    except (ValueError, TypeError):
        return None


def market_index(archives):
    result = defaultdict(list)
    for archive in archives:
        at = archive.get("historicalAtUtc") or archive.get("collectedAtUtc")
        if not at:
            continue
        for event in archive.get("featuredEvents", []):
            if all(event.get(k) for k in ("homeTeam", "awayTeam", "commenceTime")):
                result[(event["homeTeam"], event["awayTeam"])].append((at, event, archive["source_key"]))
    return result


def market_for(row, markets):
    cutoff, start = utc(row["as_of_timestamp"]), utc(row["commence_time"])
    candidates = [(at, e, key) for at, e, key in markets.get((row["home_team"], row["away_team"]), [])
                  if utc(at) <= cutoff and abs((utc(e["commenceTime"])-start).total_seconds()) <= 90]
    if len({e["eventId"] for _, e, _ in candidates}) > 1:
        return {}  # ambiguous event mapping, including doubleheaders
    for at, event, key in sorted(candidates, key=lambda v: utc(v[0]), reverse=True):
        probabilities, totals, spreads = [], [], []
        for book in event.get("bookmakers", []):
            quoted = book.get("lastUpdate")
            if not quoted or not 0 <= (cutoff-utc(quoted)).total_seconds() <= 900:
                continue
            values = book.get("markets", {})
            h2h = values.get("h2h", [])
            h = [american(r.get("price")) for r in h2h if r.get("name") == row["home_team"]]
            a = [american(r.get("price")) for r in h2h if r.get("name") == row["away_team"]]
            if len(h) == len(a) == 1 and h[0] and a[0]:
                probabilities.append(h[0]/(h[0]+a[0]))
            total_rows = values.get("totals", [])
            over = [number(r.get("point")) for r in total_rows if r.get("name") == "Over"]
            under = [number(r.get("point")) for r in total_rows if r.get("name") == "Under"]
            if len(over) == len(under) == 1 and over[0] is not None and over == under:
                totals.append(over[0])
            spread_rows = values.get("spreads", [])
            h = [r.get("point") for r in spread_rows if r.get("name") == row["home_team"]]
            a = [r.get("point") for r in spread_rows if r.get("name") == row["away_team"]]
            if len(h) == len(a) == 1 and all(isinstance(v, (int, float)) and np.isfinite(v) for v in h+a) and h[0] == -a[0]:
                spreads.append(h[0])
        if probabilities or totals or spreads:
            return {"market_home_prob": statistics.mean(probabilities) if probabilities else row["market_home_prob"],
                    "market_total": statistics.median(totals) if totals else None,
                    "market_spread": statistics.median(spreads) if spreads else None,
                    "market_archive_source": key, "market_archive_as_of": at,
                    "odds_event_id": str(event["eventId"])}
    return {}


def build(bundle, selected_date=None):
    reconstructed = index(bundle.get("reconstructed", []), "officialGamePk")
    research = index(bundle.get("research", []), "officialGamePk")
    compact = index(bundle.get("compact", []), "officialGamePk")
    full = index(bundle.get("full", []), "officialGamePk")
    games = {**compact, **full}
    schedule = index(bundle.get("schedule", []), "gamePk")
    finals = defaultdict(list)
    for f in bundle.get("finals", []):
        finals[str(f["officialGamePk"])].append(f)
    snapshots = defaultdict(list)
    for s in bundle.get("snapshots", []):
        if s.get("originalObservation") is True and s.get("outcomeKnownAtCapture") is False:
            snapshots[str(s["officialGamePk"])].append(s)
    published_starters = published_starter_index(bundle.get("published_predictions", []))
    published_team_context = published_profile_index(bundle.get("published_predictions", []))
    historical_context = bundle.get("historical_pitcher_context", {})
    history = Features(list(games.values()), bundle.get("statcast", []),
                       statcast_complete=bundle.get("statcast_coverage_complete") is True,
                       prior_statcast_profiles=bundle.get("prior_statcast_profiles"),
                       prior_statcast_year=bundle.get("prior_statcast_year"))
    markets = market_index(bundle.get("odds", []))
    crosswalk = defaultdict(set)
    player_names = defaultdict(set)
    for game in games.values():
        for team in game["teams"].values():
            tid, name = team_identity(team)
            crosswalk[tid].add(name)
            for player in team.get("players", {}).values():
                person = player["person"]
                if person.get("fullName"):
                    player_names[str(person["id"])].add(person["fullName"])
    name_to_ids = defaultdict(set)
    for tid, names in crosswalk.items():
        for name in names:
            name_to_ids[name].add(tid)
    missing_boxes = defaultdict(list)
    for pk, entries in finals.items():
        if pk in games:
            continue
        for entry in entries:
            if not entry.get("officialDate") or entry.get("completed") is not True:
                continue
            for side in ("home", "away"):
                ids = name_to_ids.get(entry.get(side+"Team"), set())
                if len(ids) == 1:
                    missing_boxes[next(iter(ids))].append((pk, entry["officialDate"]))
    rows, exclusions = [], []
    for pk in sorted(set(reconstructed) | set(schedule) | set(snapshots) | set(published_starters)):
        r, sch, game = reconstructed.get(pk, {}), schedule.get(pk, {}), games.get(pk, {})
        start = r.get("commenceTime") or sch.get("gameDate") or game.get("startAtUtc")
        if not start:
            exclusions.append({"game_id": pk, "reason": "missing_start"})
            continue
        date = r.get("slateDateEt") or str(day(start))
        if selected_date and date != selected_date:
            continue
        if (sch.get("gameType") or game.get("gameType") or "R") not in ("R", "F", "D", "L", "W"):
            exclusions.append({"game_id": pk, "reason": "not_regular_or_postseason"})
            continue
        if sch and sch.get("status", {}).get("abstractGameState") not in ("Final", "Live", "Preview"):
            exclusions.append({"game_id": pk, "reason": "unsupported_schedule_state"})
            continue
        cutoff = r.get("featureCutoffUtc") or (utc(start)-timedelta(minutes=10)).isoformat()
        if utc(cutoff) > utc(start)-timedelta(minutes=10):
            raise ValueError(f"cutoff later than T-10: {pk}")
        original = [s for s in snapshots[pk] if s.get("capturedAtUtc") and s.get("featureCutoffUtc")
                    and utc(s["capturedAtUtc"]) <= utc(start)-timedelta(minutes=10)
                    and utc(s["capturedAtUtc"]) <= utc(s["featureCutoffUtc"]) <= utc(start)-timedelta(minutes=10)
                    and utc(s["commenceTime"]) == utc(start)]
        snapshot = max(original, key=lambda s: utc(s["capturedAtUtc"])) if original else {}
        if snapshot:
            if hashlib.sha256(encode(snapshot["features"])).hexdigest() != snapshot["featureFingerprint"]:
                raise ValueError("snapshot feature fingerprint mismatch")
            cutoff = snapshot["capturedAtUtc"]
        # Snapshot fields and a later immutable KS1 starter profile are
        # independent evidence. Preserve both when both were known by T-10.
        published = published_starters.get(pk, {})
        published_problem = None
        if published and utc(published["commence_time"]) != utc(start):
            published_problem, published = "published_starter_start_mismatch", {}
        if published and published.get("date") != date:
            published_problem, published = "published_starter_date_mismatch", {}
        if published:
            cutoff = (max(utc(cutoff), utc(published["as_of"])).isoformat()
                      if snapshot else published["as_of"])
        row = {"game_id": pk, "date": date, "season": int(sch.get("season") or date[:4]),
               "commence_time": start, "as_of_timestamp": cutoff, "table_version": VERSION,
               "pregame_evidence": ("original_snapshot+versioned_ks1_t10_prediction"
                                    if snapshot and published else
                                    "original_snapshot" if snapshot else
                                    "versioned_ks1_t10_prediction" if published else "reconstructed"),
               "historical_corrections_possible": True,
               "rolling_feature_evidence": "reconstructed_prior_completed_counts",
               "park": (sch.get("venue") or game.get("venue") or {}).get("name"),
               "park_id": str((sch.get("venue") or game.get("venue") or {}).get("id") or "") or None,
               "park_run_factor": None, "park_hr_factor": None, "temp": None,
               "wind_speed": None, "wind_dir": None, "environment_status": "unavailable",
               "market_home_prob": None, "market_total": None, "market_spread": None,
               "market_probability_source_at": None, "market_archive_source": None,
               "market_archive_as_of": None, "odds_event_id": None,
               "home_score": None, "away_score": None, "home_win": None,
               "game_status": sch.get("status", {}).get("abstractGameState") or "Final" if (sch or game or r.get("label")) else "Unknown",
               "label_source": None, "final_score_status": "missing",
               "pregame_source_key": snapshot.get("source_key"),
               "pregame_version_id": None, "pregame_stored_at": None,
               "pregame_sha256": None,
               "historical_pitcher_context_mode": None,
               "pitcher_context_evidence": None,
               "lineup_bullpen_context_evidence": None,
               "historical_pitcher_context_as_of": None,
               "historical_pitcher_context_source": None,
               "reconstructed_source": json.dumps(r.get("sourceArtifact"), sort_keys=True) if r else None}
        row.update({side+"_"+feature: None for side in ("home", "away")
                    for feature in LINEUP_BULLPEN_FEATURES})
        if published:
            proof = published["source"]
            row.update(pregame_source_key=proof.get("key"),
                       pregame_version_id=proof.get("version_id"),
                       pregame_stored_at=proof.get("stored_at"),
                       pregame_sha256=proof.get("sha256"))
        frozen_team_context = published_team_context.get(pk)
        if frozen_team_context:
            current_teams = {side: team_identity(
                sch.get("teams", {}).get(side) or game.get("teams", {}).get(side))[0]
                for side in ("home", "away")
                if sch.get("teams", {}).get(side) or game.get("teams", {}).get(side)}
            context_matches = (utc(frozen_team_context["commence_time"]) == utc(start)
                               and len(current_teams) == 2
                               and current_teams == frozen_team_context["teams"])
            if not context_matches:
                exclusions.append({"game_id": pk, "reason": "published_lineup_bullpen_identity_mismatch"})
                frozen_team_context = None
            elif utc(frozen_team_context["as_of"]) > utc(cutoff):
                cutoff = frozen_team_context["as_of"]
                row["as_of_timestamp"] = cutoff
        if published_problem:
            exclusions.append({"game_id": pk, "reason": published_problem})
        missing_identity = False
        for side in ("home", "away"):
            team = sch.get("teams", {}).get(side) or game.get("teams", {}).get(side)
            identity_method = "official_game_team_id"
            if not team and r.get(side+"Team"):
                name = r[side+"Team"]
                ids = name_to_ids.get(name, set())
                if len(ids) == 1:
                    team = {"team": {"id": next(iter(ids)), "name": name}}
                    identity_method = "exact_unique_observed_name_crosswalk"
            if not team:
                missing_identity = True
                break
            tid, name = team_identity(team)
            if game and tid != team_identity(game["teams"][side])[0]:
                raise ValueError(f"official team ID conflict: {pk}")
            crosswalk[tid].add(name)
            row.update({f"{side}_id": tid, f"{side}_team": name,
                        f"{side}_identity_method": identity_method, f"{side}_identity_confidence": 1.0,
                        f"{side}_starter_id": None, f"{side}_starter_name": None,
                        f"{side}_starter_status": "missing_pregame_evidence",
                        f"{side}_actual_starter_id": None, f"{side}_actual_starter_name": None,
                        f"{side}_lineup_status": "projected", f"{side}_lineup_ids": None,
                        f"{side}_travel_km": None,
                        **{f"{side}_starter_{metric}": None for metric in (
                            "pitch_hand_left", "opponent_lhb_pct", "opponent_rhb_pct",
                            "opponent_switch_pct")}})
            observed = snapshot.get("playerWindows", {}).get("teams", {}).get(side, {})
            if observed and str(observed.get("teamId")) != tid:
                raise ValueError(f"snapshot team ID conflict: {pk}")
            if observed.get("starterId"):
                pid = str(observed["starterId"])
                players = [p for p in observed.get("players", []) if str(p["id"]) == pid]
                if len(players) != 1:
                    raise ValueError("snapshot starter lacks unique player identity")
                row[f"{side}_starter_id"], row[f"{side}_starter_name"] = pid, players[0].get("name")
                row[f"{side}_starter_status"] = "observed_pregame"
            locked = published.get("sides", {}).get(side, {})
            locked_team = published.get("teams", {}).get(side)
            if published and locked_team and str(locked_team) != tid:
                raise ValueError(f"published starter team ID conflict: {pk}")
            locked_is_latest = bool(locked.get("id") and (
                not snapshot or utc(published["as_of"]) >= utc(snapshot["capturedAtUtc"])))
            snapshot_starter_replaced = bool(
                locked_is_latest and row[f"{side}_starter_id"]
                and str(locked["id"]) != str(row[f"{side}_starter_id"]))
            if locked_is_latest:
                row[f"{side}_starter_id"] = str(locked["id"])
                row[f"{side}_starter_name"] = locked.get("name")
                row[f"{side}_starter_status"] = ("observed_versioned_t10_replacement"
                                                  if snapshot_starter_replaced
                                                  else "observed_versioned_t10")
            if observed.get("lineupConfirmed") is True and len(set(observed.get("battingOrder", []))) == 9:
                row[f"{side}_lineup_status"] = "confirmed"
                row[f"{side}_lineup_ids"] = json.dumps(observed["battingOrder"])
            schedule_at = bundle.get("schedule_observed_at")
            if (not row[f"{side}_starter_id"] and sch.get("status", {}).get("abstractGameState") == "Preview"
                    and schedule_at and utc(schedule_at) <= utc(cutoff)):
                probable = team.get("probablePitcher", {})
                if probable.get("id"):
                    row[f"{side}_starter_id"] = str(probable["id"])
                    row[f"{side}_starter_name"] = probable.get("fullName")
                    row[f"{side}_starter_status"] = "observed_probable"
            actuals = [p for p in full.get(pk, {}).get("teams", {}).get(side, {}).get("players", {}).values()
                       if p.get("stats", {}).get("pitching", {}).get("gamesStarted") == 1]
            if len(actuals) == 1:
                row[f"{side}_actual_starter_id"] = str(actuals[0]["person"]["id"])
                row[f"{side}_actual_starter_name"] = actuals[0]["person"].get("fullName")
            row.update({f"{side}_{k}": v for k, v in history.at(cutoff, tid, row[f"{side}_starter_id"]).items()})
            captured = {key: value for key, value in snapshot.get("features", {}).items()
                        if key.startswith(f"{side}_starter_") and not snapshot_starter_replaced}
            if captured:
                row.update(captured)
                row["rolling_feature_evidence"] = "immutable original snapshot starter profile"
            cutoff_day = day(cutoff)
            gaps = {key: calendar_date.fromisoformat(date)
                    for key, date in missing_boxes[tid]
                    if date[:4] == str(cutoff_day.year) and date < str(cutoff_day)}
            gaps = {key: date for key, date in gaps.items() if (cutoff_day-date).days <= 75}
            row[f"{side}_missing_history_boxes_75d"] = len(gaps)
            row[f"{side}_history_status"] = "partial_known_missing_boxes" if gaps else "complete_for_retained_finals"
            for window in (1, 3, 5):
                if any((cutoff_day-date).days <= window for date in gaps.values()):
                    for stat in ("pitches", "outs"):
                        row[f"{side}_bullpen_{stat}_{window}d"] = None
        if frozen_team_context:
            lineup_matches = all(
                not row.get(side+"_lineup_ids")
                or [str(value) for value in json.loads(row[side+"_lineup_ids"])]
                == frozen_team_context["lineups"][side]
                for side in ("home", "away"))
            if not lineup_matches:
                exclusions.append({"game_id": pk,
                                   "reason": "published_lineup_bullpen_lineup_mismatch"})
                frozen_team_context = None
            else:
                context_features = dict(frozen_team_context["features"])
                for side in ("home", "away"):
                    opposing = "away" if side == "home" else "home"
                    expected = frozen_team_context["matchup_starters"].get(side)
                    current = row.get(opposing+"_starter_id")
                    if ((str(expected) if expected is not None else None)
                            != (str(current) if current is not None else None)):
                        for window in ("7d", "30d"):
                            context_features[side+"_lineup_platoon_xwoba_"+window] = None
                            context_features[side+"_lineup_pitch_type_matchup_xwoba_"+window] = None
                row.update(context_features)
                row["lineup_bullpen_context_evidence"] = "frozen_versioned_ks1_profile"
                for side in ("home", "away"):
                    row[side+"_lineup_status"] = "confirmed"
                    row[side+"_lineup_ids"] = json.dumps(
                        [int(value) for value in frozen_team_context["lineups"][side]])
        for side in ("home", "away"):
            opposing = "away" if side == "home" else "home"
            own = snapshot.get("playerWindows", {}).get("teams", {}).get(side, {})
            other = snapshot.get("playerWindows", {}).get("teams", {}).get(opposing, {})
            starter = [p for p in own.get("players", [])
                       if str(p.get("id")) == str(row.get(f"{side}_starter_id"))]
            lineup = sorted((p for p in other.get("players", []) if p.get("lineupSlot")),
                            key=lambda p: p["lineupSlot"])
            values = starter_matchup(starter[0].get("pitchHand") if len(starter) == 1 else None,
                                     [p.get("batSide") for p in lineup] if len(lineup) == 9 else None)
            row.update({f"{side}_starter_{key}": value for key, value in values.items()})
        profile_identity_matches = all(
            not published.get("contexts")
            or not published.get("sides", {}).get(side, {}).get("id")
            or str(published["sides"][side]["id"]) == str(row.get(side+"_starter_id"))
            for side in ("home", "away"))
        if published.get("contexts") and not profile_identity_matches:
            exclusions.append({"game_id": pk, "reason": "published_profile_starter_identity_mismatch"})
        elif published.get("contexts"):
            for side in ("home", "away"):
                row.update({f"{side}_pitcher_context_{key}": value
                            for key, value in published["contexts"][side].items()})
            row["pitcher_context_evidence"] = "frozen_versioned_ks1_profile"
        context = historical_context.get(pk, {})
        context_matches = bool(
            context and utc(context.get("commence_time")) == utc(start)
            and all(context.get("teams", {}).get(side) == row.get(side+"_team")
                    for side in ("home", "away")))
        if context and not context_matches:
            exclusions.append({"game_id": pk, "reason": "historical_pitcher_context_identity_mismatch"})
        elif context_matches:
            context_applied = False
            for side in ("home", "away"):
                # A real immutable T-10 starter profile is more specific.  The
                # V8 summary accelerates only rows lacking that identity.
                if row.get(side+"_starter_id") is None:
                    row.update({f"{side}_pitcher_context_{key}": value
                                for key, value in context["sides"][side].items()})
                    context_applied = True
            if context_applied:
                row["as_of_timestamp"] = max(
                    utc(row["as_of_timestamp"]), utc(context["as_of"])).isoformat()
                row.update(historical_pitcher_context_mode=context["identity_mode"],
                           historical_pitcher_context_as_of=context["as_of"],
                           historical_pitcher_context_source=json.dumps(context["source"], sort_keys=True))
        if missing_identity:
            exclusions.append({"game_id": pk, "reason": "missing_official_team_identity"})
            continue
        labels = []
        for f in finals[pk]:
            if f.get("completed") is True:
                pair = score_pair(f.get("homeScore"), f.get("awayScore"))
                if pair:
                    labels.append((pair, f["source_key"]))
        rr = research.get(pk, {})
        pair = score_pair(rr.get("homeRuns"), rr.get("awayRuns"))
        if pair:
            labels.append((pair, "research/dataset"))
        if pk in full:
            pair = score_pair(*[full[pk]["teams"][s].get("teamStats", {}).get("batting", {}).get("runs") for s in ("home", "away")])
            if pair:
                labels.append((pair, "research/prior-games/full-box"))
        if sch.get("status", {}).get("abstractGameState") == "Final":
            pair = score_pair(*[sch["teams"][s].get("score") for s in ("home", "away")])
            if pair:
                labels.append((pair, "research/prior-games/schedule"))
        if len({pair for pair, _ in labels}) > 1:
            raise ValueError(f"conflicting final scores: {pk}")
        if labels:
            (home, away), source = labels[0]
            if home == away:
                raise ValueError(f"tied final MLB score: {pk}")
            row.update(home_score=home, away_score=away, home_win=home > away,
                       label_source=source, final_score_status="final")
        elif r.get("label", {}).get("homeWon") is not None:
            row.update(home_win=bool(r["label"]["homeWon"]), label_source="reconstructed/label")
        elif sch.get("status", {}).get("abstractGameState") in ("Live", "Preview"):
            row["final_score_status"] = "not_final_at_source_observation"
        if row["home_win"] is not None and r.get("label", {}).get("homeWon") is not None and row["home_win"] != bool(r["label"]["homeWon"]):
            raise ValueError(f"winner disagrees with stored label: {pk}")
        market = (snapshot or r).get("features", {}).get("marketHomeProbability")
        at = snapshot.get("capturedAtUtc") if snapshot else r.get("marketSourceAtUtc")
        if number(market) is not None and 0 < float(market) < 1 and at and utc(at) <= utc(cutoff):
            row["market_home_prob"], row["market_probability_source_at"] = float(market), at
        row.update(market_for(row, markets))
        conditions = snapshot.get("conditions", {}).get("features", {})
        if conditions.get("forecastTemperatureF") is not None:
            temperature = float(conditions["forecastTemperatureF"])
            if np.isfinite(temperature):
                row["temp"], row["environment_status"] = temperature, "archived_pregame_forecast_fahrenheit"
        for side in ("home", "away"):
            row[f"{side}_travel_km"] = number(conditions.get(side+"TravelKm"))
        row["lineup_status"] = "confirmed" if all(row[f"{s}_lineup_status"] == "confirmed" for s in ("home", "away")) else "projected"
        rows.append(row)
    rows.sort(key=lambda r: (r["date"], utc(r["commence_time"]), r["game_id"]))
    if not rows:
        raise ValueError("no games selected; no output will be published")
    schema, dictionary = contract(rows[0])
    table = pa.Table.from_pylist(rows, schema=schema)
    frame = table.to_pandas()
    if frame.game_id.duplicated().any() or frame[["game_id", "home_id", "away_id", "as_of_timestamp"]].isna().any().any():
        raise ValueError("required key contract failed")
    sample = frame.iloc[np.linspace(0, len(frame)-1, min(20, len(frame)), dtype=int)]
    report = {"system": "KS1", "phase": 1, "table_version": VERSION, "rows": len(frame),
              "date_range": [frame.date.min(), frame.date.max()], "seasons": [],
              "provider_calls": 0, "new_archive_download": False, "models_trained": 0,
              "source_counts": {"reconstructed_games": len(reconstructed), "recent_schedule_games": len(schedule),
                                "compact_games": len(compact), "full_box_games": len(full),
                                "final_archive_games": sum(bool(values) for values in finals.values())},
              "exclusions": exclusions, "optional_reads": bundle.get("optional_reads", []),
              "coverage": coverage(frame),
              "gaps": {c: int(frame[c].isna().sum()) for c in frame if frame[c].isna().any()},
              "bbs_gap_decision": "No provider call: scores use existing official-finals; current BBS documentation reports stored lineups unpopulated and cannot establish historical pregame starter observations.",
              "source_receipts": bundle.get("source_receipts", [])}
    for season, group in frame.groupby("season"):
        report["seasons"].append({"season": int(season), "rows": len(group), **coverage(group)})
    crosswalk_rows = [{"provider": "mlb", "team_id": tid, "aliases": sorted(names),
                       "method": "observed_official_id", "confidence": 1.0}
                      for tid, names in sorted(crosswalk.items())]
    players = [{"provider": "mlb", "player_id": pid, "names": sorted(names)} for pid, names in sorted(player_names.items())]
    return table, report, dictionary, sample, {"teams": crosswalk_rows, "players": players}


def coverage(frame):
    both = frame.home_starter_id.notna() & frame.away_starter_id.notna()
    context = (frame.home_pitcher_context_quality.notna()
               & frame.away_pitcher_context_quality.notna())
    actual = frame.home_actual_starter_id.notna() & frame.away_actual_starter_id.notna()
    final = frame.home_score.notna() & frame.away_score.notna()
    return {"with_both_pregame_starters": int(both.sum()), "pct_with_both_pregame_starters": round(100*both.mean(), 3),
            "with_both_pitcher_contexts": int(context.sum()),
            "pct_with_both_pitcher_contexts": round(100*context.mean(), 3),
            "with_strict_prior_pitcher_projection": int(
                (frame.historical_pitcher_context_mode == "strict_prior_projection").sum()),
            "with_both_actual_starters": int(actual.sum()), "pct_with_both_actual_starters": round(100*actual.mean(), 3),
            "with_final_score": int(final.sum()), "pct_with_final_score": round(100*final.mean(), 3)}


def contract(example):
    """Every output column gets an explicit storage type, role, source and meaning."""
    fields, dictionary = [], []
    for column in sorted(example):
        dtype, role, source, meaning = pa.string(), "audit", "KS1 derivation", column.replace("_", " ")
        if column in ("home_win", "historical_corrections_possible"):
            dtype = pa.bool_()
        elif column in ("season", "home_score", "away_score"):
            dtype = pa.int64()
        starter_metric = "_starter_" in column and not any(column.endswith(suffix) for suffix in (
            "_starter_id", "_starter_name", "_starter_status", "_actual_starter_id", "_actual_starter_name"))
        context_intermediate = any(column.startswith(side+prefix) for side in ("home", "away")
                                   for prefix in ("_starter_context_",
                                                  "_starter_expected_innings_last5"))
        pitcher_context_metric = (column.startswith("home_pitcher_context_")
                                  or column.startswith("away_pitcher_context_"))
        lineup_bullpen_metric = any(
            column == side+"_"+feature for side in ("home", "away")
            for feature in LINEUP_BULLPEN_FEATURES)
        if column == "lineup_bullpen_context_evidence":
            source = "checksum-bound KS1-lineup-bullpen-profile-v1 audit metadata"
        elif starter_metric or pitcher_context_metric or lineup_bullpen_metric or any(t in column for t in ("_offense_", "_team_starter_", "_bullpen_", "_rest_days", "_history_games")):
            dtype, role, source = pa.float64(), "feature", "strictly earlier completed compact/full game boxes"
            meaning += "; calendar-day windows; same-day excluded; current-season empirical prior; OPS/ISO 100 PA/AB, K-BB 100 BF, WHIP 75 outs shrinkage; *_games/*_pa/*_bf/*_appearances are observed counts"
            if lineup_bullpen_metric:
                direct_lineup = any(column.endswith("_"+name) for name in (
                    "lineup_quality_ops", "lineup_quality_obp", "lineup_quality_slg",
                    "lineup_top4_ops", "lineup_2_5_ops", "lineup_observed_batters",
                    "lineup_total_pa"))
                statcast_lineup = "_lineup_" in column and any(token in column for token in (
                    "_woba_", "_xwoba_", "_barrel_pct_", "_hard_hit_pct_",
                    "_avg_exit_velocity_", "_contact_pct_", "_swstr_pct_", "_csw_pct_",
                    "_platoon_xwoba_", "_pitch_type_matchup_xwoba_"))
                source = ("immutable pre-T10 MLB Stats API lineup season-batting observation"
                          if direct_lineup else
                          "retained Baseball Savant pitch rows bound to strictly earlier completed official games"
                          if statcast_lineup else
                          "strictly earlier completed official MLB game boxes"
                          if "_lineup_" in column else
                          "immutable pre-T10 MLB roster plus strictly earlier official boxes/retained Statcast")
                meaning += "; admitted only from checksum-bound KS1-lineup-bullpen-profile-v1"
            statcast = any(token in column for token in (
                "_complete_", "_pitches_", "_hard_hit_pct_", "_barrel_pct_", "_avg_ev_allowed_",
                "_xwoba", "_swstr_pct_", "_csw_pct_", "_velocity_", "_spin_",
                "_horizontal_break_in_", "_vertical_break_in_", "_extension_", "_fly_balls_",
                "_mix_pct_", "_whiff_pct_", "_whiff_per_pitch_pct_"))
            unavailable = any(token in column for token in (
                "_xera_", "_siera_", "_stuff_plus_", "_location_plus_",
                "_pitching_plus_", "_active_spin_pct_"))
            matchup = any(column.endswith("_"+metric) for metric in (
                "pitch_hand_left", "opponent_lhb_pct", "opponent_rhb_pct",
                "opponent_switch_pct"))
            if matchup:
                source = "original pregame playerWindows lineup and handedness observation"
                meaning += "; null unless all nine lineup bat sides and probable-starter throwing hand are verified"
            elif pitcher_context_metric:
                source = "verified V8 point-in-time pitcher summary or same-contract observed KS1 starter profile"
                meaning += "; historical projections remain explicitly marked and are never counted as confirmed starter identity"
            elif context_intermediate:
                role = "audit"
                source = "strictly earlier official game logs; intermediate for frozen pitcher-context derivation"
                meaning += "; persisted for profile integrity but never directly eligible for model training"
            elif unavailable:
                source = "not present in admitted exact-window sources; stored null and excluded from training"
                meaning += "; unavailable is not zero and no proprietary metric is approximated under this name"
            elif "_xfip_" in column:
                source = "official completed game logs plus retained Baseball Savant fly balls and point-in-time league HR/FB"
            elif statcast:
                source = "retained Baseball Savant pitch rows bound to strictly earlier completed official games"
            if column.endswith("_prior_year"):
                meaning += "; previous-season observation available before the cutoff"
            if column.endswith("_talent"):
                lineup_talent = lineup_bullpen_metric and "_lineup_" in column
                source = ("30-day and previous-season completed official MLB game boxes"
                          if lineup_talent else
                          "30-day and previous-season retained Baseball Savant pitch rows")
                meaning += ("; plate-appearance weighted with previous-season weight capped at 300 PA"
                            if lineup_talent else
                            "; pitch-count weighted with previous-season weight capped at 300 pitches")
        elif column.endswith("_missing_history_boxes_75d"):
            dtype, role, source = pa.int64(), "audit", "retained official finals lacking a compact or full box"
            meaning = "Known earlier same-season final games with missing boxes in 75 calendar days; partial history flag, never interpreted as zero workload."
        elif column.endswith("_identity_confidence"):
            dtype, role, source = pa.float64(), "audit", "official team ID or exact unique alias in observed official-ID crosswalk"
            meaning = "1.0 for deterministic official-ID or exact unique observed-name match; ambiguous names excluded, no fuzzy matches."
        elif column in ("park_run_factor", "park_hr_factor", "temp", "wind_speed", "market_home_prob", "market_total", "market_spread") or column.endswith("_travel_km"):
            dtype, role = pa.float64(), "feature"
            source = "archived pregame no-vig probability / retained odds books" if column.startswith("market_") else "unavailable in admitted pregame sources; null"
        elif column in ("home_score", "away_score", "home_win", "label_source", "final_score_status") or "_actual_starter_" in column:
            role, source = "label_only", "official-finals archive; research scores; final full boxes; final schedule"
        elif "_starter_id" in column or "_starter_name" in column or "_lineup_" in column:
            role, source = "pregame_identity", "original playerWindows snapshot or pre-cutoff Preview schedule; never final boxes"
        elif column in ("home_id", "away_id", "home_team", "away_team", "game_id", "date", "season", "commence_time", "park", "park_id"):
            role, source = "identity", "official MLB schedule / compact and full boxes / reconstructed game contract"
        if column == "market_home_prob":
            meaning = "Home no-vig probability [0,1]; average home implied/(home+away implied) across paired books, or existing reconstructed/snapshot probability."
        if column == "temp" or column.endswith("_travel_km"):
            source = "original snapshot conditions.features.forecastTemperatureF / homeTravelKm / awayTravelKm"
            meaning += "; temperature Fahrenheit; travel kilometres; null when unavailable"
        if column == "market_spread":
            meaning = "Median home run-line handicap; paired opposite away handicap required."
        if column == "as_of_timestamp":
            meaning = "Pregame feature cutoff UTC, at or before T-10. Reconstructed records are not original observations. Labels may be observed later."
        if column.endswith("actual_starter_id") or column.endswith("actual_starter_name"):
            meaning += "; identified by gamesStarted=1 in final box; excluded from model features"
        fields.append(pa.field(column, dtype, nullable=True))
        dictionary.append({"column": column, "dtype": str(dtype), "role": role, "source": source, "description": meaning})
    return pa.schema(fields), dictionary
