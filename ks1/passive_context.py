"""Point-in-time lineup and bullpen observations admitted into KS1.

This module has no provider client.  It validates the immutable observation
written by the MLB fundamentals job and turns only supported values into model
features.  Missing observations remain missing; roster membership is never an
availability claim.
"""
import hashlib
import json
import math
import re
from datetime import timedelta
from decimal import Decimal

from ks1.features import utc
from ks1.inventory import encode

CONTRACT = "KS1-lineup-bullpen-profile-v1"
TEAM_CONTEXT_VERSION = "MLB-STATSAPI-TEAM-CONTEXT-v1-prelock-observations"
BATTING_VERSION = "MLB-LINEUP-SEASON-BATTING-v1-passive-observations"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SLOT_WEIGHTS = (1.00, .98, .96, .94, .92, .90, .88, .86, .84)
LINEUP_FEATURES = ("lineup_quality_ops", "lineup_quality_obp", "lineup_quality_slg",
                   "lineup_top4_ops", "lineup_2_5_ops", "lineup_observed_batters",
                   "lineup_total_pa") + tuple(
    f"lineup_{metric}_{window}"
    for window in ("7d", "30d")
    for metric in ("ops", "obp", "slg", "iso", "k_pct", "bb_pct", "k_bb_pct",
                   "woba", "xwoba", "barrel_pct", "hard_hit_pct", "avg_exit_velocity",
                   "contact_pct", "swstr_pct", "csw_pct", "platoon_xwoba",
                   "pitch_type_matchup_xwoba")) + tuple(
    f"lineup_{metric}_{label}"
    for label in ("prior_year", "talent")
    for metric in ("ops", "obp", "slg", "iso", "k_pct", "bb_pct", "k_bb_pct"))
BULLPEN_FEATURES = tuple(
    f"bullpen_context_{metric}_{window}d"
    for window in (7, 15, 30)
    for metric in ("era", "whip", "ra9", "wins", "losses", "fip", "k_pct",
                   "bb_pct", "k_bb_pct", "appearances", "swstr_pct", "csw_pct",
                   "xwoba", "barrel_pct", "hard_hit_pct", "avg_ev_allowed", "velocity")
) + ("bullpen_context_roster_count", "bullpen_context_available_count",
     "bullpen_context_limited_count", "bullpen_context_likely_unavailable_count",
     "bullpen_context_unknown_count", "bullpen_context_fatigue_score",
     "bullpen_context_depth", "bullpen_context_high_leverage_quality",
     "bullpen_context_platoon_coverage", "bullpen_context_available_quality",
     "bullpen_context_quality", "bullpen_context_command",
     "bullpen_context_expected_innings", "bullpen_context_early_exit_quality")
MODEL_FEATURES = LINEUP_FEATURES + BULLPEN_FEATURES


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _positive_id(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return str(number) if number > 0 and str(value) in (str(number), str(Decimal(number))) else None


def _finite(value, low, high):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def _provenance(value, game_id, start, cutoff):
    if not isinstance(value, dict):
        raise ValueError("passive context provenance missing")
    endpoint = f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
    if (value.get("provider") != "MLB Stats API"
            or value.get("dataset") != TEAM_CONTEXT_VERSION
            or value.get("endpoint") != endpoint
            or not HEX64.fullmatch(str(value.get("payloadFingerprint") or ""))):
        raise ValueError("passive context provenance invalid")
    retrieved, effective = utc(value["retrievedAtUtc"]), utc(value["sourceEffectiveAtUtc"])
    if effective > retrieved or retrieved > cutoff or effective > cutoff or retrieved >= start:
        raise ValueError("passive context is not point-in-time")
    return {"provider": value["provider"], "dataset": value["dataset"],
            "endpoint": value["endpoint"], "retrieved_at": retrieved.isoformat(),
            "effective_at": effective.isoformat(),
            "payload_sha256": value["payloadFingerprint"]}


def _lineup(block, side):
    order = block.get(side+"_batting_order")
    samples = block.get(side+"_lineup_season_batting")
    if block.get(side+"_lineup_confirmed") is not True:
        raise ValueError(side+" lineup is not confirmed")
    ids = [_positive_id(value) for value in order] if isinstance(order, list) else []
    if len(ids) != 9 or None in ids or len(set(ids)) != 9:
        raise ValueError(side+" batting order invalid")
    if not isinstance(samples, list) or len(samples) != 9:
        raise ValueError(side+" batter samples invalid")
    normalized = []
    for slot, (identity, sample) in enumerate(zip(ids, samples), 1):
        if not isinstance(sample, dict) or _positive_id(sample.get("playerId")) != identity:
            raise ValueError(side+" batter identity mismatch")
        if _positive_id(sample.get("battingSlot")) != str(slot):
            raise ValueError(side+" batter slot mismatch")
        pa = _finite(sample.get("plateAppearances"), 0, 1000)
        metrics = {"ops": _finite(sample.get("ops"), 0, 5),
                   "obp": _finite(sample.get("obp"), 0, 1),
                   "slg": _finite(sample.get("slg"), 0, 4)}
        observed = sum(value is not None for value in metrics.values())
        if _finite(sample.get("rateObservationCount"), 0, 3) != observed:
            raise ValueError(side+" batter observation count mismatch")
        status = "OBSERVED" if pa and pa > 0 else "NO_PLATE_APPEARANCES" if pa == 0 else "SAMPLE_UNAVAILABLE"
        if sample.get("sampleStatus") != status or (not pa and observed):
            raise ValueError(side+" batter sample status invalid")
        normalized.append({"player_id": identity, "slot": slot,
                           "plate_appearances": pa, **metrics, "status": status})
    return ids, normalized


def _weighted(samples, metric, slots=range(1, 10)):
    selected = [s for s in samples if s["slot"] in slots]
    if not selected or any(s[metric] is None for s in selected):
        return None
    values = [(SLOT_WEIGHTS[s["slot"]-1], s[metric]) for s in selected]
    return sum(weight*value for weight, value in values)/sum(weight for weight, _ in values)


def _lineup_features(samples):
    return {
        "lineup_quality_ops": _weighted(samples, "ops"),
        "lineup_quality_obp": _weighted(samples, "obp"),
        "lineup_quality_slg": _weighted(samples, "slg"),
        "lineup_top4_ops": _weighted(samples, "ops", range(1, 5)),
        "lineup_2_5_ops": _weighted(samples, "ops", range(2, 6)),
        "lineup_observed_batters": float(sum(s["status"] == "OBSERVED" for s in samples)),
        "lineup_total_pa": (sum(s["plate_appearances"] for s in samples)
                            if all(s["plate_appearances"] is not None for s in samples)
                            else None),
    }


def build_profile(stored, game, row, as_of, history, history_as_of=None,
                  statcast_as_of=None, history_coverage=None):
    """Validate one exact persisted observation and bind it to a KS1 game."""
    raw = stored.get("data", stored) if isinstance(stored, dict) else {}
    game_id, start, cutoff = str(game["gamePk"]), utc(game["gameDate"]), utc(game["gameDate"])-timedelta(minutes=10)
    observed = utc(as_of)
    if history_coverage is not None and not all(history_coverage.get(key) is True for key in (
            "7d", "30d", "last3", "current_season_context", "prior_year", "statcast_30d")):
        raise ValueError("lineup bullpen history coverage incomplete")
    if any(value is not None and utc(value) > observed
           for value in (history_as_of, statcast_as_of)):
        raise ValueError("historical source observed after profile")
    if str(raw.get("officialGamePk") or raw.get("official_game_pk") or "") != game_id:
        raise ValueError("passive context game identity mismatch")
    if utc(raw.get("commenceTime") or raw.get("commence_time")) != start:
        raise ValueError("passive context game start mismatch")
    context = raw.get("passiveTeamContext")
    if not isinstance(context, dict):
        raise ValueError("passive context missing")
    lineup_block, bullpen_block = context.get("confirmed_lineups"), context.get("bullpen_fatigue")
    if not isinstance(lineup_block, dict) or not isinstance(bullpen_block, dict):
        raise ValueError("passive lineup or bullpen block missing")
    if (lineup_block.get("game_pk") != int(game_id)
            or lineup_block.get("lineupSeasonBattingVersion") != BATTING_VERSION):
        raise ValueError("passive lineup contract mismatch")
    lineup_source = _provenance(lineup_block.get("sourceProvenance"), game_id, start, cutoff)
    bullpen_source = _provenance(bullpen_block.get("bullpenRosterSourceProvenance"), game_id, start, cutoff)
    if observed > cutoff:
        raise ValueError("profile cannot be created after T-10")
    sides = {}
    features = {}
    for side in ("home", "away"):
        expected_team = _positive_id(row.get(side+"_id"))
        bullpen_team = _positive_id(bullpen_block.get(side+"_team_id"))
        if (expected_team is None
                or _positive_id(lineup_block.get(side+"_team_id")) != expected_team
                or (bullpen_team is not None and bullpen_team != expected_team)):
            raise ValueError(side+" passive context team identity mismatch")
        ids, samples = _lineup(lineup_block, side)
        live_ids = json.loads(row[side+"_lineup_ids"]) if row.get(side+"_lineup_ids") else None
        if live_ids is not None and [str(value) for value in live_ids] != ids:
            raise ValueError(side+" persisted/live lineup mismatch")
        roster = bullpen_block.get(side+"_bullpen_roster_player_ids")
        roster_ids = [_positive_id(value) for value in roster] if isinstance(roster, list) else []
        if not roster_ids or None in roster_ids or len(set(roster_ids)) != len(roster_ids):
            raise ValueError(side+" bullpen roster invalid")
        if any(bullpen_block.get(side+name) is not None for name in
               ("_available_relievers", "_unavailable_relievers")):
            raise ValueError("roster observation must not claim availability")
        lineup_values = _lineup_features(samples)
        opposing = "away" if side == "home" else "home"
        batter_profiles, batter_features = (history.lineup_batters_at(
            as_of, ids, row.get(opposing+"_starter_id"),
            row.get("_"+opposing+"_starter_pitch_hand"))
            if hasattr(history, "lineup_batters_at") else ([], {}))
        lineup_values.update(batter_features)
        bullpen_values = dict(history.bullpen_roster_at(as_of, row[side+"_id"], roster_ids))
        reliever_profiles = bullpen_values.pop("_reliever_profiles", [])
        features.update({side+"_"+key: value for key, value in {**lineup_values, **bullpen_values}.items()})
        sides[side] = {"team_id": row[side+"_id"], "lineup_ids": ids,
                       "opposing_starter_id": row.get(opposing+"_starter_id"),
                       "opposing_starter_pitch_hand": row.get("_"+opposing+"_starter_pitch_hand"),
                       "batter_samples": samples, "batter_history": batter_profiles,
                       "bullpen_roster_ids": roster_ids, "reliever_history": reliever_profiles,
                       "availability_status": "UNKNOWN_ROSTER_ONLY",
                       "features": {**lineup_values, **bullpen_values}}
    home_people = set(sides["home"]["lineup_ids"]+sides["home"]["bullpen_roster_ids"])
    away_people = set(sides["away"]["lineup_ids"]+sides["away"]["bullpen_roster_ids"])
    if (len(set(sides["home"]["lineup_ids"]+sides["away"]["lineup_ids"])) != 18
            or home_people & away_people):
        raise ValueError("passive context cross-team identity overlap")
    if bullpen_block.get("bullpenRosterObservationStatus") != "OBSERVED_ROSTER_ONLY":
        raise ValueError("bullpen roster status invalid")
    profile = {"contract": CONTRACT, "as_of": observed.isoformat(),
               "history_as_of": history_as_of,
               "statcast_as_of": statcast_as_of,
               "game_id": game_id, "commence_time": start.isoformat(),
               "source_roles": {"lineup_and_season_batting": lineup_source,
                                "bullpen_roster_only": bullpen_source,
                                "batter_and_bullpen_results": {
                                    "provider": "MLB Stats API retained completed game logs",
                                    "as_of": history_as_of},
                                "contact_plate_discipline_and_pitch_arsenal": {
                                    "provider": "Baseball Savant retained Statcast pitch rows",
                                    "as_of": statcast_as_of},
                                "market_context": {"provider": "The Odds API", "role": "markets only"},
                                "BBD": {"role": "fixture crosscheck only", "player_stats_claimed": False}},
               "coverage_status": "SUPPORTED_V1_COMPLETE", "sides": sides,
               "unavailable_fields": ["batter_expected_lineup_availability",
                                      "confirmed_lineup_change_from_projection",
                                      "bullpen_xERA", "bullpen_xFIP", "bullpen_SIERA",
                                      "bullpen_Stuff+", "bullpen_Location+", "bullpen_Pitching+",
                                      "bullpen_active_spin", "bullpen_leverage_role",
                                      "bullpen_pitch_mix", "bullpen_platoon_splits"]}
    semantic = {key: value for key, value in profile.items() if key != "as_of"}
    profile["semantic_sha256"] = hashlib.sha256(encode(semantic)).hexdigest()
    profile["sha256"] = hashlib.sha256(encode(profile)).hexdigest()
    return profile, features


def read_date(table, target_date):
    """Consistently read exact-date observations, rejecting duplicates."""
    from boto3.dynamodb.conditions import Key
    rows, cursor = [], None
    while True:
        args = {"KeyConditionExpression": Key("PK").eq("GAME_WINNERS#mlb#"+target_date)
                & Key("SK").begins_with("GAME#"), "ConsistentRead": True}
        if cursor:
            args["ExclusiveStartKey"] = cursor
        page = table.query(**args); rows.extend(page.get("Items") or [])
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            break
    result = {}
    for stored in rows:
        raw = _plain(stored.get("data", stored))
        game_id = str(raw.get("officialGamePk") or raw.get("official_game_pk") or "")
        if not game_id or game_id in result:
            raise ValueError("missing or duplicate persisted game identity")
        result[game_id] = _plain(stored)
    return result


def frozen_profile_features(row):
    """Recover only checksum-bound values from an immutable KS1 row."""
    raw = row.get("lineup_bullpen_profile_json")
    if not raw:
        return None
    try:
        profile = json.loads(raw)
    except (TypeError, ValueError):
        return None
    claimed = profile.pop("sha256", None)
    semantic_claimed = profile.pop("semantic_sha256", None)
    semantic = {key: value for key, value in profile.items() if key != "as_of"}
    valid = (row.get("lineup_bullpen_profile_contract") == CONTRACT
             and profile.get("contract") == CONTRACT
             and row.get("lineup_bullpen_profile_sha256") == claimed
             and row.get("lineup_bullpen_profile_semantic_sha256") == semantic_claimed
             and hashlib.sha256(encode(semantic)).hexdigest() == semantic_claimed
             and hashlib.sha256(encode({**profile, "semantic_sha256": semantic_claimed})).hexdigest() == claimed
             and str(profile.get("game_id")) == str(row.get("game_id"))
             and utc(profile["as_of"]) == utc(row["as_of"])
             and (not profile.get("history_as_of")
                  or utc(profile["history_as_of"]) <= utc(profile["as_of"]))
             and (not profile.get("statcast_as_of")
                  or utc(profile["statcast_as_of"]) <= utc(profile["as_of"]))
             and utc(profile["as_of"]) <= utc(row["commence_time"])-timedelta(minutes=10))
    if not valid:
        return None
    features = {}
    for side in ("home", "away"):
        source = (profile.get("sides") or {}).get(side) or {}
        if str(source.get("team_id")) != str(row.get(side+"_id")):
            return None
        if "opposing_starter_id" not in source:
            return None
        for key, value in (source.get("features") or {}).items():
            if key != "bullpen_context_availability_method":
                features[side+"_"+key] = value
    return {"as_of": profile["as_of"], "commence_time": profile["commence_time"],
            "teams": {side: str(profile["sides"][side]["team_id"])
                      for side in ("home", "away")},
            "lineups": {side: list(profile["sides"][side]["lineup_ids"])
                        for side in ("home", "away")},
            "matchup_starters": {side: profile["sides"][side].get("opposing_starter_id")
                                 for side in ("home", "away")},
            "features": features,
            "coverage_status": profile.get("coverage_status")}


def published_profile_index(entries):
    result = {}
    for entry in entries or []:
        row = entry.get("row", {})
        recovered = frozen_profile_features(row)
        if recovered is None:
            continue
        game_id = str(row.get("game_id") or "")
        previous = result.get(game_id)
        if previous is None or utc(recovered["as_of"]) >= utc(previous["as_of"]):
            result[game_id] = recovered
    return result
