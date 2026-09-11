from __future__ import annotations

from typing import Any, Mapping


VERSION = "MLB-FUNDAMENTALS-LOCK-AUTHORITY-v1-canonical-read-only"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def canonical_lock_at(row: Mapping[str, Any], scoring_bridge: Any) -> Any:
    """Resolve only lock authorities already accepted by MLB production contracts.

    This adds no new lock source. It aligns the fundamentals shadow evaluator
    with the same persisted lock authorities already used by the MLB clean
    cohort and production verifier. Missing evidence remains missing and all
    downstream chronology checks remain fail-closed.
    """
    vector = scoring_bridge._vector(row)
    audit = _mapping(row.get("lockedCardAudit"))
    slate_lock = _mapping(row.get("slatePredictionLock"))
    last_gate = _mapping(row.get("lastPossiblePredictionGate"))
    return (
        vector.get("lockAtUtc")
        or audit.get("lockAtUtc")
        or slate_lock.get("lockAtUtc")
        or last_gate.get("lockAtUtc")
        or row.get("lockAtUtc")
        or row.get("lockedAtUtc")
        or row.get("lockedAt")
    )


def install(scoring_bridge: Any) -> Any:
    if getattr(
        scoring_bridge,
        "_INQSI_MLB_FUNDAMENTALS_CANONICAL_LOCK_AUTHORITY_V1_INSTALLED",
        False,
    ):
        return scoring_bridge

    def _canonical_lock_at(row: Mapping[str, Any]) -> Any:
        return canonical_lock_at(row, scoring_bridge)

    scoring_bridge._lock_at = _canonical_lock_at
    scoring_bridge.MLB_FUNDAMENTALS_LOCK_AUTHORITY_VERSION = VERSION
    scoring_bridge._INQSI_MLB_FUNDAMENTALS_CANONICAL_LOCK_AUTHORITY_V1_INSTALLED = True
    return scoring_bridge
