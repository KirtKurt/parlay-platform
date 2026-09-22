"""Read-only labels for retained KS1 reports; never prediction authority.

Keep observation ownership separate from a signal's direction and a SHAP sign.
This view does not establish publication/readback or immutable T-10 lock proof.
"""
from datetime import datetime, timedelta
import json
import math
import re


BOOKS = ("draftkings", "fanduel", "betmgm", "williamhill_us", "fanatics", "betrivers")


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def object_value(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def identity(row, side, profile):
    expected, observed = row.get(side + "_starter_id"), profile.get("starter_id")
    valid = (re.fullmatch(r"[1-9][0-9]*", str(expected)) is not None
             and re.fullmatch(r"[1-9][0-9]*", str(observed)) is not None)
    status = ("verified" if valid and str(expected) == str(observed)
              else "mismatch" if valid else "unavailable")
    return {"side": side, "team": row.get(side + "_team"),
            "starter_id": expected, "starter_name": row.get(side + "_starter_name"),
            "profile_starter_id": observed, "identity_status": status}


def starter_windows(row, side, profile):
    subject = identity(row, side, profile)
    metrics = profile.get("metrics") or {}
    windows = {}
    for window in ("7d", "15d", "30d"):
        values = {key: number(metrics.get(key + "_" + window)) for key in
                  ("era", "outs", "appearances", "bf", "fip", "xfip", "whip", "k_bb_pct", "xwoba")}
        if subject["identity_status"] != "verified":
            state = "IDENTITY_UNVERIFIED"
            values = dict.fromkeys(values)
        elif values["appearances"] == 0:
            state = "NO_APPEARANCES"
        elif values["appearances"] is not None and values["appearances"] > 0 and values["outs"] == 0:
            state = "APPEARANCE_WITH_ZERO_OUTS"
        elif values["era"] is not None and values["outs"] is not None:
            state = "OBSERVED_RESULTS"
        else:
            state = "UNAVAILABLE"
        windows[window] = {
            "state": state, "era": values["era"], "outs": values["outs"],
            "appearances": values["appearances"], "batters_faced": values["bf"],
            "fip": values["fip"], "xfip": values["xfip"], "xwoba": values["xwoba"],
            # These serving fields are league-prior estimates, even with no appearances.
            "whip_shrunk_model_estimate": values["whip"],
            "k_minus_bb_pct_shrunk_model_estimate": values["k_bb_pct"],
            "raw_whip": None, "raw_k_minus_bb_pct": None,
            "earned_runs": None,
            "gaps": ["raw_whip_not_retained", "raw_k_minus_bb_pct_not_retained",
                     "earned_runs_not_retained_do_not_reverse_engineer_from_rounded_era"],
        }
    return {"subject": subject, "windows": windows,
            "consumption": "CONSUMPTION UNKNOWN; inspect per-pick attribution",
            "metric_semantics": "WHIP and K-BB% are shrunk estimates, not raw rolling rates"}


def contributions(row, selected):
    proof = object_value(row.get("signal_contributions_json"))
    verified = proof.get("additivity_verified") is True and proof.get("scale") == "raw_log_odds_SHAP"
    result = []
    for entry in proof.get("top_features", []):
        name, score = entry.get("feature", ""), number(entry.get("score"))
        side = next((s for s in ("home", "away") if name.startswith(s + "_")), None)
        population = ("team_starter_history" if "_team_starter_" in name
                      else "individual_starter" if "_starter_" in name
                      else "team_offense" if "_offense_" in name
                      else "team_bullpen_workload" if re.search(r"_bullpen_(pitches|outs)_", name)
                      else "market" if name.startswith("market_") else "other_context")
        oriented = score * (1 if selected == "home" else -1) if score is not None else None
        observed = verified and score is not None and score != 0
        result.append({"feature": name, "subject_side": side,
                       "subject_team": row.get(side + "_team") if side else None,
                       "subject_population": population,
                       "starter_id": row.get(side + "_starter_id") if side and population == "individual_starter" else None,
                       "starter_name": row.get(side + "_starter_name") if side and population == "individual_starter" else None,
                       "score_toward_home": score, "score_toward_selected": oriented,
                       "direction": ("SUPPORT" if oriented > 0 else "OPPOSITION") if observed else "UNVERIFIED",
                       "consumption": "ACTUALLY CONSUMED BY ACTIVE MODEL" if observed else "CONSUMPTION UNKNOWN",
                       "interpretation": "SHAP contribution, not causal effect"})
    return result


def selected_moneylines(row, selected, odds_rows):
    """Match exact retained event IDs, never a team-pair-only doubleheader join."""
    missing = {book: {"status": "UNAVAILABLE", "price": None} for book in BOOKS}
    event_id = row.get("odds_event_id")
    matches = [entry for entry in odds_rows if event_id and str(entry.get("event_id")) == str(event_id)]
    if len(matches) != 1:
        return {"status": "EVENT_MISSING_OR_AMBIGUOUS", "books": missing}
    entry = matches[0]
    event = object_value(entry.get("payload_json"))
    if (str(event.get("id")) != str(event_id)
            or any(event.get(s + "_team") != row.get(s + "_team") for s in ("home", "away"))):
        return {"status": "EVENT_IDENTITY_MISMATCH", "books": missing}
    row_time, start = instant(row.get("as_of")), instant(row.get("commence_time"))
    event_start, receipt = instant(event.get("commence_time")), instant(entry.get("as_of"))
    if not all((row_time, start, event_start, receipt)) or abs(event_start - start) > timedelta(minutes=5):
        return {"status": "EVENT_TIME_UNVERIFIED", "books": missing}
    latest = min(row_time, start - timedelta(minutes=10))
    if receipt > latest:
        return {"status": "CACHE_AFTER_PREDICTION_OR_CUTOFF", "books": missing,
                "cache_as_of": entry.get("as_of"), "latest_eligible": latest.isoformat()}
    for book in BOOKS:
        books = [b for b in event.get("bookmakers", []) if b.get("key") == book]
        if len(books) != 1:
            continue
        markets = [m for m in books[0].get("markets", []) if m.get("key") == "h2h"]
        if len(markets) != 1:
            continue
        market = markets[0]
        timestamp = market.get("last_update") or books[0].get("last_update")
        quote_time = instant(timestamp)
        outcomes = [o for o in market.get("outcomes", []) if o.get("name") == row.get(selected + "_team")]
        price = number(outcomes[0].get("price")) if len(outcomes) == 1 else None
        if quote_time and quote_time <= receipt and price is not None and abs(price) >= 100:
            missing[book] = {"status": "RETAINED_PRE_PREDICTION_QUOTE", "price": price,
                             "odds_timestamp": timestamp, "cache_as_of": entry.get("as_of")}
    return {"status": "EXACT_EVENT_MATCH", "event_id": event_id, "books": missing}


def report_evidence(row, detector, odds_rows=()):
    p_home = number(row.get("p_home"))
    if p_home is None or not 0 <= p_home <= 1:
        raise ValueError("invalid KS1 p_home for reporting")
    selected = "home" if p_home >= .5 else "away"
    p_selected = p_home if selected == "home" else 1 - p_home
    sides = object_value(row.get("starter_profile_json")).get("sides", {})
    starters = {side: starter_windows(row, side, sides.get(side) or {}) for side in ("home", "away")}
    flags = []
    for flag in detector.get("flags", []):
        code = flag.get("code", "")
        subject_side = (selected if code.startswith("SELECTED_STARTER_") else
                        ("away" if selected == "home" else "home") if code.startswith("OPPONENT_STARTER_") else None)
        subject = starters[subject_side]["subject"] if subject_side else None
        flags.append({**flag, "side_role": "signal_direction_not_observed_pitcher_side",
                      "observation_subject": subject,
                      "usable_as_named_pitcher_fact": subject is not None and subject["identity_status"] == "verified",
                      "consumption": "DIAGNOSTIC ONLY / NOT LEARNED"})
    return {"contract": "KS1-reporting-evidence-v1", "date": row.get("date"),
            "game_id": str(row.get("game_id")), "model_version": row.get("model_version"),
            "prediction_as_of": row.get("as_of"), "selected_side": selected,
            "selected_team": row.get(selected + "_team"), "win_probability": p_selected,
            "loss_probability": 1 - p_selected, "calibration_method": row.get("calibration_method"),
            "starters": starters, "contributions": contributions(row, selected), "flags": flags,
            "moneylines": selected_moneylines(row, selected, odds_rows),
            "authority": "reporting_only; publication/readback and immutable lock proof required separately"}
