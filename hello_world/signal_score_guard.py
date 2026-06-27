from typing import Any, Dict, List


def _score_cap_for_grade(grade: str, tags: List[str]) -> float:
    tags = set(tags or [])
    if grade == "STRONG_SOLID":
        return 100.0
    if grade == "SOLID":
        return 84.0
    if grade == "COIN_FLIP":
        return 54.0
    if grade == "FRAGILE":
        return 34.0 if "CHAOS" in tags else 44.0
    if grade == "INSUFFICIENT_HISTORY":
        return 20.0
    return 50.0


def _guarded_score(raw: float, grade: str, tags: List[str], reversals: int, divergence: float, latest_gap: float) -> float:
    score = float(raw)
    tags = set(tags or [])
    if "CHAOS" in tags:
        score -= 25
    if "BOOK_DIVERGENCE" in tags:
        score -= 12
    if "REVERSAL" in tags:
        score -= 8 * max(1, int(reversals or 0))
    if latest_gap < 0.05:
        score -= 10
    if divergence >= 0.055:
        score -= 15
    score = max(0.0, min(score, _score_cap_for_grade(grade, list(tags))))
    return round(score, 2)


def apply(history_module: Any) -> None:
    if history_module is None or getattr(history_module, "_inqsi_score_guard_installed", False):
        return
    original = history_module.side_signal

    def side_signal(series: List[Dict[str, Any]], side: str) -> Dict[str, Any]:
        row = original(series, side)
        tags = row.get("tags") or []
        grade = row.get("grade") or "FRAGILE"
        row["rawScoreBeforeGuard"] = row.get("score")
        row["score"] = _guarded_score(
            float(row.get("score") or 0),
            grade,
            tags,
            int(row.get("reversals") or 0),
            float(row.get("bookDivergence") or 0),
            float(row.get("latestGap") or 0),
        )
        row["scoreGuardApplied"] = True
        return row

    history_module.side_signal = side_signal
    history_module._inqsi_score_guard_installed = True
