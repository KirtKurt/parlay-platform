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

    terminal_repair = payload.get("missedLockTerminalReconciliation")
    terminal_repair_complete = False
    if terminal_repair is not None:
        if not isinstance(terminal_repair, Mapping):
            raise base.ReconciliationError(
                "protected_terminal_reconciliation_invalid"
            )
        repair_progress = terminal_repair.get("progressAfter")
        if not isinstance(repair_progress, Mapping):
            raise base.ReconciliationError(
                "protected_terminal_reconciliation_invalid"
            )
        reconciled = base._integer(
            terminal_repair.get("reconciledCount", 0),
            field="terminal_repair_reconciled_count",
        )
        remaining = base._integer(
            terminal_repair.get(
                "remainingMissedCount",
                repair_progress.get("missedCount", 0),
            ),
            field="terminal_repair_remaining_missed_count",
        )
        due_after = base._integer(
            repair_progress.get("dueMissingCount", 0),
            field="terminal_repair_due_missing_count",
        )
        unresolved = terminal_repair.get("unresolved") or []
        if not isinstance(unresolved, list):
            raise base.ReconciliationError(
                "protected_terminal_reconciliation_invalid"
            )
        cached_idempotent = bool(
            str(payload.get("reason") or "")
            == "POST_WINDOW_TERMINAL_STATUS_ALREADY_RECONCILED"
            and reconciled == 0
        )
        if (
            terminal_repair.get("ok") is not True
            or terminal_repair.get("postStartPredictionCreationAllowed") is not False
            or (reconciled <= 0 and not cached_idempotent)
        ):
            raise base.ReconciliationError(
                "protected_terminal_reconciliation_unhealthy"
            )
        if remaining or due_after or unresolved:
            raise base.ReconciliationError("prospective_slate_still_unresolved")
        terminal_repair_complete = True

    for field in (
        "missedGameCount",
        "missedCount",
        "dueMissingGameCount",
        "dueMissingCount",
    ):
        if field not in payload:
            continue
        if (
            base._integer(payload.get(field), field=field)
            and not terminal_repair_complete
        ):
            raise base.ReconciliationError("prospective_slate_still_unresolved")

    progress = payload.get("perGameLockProgress") or {}
    if progress and not isinstance(progress, Mapping):
        raise base.ReconciliationError("lock_progress_invalid")
    if isinstance(progress, Mapping):
        missed = base._integer(progress.get("missedCount", 0), field="missed_count")
        due = base._integer(progress.get("dueMissingCount", 0), field="due_missing_count")
        if (missed or due) and not terminal_repair_complete:
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
        "protectedTerminalReconciliationVerified": terminal_repair_complete,
    }


def main() -> int:
    base.VERSION = VERSION
    base.validate_lock_result = validate_lock_result
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
