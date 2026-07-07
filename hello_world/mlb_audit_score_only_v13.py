from __future__ import annotations

from typing import Any, Dict, List

VERSION = "MLB-AUDIT-SCORE-ONLY-v1.3"


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
    c: List[Dict[str, Any]] = []

    def add(name: str, value: float):
        c.append({"name": name, "value": round(value, 2)})

    if tier == "premium": add("premium_audit_boost", 4.0)
    if tier == "solid": add("solid_audit_boost", 3.0)
    if tier == "lean": add("lean_audit_penalty", -4.0)
    if tier == "pass": add("pass_audit_penalty", -6.0)

    if edge >= 0.25 and prob >= 0.62 and delta > 0: add("clean_market_edge_boost", 6.0)
    elif edge < 0: add("market_against_selection_penalty", -8.0)
    elif edge < 0.05: add("weak_market_edge_penalty", -4.0)

    if rev <= 2: add("low_reversal_boost", 2.0)
    elif rev == 3: add("three_reversal_caution", -2.0)
    elif 4 <= rev <= 5: add("high_reversal_penalty", -6.0)
    elif rev >= 6: add("six_plus_reversal_penalty", -10.0)

    if gap < 0.05: add("compressed_market_penalty", -6.0)
    elif gap < 0.10: add("narrow_market_penalty", -3.0)

    if "RUN_LINE_MOVEMENT" in tags and edge >= 0.10 and delta > 0 and rev <= 3:
        add("aligned_run_line_boost", 3.0)
    elif "RUN_LINE_MOVEMENT" in tags:
        add("run_line_noise_penalty", -3.0)

    if "STEAM" in tags and edge >= 0.10 and delta > 0 and rev <= 3:
        add("stable_steam_boost", 3.0)
    elif "STEAM" in tags:
        add("unstable_steam_penalty", -3.0)

    if "RESISTANCE" in tags: add("resistance_penalty", -5.0)
    if "MISSING_FUNDAMENTALS" in tags and not (edge >= 0.25 and prob >= 0.62): add("missing_fundamentals_penalty", -3.0)
    if "UNCONFIRMED_RUN_LINE_MOVE" in tags and not (edge >= 0.25 and rev <= 3): add("unconfirmed_run_line_penalty", -3.0)
    return c


def _apply_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row or {})
    before = _f(out.get("score"), 0.0)
    comps = _components(out)
    adj = round(sum(_f(x.get("value"), 0.0) for x in comps), 2)
    after = max(0.0, min(100.0, before + adj))
    out["auditScoreOnlyV13"] = {"applied": True, "version": VERSION, "scoreOnly": True, "blocksPrediction": False, "adjustment": adj, "components": comps}
    out["scoreBeforeAuditScoreOnly"] = round(before, 2)
    out["auditScoreOnlyAdjustment"] = adj
    out["scoreAfterAuditScoreOnly"] = round(after, 2)
    out["predictionRemainsAvailable"] = True
    tags = set(out.get("tags") or [])
    tags.add("AUDIT_SCORE_ONLY_V13")
    tags.add("PREDICTION_REMAINS_AVAILABLE")
    out["tags"] = sorted(tags)
    return out


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows = [_apply_row(r) for r in (result.get("predictions") or []) if isinstance(r, dict)]
    out = dict(result)
    out["predictions"] = rows
    out["auditScoreOnlyV13"] = {"applied": True, "version": VERSION, "scoreOnly": True, "blocksPrediction": False, "rowCount": len(rows)}
    if VERSION not in str(out.get("modelVersion") or ""):
        out["modelVersion"] = str(out.get("modelVersion") or "") + "+" + VERSION
    return out


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_AUDIT_SCORE_ONLY_V13_APPLIED", False):
        return module
    original = module.predict_all
    def patched_predict_all(*args, **kwargs):
        return enhance_result(original(*args, **kwargs))
    module.predict_all = patched_predict_all
    module._INQSI_MLB_AUDIT_SCORE_ONLY_V13_APPLIED = True
    return module
