#!/usr/bin/env python3
"""Reconcile the bounded MLB prospective lifecycle backlog before training.

This deployment-only repair uses the existing protected lock and settlement
Lambdas. It never writes DynamoDB directly, never creates a prediction after a
game starts, and never changes promotion, champion, wagering, or production
model authority. Each mutating replay is bound to a separate read-only,
exact-date official-schedule status proof before settlement is allowed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config


VERSION = "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v2-read-bound-official-proof"
ET = ZoneInfo("America/New_York")
LOCK_LOGICAL_ID = "MLBDailyPickLockFunction"
RESULTS_LOGICAL_ID = "MLBResultsSchedulerFunction"
TRAINER_LOGICAL_ID = "MLBMLTrainingFunction"
RELEASE_CUTOFF_ENV = "MLB_ML_RELEASE_CUTOFF_UTC"
DEFAULT_MAX_SLATE_DAYS = 14
OFFICIAL_SCHEDULE_AUTHORITY_VERSION = (
    "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date"
)


class ReconciliationError(RuntimeError):
    """Fail-closed prospective backlog reconciliation error."""


@dataclass(frozen=True)
class StackFunctions:
    lock: str
    results: str
    trainer: str


def _json_object(value: Any, *, error: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value)
    except Exception as exc:
        raise ReconciliationError(error) from exc
    if not isinstance(parsed, dict):
        raise ReconciliationError(error)
    return parsed


def _integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ReconciliationError(f"{field}_invalid")
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReconciliationError(f"{field}_invalid") from exc
    if (
        not numeric.is_finite()
        or numeric < 0
        or numeric != numeric.to_integral_value()
    ):
        raise ReconciliationError(f"{field}_invalid")
    return int(numeric)


_LIFECYCLE_STATES = frozenset(
    {
        "LOCKED_CANONICAL",
        "LOCKED_NO_PREDICTION_DATA",
        "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED",
    }
)


def _validated_lifecycle_games(
    rows: Any,
    *,
    expected_count: int,
    source: str,
    require_identity: bool,
) -> List[Dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ReconciliationError(f"{source}_lifecycle_games_invalid")
    normalized: List[Dict[str, Any]] = []
    official_seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ReconciliationError(
                f"{source}_lifecycle_game_invalid"
            )
        official_pk = str(row.get("officialGamePk") or "").strip()
        official_pk_number = _integer(
            row.get("officialGamePk"),
            field=f"{source}_official_game_pk",
        )
        game_identity = str(row.get("gameIdentity") or "").strip()
        state = str(
            row.get("terminalState")
            or row.get("state")
            or row.get("lockStatus")
            or ""
        )
        if (
            official_pk_number <= 0
            or not official_pk
            or official_pk in official_seen
            or (require_identity and not game_identity)
            or state not in _LIFECYCLE_STATES
        ):
            raise ReconciliationError(
                f"{source}_lifecycle_game_invalid"
            )
        official_seen.add(official_pk)
        normalized.append(
            {
                "officialGamePk": official_pk,
                "gameIdentity": game_identity,
                "terminalState": state,
            }
        )
    return sorted(
        normalized,
        key=lambda row: int(row["officialGamePk"]),
    )


def _lifecycle_game_set_fingerprint(
    rows: Sequence[Mapping[str, Any]],
) -> str:
    return hashlib.sha256(
        json.dumps(
            list(rows),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _lifecycle_state_count(
    rows: Sequence[Mapping[str, Any]],
    state: str,
) -> int:
    return sum(row.get("terminalState") == state for row in rows)


def _utc(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise ReconciliationError(f"{field}_timezone_missing")
    return parsed.astimezone(timezone.utc)


def prospective_slate_dates(
    release_cutoff_utc: str,
    *,
    now_utc: Optional[datetime] = None,
    max_slate_days: int = DEFAULT_MAX_SLATE_DAYS,
) -> List[str]:
    """Return cutoff-date through yesterday ET, with a hard bounded horizon."""

    if max_slate_days < 1:
        raise ReconciliationError("max_slate_days_invalid")
    cutoff_date = _utc(release_cutoff_utc, field="release_cutoff").astimezone(ET).date()
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    yesterday_et = now.astimezone(ET).date() - timedelta(days=1)
    if cutoff_date > yesterday_et:
        return []
    day_count = (yesterday_et - cutoff_date).days + 1
    if day_count > max_slate_days:
        raise ReconciliationError("prospective_backlog_exceeds_bounded_horizon")
    return [
        (cutoff_date + timedelta(days=offset)).isoformat()
        for offset in range(day_count)
    ]


def _resource_id(cloudformation: Any, stack_name: str, logical_id: str) -> str:
    response = cloudformation.describe_stack_resource(
        StackName=stack_name,
        LogicalResourceId=logical_id,
    )
    physical_id = str(
        ((response.get("StackResourceDetail") or {}).get("PhysicalResourceId"))
        or ""
    ).strip()
    if not physical_id:
        raise ReconciliationError(f"{logical_id}_physical_id_missing")
    return physical_id


def resolve_stack_functions(cloudformation: Any, stack_name: str) -> StackFunctions:
    return StackFunctions(
        lock=_resource_id(cloudformation, stack_name, LOCK_LOGICAL_ID),
        results=_resource_id(cloudformation, stack_name, RESULTS_LOGICAL_ID),
        trainer=_resource_id(cloudformation, stack_name, TRAINER_LOGICAL_ID),
    )


def release_cutoff(lambda_client: Any, trainer_function: str) -> str:
    response = lambda_client.get_function_configuration(FunctionName=trainer_function)
    variables = ((response.get("Environment") or {}).get("Variables") or {})
    value = str(variables.get(RELEASE_CUTOFF_ENV) or "").strip()
    _utc(value, field="release_cutoff")
    return value


def _read_lambda_payload(response: Mapping[str, Any]) -> bytes:
    stream = response.get("Payload")
    if stream is None or not hasattr(stream, "read"):
        raise ReconciliationError("lambda_payload_stream_missing")
    close = getattr(stream, "close", None)
    if not callable(close):
        raise ReconciliationError("lambda_payload_stream_not_closeable")
    try:
        body = stream.read()
    finally:
        close()
    if not isinstance(body, bytes):
        raise ReconciliationError("lambda_payload_not_bytes")
    return body


def invoke_json(lambda_client: Any, function_name: str, event: Dict[str, Any]) -> Dict[str, Any]:
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(event, separators=(",", ":")).encode("utf-8"),
    )
    status_code = _integer(response.get("StatusCode"), field="lambda_status_code")
    payload_bytes = _read_lambda_payload(response)
    if status_code != 200:
        raise ReconciliationError("lambda_invoke_status_not_200")
    if response.get("FunctionError"):
        raise ReconciliationError("lambda_function_error")
    payload = _json_object(
        payload_bytes.decode("utf-8"),
        error="lambda_response_json_invalid",
    )
    if "statusCode" not in payload:
        return payload
    application_status = _integer(
        payload.get("statusCode"), field="application_status_code"
    )
    body = payload.get("body")
    application = (
        _json_object(body, error="application_body_json_invalid")
        if isinstance(body, str)
        else dict(body or {})
    )
    if application_status < 200 or application_status >= 300:
        raise ReconciliationError("lambda_application_status_not_success")
    return application


def _validate_official_status(
    status: Mapping[str, Any],
    slate_date: str,
) -> Dict[str, int]:
    if status.get("ok") is not True or status.get("sport") != "mlb":
        raise ReconciliationError("official_status_unhealthy")
    if str(status.get("slateDateEt") or "") != slate_date:
        raise ReconciliationError("official_status_slate_mismatch")
    if status.get("officialScheduleBacked") is not True:
        raise ReconciliationError("official_schedule_authority_unproven")
    if (
        status.get("officialScheduleAuthorityVersion")
        != OFFICIAL_SCHEDULE_AUTHORITY_VERSION
    ):
        raise ReconciliationError("official_schedule_authority_version_invalid")
    if status.get("officialScheduleAuthoritativeStartTimes") is not True:
        raise ReconciliationError("official_schedule_start_times_unproven")

    game_count = _integer(status.get("gameCount"), field="status_game_count")
    official_count = _integer(
        status.get("officialScheduleGameCount"),
        field="official_schedule_game_count",
    )
    if official_count != game_count:
        raise ReconciliationError("official_schedule_game_count_mismatch")
    locked_predictions = _integer(
        status.get("lockedPredictionCount"), field="status_locked_prediction_count"
    )
    terminal_no_data = _integer(
        status.get("noPredictionDataCount"), field="status_terminal_no_data_count"
    )
    quarantine_count = _integer(
        status.get("missedLockValidPrelockQuarantineCount", 0),
        field="status_missed_lock_valid_prelock_quarantine_count",
    )
    locked_statuses = _integer(
        status.get("lockedStatusCount"), field="status_locked_status_count"
    )
    if (
        locked_statuses
        != locked_predictions + terminal_no_data + quarantine_count
    ):
        raise ReconciliationError("official_status_terminal_counts_inconsistent")
    if game_count and locked_statuses != game_count:
        raise ReconciliationError("official_status_terminal_coverage_incomplete")
    if game_count and status.get("lockStatusComplete") is not True:
        raise ReconciliationError("official_status_not_complete")
    lifecycle_games = _validated_lifecycle_games(
        status.get("perGameStatus", []),
        expected_count=game_count,
        source="official_status",
        require_identity=True,
    )
    observed_counts = {
        "LOCKED_CANONICAL": locked_predictions,
        "LOCKED_NO_PREDICTION_DATA": terminal_no_data,
        "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED": (
            quarantine_count
        ),
    }
    if any(
        _lifecycle_state_count(lifecycle_games, state) != expected
        for state, expected in observed_counts.items()
    ):
        raise ReconciliationError(
            "official_status_lifecycle_counts_mismatch"
        )
    provider_manifest_fingerprint = str(
        status.get("providerManifestFingerprint") or ""
    )
    if provider_manifest_fingerprint and re.fullmatch(
        r"[0-9a-f]{64}",
        provider_manifest_fingerprint,
    ) is None:
        raise ReconciliationError(
            "official_status_provider_manifest_fingerprint_invalid"
        )
    return {
        "gameCount": game_count,
        "lockedPredictionCount": locked_predictions,
        "terminalNoPredictionCount": terminal_no_data,
        "missedLockValidPrelockQuarantineCount": quarantine_count,
        "terminalExcludedCount": terminal_no_data + quarantine_count,
        "lockedStatusCount": locked_statuses,
        "lifecycleGames": lifecycle_games,
        "lifecycleGameSetFingerprint": (
            _lifecycle_game_set_fingerprint(lifecycle_games)
        ),
        "providerManifestFingerprint": provider_manifest_fingerprint,
    }


def validate_lock_result(
    payload: Mapping[str, Any],
    official_status: Mapping[str, Any],
    slate_date: str,
) -> Dict[str, Any]:
    if payload.get("ok") is not True or payload.get("sport") != "mlb":
        raise ReconciliationError("lock_reconciliation_unhealthy")
    if str(payload.get("slateDateEt") or "") != slate_date:
        raise ReconciliationError("lock_reconciliation_slate_mismatch")
    status_counts = _validate_official_status(official_status, slate_date)
    progress = payload.get("perGameLockProgress") or {}
    if not isinstance(progress, Mapping):
        raise ReconciliationError("lock_progress_missing")

    games = progress.get("games") or []
    if not isinstance(games, list):
        raise ReconciliationError("lock_progress_games_invalid")
    manifest_count = _integer(
        progress.get("manifestGameCount", len(games)),
        field="manifest_game_count",
    )
    if games and manifest_count != len(games):
        raise ReconciliationError("lock_progress_manifest_count_mismatch")
    if manifest_count != status_counts["gameCount"]:
        raise ReconciliationError("mutation_and_official_manifest_count_mismatch")

    if manifest_count == 0:
        return {
            "slateDateEt": slate_date,
            "manifestGameCount": 0,
            "canonicalPredictionCount": 0,
            "terminalNoPredictionCount": 0,
            "missedLockValidPrelockQuarantineCount": 0,
            "terminalExcludedCount": 0,
            "lockOutcomeCount": 0,
            "offDay": True,
            "officialStatusReadBound": True,
            "lifecycleGames": [],
            "lifecycleGameSetFingerprint": (
                _lifecycle_game_set_fingerprint([])
            ),
            "providerManifestFingerprint": status_counts[
                "providerManifestFingerprint"
            ],
        }

    mutation_lifecycle_games = _validated_lifecycle_games(
        games,
        expected_count=manifest_count,
        source="mutation_progress",
        require_identity=True,
    )
    if mutation_lifecycle_games != status_counts["lifecycleGames"]:
        raise ReconciliationError(
            "mutation_and_official_lifecycle_game_set_mismatch"
        )

    canonical_count = _integer(
        progress.get("canonicalCount"), field="canonical_count"
    )
    terminal_count = _integer(
        progress.get(
            "noPredictionDataCount",
            progress.get("terminalNoPredictionCount", 0),
        ),
        field="terminal_no_prediction_count",
    )
    quarantine_count = _integer(
        progress.get("missedLockValidPrelockQuarantineCount", 0),
        field="missed_lock_valid_prelock_quarantine_count",
    )
    lock_outcome_count = _integer(
        progress.get("lockOutcomeCount"), field="lock_outcome_count"
    )
    missed_count = _integer(progress.get("missedCount"), field="missed_count")
    due_count = _integer(progress.get("dueMissingCount"), field="due_missing_count")
    if missed_count or due_count:
        raise ReconciliationError("prospective_slate_still_unresolved")
    if lock_outcome_count != manifest_count:
        raise ReconciliationError("prospective_slate_terminal_coverage_incomplete")
    if (
        canonical_count + terminal_count + quarantine_count
        != lock_outcome_count
    ):
        raise ReconciliationError("prospective_slate_terminal_counts_inconsistent")
    if canonical_count != status_counts["lockedPredictionCount"]:
        raise ReconciliationError("mutation_and_status_prediction_count_mismatch")
    if terminal_count != status_counts["terminalNoPredictionCount"]:
        raise ReconciliationError("mutation_and_status_terminal_count_mismatch")
    if quarantine_count != status_counts[
        "missedLockValidPrelockQuarantineCount"
    ]:
        raise ReconciliationError(
            "mutation_and_status_quarantine_count_mismatch"
        )
    return {
        "slateDateEt": slate_date,
        "manifestGameCount": manifest_count,
        "canonicalPredictionCount": canonical_count,
        "terminalNoPredictionCount": terminal_count,
        "missedLockValidPrelockQuarantineCount": quarantine_count,
        "terminalExcludedCount": terminal_count + quarantine_count,
        "lockOutcomeCount": lock_outcome_count,
        "offDay": False,
        "officialStatusReadBound": True,
        "lifecycleGames": mutation_lifecycle_games,
        "lifecycleGameSetFingerprint": (
            _lifecycle_game_set_fingerprint(
                mutation_lifecycle_games
            )
        ),
        "providerManifestFingerprint": status_counts[
            "providerManifestFingerprint"
        ],
    }



def _validated_settlement_lifecycle_games(
    payload: Mapping[str, Any],
    *,
    official_count: int,
    canonical_count: int,
    terminal_no_data_count: int,
    quarantine_count: int,
) -> List[Dict[str, Any]]:
    label_writes = payload.get("labelWrites")
    terminal_exclusions = payload.get("terminalExclusions")
    if (
        not isinstance(label_writes, list)
        or len(label_writes) != canonical_count
        or not isinstance(terminal_exclusions, list)
        or len(terminal_exclusions)
        != terminal_no_data_count + quarantine_count
    ):
        raise ReconciliationError(
            "prospective_settlement_lifecycle_games_invalid"
        )
    rows: List[Dict[str, Any]] = []
    for label in label_writes:
        if (
            not isinstance(label, Mapping)
            or label.get("ok") is not True
            or str(label.get("status") or "")
            not in {
                "CREATED",
                "IDEMPOTENT_EXISTING",
                "IDEMPOTENT_EXISTING_POLICY_DRIFT",
                "WOULD_CREATE",
            }
        ):
            raise ReconciliationError(
                "prospective_settlement_label_write_invalid"
            )
        rows.append(
            {
                "officialGamePk": label.get("officialGamePk"),
                "gameIdentity": "",
                "terminalState": "LOCKED_CANONICAL",
            }
        )
    for terminal in terminal_exclusions:
        if (
            not isinstance(terminal, Mapping)
            or terminal.get("accuracyEligible") is not False
            or terminal.get("trainingEligible") is not False
            or terminal.get("predictionAdopted") is not False
        ):
            raise ReconciliationError(
                "prospective_settlement_terminal_exclusion_invalid"
            )
        state = str(terminal.get("status") or "")
        if state not in {
            "LOCKED_NO_PREDICTION_DATA",
            "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED",
        }:
            raise ReconciliationError(
                "prospective_settlement_terminal_exclusion_invalid"
            )
        rows.append(
            {
                "officialGamePk": terminal.get("officialGamePk"),
                "gameIdentity": "",
                "terminalState": state,
            }
        )
    normalized = _validated_lifecycle_games(
        rows,
        expected_count=official_count,
        source="prospective_settlement",
        require_identity=False,
    )
    expected_counts = {
        "LOCKED_CANONICAL": canonical_count,
        "LOCKED_NO_PREDICTION_DATA": terminal_no_data_count,
        "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED": (
            quarantine_count
        ),
    }
    if any(
        _lifecycle_state_count(normalized, state) != expected
        for state, expected in expected_counts.items()
    ):
        raise ReconciliationError(
            "prospective_settlement_lifecycle_counts_mismatch"
        )
    return normalized

def validate_settlement_result(
    payload: Mapping[str, Any],
    slate_date: str,
) -> Dict[str, Any]:
    def count(field: str) -> int:
        value = payload.get(field)
        if isinstance(value, bool):
            raise ReconciliationError(
                f"prospective_settlement_{field}_invalid"
            )
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ReconciliationError(
                f"prospective_settlement_{field}_invalid"
            ) from exc
        if (
            not numeric.is_finite()
            or numeric < 0
            or numeric != numeric.to_integral_value()
        ):
            raise ReconciliationError(
                f"prospective_settlement_{field}_invalid"
            )
        return int(numeric)

    if payload.get("ok") is not True:
        raise ReconciliationError("prospective_settlement_unhealthy")
    returned_date = str(
        payload.get("slateDateEt")
        or payload.get("slate_date")
        or payload.get("date")
        or ""
    )
    if returned_date != slate_date:
        raise ReconciliationError("prospective_settlement_slate_mismatch")

    official = count("officialGameCount")
    final = count("officialFinalCount")
    canonical = count("canonicalLockCount")
    no_data = count("terminalNoPredictionCount")
    quarantine = count(
        "missedLockValidPrelockQuarantineCount"
    )
    terminal_outcomes = count("terminalOutcomeCount")
    terminal_excluded = count("terminalExcludedCount")
    label_writes = count("labelWriteCount")
    rejected_locks = count("rejectedCanonicalLockCount")
    lock_terminal_conflicts = count("lockTerminalConflictCount")
    skipped = count("skippedNotFinalCount")
    missing = count("missingCanonicalLockCount")
    identity_rejections = count("identityRejectionCount")
    label_conflicts = count("labelConflictCount")
    terminal_rejections = payload.get("rejectedTerminalOutcomes")
    lifecycle_games = _validated_settlement_lifecycle_games(
        payload,
        official_count=official,
        canonical_count=canonical,
        terminal_no_data_count=no_data,
        quarantine_count=quarantine,
    )
    if (
        final != official
        or canonical + no_data + quarantine != official
        or terminal_outcomes != no_data + quarantine
        or terminal_excluded != terminal_outcomes
        or label_writes != canonical
        or rejected_locks != 0
        or lock_terminal_conflicts != 0
        or skipped != 0
        or missing != 0
        or identity_rejections != 0
        or label_conflicts != 0
        or terminal_rejections != []
        or payload.get("authoritativeSettlement") is not True
        or payload.get("legacySettlementAuthority") is not False
        or str(payload.get("status") or "")
        != "CANONICAL_FINAL_LABELS_COMPLETE"
        or payload.get("immutablePregameRowsMutated") is not False
        or payload.get("immutablePregameReadbackErrors") != []
    ):
        raise ReconciliationError(
            "prospective_settlement_terminal_coverage_invalid"
        )
    return {
        "slateDateEt": slate_date,
        "ok": True,
        "finalized": True,
        "status": "CANONICAL_FINAL_LABELS_COMPLETE",
        "authoritativeSettlement": True,
        "officialGameCount": official,
        "officialFinalCount": final,
        "canonicalLockCount": canonical,
        "terminalNoPredictionCount": no_data,
        "missedLockValidPrelockQuarantineCount": quarantine,
        "terminalOutcomeCount": terminal_outcomes,
        "terminalExcludedCount": terminal_excluded,
        "labelWriteCount": label_writes,
        "settledLabelCount": label_writes,
        "rejectedCanonicalLockCount": 0,
        "rejectedTerminalOutcomeCount": 0,
        "lockTerminalConflictCount": 0,
        "missingCanonicalLockCount": 0,
        "identityRejectionCount": 0,
        "labelConflictCount": 0,
        "immutablePregameRowsMutated": False,
        "lifecycleGames": lifecycle_games,
        "lifecycleGameSetFingerprint": (
            _lifecycle_game_set_fingerprint(lifecycle_games)
        ),
    }


def validate_settlement_lock_binding(
    lock_evidence: Mapping[str, Any],
    settlement: Mapping[str, Any],
) -> None:
    bindings = (
        ("manifestGameCount", "officialGameCount"),
        ("canonicalPredictionCount", "canonicalLockCount"),
        ("terminalNoPredictionCount", "terminalNoPredictionCount"),
        (
            "missedLockValidPrelockQuarantineCount",
            "missedLockValidPrelockQuarantineCount",
        ),
        ("terminalExcludedCount", "terminalExcludedCount"),
        ("terminalExcludedCount", "terminalOutcomeCount"),
    )
    if any(
        lock_evidence.get(lock_field)
        != settlement.get(settlement_field)
        for lock_field, settlement_field in bindings
    ):
        raise ReconciliationError(
            "prospective_settlement_lock_evidence_mismatch"
        )
    lock_games = lock_evidence.get("lifecycleGames")
    settlement_games = settlement.get("lifecycleGames")
    if not isinstance(lock_games, list) or not isinstance(
        settlement_games, list
    ):
        raise ReconciliationError(
            "prospective_settlement_lock_lifecycle_evidence_missing"
        )
    if lock_games != settlement_games:
        raise ReconciliationError(
            "prospective_settlement_lock_lifecycle_game_set_mismatch"
        )

def reconcile(
    cloudformation: Any,
    lambda_client: Any,
    *,
    stack_name: str,
    now_utc: Optional[datetime] = None,
    max_slate_days: int = DEFAULT_MAX_SLATE_DAYS,
) -> Dict[str, Any]:
    functions = resolve_stack_functions(cloudformation, stack_name)
    cutoff = release_cutoff(lambda_client, functions.trainer)
    slate_dates = prospective_slate_dates(
        cutoff,
        now_utc=now_utc,
        max_slate_days=max_slate_days,
    )
    rows: List[Dict[str, Any]] = []
    for slate_date in slate_dates:
        lock_event = {
            "sport": "mlb",
            "run": "prospective_terminal_backlog_reconciliation",
            "slateDateEt": slate_date,
            "force": True,
        }
        lock_payload = invoke_json(lambda_client, functions.lock, lock_event)
        status_event = {
            "httpMethod": "GET",
            "path": "/v1/mlb/locks/status",
            "queryStringParameters": {"date": slate_date},
        }
        official_status = invoke_json(
            lambda_client,
            functions.lock,
            status_event,
        )
        lock_evidence = validate_lock_result(
            lock_payload,
            official_status,
            slate_date,
        )

        settlement_event = {
            "sport": "mlb",
            "run": "prospective_backlog_settlement",
            "slate_date": slate_date,
            "days_from": 0,
        }
        settlement_payload = invoke_json(
            lambda_client,
            functions.results,
            settlement_event,
        )
        settlement_evidence = validate_settlement_result(
            settlement_payload,
            slate_date,
        )
        validate_settlement_lock_binding(
            lock_evidence,
            settlement_evidence,
        )
        rows.append(
            {
                **lock_evidence,
                "settlement": settlement_evidence,
                "protectedLockReplay": True,
                "readOnlyOfficialStatusProof": True,
                "directTableWrite": False,
                "postStartPredictionCreationAllowed": False,
            }
        )

    return {
        "ok": True,
        "version": VERSION,
        "stackName": stack_name,
        "releaseCutoffUtc": cutoff,
        "firstSlateDateEt": slate_dates[0] if slate_dates else None,
        "lastSlateDateEt": slate_dates[-1] if slate_dates else None,
        "reconciledSlateCount": len(rows),
        "slates": rows,
        "boundedMaximumSlateDays": max_slate_days,
        "protectedLockReplay": True,
        "readOnlyOfficialStatusProof": True,
        "protectedSettlementReplay": True,
        "directTableWrite": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "promotionAuthorityChanged": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
    }


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(report), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-slate-days",
        type=int,
        default=DEFAULT_MAX_SLATE_DAYS,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    session = boto3.session.Session(region_name=args.region)
    config = Config(connect_timeout=10, read_timeout=930, retries={"max_attempts": 3, "mode": "standard"})
    cloudformation = session.client("cloudformation", config=config)
    lambda_client = session.client("lambda", config=config)
    try:
        report = reconcile(
            cloudformation,
            lambda_client,
            stack_name=args.stack_name,
            max_slate_days=args.max_slate_days,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "version": VERSION,
            "stackName": args.stack_name,
            "error": f"{type(exc).__name__}:{exc}",
            "directTableWrite": False,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "promotionAuthorityChanged": False,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
        }
        _write_report(args.output, report)
        print(json.dumps(report, sort_keys=True), file=sys.stderr)
        return 1
    _write_report(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
