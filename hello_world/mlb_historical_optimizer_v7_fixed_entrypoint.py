"""Production-safe V7 historical optimizer entrypoint.

This wrapper installs the supervised chronological learner into the same runtime
used by the canonical historical optimizer and replaces the legacy permissive
label conversion with a strict binary-label contract. Missing or malformed
labels are rejected instead of being silently converted to away-team wins.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Sequence

import mlb_historical_optimizer_entrypoint as base_entrypoint
import mlb_historical_supervised_v9 as supervised

VERSION = "MLB-HISTORICAL-V7-FIXED-ENTRYPOINT-v1"


def _strict_binary_home_won(row: Mapping[str, Any]) -> int:
    """Return an authoritative binary label or fail closed.

    bool is accepted because canonical settlement commonly emits booleans.
    Integer 0/1 is accepted for persisted historical rows. All other values,
    including None, empty strings, and arbitrary truthy values, are rejected.
    """
    if "homeWon" not in row:
        raise RuntimeError("MLB_SUPERVISED_HOME_WON_LABEL_MISSING")
    value = row.get("homeWon")
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    raise RuntimeError("MLB_SUPERVISED_HOME_WON_LABEL_INVALID")


def _strict_examples(
    records: Sequence[Mapping[str, Any]],
    dates: Iterable[str],
    policy: Mapping[str, Any],
):
    allowed = {str(day) for day in dates}
    examples = []
    for row in records:
        day = str(row.get("slateDateEt") or "")
        if day not in allowed:
            continue
        label = _strict_binary_home_won(row)
        values = supervised.pair_features(
            row.get("homeSignal") or {},
            row.get("awaySignal") or {},
            policy,
        )
        examples.append(
            (day, [supervised._f(values.get(name)) for name in supervised.FEATURES], label)
        )
    return examples


# Install the strict contract before installing the learner. The install call is
# idempotent and binds supervised search, feature extraction, probability output,
# and promotion-gate evaluation into the canonical optimizer runtime.
supervised._examples = _strict_examples
supervised.install(
    base_entrypoint.optimizer_handler.optimizer,
    base_entrypoint.optimizer_handler.policy_runtime,
)


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    return base_entrypoint.lambda_handler(event, context)
