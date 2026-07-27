"""Current-season-aware inner fold policy for supervised MLB shadow training."""
from __future__ import annotations

from datetime import date
from typing import Any, List, Sequence, Tuple

VERSION = "MLB-SUPERVISED-INNER-FOLDS-v2.2-current-season-development"


def current_season_folds(
    original: Any, train_days: Sequence[str]
) -> List[Tuple[List[str], List[str]]]:
    days = sorted(set(str(value) for value in train_days if str(value)))
    base_folds = original(days)
    if not days:
        return base_folds
    latest_year = date.fromisoformat(days[-1]).year
    current = [value for value in days if date.fromisoformat(value).year == latest_year]
    # Require enough current-season dates to create a real chronological fit and
    # a five-day validation block. Before that point, retain the original folds.
    if len(current) < 10:
        return base_folds
    validation = current[-5:]
    fit = [value for value in days if value < validation[0]]
    if len(fit) < 25:
        return base_folds
    earlier = [fold for fold in base_folds if max(fold[1]) < current[0]]
    # Keep two prior-regime folds plus one strictly current-season fold. This
    # prevents postseason-only validation dates from dominating selection while
    # preserving a broad chronological stability check.
    selected = earlier[-2:] + [(fit, validation)]
    return selected if len(selected) >= 2 else base_folds


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_CURRENT_SEASON_FOLDS_V2_2_INSTALLED", False):
        return model_module
    original = model_module.inner_expanding_folds

    def replacement(train_days, *, fold_count=3):
        del fold_count
        return current_season_folds(original, train_days)

    model_module.inner_expanding_folds = replacement
    model_module.CURRENT_SEASON_FOLD_POLICY_VERSION = VERSION
    model_module._INQSI_CURRENT_SEASON_FOLDS_V2_2_INSTALLED = True
    return model_module
