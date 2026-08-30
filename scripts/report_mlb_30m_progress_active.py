#!/usr/bin/env python3
"""Render the canonical MLB progress pulse with the deployed cohort identity.

The underlying reporter intentionally retains legacy internal field names for
backward-compatible state decoding. This read-only adapter changes only the
human-visible labels so an R8 runtime is never presented as the retired R7
cohort. It does not alter collection, partitioning, training, selection,
promotion, publication, or authority behavior.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from typing import Any, Optional

import report_mlb_30m_progress as reporter


_ORIGINAL_COMMENT = reporter._comment


def _active_cohort_label(state: Mapping[str, Any]) -> str:
    learning = state.get("r7")
    if not isinstance(learning, Mapping):
        learning = {}
    experiment_id = str(
        learning.get("reportedExperimentId")
        or state.get("experimentId")
        or ""
    ).strip()
    match = re.search(r"(?:^|[-_])(r\d+)$", experiment_id, flags=re.IGNORECASE)
    return match.group(1).upper() if match else "MLB learning"


def _rewrite_visible_labels(body: str, *, cohort_label: str) -> str:
    replacements = {
        "### R7 prospective experiment": (
            f"### {cohort_label} historical + live experiment"
        ),
        "**R7 recovery workflow:**": f"**{cohort_label} recovery workflow:**",
        "| Bedrock / R7 / unknown authority picks |": (
            "| Bedrock / AWS ML / unknown authority picks |"
        ),
        "| Qualified R7 champion |": "| Qualified MLB champion |",
    }
    rendered = body
    for before, after in replacements.items():
        rendered = rendered.replace(before, after)
    return rendered


def _active_comment(
    state: Mapping[str, Any],
    previous: Optional[Mapping[str, Any]],
) -> str:
    body = _ORIGINAL_COMMENT(state, previous)
    return _rewrite_visible_labels(
        body,
        cohort_label=_active_cohort_label(state),
    )


def main() -> int:
    reporter._comment = _active_comment
    return reporter.main()


if __name__ == "__main__":
    sys.exit(main())
