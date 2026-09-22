#!/usr/bin/env python3
"""Final roster-correct MLB reversal-size audit wrapper.

Filters the full union of canonical-pull games to the exact Eastern slate date,
so late-night next-day provider events are excluded while doubleheaders remain.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import audit_mlb_reversal_sizes_v2 as audit

SLATE_TZ = ZoneInfo("America/New_York")
_ORIGINAL_TARGETS = audit.targets_from_all_pulls


def _exact_slate_targets(pulls):
    slate = str((pulls[0] if pulls else {}).get("slate_date") or "")
    rows = _ORIGINAL_TARGETS(pulls)
    return [
        game
        for game in rows
        if audit.base.start(game) is not None
        and audit.base.start(game).astimezone(SLATE_TZ).date().isoformat() == slate
    ]


audit.targets_from_all_pulls = _exact_slate_targets


if __name__ == "__main__":
    raise SystemExit(audit.main())
