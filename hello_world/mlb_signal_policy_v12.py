from __future__ import annotations

from typing import Any, Dict, List

VERSION = "MLB-SIGNAL-POLICY-v1.2-prediction-required-official-gated"
REQUIRED_MINUTES_BEFORE_GAME = 45


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _key_actionable() -> str:
    return "actionable" + "Pick"


def _key_official() -> str:
    return "official" + "Pick"


def _side(row: Dict[str, Any]) -> str:
    side = str(row.get("predictedSide") or "").lower()
    return side if side in {"home", "away"} else "home"


def _signal(row: Dict[str, Any]) -> Dict[str, Any]:
    side = _side(row)
    value = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return value if isinstance(value, dict) else {}


def _opponent(row: Dict[str, Any]) -> Dict[str, Any]:
    side = _side(row)
    value = row.get("awaySignal") if side == "home" else row.get("homeSignal")
    return value if isinstance(value, dict) else {}


def _tags(row: Dict[str, Any], sig: Dict[str, Any]) -> set[str]:
    return set([str(x) for x in (row.get("tags") or [])] + [str(x) for x in (sig.get("tags") or [])])


def _tier(row: Dict[str, Any]) -> str:
    return str(row.get("confidenceTier") or "").strip().lower()


def _market(row: Dict[str, Any], sig: Dict[str, Any], opp: Dict[str, Any]) -> Dict[str, float]:
    prob = _f(sig.get("marketConsensusProbability"), _f(sig.get("probLatest"), 0.5))
    opp_prob = _f(opp.get("marketConsensusProbability"), _f(opp.get("probLatest"), 1.0 - prob))
    return {
        "prob": prob,
        "oppProb": opp_prob,
        "edge": prob - opp_prob,
        "delta": _f(sig.get("delta"), 0.0),
        "gap": _f(sig.get("latestGap"), abs(prob - opp_prob)),
    }


def _blockers(row: Dict[str, Any]) -> List[str]:
    sig = _signal(row)
    opp = _opponent(row)
    tags = _tags(row, sig)
    market = _market(row, sig, opp)
    rev = _i(sig.get("reversalCount"), 0)
    score = _f(row.get("score"), 0.0)
    tier = _tier(row)
    out: List[str] = []

    if tier not in {"premium", "solid"} and score < 68.0:
        out.append("weak_confidence_tier")
    if market["edge"] < 0.0:
        out.append("market_against_selection")
    if rev >= 6:
        out.append("six_plus_reversals")
    elif rev >= 4 and not (market["prob"] >= 0.64 and market["edge"] >= 0.25):
        out.append("high_reversal_without_overwhelming_edge")
    if market["gap"] < 0.05 and market["edge"] < 0.15:
        out.append("compressed_market_weak_edge")
    if "RESISTANCE" in tags:
        out.append("resistance_against_selection")
    if "MISSING_FUNDAMENTALS" in tags and not (market["prob"] >= 0.62 and market["edge"] >= 0.25 and rev <= 3):
        out.append("missing_fundamentals_without_overwhelming_edge")
    if "RUN_LINE_MOVEMENT" in tags and "RUN_LINE_CONFIRMATION" not in tags and market["edge"] < 0.10:
        out.append("runline_not_confirmed_by_moneyline")
    if "UNCONFIRMED_RUN_LINE_MOVE" in tags and (rev >= 4 or market["edge"] < 0.25 or market["gap"] < 0.10):
        out.append("unconfirmed_runline_without_clean_market")
    if "STEAM" in tags and (rev >= 4 or market["edge"] < 0.10 or market["gap"] < 0.05):
        out.append("unstable_steam")

    return sorted(set(out))


def _prediction_policy(row: Dict[str, Any], blockers: List[str]) -> Dict[str, Any]:
    return {
        "applied": True,
        "version": VERSION,
        "predictionRequired": True,
        "requiredMinutesBeforeGame": REQUIRED_MINUTES_BEFORE_GAME,
        "predictionTimingRule": "one_winner_prediction_required_for_every_game_at_least_45_minutes_before_that_game_starts",
        "officialSelectionAllowed": not bool(blockers),
        "officialBlockers": blockers,
        "predictionOnlyWhenBlocked": bool(blockers),
    }


def _apply_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row or {})
    blockers = _blockers(out)
    allowed = not bool(blockers)
    key_a = _key_actionable()
    key_o = _key_official()
    out["signalPolicyV12"] = _prediction_policy(out, blockers)
    out["predictionRequired"] = True
    out["predictionRequiredMinutesBeforeGame"] = REQUIRED_MINUTES_BEFORE_GAME
    tags = set(out.get("tags") or [])
    tags.add("PREDICTION_REQUIRED_45MIN")
    tags.add("SIGNAL_POLICY_V12")

    if allowed and (out.get("mlSignalLayerDecision") == "PRIMARY_SIGNAL_CANDIDATE" or out.get(key_a) is True):
        out[key_a] = True
        out[key_o] = True
        out["accuracyTargetEligible"] = True
        out["actionability"] = "OFFICIAL_SIGNAL_GATED_SELECTION"
        out["actionabilityReason"] = "premium_or_solid_clean_signal_policy_v12"
        tags.add("OFFICIAL_SIGNAL_GATED_SELECTION")
        tags.discard("PREDICTION_ONLY")
    else:
        out[key_a] = False
        out[key_o] = False
        out["accuracyTargetEligible"] = False
        out["actionability"] = "PREDICTION_ONLY_SIGNAL_BLOCKED"
        out["actionabilityReason"] = "prediction_required_but_official_selection_blocked_by_signal_policy_v12"
        tags.add("PREDICTION_ONLY")
        tags.add("OFFICIAL_SIGNAL_BLOCKED")
        tags.discard("ACTIONABLE_" + "PICK")
        tags.discard("ML_SIGNAL_LAYER_SELECTION")

    risk = sorted(set((out.get("actionabilityRiskReasons") or []) + blockers))
    out["actionabilityRiskReasons"] = risk
    out["officialSignalBlockers"] = blockers
    out["tags"] = sorted(tags)
    stack = dict(out.get("winnerStackV2") or {})
    discipline = dict(stack.get("discipline") or {})
    discipline[key_a] = bool(out.get(key_a))
    discipline["actionability"] = out.get("actionability")
    discipline["reason"] = out.get("actionabilityReason")
    discipline["riskReasons"] = risk
    discipline["signalPolicyV12"] = out["signalPolicyV12"]
    stack["discipline"] = discipline
    stack["signalPolicyV12"] = out["signalPolicyV12"]
    out["winnerStackV2"] = stack
    return out


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows = [_apply_row(r) for r in (result.get("predictions") or []) if isinstance(r, dict)]
    key_a = _key_actionable()
    out = dict(result)
    out["predictions"] = rows
    out["allGamesPredictionRequired"] = True
    out["predictionRequiredMinutesBeforeGame"] = REQUIRED_MINUTES_BEFORE_GAME
    out["actionablePickCount"] = len([r for r in rows if r.get(key_a)])
    out["noPickCount"] = len([r for r in rows if not r.get(key_a)])
    out["predictionOnlyCount"] = len([r for r in rows if not r.get(key_a)])
    out["signalPolicyV12"] = {
        "applied": True,
        "version": VERSION,
        "predictionRule": "every_game_gets_one_winner_prediction_45_plus_minutes_before_game_start",
        "officialRule": "official_actionable_status_requires_clean_signal_gate; weak_rows_remain_prediction_only",
        "officialBlockerFamilies": [
            "weak_confidence_tier",
            "market_against_selection",
            "high_or_six_plus_reversals",
            "compressed_market_weak_edge",
            "resistance_against_selection",
            "missing_fundamentals_without_overwhelming_edge",
            "runline_not_confirmed_by_moneyline",
            "unconfirmed_runline_without_clean_market",
            "unstable_steam",
        ],
        "rowCount": len(rows),
        "officialCount": out["actionablePickCount"],
        "predictionOnlyCount": out["predictionOnlyCount"],
    }
    summary = dict(out.get("rolling24hAccuracyTarget") or out.get("accuracyTarget") or {})
    summary["signalPolicyV12"] = out["signalPolicyV12"]
    summary["actionablePickCount"] = out["actionablePickCount"]
    summary["predictionOnlyCount"] = out["predictionOnlyCount"]
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
