from __future__ import annotations

from typing import Any, Dict, List

VERSION = "MLB-SIGNAL-POLICY-v1.7-reversal-instability-gate"
REQUIRED_MINUTES_BEFORE_GAME = 45
DAILY_SLATE_DISPLAY_RULE = "show_one_required_winner_prediction_for_every_game_45_minutes_before_first_game_of_day"


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return d if v is None or v == "" else float(v)
    except Exception:
        return d


def _i(v: Any, d: int = 0) -> int:
    try:
        return int(round(float(v)))
    except Exception:
        return d


def _side(row: Dict[str, Any]) -> str:
    s = str(row.get("predictedSide") or "").lower()
    return s if s in {"home", "away"} else "home"


def _sig(row: Dict[str, Any]) -> Dict[str, Any]:
    val = row.get("homeSignal") if _side(row) == "home" else row.get("awaySignal")
    return val if isinstance(val, dict) else {}


def _opp(row: Dict[str, Any]) -> Dict[str, Any]:
    val = row.get("awaySignal") if _side(row) == "home" else row.get("homeSignal")
    return val if isinstance(val, dict) else {}


def _tags(row: Dict[str, Any], sig: Dict[str, Any]) -> set[str]:
    return set([str(x) for x in (row.get("tags") or [])] + [str(x) for x in (sig.get("tags") or [])])


def _temporal_horizon(sig: Dict[str, Any], name: str) -> Dict[str, Any]:
    temporal = sig.get("temporalFeatures") or {}
    if not isinstance(temporal, dict):
        return {}
    horizons = temporal.get("horizons") or {}
    if not isinstance(horizons, dict):
        return {}
    horizon = horizons.get(name) or {}
    return horizon if isinstance(horizon, dict) else {}


def _signal_risk_gate_reasons(row: Dict[str, Any]) -> List[str]:
    sig = _sig(row)
    tags = _tags(row, sig)
    prob = _f(sig.get("marketConsensusProbability"), _f(sig.get("probLatest"), 0.5))
    delta = _f(sig.get("delta"), 0.0)
    rev = _i(sig.get("reversalCount"), 0)
    confirmations = {"BOOK_AGREEMENT", "STEAM", "RUN_LINE_CONFIRMATION"}
    independently_confirmed = bool(tags & confirmations)

    h15 = _temporal_horizon(sig, "15m")
    h60 = _temporal_horizon(sig, "60m")
    h180 = _temporal_horizon(sig, "180m")
    hfull = _temporal_horizon(sig, "full")
    rev60 = _i(h60.get("reversalCount"), 0)
    rev180 = _i(h180.get("reversalCount"), 0)
    revfull = _i(hfull.get("reversalCount"), 0)
    v15 = _f(h15.get("velocityPpHr"), 0.0)
    v60 = _f(h60.get("velocityPpHr"), 0.0)
    v180 = _f(h180.get("velocityPpHr"), 0.0)

    reasons: List[str] = []
    if delta > 0 and rev >= 3 and prob < 0.58 and not independently_confirmed:
        reasons.append("positive_move_high_reversal_without_confirmation")
    if (rev60 >= 2 or rev180 >= 5 or revfull >= 10) and not independently_confirmed:
        reasons.append("multi_horizon_reversal_instability")
    late_direction_conflict = (
        (v15 != 0.0 and v60 != 0.0 and v15 * v60 < 0.0)
        or (v15 != 0.0 and v180 != 0.0 and v15 * v180 < 0.0)
    )
    if late_direction_conflict and not independently_confirmed:
        reasons.append("late_direction_conflict_without_confirmation")
    return sorted(set(reasons))


def _components(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    sig = _sig(row)
    opp = _opp(row)
    tags = _tags(row, sig)
    tier = str(row.get("confidenceTier") or "").lower()
    prob = _f(sig.get("marketConsensusProbability"), _f(sig.get("probLatest"), 0.5))
    opp_prob = _f(opp.get("marketConsensusProbability"), _f(opp.get("probLatest"), 1.0 - prob))
    edge = prob - opp_prob
    delta = _f(sig.get("delta"), 0.0)
    gap = _f(sig.get("latestGap"), abs(edge))
    rev = _i(sig.get("reversalCount"), 0)
    move_per_reversal = abs(delta) / max(rev, 1)
    out: List[Dict[str, Any]] = []

    def add(name: str, value: float):
        out.append({"name": name, "value": round(float(value), 2)})

    if tier == "premium":
        add("premium_bucket_boost", 4.0)
    elif tier == "solid":
        add("solid_bucket_boost", 3.0)
    elif tier == "lean":
        add("lean_bucket_penalty", -4.0)
    elif tier == "pass":
        add("pass_bucket_penalty", -6.0)

    if edge >= 0.25 and prob >= 0.62 and delta > 0:
        add("clean_market_edge_boost", 6.0)
    elif edge < 0:
        add("market_against_selection_penalty", -8.0)
    elif edge < 0.05:
        add("weak_market_edge_penalty", -4.0)

    if rev <= 2:
        add("low_reversal_boost", 2.0)
    elif rev == 3:
        add("three_reversal_caution", -4.0)
    elif 4 <= rev <= 5:
        add("high_reversal_penalty", -7.0)
    elif rev >= 6:
        add("six_plus_reversal_penalty", -11.0)

    independent_confirmation = bool(tags & {"BOOK_AGREEMENT", "STEAM", "RUN_LINE_CONFIRMATION"})
    if rev >= 2 and move_per_reversal >= 0.01 and not independent_confirmation:
        add("large_unconfirmed_reversal_move_penalty", -4.0)

    risk_gate_reasons = _signal_risk_gate_reasons(row)
    if "multi_horizon_reversal_instability" in risk_gate_reasons:
        add("multi_horizon_reversal_instability_penalty", -5.0)
    if "late_direction_conflict_without_confirmation" in risk_gate_reasons:
        add("late_direction_conflict_penalty", -5.0)
    if "positive_move_high_reversal_without_confirmation" in risk_gate_reasons:
        add("positive_move_unconfirmed_reversal_trap_penalty", -4.0)

    if gap < 0.05:
        add("compressed_market_penalty", -6.0)
    elif gap < 0.10:
        add("narrow_market_penalty", -3.0)

    if "RUN_LINE_MOVEMENT" in tags and edge >= 0.10 and delta > 0 and rev <= 1:
        add("aligned_run_line_boost", 3.0)
    elif "RUN_LINE_MOVEMENT" in tags:
        add("run_line_noise_penalty", -3.0)

    if "STEAM" in tags and edge >= 0.10 and delta > 0 and rev <= 1:
        add("stable_steam_boost", 3.0)
    elif "STEAM" in tags:
        add("unstable_steam_penalty", -3.0)

    if "RESISTANCE" in tags:
        add("resistance_penalty", -5.0)
    if "MISSING_FUNDAMENTALS" in tags and not (edge >= 0.25 and prob >= 0.62):
        add("missing_fundamentals_penalty", -3.0)
    if "UNCONFIRMED_RUN_LINE_MOVE" in tags and not (edge >= 0.25 and rev <= 1):
        add("unconfirmed_run_line_penalty", -3.0)
    return out


def _is_playable(row: Dict[str, Any]) -> bool:
    # Official prediction status is intentionally excluded. A locked prediction may
    # be official while still failing the higher-confidence playable gate.
    if _signal_risk_gate_reasons(row):
        return False
    return bool(
        row.get("playable") is True
        or row.get("playablePick") is True
        or row.get("actionablePick") is True
        or row.get("accuracyTargetEligible") is True
        or row.get("recommendationStatus") == "PLAYABLE_PREDICTION"
    )


def _is_official(row: Dict[str, Any]) -> bool:
    # Official display means the platform has made the required winner prediction.
    # Playable/actionable remains separate and is shown on the display card.
    return bool(
        row.get("officialPrediction") is True
        or row.get("platformPick") is True
        or row.get("customerVisibleWinnerPick") is True
        or row.get("predictedWinner")
    )


def _display_card(row: Dict[str, Any]) -> Dict[str, Any]:
    score_after = row.get("scoreAfterSignalPolicyV13", row.get("score"))
    playable = _is_playable(row)
    gate = row.get("signalRiskGate") or {}
    return {
        "gameId": row.get("gameId"),
        "gameKey": row.get("gameKey"),
        "homeTeam": row.get("homeTeam"),
        "awayTeam": row.get("awayTeam"),
        "commenceTime": row.get("commenceTime"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "confidenceTier": row.get("confidenceTier"),
        "score": row.get("score"),
        "scoreAfterSignalPolicyV13": score_after,
        "winProbabilityPct": row.get("winProbabilityPct"),
        "displayGroup": "required_game_prediction" if _is_official(row) else "missing_required_prediction",
        "isOfficial": _is_official(row),
        "isPlayable": playable,
        "platformPick": bool(row.get("predictedWinner")),
        "customerVisibleWinnerPick": bool(row.get("predictedWinner")),
        "recommendationStatus": "PLAYABLE_PREDICTION" if playable else "LOW_CONFIDENCE_PREDICTION_NOT_PLAYABLE",
        "showAtSlateLock": True,
        "displayRule": DAILY_SLATE_DISPLAY_RULE,
        "actionability": row.get("actionability"),
        "actionabilityReason": row.get("actionabilityReason"),
        "riskReasons": sorted(set((row.get("actionabilityRiskReasons") or []) + (gate.get("reasons") or []))),
    }


def _apply_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row or {})
    before = _f(out.get("score"), 0.0)
    comps = _components(out)
    adj = round(sum(_f(c.get("value"), 0.0) for c in comps), 2)
    after = max(0.0, min(100.0, before + adj))
    has_winner = bool(out.get("predictedWinner"))
    risk_gate_reasons = _signal_risk_gate_reasons(out)
    if risk_gate_reasons:
        out["playable"] = False
        out["playablePick"] = False
        out["actionablePick"] = False
        out["accuracyTargetEligible"] = False
        out["actionabilityRiskReasons"] = sorted(
            set((out.get("actionabilityRiskReasons") or []) + risk_gate_reasons)
        )
        out["signalRiskGate"] = {
            "applied": True,
            "blocked": True,
            "version": VERSION,
            "reasons": risk_gate_reasons,
            "policy": "Composite reversal and late-instability risk may block playability but never removes the required official winner prediction.",
        }
    else:
        out["signalRiskGate"] = {
            "applied": True,
            "blocked": False,
            "version": VERSION,
            "reasons": [],
            "policy": "Composite reversal and late-instability risk may block playability but never removes the required official winner prediction.",
        }

    out["predictionRequired"] = True
    out["requiredGameWinnerPrediction"] = has_winner
    out["winnerPredictionAvailable"] = has_winner
    out["platformPick"] = has_winner
    out["officialPrediction"] = has_winner
    out["customerVisibleWinnerPick"] = has_winner
    out["displayPrediction"] = has_winner
    out["predictionRequiredMinutesBeforeGame"] = REQUIRED_MINUTES_BEFORE_GAME
    out["showAtSlateLock"] = True
    out["dailySlateDisplayRule"] = DAILY_SLATE_DISPLAY_RULE
    out["signalPolicyV13"] = {
        "applied": True,
        "version": VERSION,
        "scoreOnly": False,
        "blocksPrediction": False,
        "blocksPlayabilityOnCompositeInstability": True,
        "requiredMinutesBeforeGame": REQUIRED_MINUTES_BEFORE_GAME,
        "showOneWinnerForEveryGameAtSlateLock": True,
        "scoreAdjustment": adj,
        "components": comps,
        "signalRiskGate": out["signalRiskGate"],
    }
    out["scoreBeforeSignalPolicyV13"] = round(before, 2)
    out["signalPolicyV13Adjustment"] = adj
    out["scoreAfterSignalPolicyV13"] = round(after, 2)
    out["predictionRemainsAvailable"] = has_winner
    out["displayGroup"] = "required_game_prediction" if has_winner else "missing_required_prediction"
    out["isOfficialDisplayPick"] = has_winner
    out["recommendationStatus"] = "PLAYABLE_PREDICTION" if _is_playable(out) else "LOW_CONFIDENCE_PREDICTION_NOT_PLAYABLE"
    tags = set(out.get("tags") or [])
    tags.add("SIGNAL_POLICY_V13_REVERSAL_INSTABILITY_GATE")
    if risk_gate_reasons:
        tags.add("SIGNAL_RISK_GATE_BLOCKED")
    if has_winner:
        tags.add("REQUIRED_GAME_WINNER_PREDICTION")
        tags.add("PREDICTION_REMAINS_AVAILABLE")
        tags.add("SHOW_AT_SLATE_LOCK")
        tags.add("PLATFORM_PICK")
    if not _is_playable(out):
        tags.discard("NO_PICK")
        tags.discard("NO_PICK_DISCIPLINE")
        tags.add("LOW_CONFIDENCE_PREDICTION")
        tags.add("NOT_PLAYABLE")
    out["tags"] = sorted(tags)
    return out


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows = [_apply_row(r) for r in (result.get("predictions") or []) if isinstance(r, dict)]
    required_cards = [_display_card(r) for r in rows if r.get("predictedWinner")]
    playable_cards = [_display_card(r) for r in rows if _is_playable(r)]
    low_confidence_cards = [_display_card(r) for r in rows if r.get("predictedWinner") and not _is_playable(r)]
    risk_blocked = [r for r in rows if (r.get("signalRiskGate") or {}).get("blocked") is True]
    out = dict(result)
    out["predictions"] = rows
    out["allGamesPredictionRequired"] = True
    out["predictionRequiredMinutesBeforeGame"] = REQUIRED_MINUTES_BEFORE_GAME
    out["showAllPredictionsAtSlateLock"] = True
    out["showAllNonPlayablePredictionsAtSlateLock"] = True
    out["dailySlateDisplayRule"] = DAILY_SLATE_DISPLAY_RULE
    out["requiredWinnerPredictionDisplay"] = required_cards
    out["officialPredictionDisplay"] = required_cards
    out["playablePredictionDisplay"] = playable_cards
    out["nonOfficialPredictionDisplay"] = low_confidence_cards
    out["signalPolicyV13"] = {
        "applied": True,
        "version": VERSION,
        "scoreOnly": False,
        "blocksPrediction": False,
        "blocksPlayabilityOnCompositeInstability": True,
        "rowCount": len(rows),
        "requiredPredictionDisplayCount": len(required_cards),
        "officialDisplayCount": len(required_cards),
        "playableDisplayCount": len(playable_cards),
        "lowConfidenceDisplayCount": len(low_confidence_cards),
        "riskGateBlockedCount": len(risk_blocked),
        "showOneWinnerForEveryGameAtSlateLock": True,
        "displayRule": DAILY_SLATE_DISPLAY_RULE,
        "policy": "Audit findings adjust score and may block playability for composite reversal or late-instability risk. No signal rule removes a required game prediction.",
    }
    summary = dict(out.get("rolling24hAccuracyTarget") or out.get("accuracyTarget") or {})
    summary["signalPolicyV13"] = out["signalPolicyV13"]
    summary["requiredPredictionDisplayCount"] = len(required_cards)
    summary["officialDisplayCount"] = len(required_cards)
    summary["playableDisplayCount"] = len(playable_cards)
    summary["lowConfidenceDisplayCount"] = len(low_confidence_cards)
    summary["riskGateBlockedCount"] = len(risk_blocked)
    summary["allGamesHaveDisplayedWinnerPrediction"] = bool(rows and len(required_cards) == len(rows))
    out["rolling24hAccuracyTarget"] = summary
    out["accuracyTarget"] = summary
    if VERSION not in str(out.get("modelVersion") or ""):
        out["modelVersion"] = str(out.get("modelVersion") or "") + "+" + VERSION
    return out


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_SIGNAL_POLICY_V12_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        return enhance_result(original(*args, **kwargs))

    module.predict_all = patched_predict_all
    module._INQSI_MLB_SIGNAL_POLICY_V12_APPLIED = True
    return module
