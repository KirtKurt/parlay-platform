from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

VERSION = "MLB-ML-CLEAN-COHORT-DEDUPE-v1-eligible-row-precedence"


def _identity(module: Any, row: Dict[str, Any]) -> Tuple[str, str]:
    game_id = str(module._game_id(row) or "")
    lock_at = module._lock_at(row)
    return game_id, str(lock_at or "")


def apply(cohort_module: Any):
    if getattr(cohort_module, "_INQSI_MLB_COHORT_DEDUPE_FIX_APPLIED", False):
        return cohort_module

    original_build = cohort_module.build

    def build(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        source: List[Dict[str, Any]] = list(rows or [])
        chosen: Dict[Tuple[str, str], Dict[str, Any]] = {}
        order: List[Tuple[str, str]] = []

        for row in source:
            key = _identity(cohort_module, row)
            if key not in chosen:
                chosen[key] = row
                order.append(key)
                continue

            current = chosen[key]
            current_ok, _ = cohort_module.eligibility(current)
            candidate_ok, _ = cohort_module.eligibility(row)

            # A valid post-fix immutable row must always replace a legacy or
            # otherwise quarantined duplicate for the same game and lock.
            if candidate_ok and not current_ok:
                chosen[key] = row

        selected = [chosen[key] for key in order]
        out = original_build(selected)
        out["dedupeVersion"] = VERSION
        out["dedupePolicy"] = "eligible_post_fix_row_precedes_legacy_or_ineligible_duplicate"
        out["inputRowsBeforeDedupe"] = len(source)
        out["rowsAfterDedupe"] = len(selected)
        out["duplicatesRemoved"] = len(source) - len(selected)
        return out

    cohort_module.build = build
    cohort_module._INQSI_MLB_COHORT_DEDUPE_FIX_APPLIED = True
    return cohort_module
