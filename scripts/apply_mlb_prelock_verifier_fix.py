#!/usr/bin/env python3
"""Repair MLB production-verifier lock timing semantics.

Before any game reaches its own T-minus-45 cutoff, an empty lock-status payload is
normal and must not make an otherwise healthy pull/prediction verification red.
Once a cutoff is due, the verifier remains fail-closed and requires that exact
game's terminal status and immutable locked vector. Full-roster equality remains
required after the final cutoff or an explicitly terminal slate.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "hello_world" / "mlb_production_verifier.py"
CONTRACT = ROOT / ".github" / "workflows" / "mlb-production-source-contract.yml"


NEW_PROGRESS = r'''def _official_lock_times(games: Iterable[Dict[str, Any]]) -> Dict[str, datetime]:
    """Return exact official T-minus-45 cutoffs keyed by canonical identity.

    The schedule, not an optional status row, determines whether a lock is due.
    An incomplete map is returned as empty so legacy test fixtures and historical
    payloads continue through the status-row fallback instead of inventing time.
    """

    source = [game for game in games if isinstance(game, dict)]
    schedule: Dict[str, datetime] = {}
    for game in source:
        identity = _row_identity(game)
        start = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
        if not identity or not start or identity in schedule:
            return {}
        schedule[identity] = start - timedelta(minutes=45)
    return schedule if source and len(schedule) == len(source) else {}


def _per_game_lock_progress(
    lock_status: Dict[str, Any],
    *,
    checked_at: datetime,
    expected_identities: Iterable[str],
    expected_lock_times: Optional[Dict[str, datetime]] = None,
) -> Dict[str, Any]:
    expected = {
        _identity_from_value(value) for value in expected_identities if value
    }
    expected_count = len(expected)
    schedule: Dict[str, datetime] = {}
    for raw_identity, raw_lock_at in (expected_lock_times or {}).items():
        identity = _identity_from_value(raw_identity)
        lock_at = raw_lock_at if isinstance(raw_lock_at, datetime) else _parse_dt(raw_lock_at)
        if identity in expected and lock_at:
            schedule[identity] = lock_at.astimezone(timezone.utc)
    schedule_complete = bool(expected_count and set(schedule) == expected)

    raw_statuses = lock_status.get("perGameStatus") or []
    statuses = [row for row in raw_statuses if isinstance(row, dict)]
    invalid: List[Dict[str, Any]] = []
    seen_game_ids: set[str] = set()
    valid_rows: Dict[str, Dict[str, Any]] = {}
    fallback_due: set[str] = set()
    fallback_pending: set[str] = set()
    fallback_cutoffs: List[datetime] = []

    for row in statuses:
        game_id = _row_identity(row)
        reported_lock_at = _parse_dt(row.get("scheduledLockAtUtc"))
        validation_errors: List[str] = []
        if not game_id:
            validation_errors.append("game_identity_missing")
        elif game_id in seen_game_ids:
            validation_errors.append("duplicate_game_identity")
        else:
            seen_game_ids.add(game_id)
        if not reported_lock_at:
            validation_errors.append("scheduled_lock_at_missing_or_invalid")
        if (
            schedule_complete
            and game_id in schedule
            and reported_lock_at
            and reported_lock_at != schedule[game_id]
        ):
            validation_errors.append("scheduled_lock_at_official_mismatch")
        compact = {
            "gameId": game_id or None,
            "scheduledLockAtUtc": reported_lock_at.isoformat() if reported_lock_at else None,
            "lockStatus": row.get("lockStatus") or row.get("state"),
            "lockOutcomeRecorded": row.get("lockOutcomeRecorded") is True,
            "lockedPrediction": row.get("lockedPrediction") is True,
            "validationErrors": validation_errors,
        }
        if validation_errors:
            invalid.append(compact)
            continue
        valid_rows[game_id] = compact
        if not schedule_complete:
            fallback_cutoffs.append(reported_lock_at)
            if reported_lock_at <= checked_at:
                fallback_due.add(game_id)
            else:
                fallback_pending.add(game_id)

    if schedule_complete:
        due_ids = {identity for identity, lock_at in schedule.items() if lock_at <= checked_at}
        pending_ids = expected - due_ids
        final_cutoff = max(schedule.values()) if schedule else None
    else:
        due_ids = fallback_due
        pending_ids = fallback_pending
        final_cutoff = max(fallback_cutoffs) if fallback_cutoffs else None

    due_rows: List[Dict[str, Any]] = []
    missing_due: List[Dict[str, Any]] = []
    for identity in sorted(due_ids):
        row = valid_rows.get(identity)
        if row is None:
            lock_at = schedule.get(identity)
            row = {
                "gameId": identity,
                "scheduledLockAtUtc": lock_at.isoformat() if lock_at else None,
                "lockStatus": None,
                "lockOutcomeRecorded": False,
                "lockedPrediction": False,
                "validationErrors": ["due_status_row_missing"],
            }
        due_rows.append(row)
        if row.get("lockOutcomeRecorded") is not True:
            missing_due.append(row)

    observed = set(valid_rows)
    missing_identities = sorted(expected - observed)
    unexpected_identities = sorted(observed - expected)
    status_complete = bool(
        expected_count > 0
        and len(statuses) == expected_count
        and len(valid_rows) == expected_count
        and not invalid
        and not missing_identities
        and not unexpected_identities
    )
    final_cutoff_reached = bool(final_cutoff and checked_at >= final_cutoff)
    terminal_slate = bool(
        lock_status.get("lockStatusComplete") is True
        or lock_status.get("dailyCardComplete") is True
    )
    return {
        "statusComplete": status_complete,
        "statusCount": len(statuses),
        "uniqueGameCount": len(observed),
        "gameIdentities": sorted(observed),
        "expectedGameIdentities": sorted(expected),
        "missingGameIdentities": missing_identities,
        "unexpectedGameIdentities": unexpected_identities,
        "invalidStatusCount": len(invalid),
        "invalidStatuses": invalid,
        "dueGameCount": len(due_ids),
        "dueGameIdentities": sorted(due_ids),
        "dueTerminalGameCount": len(due_ids) - len(missing_due),
        "dueMissingGameCount": len(missing_due),
        "dueMissingGames": missing_due,
        "pendingGameCount": len(pending_ids),
        "pendingGameIdentities": sorted(pending_ids),
        "finalPerGameCutoffAtUtc": final_cutoff.isoformat() if final_cutoff else None,
        "finalPerGameCutoffReached": final_cutoff_reached,
        "terminalSlate": terminal_slate,
        "fullSlateVectorEvaluationDue": bool(terminal_slate or final_cutoff_reached),
        "officialLockScheduleComplete": schedule_complete,
        "lockTimingAuthority": (
            "official_schedule_tminus45" if schedule_complete else "status_payload_fallback"
        ),
        "policy": (
            "Only games whose official scheduled T-45 cutoff is at or before "
            "checkedAt must have a terminal lock outcome. An empty status payload "
            "before the first cutoff is healthy. Full-slate status/vector coverage "
            "is required only after the final cutoff or an explicitly terminal slate."
        ),
    }
'''


NEW_LOCK_BLOCK = r'''    lock_official_count = lock_status.get("officialScheduleGameCount")
    lock_manifest_count = lock_status.get("manifestGameCount")
    lock_verified_count = lock_status.get("verifiedFullSlateGameCount")
    lock_authority_observed_valid = bool(
        official_authority_valid
        and lock_status.get("officialScheduleBacked") is True
        and _exact_count(lock_status.get("gameCount"), official_count)
        and _exact_count(lock_manifest_count, official_count)
        and _exact_count(lock_verified_count, official_count)
        and _exact_count(lock_official_count, official_count)
        and str(lock_status.get("officialScheduleAuthorityFingerprint") or "")
        == str(roster.get("officialScheduleAuthorityFingerprint") or "")
        and bool(lock_status.get("officialScheduleAuthorityFingerprint"))
    )

    game_count = _count_or_zero(predictions.get("gameCount"))
    prediction_count = _count_or_zero(predictions.get("count"))
    pull_count = len(pulls)
    expected_slate_count = official_count
    official_lock_times = _official_lock_times(official_games)
    per_game = _per_game_lock_progress(
        lock_status,
        checked_at=checked_at,
        expected_identities=official_identities,
        expected_lock_times=official_lock_times,
    )
    lock_status_rows = lock_status.get("perGameStatus") or []
    lock_evidence_present = bool(
        (isinstance(lock_status_rows, list) and lock_status_rows)
        or lock_status.get("officialScheduleBacked") is True
        or lock_status.get("officialScheduleAuthorityFingerprint")
        or lock_status.get("locked") is True
        or lock_status.get("lockStatusComplete") is True
        or lock_status.get("dailyCardComplete") is True
    )
    lock_evidence_required = bool(
        per_game["dueGameCount"] > 0
        or per_game["fullSlateVectorEvaluationDue"] is True
        or lock_evidence_present
    )
    lock_authority_valid = bool(
        not lock_evidence_required or lock_authority_observed_valid
    )
    if lock_evidence_required and lock_status.get("ok") is not True:
        blockers.append("LOCK_STATUS_FAILED")
    if lock_evidence_required and not lock_authority_observed_valid:
        blockers.append("LOCK_STATUS_ROSTER_AUTHORITY_MISMATCH")

    malformed_status_membership = bool(
        per_game["invalidStatusCount"]
        or per_game["unexpectedGameIdentities"]
    )
    full_status_membership_required = bool(
        per_game["fullSlateVectorEvaluationDue"] is True
        or (lock_evidence_present and per_game["dueGameCount"] == 0)
    )
    if malformed_status_membership:
        blockers.append("PER_GAME_LOCK_STATUS_MISSING_OR_INVALID")
        blockers.append("PER_GAME_LOCK_ROSTER_MEMBERSHIP_MISMATCH")
    elif full_status_membership_required and per_game["statusComplete"] is not True:
        blockers.append("PER_GAME_LOCK_STATUS_MISSING_OR_INVALID")
        blockers.append("PER_GAME_LOCK_ROSTER_MEMBERSHIP_MISMATCH")

    lock_membership_valid = bool(
        not lock_evidence_required
        or (
            lock_authority_observed_valid
            and not malformed_status_membership
            and (
                per_game["statusComplete"] is True
                if full_status_membership_required
                else per_game["dueMissingGameCount"] == 0
            )
        )
    )
    integrity = _locked_row_integrity(
        slate_date,
        official_identities,
        per_game["dueGameIdentities"],
        per_game["fullSlateVectorEvaluationDue"],
    )

    if not integrity.get("authoritySafe"):
        blockers.append("CANONICAL_LOCK_ROSTER_MEMBERSHIP_MISMATCH")
    if not integrity.get("dueCoverageComplete"):
        blockers.append("LOCKED_ROWS_MISSING_VALID_FROZEN_FINGERPRINTS")
'''


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"expected one anchor in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    text = VERIFIER.read_text(encoding="utf-8")
    text = text.replace(
        "from datetime import datetime, timezone",
        "from datetime import datetime, timedelta, timezone",
        1,
    )
    start = text.index("def _per_game_lock_progress(")
    end = text.index("\ndef _verification_payload(", start)
    text = text[:start] + NEW_PROGRESS + text[end:]

    block_start = text.index(
        '    lock_official_count = lock_status.get("officialScheduleGameCount")'
    )
    block_end = text.index('    if mode in {"continuous", "ingest"}:', block_start)
    text = text[:block_start] + NEW_LOCK_BLOCK + "\n" + text[block_end:]

    old_lock_status_report = '''            "lockStatus": {
                "ok": lock_status.get("ok") is True and lock_authority_valid,
                "reportedGameCount": lock_status.get("gameCount"),
                "reportedManifestGameCount": lock_manifest_count,
                "reportedVerifiedFullSlateGameCount": lock_verified_count,
                "reportedOfficialScheduleGameCount": lock_official_count,
                "officialScheduleBacked": lock_status.get("officialScheduleBacked") is True,
                "officialScheduleAuthorityFingerprint": lock_status.get(
                    "officialScheduleAuthorityFingerprint"
                ),
            },
'''
    new_lock_status_report = '''            "lockStatus": {
                "ok": lock_authority_valid and lock_membership_valid,
                "required": lock_evidence_required,
                "evidencePresent": lock_evidence_present,
                "observedAuthorityValid": lock_authority_observed_valid,
                "membershipValid": lock_membership_valid,
                "reportedGameCount": lock_status.get("gameCount"),
                "reportedManifestGameCount": lock_manifest_count,
                "reportedVerifiedFullSlateGameCount": lock_verified_count,
                "reportedOfficialScheduleGameCount": lock_official_count,
                "officialScheduleBacked": lock_status.get("officialScheduleBacked") is True,
                "officialScheduleAuthorityFingerprint": lock_status.get(
                    "officialScheduleAuthorityFingerprint"
                ),
            },
'''
    if new_lock_status_report not in text:
        if old_lock_status_report not in text:
            raise SystemExit("lock status report anchor missing")
        text = text.replace(old_lock_status_report, new_lock_status_report, 1)

    old_identity = '''            "identitySetsEqual": bool(
                official_authority_valid
                and prediction_identity_valid
                and lock_authority_valid
                and per_game["statusComplete"] is True
                and per_game["gameIdentities"] == official_identities
                and integrity.get("authoritySafe") is True
            ),
'''
    new_identity = '''            "identitySetsEqual": bool(
                official_authority_valid
                and prediction_identity_valid
                and lock_authority_valid
                and lock_membership_valid
                and integrity.get("authoritySafe") is True
            ),
'''
    if new_identity not in text:
        if old_identity not in text:
            raise SystemExit("identity-set report anchor missing")
        text = text.replace(old_identity, new_identity, 1)

    old_lock_report = '''            "expectedSlateGameCount": expected_slate_count,
            "expectedLockedGameCount": per_game["dueGameCount"],
            "perGameProgress": per_game,
'''
    new_lock_report = '''            "expectedSlateGameCount": expected_slate_count,
            "expectedLockedGameCount": per_game["dueGameCount"],
            "lockEvidenceRequired": lock_evidence_required,
            "lockEvidencePresent": lock_evidence_present,
            "perGameProgress": per_game,
'''
    if new_lock_report not in text:
        if old_lock_report not in text:
            raise SystemExit("lock report anchor missing")
        text = text.replace(old_lock_report, new_lock_report, 1)

    VERIFIER.write_text(text, encoding="utf-8")

    replace_once(
        CONTRACT,
        "            tests/unit/test_mlb_production_verifier_per_game.py \\\n",
        "            tests/unit/test_mlb_production_verifier_per_game.py \\\n"
        "            tests/unit/test_mlb_production_verifier_prelock_quiet.py \\\n",
    )
    replace_once(
        CONTRACT,
        "          python -m py_compile hello_world/mlb_probability_actionability_guard.py\n",
        "          python -m py_compile hello_world/mlb_probability_actionability_guard.py\n"
        "          python -m py_compile hello_world/mlb_production_verifier.py\n",
    )
    print("MLB pre-lock verifier repair applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
