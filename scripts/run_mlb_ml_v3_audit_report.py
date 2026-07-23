#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import copy
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

REPORT_PATH = ROOT / "runtime_reports" / "mlb_ml_v3_audit_execution_latest.json"
V2_TRAINING_STATUS_MAX_AGE_MINUTES = 8.0 * 60.0
V2_SELECTION_CAPTURE_STATUS_MAX_AGE_MINUTES = 45.0
PRODUCTION_STACK_NAME = "parlay-platform-dev"
TRAINER_LOGICAL_ID = "MLBMLTrainingFunction"
PRODUCTION_ACCEPTANCE_SCOPE_VERSION = (
    "MLB-PRODUCTION-ACCEPTANCE-SCOPE-v3-canonical-finalized-outcomes"
)
PRODUCTION_ACCEPTANCE_SCOPE_FINGERPRINT_VERSION = (
    "MLB-PRODUCTION-ACCEPTANCE-SCOPE-SHA256-v3"
)
PRODUCTION_ACCEPTANCE_ROWS_FINGERPRINT_VERSION = (
    "MLB-PRODUCTION-ACCEPTANCE-RAW-ROWS-SHA256-v1"
)
CANONICAL_FINALIZED_SLATE_EVIDENCE_VERSION = (
    "MLB-CANONICAL-FINALIZED-SLATE-EVIDENCE-v2-official-final-outcomes"
)
CANONICAL_FINALIZED_SLATE_EVIDENCE_FINGERPRINT_VERSION = (
    "MLB-CANONICAL-FINALIZED-SLATE-EVIDENCE-SHA256-v2"
)
LOCK_MINUTES_BEFORE_GAME = 45


def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _scope_fingerprint(value: dict) -> str:
    material = {
        key: item
        for key, item in value.items()
        if key != "scopeFingerprint"
    }
    encoded = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rows_fingerprint(rows: list[Any]) -> str:
    return _canonical_fingerprint(rows)


def _evidence_fingerprint(value: dict) -> str:
    return _canonical_fingerprint(
        {
            key: item
            for key, item in value.items()
            if key != "evidenceFingerprint"
        }
    )


def _pct(numerator: int, denominator: int):
    return round(numerator / denominator * 100.0, 2) if denominator else None


def _official_game_pk_values(row: dict) -> list[str]:
    authority = row.get("canonicalLockAuthority") or {}
    crosswalk = authority.get("providerAliasCrosswalk") or {}
    values = set()
    for container in (row, authority, crosswalk):
        if not isinstance(container, dict):
            continue
        for key in ("officialGamePk", "official_game_pk"):
            value = str(container.get(key) or "").strip()
            if value:
                values.add(value)
        for key in (
            "officialGameId",
            "official_game_id",
            "gameId",
            "game_id",
            "gameIdentity",
            "canonicalLockedGameId",
        ):
            value = str(container.get(key) or "").strip()
            if value.startswith("mlb_statsapi:"):
                values.add(value.split(":", 1)[1])
    return sorted(value for value in values if value)


def _canonical_schedule_game_evidence(game: dict) -> dict:
    return {
        "officialGamePk": str(game.get("officialGamePk") or "").strip(),
        "officialDate": str(game.get("officialDate") or "").strip(),
        "gameDate": game.get("gameDate"),
        "awayTeam": game.get("awayTeam"),
        "homeTeam": game.get("homeTeam"),
        "awayScore": game.get("awayScore"),
        "homeScore": game.get("homeScore"),
        "winner": game.get("winner"),
        "completed": game.get("completed") is True,
        "officialStatus": dict(game.get("officialStatus") or {}),
        "sourcePayloadFingerprint": game.get("sourcePayloadFingerprint"),
    }


def _official_finalized_game_outcome_errors(
    game: Any,
    *,
    expected_slate_date_et: Optional[str] = None,
    expected_official_game_pk: Optional[str] = None,
) -> list[str]:
    """Validate one exact MLB Stats API FINAL result and its source digest."""
    import inqsi_pull_history as history
    import mlb_canonical_final_labels_v1 as canonical_labels
    import mlb_rolling_24h_audit as rolling_audit

    if not isinstance(game, dict):
        return ["official_finalized_game_outcome_missing"]
    errors = []
    official_pk = str(game.get("officialGamePk") or "").strip()
    official_date = str(game.get("officialDate") or "").strip()
    away_team = str(game.get("awayTeam") or "").strip()
    home_team = str(game.get("homeTeam") or "").strip()
    away_score = game.get("awayScore")
    home_score = game.get("homeScore")
    winner = str(game.get("winner") or "").strip()
    if not re.fullmatch(r"[0-9]+", official_pk):
        errors.append("official_finalized_game_pk_invalid")
    if (
        expected_official_game_pk is not None
        and official_pk != str(expected_official_game_pk)
    ):
        errors.append("official_finalized_game_pk_mismatch")
    if not official_date or (
        expected_slate_date_et is not None
        and official_date != str(expected_slate_date_et)
    ):
        errors.append("official_finalized_game_date_mismatch")
    game_date = _parse_dt(game.get("gameDate"))
    if game_date is None:
        errors.append("official_finalized_game_commence_time_invalid")
    elif official_date and (
        game_date.astimezone(canonical_labels.SLATE_TZ).date().isoformat()
        != official_date
    ):
        errors.append("official_finalized_game_commence_date_mismatch")
    if (
        not away_team
        or not home_team
        or rolling_audit.normalize_team(away_team)
        == rolling_audit.normalize_team(home_team)
    ):
        errors.append("official_finalized_game_team_identity_invalid")
    if (
        isinstance(away_score, bool)
        or not isinstance(away_score, int)
        or away_score < 0
        or isinstance(home_score, bool)
        or not isinstance(home_score, int)
        or home_score < 0
        or away_score == home_score
    ):
        errors.append("official_finalized_game_score_invalid")
        expected_winner = None
    else:
        expected_winner = home_team if home_score > away_score else away_team
    if (
        game.get("completed") is not True
        or str((game.get("officialStatus") or {}).get("abstractGameState") or "")
        .strip()
        .upper()
        != "FINAL"
    ):
        errors.append("official_finalized_game_status_invalid")
    if (
        not winner
        or expected_winner is None
        or rolling_audit.normalize_team(winner)
        != rolling_audit.normalize_team(expected_winner)
    ):
        errors.append("official_finalized_game_winner_invalid")
    expected_source_fingerprint = history.canonical_payload_fingerprint(
        canonical_labels._official_final_evidence(game)
    )
    if (
        not re.fullmatch(
            r"[0-9a-f]{64}", str(game.get("sourcePayloadFingerprint") or "")
        )
        or game.get("sourcePayloadFingerprint") != expected_source_fingerprint
    ):
        errors.append("official_finalized_game_source_fingerprint_invalid")
    return sorted(set(errors))


def _audit_row_official_outcome_binding_errors(
    row: Any,
    official_game: Any,
    *,
    expected_slate_date_et: Optional[str] = None,
    expected_official_game_pk: Optional[str] = None,
) -> list[str]:
    """Bind provider-rendered settlement fields to one official FINAL game."""
    import mlb_rolling_24h_audit as rolling_audit

    errors = _official_finalized_game_outcome_errors(
        official_game,
        expected_slate_date_et=expected_slate_date_et,
        expected_official_game_pk=expected_official_game_pk,
    )
    if not isinstance(row, dict) or not isinstance(official_game, dict):
        return sorted(set(errors + ["official_finalized_game_outcome_authority_missing"]))
    for side in ("away", "home"):
        row_team = str(row.get(f"{side}Team") or "").strip()
        official_team = str(official_game.get(f"{side}Team") or "").strip()
        if (
            not row_team
            or not official_team
            or rolling_audit.normalize_team(row_team)
            != rolling_audit.normalize_team(official_team)
        ):
            errors.append("official_finalized_game_team_identity_mismatch")
    for side in ("away", "home"):
        row_score = row.get(f"{side}Score")
        official_score = official_game.get(f"{side}Score")
        if (
            isinstance(row_score, bool)
            or not isinstance(row_score, int)
            or row_score != official_score
        ):
            errors.append("official_finalized_game_score_mismatch")
    official_winner = str(official_game.get("winner") or "").strip()
    row_winner = str(row.get("winner") or "").strip()
    if (
        not row_winner
        or not official_winner
        or rolling_audit.normalize_team(row_winner)
        != rolling_audit.normalize_team(official_winner)
    ):
        errors.append("official_finalized_game_winner_mismatch")
    predicted_winner = str(row.get("predictedWinner") or "").strip()
    if not predicted_winner:
        errors.append("predicted_winner_missing_or_invalid")
    elif official_winner:
        derived_correct = (
            rolling_audit.normalize_team(predicted_winner)
            == rolling_audit.normalize_team(official_winner)
        )
        if (
            not isinstance(row.get("correct"), bool)
            or row.get("correct") is not derived_correct
        ):
            errors.append("official_correctness_derivation_mismatch")
    return sorted(set(errors))


def _canonical_finalized_slate_evidence(
    report: dict,
    *,
    now_utc: Optional[datetime] = None,
    official_schedule_loader: Optional[Callable[[str], dict]] = None,
) -> dict:
    """Read exact-date canonical schedules for every ET date in the audit window.

    Only a nonempty, entirely FINAL slate whose games all belong to the rolling
    window and whose T-45 timestamps are at or after the r3 cutoff becomes an
    acceptance authority.  In-progress, empty, or aged-out dates remain visible
    diagnostics but cannot prove a full slate.
    """
    import mlb_canonical_final_labels_v1 as canonical_labels
    import mlb_ml_experiment_v2 as experiment

    checked_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = _parse_dt(experiment.PRODUCTION_RELEASE_CUTOFF_UTC)
    window_hours_raw = report.get("windowHours", 24)
    try:
        window_hours = int(window_hours_raw)
    except Exception:
        window_hours = 24
    if window_hours <= 0:
        window_hours = 24
    window_start = checked_at - timedelta(hours=window_hours)
    loader = official_schedule_loader or canonical_labels.fetch_official_schedule

    candidate_dates = set()
    if cutoff is not None and checked_at >= cutoff:
        start = max(window_start, cutoff)
        cursor = start.astimezone(canonical_labels.SLATE_TZ).date()
        end = checked_at.astimezone(canonical_labels.SLATE_TZ).date()
        while cursor <= end:
            candidate_dates.add(cursor.isoformat())
            cursor += timedelta(days=1)
    for row in report.get("rows") or []:
        if not isinstance(row, dict):
            continue
        commence = _parse_dt(row.get("commenceTime"))
        if commence is not None:
            scheduled_lock = commence - timedelta(
                minutes=LOCK_MINUTES_BEFORE_GAME
            )
            if cutoff is None or scheduled_lock >= cutoff:
                candidate_dates.add(
                    commence.astimezone(canonical_labels.SLATE_TZ)
                    .date()
                    .isoformat()
                )

    checks = []
    errors = []
    finalized_slates = {}
    for slate_date in sorted(candidate_dates):
        try:
            schedule = loader(slate_date)
            if not isinstance(schedule, dict) or schedule.get("ok") is not True:
                raise RuntimeError("canonical schedule result missing or invalid")
            if str(schedule.get("slateDateEt") or "") != slate_date:
                raise RuntimeError("canonical schedule date mismatch")
            if (
                schedule.get("source") != canonical_labels.SOURCE
                or schedule.get("sourceUrl")
                != canonical_labels.official_finals_url(slate_date)
            ):
                raise RuntimeError("canonical schedule source authority mismatch")
            games = schedule.get("games")
            if not isinstance(games, list):
                raise RuntimeError("canonical schedule games missing or invalid")
            count = schedule.get("officialGameCount")
            final_count = schedule.get("officialFinalCount")
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or count != len(games)
                or isinstance(final_count, bool)
                or not isinstance(final_count, int)
                or final_count < 0
                or final_count > count
            ):
                raise RuntimeError("canonical schedule counts invalid")
            canonical_games = [
                _canonical_schedule_game_evidence(game)
                for game in games
                if isinstance(game, dict)
            ]
            if len(canonical_games) != count:
                raise RuntimeError("canonical schedule game row invalid")
            official_game_pks = [
                game["officialGamePk"] for game in canonical_games
            ]
            if (
                any(not re.fullmatch(r"[0-9]+", value) for value in official_game_pks)
                or len(set(official_game_pks)) != len(official_game_pks)
            ):
                raise RuntimeError("canonical schedule gamePk set invalid")
            commence_times = [_parse_dt(game.get("gameDate")) for game in canonical_games]
            if any(value is None for value in commence_times):
                raise RuntimeError("canonical schedule gameDate missing or invalid")
            all_final = bool(
                count > 0
                and final_count == count
                and all(game.get("completed") is True for game in canonical_games)
            )
            if all_final:
                outcome_errors = [
                    error
                    for game in canonical_games
                    for error in _official_finalized_game_outcome_errors(
                        game,
                        expected_slate_date_et=slate_date,
                        expected_official_game_pk=str(
                            game.get("officialGamePk") or ""
                        ),
                    )
                ]
                if outcome_errors:
                    raise RuntimeError(
                        "canonical finalized game outcome invalid: "
                        + ",".join(sorted(set(outcome_errors)))
                    )
            full_slate_within_window = bool(
                all_final
                and all(
                    window_start <= value <= checked_at
                    for value in commence_times
                    if value is not None
                )
            )
            full_slate_after_cutoff = bool(
                cutoff is not None
                and all_final
                and all(
                    value - timedelta(minutes=LOCK_MINUTES_BEFORE_GAME) >= cutoff
                    for value in commence_times
                    if value is not None
                )
            )
            eligible = bool(
                all_final
                and full_slate_within_window
                and full_slate_after_cutoff
            )
            check = {
                "slateDateEt": slate_date,
                "officialGameCount": count,
                "officialFinalCount": final_count,
                "slateFinalized": all_final,
                "fullSlateWithinRollingWindow": full_slate_within_window,
                "fullSlateAfterReleaseCutoff": full_slate_after_cutoff,
                "acceptanceAuthorityEligible": eligible,
                "scheduleSource": schedule.get("source"),
                "scheduleSourceUrl": schedule.get("sourceUrl"),
                "error": None,
            }
            checks.append(check)
            if eligible:
                authority = experiment.build_official_finalized_slate_authority(
                    slate_date_et=slate_date,
                    official_game_pks=official_game_pks,
                    schedule_source=str(schedule.get("source") or ""),
                    schedule_source_url=str(schedule.get("sourceUrl") or ""),
                )
                finalized_slates[slate_date] = {
                    "authority": authority,
                    "games": canonical_games,
                }
        except Exception as exc:
            error = {
                "slateDateEt": slate_date,
                "errorType": type(exc).__name__,
                "error": str(exc),
            }
            errors.append(error)
            checks.append(
                {
                    "slateDateEt": slate_date,
                    "officialGameCount": None,
                    "officialFinalCount": None,
                    "slateFinalized": False,
                    "fullSlateWithinRollingWindow": False,
                    "fullSlateAfterReleaseCutoff": False,
                    "acceptanceAuthorityEligible": False,
                    "scheduleSource": None,
                    "scheduleSourceUrl": None,
                    "error": error,
                }
            )

    evidence = {
        "version": CANONICAL_FINALIZED_SLATE_EVIDENCE_VERSION,
        "checkedAtUtc": checked_at.isoformat(),
        "windowHours": window_hours,
        "windowStartUtc": window_start.isoformat(),
        "releaseCutoffUtc": cutoff.isoformat() if cutoff else None,
        "candidateSlateDates": sorted(candidate_dates),
        "checks": checks,
        "finalizedSlates": finalized_slates,
        "errors": errors,
        "evidenceFingerprintVersion": (
            CANONICAL_FINALIZED_SLATE_EVIDENCE_FINGERPRINT_VERSION
        ),
    }
    evidence["evidenceFingerprint"] = _evidence_fingerprint(evidence)
    return evidence


def _validated_finalized_slate_evidence(value: Any) -> tuple[dict, list[str]]:
    import mlb_canonical_final_labels_v1 as canonical_labels
    import mlb_ml_experiment_v2 as experiment

    errors = []
    if not isinstance(value, dict):
        return {}, ["canonical_finalized_slate_evidence_missing"]
    if value.get("version") != CANONICAL_FINALIZED_SLATE_EVIDENCE_VERSION:
        errors.append("canonical_finalized_slate_evidence_version_mismatch")
    if (
        value.get("evidenceFingerprintVersion")
        != CANONICAL_FINALIZED_SLATE_EVIDENCE_FINGERPRINT_VERSION
    ):
        errors.append("canonical_finalized_slate_evidence_fingerprint_version_mismatch")
    if value.get("evidenceFingerprint") != _evidence_fingerprint(value):
        errors.append("canonical_finalized_slate_evidence_fingerprint_mismatch")
    if value.get("releaseCutoffUtc") != experiment.PRODUCTION_RELEASE_CUTOFF_UTC:
        errors.append("canonical_finalized_slate_evidence_cutoff_mismatch")
    checked_at = _parse_dt(value.get("checkedAtUtc"))
    window_start = _parse_dt(value.get("windowStartUtc"))
    if checked_at is None or window_start is None or window_start >= checked_at:
        errors.append("canonical_finalized_slate_evidence_window_invalid")
    candidate_dates = value.get("candidateSlateDates")
    checks = value.get("checks")
    raw_errors = value.get("errors")
    finalized = value.get("finalizedSlates")
    if not (
        isinstance(candidate_dates, list)
        and candidate_dates == sorted(set(candidate_dates))
        and isinstance(checks, list)
        and isinstance(raw_errors, list)
        and isinstance(finalized, dict)
    ):
        errors.append("canonical_finalized_slate_evidence_shape_invalid")
        return {}, sorted(set(errors))
    if raw_errors:
        errors.append("canonical_finalized_slate_evidence_fetch_failed")
    check_dates = [
        str(check.get("slateDateEt") or "")
        for check in checks
        if isinstance(check, dict)
    ]
    if (
        len(check_dates) != len(checks)
        or sorted(check_dates) != candidate_dates
        or len(set(check_dates)) != len(check_dates)
    ):
        errors.append("canonical_finalized_slate_evidence_check_set_mismatch")
    eligible_check_dates = sorted(
        str(check.get("slateDateEt") or "")
        for check in checks
        if isinstance(check, dict)
        and check.get("acceptanceAuthorityEligible") is True
    )
    if eligible_check_dates != sorted(finalized):
        errors.append("canonical_finalized_slate_evidence_authority_set_mismatch")

    validated = {}
    for slate_date, slate in sorted(finalized.items()):
        if not isinstance(slate, dict):
            errors.append("canonical_finalized_slate_record_invalid")
            continue
        authority = slate.get("authority")
        authority_errors = experiment.official_finalized_slate_authority_errors(
            authority,
            expected_slate_date_et=slate_date,
        )
        if (
            (authority or {}).get("scheduleSource") != canonical_labels.SOURCE
            or (authority or {}).get("scheduleSourceUrl")
            != canonical_labels.official_finals_url(slate_date)
        ):
            authority_errors.append(
                "official_finalized_slate_source_authority_mismatch"
            )
        errors.extend(authority_errors)
        games = slate.get("games")
        if not isinstance(games, list):
            errors.append("canonical_finalized_slate_games_missing")
            continue
        expected_pks = list((authority or {}).get("officialGamePks") or [])
        matching_check = next(
            (
                check
                for check in checks
                if isinstance(check, dict)
                and str(check.get("slateDateEt") or "") == slate_date
            ),
            {},
        )
        if not (
            matching_check.get("acceptanceAuthorityEligible") is True
            and matching_check.get("slateFinalized") is True
            and matching_check.get("fullSlateWithinRollingWindow") is True
            and matching_check.get("fullSlateAfterReleaseCutoff") is True
            and matching_check.get("officialGameCount") == len(expected_pks)
            and matching_check.get("officialFinalCount") == len(expected_pks)
            and matching_check.get("scheduleSource")
            == (authority or {}).get("scheduleSource")
            and matching_check.get("scheduleSourceUrl")
            == (authority or {}).get("scheduleSourceUrl")
            and matching_check.get("error") is None
        ):
            errors.append("canonical_finalized_slate_check_authority_mismatch")
        actual_pks = []
        for game in games:
            if not isinstance(game, dict):
                errors.append("canonical_finalized_slate_game_invalid")
                continue
            official_pk = str(game.get("officialGamePk") or "").strip()
            actual_pks.append(official_pk)
            if (
                not re.fullmatch(r"[0-9]+", official_pk)
                or str(game.get("officialDate") or "") != slate_date
                or game.get("completed") is not True
                or _parse_dt(game.get("gameDate")) is None
            ):
                errors.append("canonical_finalized_slate_game_contract_invalid")
            errors.extend(
                _official_finalized_game_outcome_errors(
                    game,
                    expected_slate_date_et=slate_date,
                    expected_official_game_pk=official_pk,
                )
            )
        if sorted(actual_pks) != sorted(expected_pks):
            errors.append("canonical_finalized_slate_game_set_mismatch")
        if not authority_errors and sorted(actual_pks) == sorted(expected_pks):
            validated[slate_date] = slate
    return validated if not errors else {}, sorted(set(errors))


def _post_cutoff_production_acceptance_scope(report: dict) -> dict:
    import mlb_canonical_final_labels_v1 as canonical_labels
    import mlb_ml_experiment_v2 as experiment
    import mlb_rolling_24h_audit as rolling_audit

    cutoff = _parse_dt(experiment.PRODUCTION_RELEASE_CUTOFF_UTC)
    rows = report.get("rows")
    construction_blockers = []
    if cutoff is None:
        construction_blockers.append("production_release_cutoff_invalid")
    if not isinstance(rows, list):
        construction_blockers.append("audit_rows_missing_or_invalid")
        rows = []
    finalized_slate_evidence = report.get("canonicalFinalizedSlateEvidence")
    finalized_slates, finalized_evidence_errors = (
        _validated_finalized_slate_evidence(finalized_slate_evidence)
    )
    construction_blockers.extend(finalized_evidence_errors)
    source_rows_fingerprint = _rows_fingerprint(rows)

    quarantined_games = []
    scoped_entries = []
    invalid_time_rows = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            invalid_time_rows.append({"index": index, "reason": "row_not_object"})
            continue
        game_id = str(row.get("id") or row.get("gameId") or row.get("gameKeyBase") or "")
        commence = _parse_dt(row.get("commenceTime"))
        if commence is None:
            invalid_time_rows.append(
                {
                    "index": index,
                    "gameId": game_id or None,
                    "reason": "commence_time_missing_or_invalid",
                }
            )
            continue
        derived_slate_date = (
            commence.astimezone(canonical_labels.SLATE_TZ).date().isoformat()
        )
        supplied_slate_date = str(row.get("slateDateEt") or "").strip()
        if supplied_slate_date and supplied_slate_date != derived_slate_date:
            invalid_time_rows.append(
                {
                    "index": index,
                    "gameId": game_id or None,
                    "reason": "slate_date_does_not_match_commence_time",
                }
            )
            continue
        scheduled_lock = commence - timedelta(minutes=LOCK_MINUTES_BEFORE_GAME)
        entry = {
            "row": row,
            "gameId": game_id or None,
            "slateDateEt": derived_slate_date,
            "commenceTime": commence.isoformat(),
            "scheduledLockAtUtc": scheduled_lock.isoformat(),
        }
        if cutoff is not None and scheduled_lock < cutoff:
            quarantined_games.append(
                {
                    key: entry[key]
                    for key in (
                        "gameId",
                        "slateDateEt",
                        "commenceTime",
                        "scheduledLockAtUtc",
                    )
                }
            )
        else:
            scoped_entries.append(entry)

    if invalid_time_rows:
        construction_blockers.append("audit_rows_cannot_be_cutoff_scoped")

    preliminary_proofs = []
    for entry in scoped_entries:
        row = entry["row"]
        locked_audit = row.get("lockedCardAudit") or {}
        authority = row.get("canonicalLockAuthority") or {}
        official_values = _official_game_pk_values(row)
        official_pk = official_values[0] if len(official_values) == 1 else None
        official_outcomes = {
            str(game.get("officialGamePk") or ""): game
            for game in (
                (finalized_slates.get(entry["slateDateEt"]) or {}).get("games")
                or []
            )
            if isinstance(game, dict)
        }
        official_outcome = official_outcomes.get(str(official_pk or ""))
        outcome_binding_errors = (
            _audit_row_official_outcome_binding_errors(
                row,
                official_outcome,
                expected_slate_date_et=entry["slateDateEt"],
                expected_official_game_pk=official_pk,
            )
            if official_outcome is not None and official_pk is not None
            else ["official_finalized_game_outcome_authority_missing"]
        )
        lock_at = _parse_dt(locked_audit.get("lockAtUtc"))
        authority_lock_at = _parse_dt(authority.get("canonicalLockAtUtc"))
        canonical_authority = bool(
            row.get("status") == "GRADED"
            and rolling_audit._is_canonical_graded_row(row)
        )
        reasons = []
        if row.get("completed") is not True:
            reasons.append("completed_final_marker_missing")
        if row.get("status") != "GRADED":
            reasons.append("completed_game_not_canonically_graded")
        if not canonical_authority:
            reasons.append("canonical_immutable_lock_authority_invalid")
        if len(official_values) != 1 or not re.fullmatch(r"[0-9]+", official_pk or ""):
            reasons.append("exact_official_game_pk_missing_or_conflicting")
        elif str(authority.get("officialGamePk") or "") != official_pk:
            reasons.append("canonical_authority_official_game_pk_mismatch")
        if lock_at is None:
            reasons.append("canonical_lock_at_missing_or_invalid")
        if authority_lock_at is None or authority_lock_at != lock_at:
            reasons.append("canonical_lock_at_authority_missing_or_mismatch")
        if lock_at is not None and cutoff is not None and lock_at < cutoff:
            reasons.append("canonical_lock_before_release_cutoff")
        if not row.get("winner") or not isinstance(row.get("correct"), bool):
            reasons.append("official_final_label_missing_or_invalid")
        reasons.extend(outcome_binding_errors)
        preliminary_proofs.append(
            {
                "gameId": entry["gameId"],
                "slateDateEt": entry["slateDateEt"],
                "officialGamePk": official_pk,
                "commenceTime": entry["commenceTime"],
                "scheduledLockAtUtc": entry["scheduledLockAtUtc"],
                "canonicalLockAtUtc": lock_at.isoformat() if lock_at else None,
                "canonicalAuthorityLockAtUtc": (
                    authority_lock_at.isoformat() if authority_lock_at else None
                ),
                "canonicalSourcePk": authority.get("sourcePk"),
                "canonicalSourceSk": authority.get("sourceSk"),
                "status": row.get("status"),
                "completed": row.get("completed") is True,
                "awayTeam": row.get("awayTeam"),
                "homeTeam": row.get("homeTeam"),
                "awayScore": row.get("awayScore"),
                "homeScore": row.get("homeScore"),
                "winner": row.get("winner"),
                "predictedWinner": row.get("predictedWinner"),
                "correct": row.get("correct") if isinstance(row.get("correct"), bool) else None,
                "officialPrediction": row.get("officialPrediction") is True,
                "officialOutcomeAuthority": (
                    copy.deepcopy(official_outcome)
                    if isinstance(official_outcome, dict)
                    else None
                ),
                "officialOutcomeAuthorityProven": not outcome_binding_errors,
                "canonicalAuthorityProven": canonical_authority,
                "exactOfficialGameIdProven": bool(
                    canonical_authority
                    and official_pk
                    and re.fullmatch(r"[0-9]+", official_pk)
                    and str(authority.get("officialGamePk") or "") == official_pk
                ),
                "canonicalLockAtOrAfterCutoffProven": bool(
                    canonical_authority
                    and lock_at is not None
                    and authority_lock_at == lock_at
                    and cutoff is not None
                    and lock_at >= cutoff
                ),
                "blockers": sorted(set(reasons)),
            }
        )

    official_pk_counts = {}
    for proof in preliminary_proofs:
        official_pk = proof.get("officialGamePk")
        if official_pk:
            official_pk_counts[official_pk] = official_pk_counts.get(official_pk, 0) + 1
    for proof in preliminary_proofs:
        if official_pk_counts.get(proof.get("officialGamePk"), 0) > 1:
            proof["blockers"] = sorted(
                set(proof["blockers"] + ["duplicate_official_game_pk_in_scope"])
            )
            proof["exactOfficialGameIdProven"] = False
        proof["acceptedCanonicalGrade"] = not proof["blockers"]

    accepted = [proof for proof in preliminary_proofs if proof["acceptedCanonicalGrade"]]
    defects = [
        {
            key: proof.get(key)
            for key in (
                "gameId",
                "officialGamePk",
                "scheduledLockAtUtc",
                "canonicalLockAtUtc",
                "status",
                "blockers",
            )
        }
        for proof in preliminary_proofs
        if not proof["acceptedCanonicalGrade"]
    ]
    official = [proof for proof in accepted if proof["officialPrediction"]]
    exact_identity_count = sum(
        proof["exactOfficialGameIdProven"] for proof in preliminary_proofs
    )
    post_cutoff_official_game_pks = sorted(
        str(proof["officialGamePk"])
        for proof in preliminary_proofs
        if proof["exactOfficialGameIdProven"]
    )
    canonical_authority_count = sum(
        proof["canonicalAuthorityProven"] for proof in preliminary_proofs
    )
    official_outcome_authority_count = sum(
        proof["officialOutcomeAuthorityProven"] for proof in preliminary_proofs
    )
    post_cutoff_lock_count = sum(
        proof["canonicalLockAtOrAfterCutoffProven"] for proof in preliminary_proofs
    )
    completed_count = len(preliminary_proofs)
    graded_count = len(accepted)
    official_count = len(official)
    post_cutoff_blocker_codes = sorted(
        {
            reason
            for proof in preliminary_proofs
            for reason in proof.get("blockers") or []
        }
    )
    coverage = {
        "canonicalGradedPct": _pct(graded_count, completed_count),
        "canonicalAuthorityPct": _pct(canonical_authority_count, completed_count),
        "officialOutcomeAuthorityPct": _pct(
            official_outcome_authority_count, completed_count
        ),
        "exactOfficialGameIdPct": _pct(exact_identity_count, completed_count),
        "canonicalLockAtOrAfterCutoffPct": _pct(
            post_cutoff_lock_count, completed_count
        ),
        "officialPredictionPct": _pct(official_count, completed_count),
        "canonicalGradedComplete": graded_count == completed_count,
        "officialOutcomeAuthorityComplete": (
            official_outcome_authority_count == completed_count
        ),
        "exactOfficialGameIdComplete": exact_identity_count == completed_count,
        "canonicalLockAtOrAfterCutoffComplete": (
            post_cutoff_lock_count == completed_count
        ),
    }
    finalized_slate_proofs = []
    for slate_date, slate in sorted(finalized_slates.items()):
        authority = slate["authority"]
        expected_game_pks = sorted(
            str(value) for value in authority.get("officialGamePks") or []
        )
        slate_rows = [
            proof
            for proof in preliminary_proofs
            if proof.get("slateDateEt") == slate_date
        ]
        observed_game_pks = sorted(
            str(proof.get("officialGamePk") or "")
            for proof in slate_rows
            if str(proof.get("officialGamePk") or "")
        )
        observed_counts = {
            game_pk: observed_game_pks.count(game_pk)
            for game_pk in sorted(set(observed_game_pks))
        }
        duplicate_game_pks = sorted(
            game_pk for game_pk, count in observed_counts.items() if count > 1
        )
        observed_set = set(observed_game_pks)
        expected_set = set(expected_game_pks)
        missing_game_pks = sorted(expected_set - observed_set)
        unexpected_game_pks = sorted(observed_set - expected_set)
        noncanonical_game_pks = sorted(
            str(proof.get("officialGamePk") or "")
            for proof in slate_rows
            if (
                proof.get("acceptedCanonicalGrade") is not True
                and str(proof.get("officialGamePk") or "") in expected_set
            )
        )
        unidentified_row_count = sum(
            not str(proof.get("officialGamePk") or "") for proof in slate_rows
        )
        slate_errors = []
        if missing_game_pks:
            slate_errors.append("official_finalized_slate_missing_audit_rows")
        if unexpected_game_pks:
            slate_errors.append("audit_rows_not_in_official_finalized_slate")
        if duplicate_game_pks:
            slate_errors.append("duplicate_audit_official_game_pk")
        if noncanonical_game_pks or unidentified_row_count:
            slate_errors.append("official_slate_rows_not_all_canonically_graded")
        if len(slate_rows) != len(expected_game_pks):
            slate_errors.append("official_finalized_slate_row_count_mismatch")
        achieved = not slate_errors
        finalized_slate_proofs.append(
            {
                "slateDateEt": slate_date,
                "officialGameCount": len(expected_game_pks),
                "observedAuditRowCount": len(slate_rows),
                "officialGamePks": expected_game_pks,
                "observedOfficialGamePks": observed_game_pks,
                "missingOfficialGamePks": missing_game_pks,
                "unexpectedOfficialGamePks": unexpected_game_pks,
                "duplicateObservedOfficialGamePks": duplicate_game_pks,
                "noncanonicalOfficialGamePks": noncanonical_game_pks,
                "unidentifiedAuditRowCount": unidentified_row_count,
                "officialGameSetFingerprint": authority.get(
                    "officialGameSetFingerprint"
                ),
                "authorityFingerprint": authority.get("authorityFingerprint"),
                "exactFullSlateSettlementProven": achieved,
                "errors": sorted(set(slate_errors)),
            }
        )

    exact_full_slate_proofs = [
        proof
        for proof in finalized_slate_proofs
        if proof["exactFullSlateSettlementProven"]
    ]
    full_slate_defect_count = sum(
        not proof["exactFullSlateSettlementProven"]
        for proof in finalized_slate_proofs
    )
    first_complete_slate_proof = {
        "achieved": bool(exact_full_slate_proofs),
        "qualifyingSlateDateEt": (
            exact_full_slate_proofs[0]["slateDateEt"]
            if exact_full_slate_proofs
            else None
        ),
        "evaluatedFinalizedSlateCount": len(finalized_slate_proofs),
        "exactFullSlateCount": len(exact_full_slate_proofs),
        "authority": (
            "one nonempty canonical FINAL gamePk set must exactly equal the "
            "same slate's unique post-cutoff audit row set, and every row must "
            "retain current immutable LOCKED#GAME grade authority and match "
            "its exact official FINAL scores, winner, and derived correctness"
        ),
    }
    experiment_identity = {
        "experimentVersion": experiment.VERSION,
        "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
        "releaseContractId": experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        "releaseCutoffUtc": cutoff.isoformat() if cutoff else None,
    }
    scope = {
        "ok": not construction_blockers,
        "version": PRODUCTION_ACCEPTANCE_SCOPE_VERSION,
        "experimentIdentity": experiment_identity,
        "releaseCutoffUtc": cutoff.isoformat() if cutoff else None,
        "scopeBasis": "scheduled_game_tminus45_at_or_after_release_cutoff",
        "scheduledLockOffsetMinutes": LOCK_MINUTES_BEFORE_GAME,
        "canonicalLockMustAlsoBeAtOrAfterReleaseCutoff": True,
        "exactOfficialGameIdRequired": True,
        "exactOfficialFinalizedSlateRequired": True,
        "exactOfficialFinalOutcomeRequired": True,
        "sourceRowCount": len(rows),
        "sourceRowsFingerprintVersion": (
            PRODUCTION_ACCEPTANCE_ROWS_FINGERPRINT_VERSION
        ),
        "sourceRowsFingerprint": source_rows_fingerprint,
        "canonicalFinalizedSlateEvidenceFingerprint": (
            finalized_slate_evidence.get("evidenceFingerprint")
            if isinstance(finalized_slate_evidence, dict)
            else None
        ),
        "unscopedInvalidRowCount": len(invalid_time_rows),
        "preCutoffQuarantinedFinalGameCount": len(quarantined_games),
        "postCutoffCompletedFinalGameCount": completed_count,
        "postCutoffGradedPredictionCount": graded_count,
        "postCutoffMissingPredictionCount": len(defects),
        "postCutoffOfficialPredictionCount": official_count,
        "postCutoffExactOfficialGameIdCount": exact_identity_count,
        "postCutoffOfficialGamePks": post_cutoff_official_game_pks,
        "postCutoffCanonicalAuthorityCount": canonical_authority_count,
        "postCutoffOfficialOutcomeAuthorityCount": (
            official_outcome_authority_count
        ),
        "postCutoffCanonicalLockAtOrAfterCutoffCount": post_cutoff_lock_count,
        "postCutoffOfficialAccuracyPct": _pct(
            sum(proof["correct"] is True for proof in official), official_count
        ),
        "postCutoffAllGamesAccuracyPct": _pct(
            sum(proof["correct"] is True for proof in accepted), graded_count
        ),
        "coverage": coverage,
        "preCutoffQuarantinedGames": quarantined_games,
        "postCutoffGameProofs": preliminary_proofs,
        "postCutoffDefects": defects,
        "postCutoffBlockerCodes": post_cutoff_blocker_codes,
        "postCutoffFinalizedSlateCount": len(finalized_slate_proofs),
        "postCutoffExactFullSlateCount": len(exact_full_slate_proofs),
        "postCutoffFullSlateCoverageDefectCount": full_slate_defect_count,
        "postCutoffFinalizedSlateProofs": finalized_slate_proofs,
        "firstCompletePostCutoffSlateProof": first_complete_slate_proof,
        "fullSlateSettlementCoverageOk": full_slate_defect_count == 0,
        "settlementCoverageOk": bool(
            not construction_blockers
            and not defects
            and full_slate_defect_count == 0
        ),
        "invalidTimeRows": invalid_time_rows,
        "scopeConstructionBlockers": sorted(set(construction_blockers)),
        # Compatibility alias retained for the first acceptance consumer.
        "errors": sorted(set(construction_blockers)),
        "policy": (
            "Pre-r3 finals remain visible in the legacy rolling dashboard but "
            "cannot fail r3 acceptance. Every completed game whose scheduled "
            "T-45 is at or after the experiment cutoff must prove one exact "
            "official MLB gamePk, current immutable LOCKED#GAME authority, and "
            "a canonical lock timestamp at or after that same cutoff. A fully "
            "finalized slate is accepted only when the raw audit rows exactly "
            "equal its canonical official FINAL gamePk authority and each "
            "row's teams, scores, winner, and derived correctness match the "
            "official game-specific outcome evidence."
        ),
        "scopeFingerprintVersion": PRODUCTION_ACCEPTANCE_SCOPE_FINGERPRINT_VERSION,
    }
    scope["scopeFingerprint"] = _scope_fingerprint(scope)
    return scope


def _read_deployed_trainer_identity() -> dict:
    """Resolve the identity stamped into the Lambda that actually runs training."""
    import boto3

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    client_kwargs = {"region_name": region} if region else {}
    cloudformation = boto3.client("cloudformation", **client_kwargs)
    lambdas = boto3.client("lambda", **client_kwargs)
    detail = cloudformation.describe_stack_resource(
        StackName=os.environ.get("MLB_PRODUCTION_STACK_NAME", PRODUCTION_STACK_NAME),
        LogicalResourceId=TRAINER_LOGICAL_ID,
    )["StackResourceDetail"]
    function_name = str(detail.get("PhysicalResourceId") or "").strip()
    if not function_name:
        raise RuntimeError("deployed MLB trainer physical identity is missing")
    config = lambdas.get_function_configuration(FunctionName=function_name)
    environment = (config.get("Environment") or {}).get("Variables") or {}
    identity = {
        "gitSha": str(environment.get("INQSI_DEPLOY_GIT_SHA") or "").strip(),
        "templateSha256": str(
            environment.get("INQSI_DEPLOY_TEMPLATE_SHA256") or ""
        ).strip(),
    }
    if not (
        re.fullmatch(r"[0-9a-f]{40}", identity["gitSha"])
        and re.fullmatch(r"[0-9a-f]{64}", identity["templateSha256"])
    ):
        raise RuntimeError("deployed MLB trainer release identity is invalid")
    return identity


def _read_v2_training_state(*, now_utc=None, deployed_identity=None) -> dict:
    """Read the AWS-native V2 control records without invoking or mutating it."""
    import boto3
    import mlb_ml_experiment_v2 as experiment
    import mlb_ml_aws_training_v1 as trainer

    table_name = os.environ.get("SNAPSHOTS_TABLE", "")
    experiment_id = os.environ.get(
        "MLB_ML_EXPERIMENT_ID", "mlb-v2-2026-07-24-future-prospective-r4"
    )
    if not table_name:
        raise RuntimeError("SNAPSHOTS_TABLE is required for V2 status monitoring")
    table = boto3.resource("dynamodb").Table(table_name)

    def read(pk, sk):
        item = table.get_item(
            Key={"PK": pk, "SK": sk}, ConsistentRead=True
        ).get("Item") or {}
        # DynamoDB resource reads return every number as Decimal. Normalize the
        # control record through the same boundary as the authoritative trainer
        # before recomputing manifest and status fingerprints.
        data = trainer._plain(item.get("data") or {})
        return data if isinstance(data, dict) else {}

    experiment_pk = f"MLB_ML_EXPERIMENT#V2#{experiment_id}"
    manifest_before = read(experiment_pk, "MANIFEST")
    candidate = read(experiment_pk, "CANDIDATE#LATEST")
    generic_latest_status = read(experiment_pk, "STATUS#LATEST")
    training_status = read(experiment_pk, "STATUS#LATEST#TRAINING")
    selection_capture_status = read(
        experiment_pk, "STATUS#LATEST#SELECTION_CAPTURE"
    )
    # A trainer run can advance the manifest between the first control-record
    # read and the mode-specific heartbeat reads. Re-read after both heartbeats
    # and fail closed unless the revision/digest pair is unchanged.
    manifest_after = read(experiment_pk, "MANIFEST")
    champion = read("MLB_ML_CHAMPION#V2", "ACTIVE")
    checked_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    deployed_identity = deployed_identity or _read_deployed_trainer_identity()
    manifest_read_before = {
        "revision": manifest_before.get("revision"),
        "manifestDigest": manifest_before.get("manifestDigest"),
    }
    manifest_read_after = {
        "revision": manifest_after.get("revision"),
        "manifestDigest": manifest_after.get("manifestDigest"),
    }
    manifest_read_stable = bool(
        manifest_before
        and manifest_after
        and manifest_read_before == manifest_read_after
        and manifest_read_after["revision"] is not None
        and manifest_read_after["manifestDigest"]
    )
    manifest = manifest_after
    manifest_valid = bool(
        manifest
        and manifest.get("version") == experiment.VERSION
        and manifest.get("experimentId") == experiment.PRODUCTION_EXPERIMENT_ID
        and manifest.get("manifestDigest")
        == experiment.manifest_digest(manifest)
    )
    release_activation_errors = (
        experiment.release_activation_errors(
            manifest.get("releaseActivation"),
            expected_experiment_id=str(manifest.get("experimentId") or ""),
            expected_release_contract_id=str(
                manifest.get("releaseContractId") or ""
            ),
            expected_release_cutoff_utc=str(
                manifest.get("releaseCutoffUtc") or ""
            ),
            expected_created_at_utc=str(manifest.get("createdAtUtc") or ""),
        )
        if manifest_valid
        else ["release_activation_manifest_invalid"]
    )
    release_activation_valid = bool(
        manifest_valid and not release_activation_errors
    )
    current_manifest_digest = manifest.get("manifestDigest") if manifest_valid else None

    def status_health(status, *, execution_mode, maximum_age_minutes):
        created_at = _parse_dt(status.get("createdAtUtc"))
        age_minutes = (
            round((checked_at - created_at).total_seconds() / 60.0, 2)
            if created_at
            else None
        )
        present = bool(status)
        fresh = bool(
            age_minutes is not None
            and 0 <= age_minutes <= maximum_age_minutes
        )
        errors = []
        if not present:
            errors.append("status_missing")
        if present and status.get("ok") is not True:
            errors.append("status_not_ok")
        if present and status.get("executionMode") != execution_mode:
            errors.append("status_mode_mismatch")
        if present and status.get("version") != trainer.VERSION:
            errors.append("status_version_mismatch")
        if present and status.get("experimentId") != experiment.PRODUCTION_EXPERIMENT_ID:
            errors.append("status_experiment_mismatch")
        if present and status.get("statusFingerprintVersion") != trainer.STATUS_FINGERPRINT_VERSION:
            errors.append("status_fingerprint_version_mismatch")
        if present and status.get("statusFingerprint") != trainer._status_fingerprint(status):
            errors.append("status_fingerprint_mismatch")
        if present and status.get("executionConcurrencyControl") != (
            trainer.execution_concurrency_control(acquired_for_run=True)
        ):
            errors.append("status_execution_lease_contract_mismatch")
        if not manifest_valid:
            errors.append("current_manifest_missing_or_invalid")
        elif not release_activation_valid:
            errors.append("current_manifest_release_activation_invalid")
        elif not manifest_read_stable:
            errors.append("manifest_changed_during_status_read")
        elif present and status.get("manifestDigest") != current_manifest_digest:
            errors.append("status_manifest_mismatch")
        if created_at is None:
            errors.append("status_timestamp_invalid")
        elif age_minutes < 0:
            errors.append("status_from_future")
        elif not fresh:
            errors.append("status_stale")
        deployment = status.get("deploymentIdentity") or {}
        deployment_matches = bool(present and deployment == deployed_identity)
        if present and not deployment_matches:
            errors.append("status_deployment_identity_mismatch")
        return {
            "ok": not errors,
            "executionMode": execution_mode,
            "statusPresent": present,
            "latestRunStatus": status.get("status"),
            "latestRunCreatedAtUtc": status.get("createdAtUtc"),
            "latestRunTimestampValid": created_at is not None,
            "latestRunAgeMinutes": age_minutes,
            "latestRunFresh": fresh,
            "latestRunMaxAgeMinutes": maximum_age_minutes,
            "deploymentIdentity": deployment,
            "deploymentIdentityMatches": deployment_matches,
            "statusVersion": status.get("version"),
            "experimentId": status.get("experimentId"),
            "manifestDigest": status.get("manifestDigest"),
            "statusFingerprintVersion": status.get("statusFingerprintVersion"),
            "executionConcurrencyControl": status.get(
                "executionConcurrencyControl"
            ),
            "latestRun": status,
            "errors": errors,
        }

    training_health = status_health(
        training_status,
        execution_mode="training",
        maximum_age_minutes=V2_TRAINING_STATUS_MAX_AGE_MINUTES,
    )
    selection_capture_health = status_health(
        selection_capture_status,
        execution_mode="selection_capture",
        maximum_age_minutes=V2_SELECTION_CAPTURE_STATUS_MAX_AGE_MINUTES,
    )
    deployment_identity_agreement = bool(
        training_health["deploymentIdentity"]
        and training_health["deploymentIdentity"]
        == selection_capture_health["deploymentIdentity"]
    )
    return {
        "ok": bool(
            training_health["ok"]
            and selection_capture_health["ok"]
            and deployment_identity_agreement
            and experiment_id == experiment.PRODUCTION_EXPERIMENT_ID
            and manifest_read_stable
            and release_activation_valid
        ),
        "readOnly": True,
        "experimentId": experiment_id,
        "experimentIdValid": experiment_id == experiment.PRODUCTION_EXPERIMENT_ID,
        "manifestDigest": current_manifest_digest,
        "manifestReadStable": manifest_read_stable,
        "manifestReadBefore": manifest_read_before,
        "manifestReadAfter": manifest_read_after,
        "manifestPresent": bool(manifest),
        "manifestValid": manifest_valid if manifest else None,
        "releaseActivationValid": (
            release_activation_valid if manifest else None
        ),
        "releaseActivationErrors": release_activation_errors if manifest else [],
        "manifestPhase": manifest.get("phase"),
        "partitionCounts": {
            name: int(((manifest.get("partitions") or {}).get(name) or {}).get("rowCount") or 0)
            for name in experiment.PARTITION_ORDER
        },
        "latestCandidatePresent": bool(candidate),
        "latestCandidateArtifactDigest": candidate.get("artifactDigest"),
        "candidatePromotionDecision": (candidate.get("promotionGate") or {}).get(
            "promotionDecision"
        ),
        # Compatibility aliases deliberately describe the full training run,
        # never the more frequently overwritten generic record.
        "statusPresent": training_health["statusPresent"],
        "latestRunStatus": training_health["latestRunStatus"],
        "latestRunCreatedAtUtc": training_health["latestRunCreatedAtUtc"],
        "latestRunTimestampValid": training_health["latestRunTimestampValid"],
        "latestRunAgeMinutes": training_health["latestRunAgeMinutes"],
        "latestRunFresh": training_health["latestRunFresh"],
        "latestRunMaxAgeMinutes": training_health["latestRunMaxAgeMinutes"],
        "trainingHealth": training_health,
        "selectionCaptureHealth": selection_capture_health,
        "deploymentIdentityAgreement": deployment_identity_agreement,
        "deployedTrainerIdentity": deployed_identity,
        "genericLatestStatusDiagnosticOnly": generic_latest_status,
        "milestones": training_status.get("milestones") or {},
        "championPresent": bool(champion),
        "championApprovalMode": champion.get("approvalMode"),
        "firstPromotionRequiresManualReview": True,
        "automaticPromotionEnabled": training_status.get(
            "automaticPromotionEnabled"
        ),
    }


def main() -> int:
    payload = {
        "ok": False,
        "proofType": "MLB_ML_V3_AWS_AUDIT_EXECUTION",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "snapshotsTableConfigured": bool(os.environ.get("SNAPSHOTS_TABLE")),
            "oddsApiKeyConfigured": bool(os.environ.get("ODDS_API_KEY")),
            "autoPromote": os.environ.get("INQSI_MLB_ML_AUTO_PROMOTE"),
            "storeEnabled": os.environ.get("INQSI_MLB_ML_AUDIT_STORE", "false"),
            "allowLocalFileChampion": os.environ.get("INQSI_MLB_ALLOW_LOCAL_FILE_CHAMPION"),
        },
    }
    try:
        # Install the authority thresholds and official-lock quality classifier
        # explicitly. The workflow adds hello_world to sys.path after Python
        # startup, so relying on sitecustomize would leave the AWS audit using
        # the pre-60%-gate official classification.
        import mlb_accuracy_target_policy_v1

        policy_install = mlb_accuracy_target_policy_v1.install()

        import mlb_rolling_24h_audit
        import mlb_locked_card_audit_v1
        import mlb_ml_audit_feature_bridge_v1
        import mlb_doubleheader_safe_audit_patch

        mlb_locked_card_audit_v1.apply(mlb_rolling_24h_audit)
        mlb_ml_audit_feature_bridge_v1.apply(mlb_locked_card_audit_v1)
        mlb_doubleheader_safe_audit_patch.apply(mlb_rolling_24h_audit)

        # GitHub is a read-only audit surface. AWS owns the live experiment,
        # challenger storage, and promotion lifecycle.
        if os.environ.get("INQSI_MLB_ML_AUDIT_STORE", "false").lower() in {"1", "true", "yes"}:
            raise RuntimeError("GITHUB_MLB_AUDIT_STORAGE_FORBIDDEN_AWS_TRAINER_IS_AUTHORITATIVE")
        report = mlb_rolling_24h_audit.build(store=False, write_file=True)
        canonical_finalized_slate_evidence = (
            _canonical_finalized_slate_evidence(report)
        )
        report["canonicalFinalizedSlateEvidence"] = (
            canonical_finalized_slate_evidence
        )
        production_acceptance_scope = (
            _post_cutoff_production_acceptance_scope(report)
        )
        v2_training = _read_v2_training_state()
        accuracy = report.get("realWorldAccuracy") or {}
        optimization = report.get("mlOptimizationV3") or {}
        authority = report.get("mlTrainingAuthority") or {}
        critical = accuracy.get("mlCriticalFixStatus") or {}
        failures = []
        if policy_install.get("ok") is not True:
            failures.append("accuracy_target_policy_install_failed")
        if "official_lock_60pct_confirmed_direction_gate" not in (policy_install.get("patched") or []):
            failures.append("official_lock_quality_gate_not_installed")
        if accuracy.get("applied") is not True:
            failures.append("real_world_accuracy_not_applied")
        if (report.get("accuracyLedger") or {}).get("immutable") is not True:
            failures.append("immutable_accuracy_ledger_not_enabled")
        if critical.get("ok") is not True:
            failures.append("critical_ml_blocker_installation_failed")
        if optimization.get("applied") is not True:
            failures.append("ml_optimization_v3_not_applied")
        if authority.get("authoritative") != "awsNativeV2_fixed_prospective_only":
            failures.append("wrong_training_authority")
        if os.environ.get("INQSI_MLB_ML_AUTO_PROMOTE", "false").lower() in {"1", "true", "yes"}:
            failures.append("github_audit_must_not_enable_automatic_promotion")
        if authority.get("automaticChampionPromotion") is not False:
            failures.append("legacy_github_audit_automatic_promotion_not_disabled")
        if optimization.get("automaticPromotionEnabled") is not False:
            failures.append("legacy_github_optimization_write_authority_not_disabled")
        if v2_training.get("manifestPresent") and v2_training.get("manifestValid") is not True:
            failures.append("aws_v2_experiment_manifest_invalid")
        if (
            v2_training.get("manifestPresent")
            and v2_training.get("releaseActivationValid") is not True
        ):
            failures.append("aws_v2_release_activation_invalid")
        if (v2_training.get("trainingHealth") or {}).get("ok") is not True:
            failures.append("aws_v2_training_status_unhealthy_stale_or_invalid")
        if (v2_training.get("selectionCaptureHealth") or {}).get("ok") is not True:
            failures.append("aws_v2_selection_capture_status_unhealthy_stale_or_invalid")
        if v2_training.get("deploymentIdentityAgreement") is not True:
            failures.append("aws_v2_training_capture_deployment_identity_mismatch")
        if v2_training.get("ok") is not True:
            failures.append("aws_v2_mode_specific_status_unhealthy")
        if v2_training.get("automaticPromotionEnabled") is not False:
            failures.append("aws_v2_automatic_promotion_not_proven_disabled")
        if production_acceptance_scope.get("ok") is not True:
            failures.append("post_cutoff_production_acceptance_scope_invalid")
        if (
            v2_training.get("championPresent")
            and v2_training.get("championApprovalMode")
            not in {
                "manual_first_shadow_approval",
                "automatic_stable_champion_replacement",
                "automatic_stable_shadow_champion_replacement",
            }
        ):
            failures.append("aws_v2_champion_approval_mode_invalid")

        payload.update({
            "ok": not failures,
            "failures": failures,
            "reportCreatedAt": report.get("createdAt"),
            "reportOk": report.get("ok"),
            "windowHours": report.get("windowHours"),
            "summary": report.get("summary"),
            # These are the raw settlement facts from which the acceptance
            # consumer independently reconstructs the post-cutoff scope.
            "rows": report.get("rows") or [],
            "canonicalFinalizedSlateEvidence": (
                canonical_finalized_slate_evidence
            ),
            "productionAcceptanceScope": production_acceptance_scope,
            "accuracyTargetPolicyInstall": policy_install,
            "accuracyLedger": report.get("accuracyLedger"),
            "mlCriticalFixStatus": critical,
            "mlOptimizationV3": optimization,
            "mlTrainingAuthority": authority,
            "mlTrainingV2": v2_training,
            "dailyLockAuditFallback": {
                "applied": False,
                "officialAuditEligible": False,
                "policy": "Daily-card and legacy fallback rows are diagnostic-only; official audit and learning require exact canonical LOCKED#GAME authority.",
            },
            "stored": report.get("stored"),
            "storeError": report.get("storeError"),
            "githubLearningWritesDisabled": True,
            "awsNativeTrainerAuthoritative": True,
            "productionAuthoritySource": "persisted_canonical_rules_market_prediction_v2_shadow_only",
        })
    except Exception as exc:
        payload.update({
            "ok": False,
            "failures": ["audit_exception"],
            "exceptionType": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        })

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
