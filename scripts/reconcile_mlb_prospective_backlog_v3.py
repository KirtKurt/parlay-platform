#!/usr/bin/env python3
"""Run bounded MLB backlog reconciliation with official read authority.

The protected mutation response is operational/provider-scoped. The separate
read-only lock-status response is the exact official full-slate authority. This
wrapper preserves mutation health and missed/due failure checks, but derives
terminal completeness only from the official read-back.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

import reconcile_mlb_prospective_backlog as base


VERSION = "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v3-official-status-authority"


def validate_lock_result(
    payload: Mapping[str, Any],
    official_status: Mapping[str, Any],
    slate_date: str,
) -> Dict[str, Any]:
    if payload.get("ok") is not True or payload.get("sport") != "mlb":
        raise base.ReconciliationError("lock_reconciliation_unhealthy")
    if str(payload.get("slateDateEt") or "") != slate_date:
        raise base.ReconciliationError("lock_reconciliation_slate_mismatch")

    progress = payload.get("perGameLockProgress") or {}
    if progress and not isinstance(progress, Mapping):
        raise base.ReconciliationError("lock_progress_invalid")
    if isinstance(progress, Mapping):
        missed = base._integer(progress.get("missedCount", 0), field="missed_count")
        due = base._integer(progress.get("dueMissingCount", 0), field="due_missing_count")
        if missed or due:
            raise base.ReconciliationError("prospective_slate_still_unresolved")

    official = base._validate_official_status(official_status, slate_date)
    game_count = official["gameCount"]
    canonical = official["lockedPredictionCount"]
    terminal = official["terminalNoPredictionCount"]
    locked_statuses = official["lockedStatusCount"]
    if locked_statuses != canonical + terminal:
        raise base.ReconciliationError("official_status_terminal_counts_inconsistent")
    if game_count and locked_statuses != game_count:
        raise base.ReconciliationError("official_status_terminal_coverage_incomplete")

    mutation_manifest = None
    mutation_canonical = None
    mutation_terminal = None
    mutation_outcomes = None
    if isinstance(progress, Mapping):
        for field, target in (
            ("manifestGameCount", "mutation_manifest"),
            ("canonicalCount", "mutation_canonical"),
            ("noPredictionDataCount", "mutation_terminal"),
            ("lockOutcomeCount", "mutation_outcomes"),
        ):
            value = progress.get(field)
            if value is None:
                continue
            parsed = base._integer(value, field=field)
            if target == "mutation_manifest":
                mutation_manifest = parsed
            elif target == "mutation_canonical":
                mutation_canonical = parsed
            elif target == "mutation_terminal":
                mutation_terminal = parsed
            else:
                mutation_outcomes = parsed

    return {
        "slateDateEt": slate_date,
        "manifestGameCount": game_count,
        "canonicalPredictionCount": canonical,
        "terminalNoPredictionCount": terminal,
        "lockOutcomeCount": locked_statuses,
        "offDay": game_count == 0,
        "officialStatusReadBound": True,
        "terminalCoverageAuthority": "official_exact_date_read_status",
        "mutationDiagnosticsProviderScoped": True,
        "mutationManifestGameCount": mutation_manifest,
        "mutationCanonicalPredictionCount": mutation_canonical,
        "mutationTerminalNoPredictionCount": mutation_terminal,
        "mutationLockOutcomeCount": mutation_outcomes,
    }


def main() -> int:
    base.VERSION = VERSION
    base.validate_lock_result = validate_lock_result
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
