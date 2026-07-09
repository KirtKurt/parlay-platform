from __future__ import annotations

from typing import Any, Dict, List, Optional

NON_PREDICTIVE_TAGS = {
    # Workflow / gate status
    "FINAL_LOCKED",
    "FINAL_GATE_OPEN",
    "LOCK_CLOSED",
    "GAME_STARTED_OR_CLOSED",
    "PRE_FINAL_GATE",
    "PRE_SLATE_LOCK",
    "SLATE_LOCKED",
    "SLATE_WIDE_45_MIN_LOCK_POLICY",
    "FINAL_GATE_BLOCKED_MISSING_SPORTSDATAIO",
    # Provider/source status
    "SPORTSDATAIO_FINAL_GATE_MISSING",
    "SPORTSDATAIO_FINAL_GATE_APPLIED",
    "FUNDAMENTALS_APPLIED",
    "ODDS_API_ONLY",
    # Ranking / discipline labels that are outcomes of the model, not inputs
    "ACTIONABLE_PICK",
    "NO_PICK",
    "NO_PICK_DISCIPLINE",
    "CALIBRATED_PROBABILITY",
    "WINNER_STACK_V2",
    # ML signal-layer outputs are post-model decisions, not raw market evidence.
    "ML_SIGNAL_LAYERS",
    "ML_SIGNAL_HARD_BLOCKED",
    "ML_PRIMARY_SIGNAL_CANDIDATE",
    "ML_WATCHLIST_SIGNAL",
    "ML_NO_PRIMARY_SIGNAL",
    "ML_SIGNAL_LAYER_SELECTION",
}


def _clean_row(row: Dict[str, Any]) -> Dict[str, Any]:
    copied = dict(row or {})
    tags = list(copied.get("tags") or [])
    predictive = [tag for tag in tags if tag not in NON_PREDICTIVE_TAGS]
    excluded = [tag for tag in tags if tag in NON_PREDICTIVE_TAGS]
    copied["tags"] = predictive
    copied["learningExcludedOperationalTags"] = excluded
    return copied


def _clean_rows(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return [_clean_row(row) for row in (rows or [])]


def apply(module):
    if getattr(module, "_INQSI_MLB_LEARNING_SIGNAL_FILTER_APPLIED", False):
        return module
    original_score_learning = module.score_learning

    def filtered_score_learning(rows, historical_rows=None):
        result = original_score_learning(_clean_rows(rows), historical_rows=_clean_rows(historical_rows))
        if isinstance(result, dict):
            result["excludedOperationalTags"] = sorted(NON_PREDICTIVE_TAGS)
            result["operationalTagFilterPolicy"] = (
                "Final-gate, provider, model-discipline, ML signal-layer, and status tags are kept on audit rows for proof, "
                "but removed from signal-learning inputs so the model only learns from predictive market/fundamentals signals."
            )
        return result

    module.score_learning = filtered_score_learning
    module._INQSI_MLB_LEARNING_SIGNAL_FILTER_APPLIED = True
    return module
