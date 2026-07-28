"""Fail-closed settled-outcome handling for the MLB V7 recovery runtime."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

VERSION = "MLB-HISTORICAL-V7-LABEL-INTEGRITY-v1"


def strict_binary_label(value: Any):
    if value is True or value == 1 or value == 1.0 or value == "1":
        return 1
    if value is False or value == 0 or value == 0.0 or value == "0":
        return 0
    return None


def install(learner: Any) -> None:
    """Replace the learner's example materializer with a strict-label version.

    The original implementation used ``int(row.get('homeWon') or 0)``, which
    converted missing outcomes into away wins. This patch is installed only by
    the V7 recovery entrypoint and excludes every non-binary outcome.
    """
    if getattr(learner, "_INQIS_V7_LABEL_INTEGRITY_INSTALLED", False):
        return

    def examples(records: Sequence[Mapping[str, Any]], dates: Iterable[str], policy: Mapping[str, Any]):
        allowed = {str(day) for day in dates}
        output = []
        for row in records:
            day = str(row.get("slateDateEt") or "")
            if day not in allowed:
                continue
            label = strict_binary_label(row.get("homeWon"))
            if label is None:
                continue
            values = learner.pair_features(
                row.get("homeSignal") or {}, row.get("awaySignal") or {}, policy
            )
            output.append((day, [learner._f(values.get(name)) for name in learner.FEATURES], label))
        return output

    learner._examples = examples
    learner.V7_LABEL_INTEGRITY_VERSION = VERSION
    learner._INQIS_V7_LABEL_INTEGRITY_INSTALLED = True
