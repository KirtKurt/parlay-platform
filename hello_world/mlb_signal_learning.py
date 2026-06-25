from __future__ import annotations

from typing import Any, Dict, List, Optional

from mlb_audit import (
    SETTLEMENT_VERSION,
    _count_statuses,
    _is_individual_prediction,
    _jsonable,
    _market_name,
    _predictions_for_slate,
    _slate_date_et,
    final_mlb_scores_report,
)


LEARNING_VERSION = "MLB-B1.0-signal-learning-v1"
MIN_SETTLED_ROWS_FOR_WEIGHT_CHANGE = 20
MIN_MARKET_ROWS_FOR_WEIGHT_CHANGE = 8

SOURCE_REQUIRED_ADVANCED_INPUTS = [
    "FIP/xFIP",
    "wRC+",
    "confirmed_lineups",
    "injuries_news",
    "bullpen_availability",
    "weather_roof",
    "public_handle",
    "travel_rest",
    "defense",
]

SIGNAL_FIELDS = [
    "hot_delta",
    "home_delta",
    "away_delta",
    "spread_delta",
    "total_delta",
    "confidence_score",
    "book_agreement",
    "book_divergence",
    "closing_line_value",
]


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _movement_value(pred: Dict[str, Any], key: str) -> Optional[float]:
    if key in pred:
        return _to_float(pred.get(key))
    movement = pred.get("movement") or {}
    if key in movement:
        return _to_float(movement.get(key))
    if key == "hot_delta":
        home = _movement_value(pred, "home_delta")
        away = _movement_value(pred, "away_delta")
        if home is None and away is None:
            return None
        if home is None:
            return away
        if away is None:
            return home
        return home if abs(home) >= abs(away) else away
    return None


def _book_agreement_count(pred: Dict[str, Any]) -> Optional[float]:
    data = pred.get("book_agreement") or {}
    for key in ("agreeing_books", "common_books"):
        if key in data:
            return _to_float(data.get(key))
    return None


def _book_divergence_value(pred: Dict[str, Any]) -> Optional[float]:
    for key in ("book_divergence", "bookDivergence", "book_divergence_avg"):
        if key in pred:
            return _to_float(pred.get(key))
    data = pred.get("latest_consensus") or {}
    if "book_divergence" in data:
        return _to_float(data.get("book_divergence"))
    return None


def _closing_line_value(pred: Dict[str, Any]) -> Optional[float]:
    for key in ("closing_line_value", "clv", "CLV"):
        if key in pred:
            return _to_float(pred.get(key))
    ctx = pred.get("advanced_context") or {}
    for key in ("closing_line_value", "clv", "CLV"):
        if key in ctx:
            return _to_float(ctx.get(key))
    return None


def _signal_value(pred: Dict[str, Any], field: str) -> Optional[float]:
    if field in {"hot_delta", "home_delta", "away_delta", "spread_delta", "total_delta"}:
        return _movement_value(pred, field)
    if field == "confidence_score":
        return _to_float(pred.get("confidence_score") or pred.get("confidence"))
    if field == "book_agreement":
        return _book_agreement_count(pred)
    if field == "book_divergence":
        return _book_divergence_value(pred)
    if field == "closing_line_value":
        return _closing_line_value(pred)
    return _to_float(pred.get(field))


def _avg(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


def _split_values(rows: List[Dict[str, Any]], field: str) -> Dict[str, Any]:
    wins = []
    losses = []
    missing = 0
    for row in rows:
        value = _signal_value(row, field)
        if value is None:
            missing += 1
            continue
        if row.get("success") is True:
            wins.append(value)
        elif row.get("success") is False:
            losses.append(value)
    return {
        "field": field,
        "win_avg": _avg(wins),
        "loss_avg": _avg(losses),
        "win_count": len(wins),
        "loss_count": len(losses),
        "missing_count": missing,
        "directional_gap": round((_avg(wins) or 0) - (_avg(losses) or 0), 6) if wins and losses else None,
    }


def _reason_code_summary(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, int]] = {}
    for row in rows:
        for code in row.get("reason_codes") or []:
            stats = buckets.setdefault(str(code), {"wins": 0, "losses": 0, "rows": 0})
            stats["rows"] += 1
            if row.get("success") is True:
                stats["wins"] += 1
            elif row.get("success") is False:
                stats["losses"] += 1
    output = []
    for code, stats in buckets.items():
        graded = stats["wins"] + stats["losses"]
        output.append({
            "reason_code": code,
            **stats,
            "hit_rate_pct": round(stats["wins"] / graded * 100, 2) if graded else None,
        })
    output.sort(key=lambda x: (x.get("rows", 0), x.get("hit_rate_pct") or -1), reverse=True)
    return output


def _market_summary(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        market = _market_name(row)
        data = out.setdefault(market, {"wins": 0, "losses": 0, "push": 0, "ungradable": 0, "rows": 0})
        data["rows"] += 1
        status = row.get("status")
        if row.get("success") is True:
            data["wins"] += 1
        elif row.get("success") is False:
            data["losses"] += 1
        elif status == "PUSH":
            data["push"] += 1
        elif status == "UNGRADABLE":
            data["ungradable"] += 1
    for data in out.values():
        graded = data["wins"] + data["losses"]
        data["graded"] = graded
        data["hit_rate_pct"] = round(data["wins"] / graded * 100, 2) if graded else None
    return out


def _status_from_evidence(graded_count: int, market_summary: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    blockers = []
    if graded_count < MIN_SETTLED_ROWS_FOR_WEIGHT_CHANGE:
        blockers.append({
            "type": "INSUFFICIENT_SETTLED_ROWS",
            "actual": graded_count,
            "required": MIN_SETTLED_ROWS_FOR_WEIGHT_CHANGE,
        })
    for market, stats in market_summary.items():
        if stats.get("graded", 0) and stats.get("graded", 0) < MIN_MARKET_ROWS_FOR_WEIGHT_CHANGE:
            blockers.append({
                "type": "INSUFFICIENT_MARKET_ROWS",
                "market": market,
                "actual": stats.get("graded", 0),
                "required": MIN_MARKET_ROWS_FOR_WEIGHT_CHANGE,
            })
    if blockers:
        return {
            "learning_status": "OBSERVE_ONLY",
            "weight_update_allowed": False,
            "blockers": blockers,
            "message": "Settled evidence was captured, but weights are not changed until the minimum sample gate is met.",
        }
    return {
        "learning_status": "READY_FOR_REVIEW",
        "weight_update_allowed": False,
        "blockers": [],
        "message": "Evidence gate is met, but automatic weight mutation is still disabled until reviewed.",
    }


def build_signal_learning_report(slate_date: Optional[str] = None, fetch_scores: bool = False, days_from: int = 3) -> Dict[str, Any]:
    """Build an observe-only signal-learning report from settled MLB outcomes.

    This function does not grade live games and does not mutate model weights. It
    reads already-settled rows from PredictionsTable and compares winner/loser
    signal features so the next reviewed patch can be evidence-based.
    """
    slate_date = slate_date or _slate_date_et()
    score_report = final_mlb_scores_report(slate_date=slate_date, days_from=days_from, fetch_scores=fetch_scores)
    items = _predictions_for_slate(slate_date)
    individual = [row for row in items if _is_individual_prediction(row)]
    settled = [row for row in individual if row.get("status") in {"CORRECT", "WRONG", "PUSH", "UNGRADABLE"}]
    graded = [row for row in settled if row.get("success") in {True, False}]
    wins = [row for row in graded if row.get("success") is True]
    losses = [row for row in graded if row.get("success") is False]
    status_counts = _count_statuses(individual, field="status")
    market_summary = _market_summary(settled)
    signal_diagnostics = [_split_values(graded, field) for field in SIGNAL_FIELDS]
    evidence_status = _status_from_evidence(len(graded), market_summary)

    accuracy = round(len(wins) / len(graded) * 100, 2) if graded else None
    missing_advanced = {field: "SOURCE_REQUIRED" for field in SOURCE_REQUIRED_ADVANCED_INPUTS}

    return {
        "ok": True,
        "sport": "mlb",
        "slate_date_et": slate_date,
        "learning_version": LEARNING_VERSION,
        "settlement_version": SETTLEMENT_VERSION,
        "final_only": True,
        "live_or_in_progress_policy": "DO_NOT_GRADE_UNTIL_COMPLETED_TRUE",
        "score_fetch": {
            "fetch_scores": fetch_scores,
            "final_score_count": score_report.get("final_score_count"),
            "fetch_report": score_report.get("fetch_report"),
        },
        "sample": {
            "individual_prediction_rows": len(individual),
            "settled_rows": len(settled),
            "graded_rows": len(graded),
            "wins": len(wins),
            "losses": len(losses),
            "accuracy_pct": accuracy,
            "status_counts": status_counts,
        },
        "market_summary": market_summary,
        "reason_code_summary": _reason_code_summary(graded),
        "signal_diagnostics": signal_diagnostics,
        "advanced_inputs": {
            "policy": "Do not infer missing advanced data. Missing inputs are marked source-required.",
            "source_required": missing_advanced,
        },
        "clv_policy": "CLV is included only when a stored closing snapshot or explicit closing_line_value exists.",
        "weight_update": evidence_status,
        "deployment_safe": True,
        "message": "Signal-learning report built in observe-only mode from settled MLB data. No live games were graded and no weights were automatically changed.",
    }
