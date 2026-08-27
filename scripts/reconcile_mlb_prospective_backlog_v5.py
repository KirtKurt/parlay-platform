#!/usr/bin/env python3
"""Reconcile MLB prospective backlog with settlement-triggered terminal replay.

Read-only lock status bodies may project a missed lifecycle game as terminal
coverage before a durable no-prediction outcome exists. Canonical settlement is
the stronger durability authority. When, and only when, settlement returns the
exact conflict-free 409 shape proving official finals lack a canonical lock or
durable terminal outcome, this adapter invokes the existing protected lock
replay, verifies the exact-date official status, and retries the full bounded
reconciliation. The 409 is never treated as success and no storage is written
directly by this script.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Mapping, Optional

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v3 as v3
import reconcile_mlb_prospective_backlog_v4 as v4

VERSION = (
    "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v5.8-"
    "eventbridge-owner-cooperative-terminal-replay"
)
STATUS_PATH = "/v1/mlb/locks/status"
COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS = 100
SETTLEMENT_RUN = "prospective_backlog_settlement_v4"
TERMINAL_REPLAY_RUN = "prospective_terminal_backlog_reconciliation_v5"
MISSING_LOCK_REASON = "MISSING_VALID_CANONICAL_LOCK_OR_TERMINAL_OUTCOME"
SAFE_APPLICATION_FIELDS = (
    "error",
    "reason",
    "status",
    "overall_status",
    "lockStatus",
    "lifecycleStatus",
    "slateDateEt",
    "slate_date_et",
    "run",
    "authoritativeSettlement",
    "officialGameCount",
    "officialFinalCount",
    "canonicalLockCount",
    "rejectedCanonicalLockCount",
    "terminalNoPredictionCount",
    "missedLockValidPrelockQuarantineCount",
    "terminalExcludedCount",
    "lockTerminalConflictCount",
    "terminalNoPredictionExcludedCount",
    "skippedNotFinalCount",
    "missingCanonicalLockCount",
    "identityRejectionCount",
    "labelWriteCount",
    "labelCreatedCount",
    "labelIdempotentCount",
    "labelPolicyDriftIdempotentCount",
    "labelWouldCreateCount",
    "labelConflictCount",
    "immutablePregameRowsMutated",
)
SAFE_FAILURE_COLLECTIONS = (
    "rejectedCanonicalLocks",
    "rejectedTerminalOutcomes",
    "lockTerminalConflictOfficialGamePks",
    "missingCanonicalLocks",
    "identityRejections",
    "labelWrites",
    "immutablePregameReadbackErrors",
)
SAFE_FAILURE_ROW_FIELDS = (
    "officialGamePk",
    "sourcePk",
    "sourceSk",
    "status",
    "reason",
    "error",
    "errors",
    "candidateCount",
    "lockedTeams",
    "officialTeams",
    "existingSettlementFingerprint",
    "proposedSettlementFingerprint",
    "existingImmutableFactsFingerprint",
    "proposedImmutableFactsFingerprint",
)
MAX_DIAGNOSTIC_ITEMS = 8
MAX_DIAGNOSTIC_STRING = 480
PROTECTED_REPLAY_LEASE_SECONDS = 960
PROTECTED_REPLAY_SCHEDULE_PERIOD_SECONDS = 60
PROTECTED_REPLAY_SCHEDULING_MARGIN_SECONDS = (
    PROTECTED_REPLAY_SCHEDULE_PERIOD_SECONDS
)
MAX_PROTECTED_REPLAY_ATTEMPTS = 90
PROTECTED_REPLAY_EXECUTION_BUDGET_SECONDS = 600
PROTECTED_REPLAY_MAX_MANIFEST_GAMES = 15
PROTECTED_REPLAY_MAX_EVENTBRIDGE_TICKS_PER_TARGET = 2
PROTECTED_REPLAY_TWO_PHASE_TARGET_COUNT = (
    PROTECTED_REPLAY_MAX_MANIFEST_GAMES * 2
)
PROTECTED_REPLAY_WORST_CASE_HANDOFF_SECONDS = (
    (
        PROTECTED_REPLAY_TWO_PHASE_TARGET_COUNT
        * PROTECTED_REPLAY_MAX_EVENTBRIDGE_TICKS_PER_TARGET
        + 1
    )
    * PROTECTED_REPLAY_SCHEDULE_PERIOD_SECONDS
    + PROTECTED_REPLAY_LEASE_SECONDS
    + PROTECTED_REPLAY_SCHEDULING_MARGIN_SECONDS
)
PROTECTED_REPLAY_COOPERATIVE_BOUND_SECONDS = (
    PROTECTED_REPLAY_LEASE_SECONDS
    + PROTECTED_REPLAY_SCHEDULING_MARGIN_SECONDS
    + PROTECTED_REPLAY_EXECUTION_BUDGET_SECONDS
)
# The first owner-handoff poll is quick. Subsequent 61-second polls cannot
# phase-lock to the one-minute schedule. Ninety attempts provide about
# 90 minutes, exceeding the derived 15-game PROCESS + VERIFY bound when each
# target needs two EventBridge ticks, the completion tick, and one full extant
# writer lease/alignment interval. The poller gains no ownership or write
# authority; the timeout remains finite and fail closed.
PROTECTED_REPLAY_RETRY_DELAYS_SECONDS = (20,) + (61,) * 88
PROTECTED_REPLAY_RETRY_HORIZON_SECONDS = sum(
    PROTECTED_REPLAY_RETRY_DELAYS_SECONDS
)
PROTECTED_REPLAY_RETRY_PHASES_SECONDS = tuple(
    sum(PROTECTED_REPLAY_RETRY_DELAYS_SECONDS[:index])
    % PROTECTED_REPLAY_SCHEDULE_PERIOD_SECONDS
    for index in range(1, len(PROTECTED_REPLAY_RETRY_DELAYS_SECONDS) + 1)
)
if (
    len(PROTECTED_REPLAY_RETRY_DELAYS_SECONDS)
    != MAX_PROTECTED_REPLAY_ATTEMPTS - 1
    or PROTECTED_REPLAY_RETRY_HORIZON_SECONDS
    < PROTECTED_REPLAY_COOPERATIVE_BOUND_SECONDS
    or PROTECTED_REPLAY_RETRY_HORIZON_SECONDS
    < PROTECTED_REPLAY_WORST_CASE_HANDOFF_SECONDS
):
    raise RuntimeError("protected_terminal_replay_retry_horizon_invalid")
if (
    len(set(PROTECTED_REPLAY_RETRY_PHASES_SECONDS))
    < PROTECTED_REPLAY_SCHEDULE_PERIOD_SECONDS
    or max(
        PROTECTED_REPLAY_RETRY_PHASES_SECONDS.count(phase)
        for phase in set(PROTECTED_REPLAY_RETRY_PHASES_SECONDS)
    )
    > 2
):
    raise RuntimeError(
        "protected_terminal_replay_retry_schedule_phase_locked"
    )
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|authorization|password|credential)"
    r"(\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|(?:(bearer)\s+)?[^\s,;\]}]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")


class DurableTerminalReplayRequired(base.ReconciliationError):
    """A conflict-free settlement gap requires the protected terminal replay."""

    def __init__(self, slate_date: str, detail: Mapping[str, Any]):
        self.slate_date = slate_date
        self.detail = dict(detail)
        super().__init__(
            "settlement_requires_protected_terminal_replay:"
            + json.dumps(self.detail, sort_keys=True, separators=(",", ":"))
        )


def _is_read_only_status_event(event: Mapping[str, Any]) -> bool:
    return (
        str(event.get("httpMethod") or "").upper() == "GET"
        and str(event.get("path") or "") == STATUS_PATH
    )


def _event_kind(event: Mapping[str, Any]) -> str:
    if _is_read_only_status_event(event):
        return "read_only_lock_status"
    return str(event.get("run") or "mutation_or_settlement")


def _event_slate_date(event: Mapping[str, Any]) -> str:
    query = event.get("queryStringParameters")
    query_date = query.get("date") if isinstance(query, Mapping) else None
    return str(
        event.get("slateDateEt")
        or event.get("slate_date")
        or query_date
        or ""
    )


def _redact_sensitive_assignment(match: re.Match[str]) -> str:
    scheme = "Bearer " if match.group(3) else ""
    return f"{match.group(1)}{match.group(2)}{scheme}[REDACTED]"


def _redacted_bounded_string(value: Any, *, tail: bool = False) -> str:
    text = str(value or "")
    text = _SENSITIVE_ASSIGNMENT_RE.sub(_redact_sensitive_assignment, text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _AWS_ACCESS_KEY_RE.sub("[REDACTED_AWS_ACCESS_KEY]", text)
    if tail:
        return text[-MAX_DIAGNOSTIC_STRING:]
    return text[:MAX_DIAGNOSTIC_STRING]


def _bounded_string(value: Any) -> str:
    return _redacted_bounded_string(value)


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_string(value)
    if isinstance(value, (tuple, list)):
        return [_safe_scalar(item) for item in value[:MAX_DIAGNOSTIC_ITEMS]]
    return _bounded_string(value)


def _safe_failure_row(value: Any) -> Any:
    if isinstance(value, Mapping):
        row: Dict[str, Any] = {}
        for key in SAFE_FAILURE_ROW_FIELDS:
            item = value.get(key)
            if item not in (None, "", [], {}):
                row[key] = _safe_scalar(item)
        return row or {"diagnostic": "failure_row_redacted"}
    return _safe_scalar(value)


def _safe_failure_sample(values: Any) -> list[Any]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes, Mapping)):
        return []
    return [_safe_failure_row(value) for value in list(values)[:MAX_DIAGNOSTIC_ITEMS]]


def _safe_application_detail(
    application_status: int,
    application: Mapping[str, Any],
    event: Mapping[str, Any],
) -> str:
    detail: Dict[str, Any] = {
        "applicationStatusCode": application_status,
        "eventKind": _event_kind(event),
    }
    for key in SAFE_APPLICATION_FIELDS:
        value = application.get(key)
        if value not in (None, "", [], {}):
            detail[key] = _safe_scalar(value)
    for key in SAFE_FAILURE_COLLECTIONS:
        values = application.get(key)
        if values not in (None, "", [], {}):
            detail[f"{key}Sample"] = _safe_failure_sample(values)
            if isinstance(values, (list, tuple)):
                detail[f"{key}ObservedCount"] = len(values)
    return json.dumps(detail, sort_keys=True, default=str, separators=(",", ":"))


def _safe_lambda_function_error_detail(
    function_name: str,
    event: Mapping[str, Any],
    response: Mapping[str, Any],
    payload_bytes: bytes,
) -> str:
    """Return bounded, redacted FunctionError evidence and no request payload."""

    detail: Dict[str, Any] = {
        "functionName": _redacted_bounded_string(function_name),
        "functionError": _redacted_bounded_string(response.get("FunctionError")),
        "eventKind": _event_kind(event),
        "slateDateEt": _event_slate_date(event),
        "requestPayloadIncluded": False,
        "secretExposed": False,
    }
    try:
        parsed = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        parsed = None
        detail["payloadParseError"] = type(exc).__name__
    if isinstance(parsed, Mapping):
        error_type = parsed.get("errorType")
        error_message = parsed.get("errorMessage")
        if error_type not in (None, ""):
            detail["errorType"] = _redacted_bounded_string(error_type)
        if error_message not in (None, ""):
            detail["errorMessage"] = _redacted_bounded_string(error_message)

    encoded_log = response.get("LogResult")
    if encoded_log not in (None, ""):
        try:
            decoded_log = base64.b64decode(str(encoded_log), validate=True).decode(
                "utf-8", errors="replace"
            )
            detail["redactedLogTail"] = _redacted_bounded_string(
                decoded_log,
                tail=True,
            )
        except Exception as exc:
            detail["logTailParseError"] = type(exc).__name__

    return json.dumps(detail, sort_keys=True, default=str, separators=(",", ":"))


def _nonnegative_integer(value: Any, *, field: str) -> int:
    return base._integer(value, field=field)


def _terminal_replay_detail(
    application_status: int,
    application: Mapping[str, Any],
    event: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Recognize only the exact conflict-free durable-terminal settlement gap."""

    if application_status != 409 or str(event.get("run") or "") != SETTLEMENT_RUN:
        return None
    if application.get("authoritativeSettlement") is not True:
        return None
    if str(application.get("status") or application.get("overall_status") or "") != "FAILED_CLOSED":
        return None
    if application.get("immutablePregameRowsMutated") is not False:
        return None
    rejected_terminal = application.get("rejectedTerminalOutcomes") or []
    if not isinstance(rejected_terminal, list) or rejected_terminal:
        return None

    slate_date = str(event.get("slate_date") or event.get("slateDateEt") or "")
    returned_date = str(
        application.get("slateDateEt") or application.get("slate_date_et") or ""
    )
    if not slate_date or returned_date != slate_date:
        return None

    try:
        official = _nonnegative_integer(application.get("officialGameCount"), field="official_game_count")
        finals = _nonnegative_integer(application.get("officialFinalCount"), field="official_final_count")
        canonical = _nonnegative_integer(application.get("canonicalLockCount"), field="canonical_lock_count")
        terminal = _nonnegative_integer(application.get("terminalNoPredictionCount"), field="terminal_no_prediction_count")
        quarantine = _nonnegative_integer(
            application.get("missedLockValidPrelockQuarantineCount", 0),
            field="missed_lock_valid_prelock_quarantine_count",
        )
        missing = _nonnegative_integer(application.get("missingCanonicalLockCount"), field="missing_canonical_lock_count")
        rejected = _nonnegative_integer(application.get("rejectedCanonicalLockCount"), field="rejected_canonical_lock_count")
        conflicts = _nonnegative_integer(application.get("lockTerminalConflictCount"), field="lock_terminal_conflict_count")
        identity_rejections = _nonnegative_integer(application.get("identityRejectionCount"), field="identity_rejection_count")
        label_conflicts = _nonnegative_integer(application.get("labelConflictCount"), field="label_conflict_count")
        skipped = _nonnegative_integer(application.get("skippedNotFinalCount"), field="skipped_not_final_count")
    except base.ReconciliationError:
        return None

    if not official or finals != official or skipped:
        return None
    if not missing or rejected or conflicts or identity_rejections or label_conflicts:
        return None
    if canonical + terminal + quarantine + missing != official:
        return None

    missing_rows = application.get("missingCanonicalLocks") or []
    if not isinstance(missing_rows, list) or len(missing_rows) != missing:
        return None
    if any(
        not isinstance(row, Mapping)
        or not str(row.get("officialGamePk") or "")
        or str(row.get("reason") or "") != MISSING_LOCK_REASON
        for row in missing_rows
    ):
        return None

    return {
        "applicationStatusCode": application_status,
        "slateDateEt": slate_date,
        "officialGameCount": official,
        "officialFinalCount": finals,
        "canonicalLockCount": canonical,
        "terminalNoPredictionCount": terminal,
        "missedLockValidPrelockQuarantineCount": quarantine,
        "terminalExcludedCount": terminal + quarantine,
        "missingCanonicalLockCount": missing,
        "missingOfficialGamePks": [
            str(row.get("officialGamePk")) for row in missing_rows[:MAX_DIAGNOSTIC_ITEMS]
        ],
        "missingOfficialGamePkCount": len(missing_rows),
        "authoritativeSettlement": True,
        "conflictFree": True,
        "immutablePregameRowsMutated": False,
    }


def invoke_json_preserving_status_body(
    lambda_client: Any,
    function_name: str,
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Perform one SDK delivery; preserve status evidence and signal safe replay."""

    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        LogType="Tail",
        Payload=json.dumps(event, separators=(",", ":")).encode("utf-8"),
    )
    status_code = base._integer(response.get("StatusCode"), field="lambda_status_code")
    payload_bytes = base._read_lambda_payload(response)
    if status_code != 200:
        raise base.ReconciliationError("lambda_invoke_status_not_200")
    if response.get("FunctionError"):
        raise base.ReconciliationError(
            "lambda_function_error:"
            + _safe_lambda_function_error_detail(
                function_name,
                event,
                response,
                payload_bytes,
            )
        )

    payload = base._json_object(payload_bytes.decode("utf-8"), error="lambda_response_json_invalid")
    if "statusCode" not in payload:
        return payload

    application_status = base._integer(payload.get("statusCode"), field="application_status_code")
    body = payload.get("body")
    application = (
        base._json_object(body, error="application_body_json_invalid")
        if isinstance(body, str)
        else dict(body or {})
    )
    if 200 <= application_status < 300:
        return application
    if not _is_read_only_status_event(event):
        replay_detail = _terminal_replay_detail(application_status, application, event)
        if replay_detail is not None:
            raise DurableTerminalReplayRequired(replay_detail["slateDateEt"], replay_detail)
        raise base.ReconciliationError(
            "lambda_application_status_not_success:"
            + _safe_application_detail(application_status, application, event)
        )

    application = dict(application)
    application["_applicationStatusCode"] = application_status
    application["_nonSuccessStatusBodyPreserved"] = True
    return application


@contextmanager
def _status_body_adapter():
    original = base.invoke_json
    base.invoke_json = invoke_json_preserving_status_body
    try:
        yield
    finally:
        base.invoke_json = original


def _validated_safe_cooperative_completion_receipt(
    replay: Mapping[str, Any],
    slate_date: str,
) -> Dict[str, Any]:
    """Validate the exact redacted receipt persisted by the protected owner."""

    def count(value: Any, field: str) -> int:
        if isinstance(value, bool):
            raise base.ReconciliationError(
                f"cooperative_completion_receipt_{field}_invalid"
            )
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise base.ReconciliationError(
                f"cooperative_completion_receipt_{field}_invalid"
            ) from exc
        if (
            not numeric.is_finite()
            or numeric < 0
            or numeric != numeric.to_integral_value()
        ):
            raise base.ReconciliationError(
                f"cooperative_completion_receipt_{field}_invalid"
            )
        return int(numeric)

    progress = replay.get("perGameLockProgress")
    repair = replay.get("missedLockTerminalReconciliation")
    if not isinstance(progress, Mapping) or not isinstance(repair, Mapping):
        raise base.ReconciliationError(
            "cooperative_completion_receipt_progress_missing"
        )
    repair_progress = repair.get("progressAfter")
    if not isinstance(repair_progress, Mapping):
        raise base.ReconciliationError(
            "cooperative_completion_receipt_repair_progress_missing"
        )

    version = (
        "MLB-COOPERATIVE-TERMINAL-CHUNK-"
        "v4-valid-prelock-quarantine"
    )
    count_fields = (
        "manifestGameCount",
        "processedGameCount",
        "verifiedGameCount",
        "verificationIndex",
        "canonicalCount",
        "noPredictionDataCount",
        "missedLockValidPrelockQuarantineCount",
        "lockOutcomeCount",
        "missedCount",
        "dueMissingCount",
        "atomicDurableItemCount",
    )
    counts = {
        field: count(progress.get(field), field)
        for field in count_fields
    }
    manifest = counts["manifestGameCount"]
    processed = counts["processedGameCount"]
    verified = counts["verifiedGameCount"]
    verification_index = counts["verificationIndex"]
    canonical = counts["canonicalCount"]
    no_data = counts["noPredictionDataCount"]
    quarantine = counts[
        "missedLockValidPrelockQuarantineCount"
    ]
    lock_outcomes = counts["lockOutcomeCount"]
    atomic_items = counts["atomicDurableItemCount"]
    reconciled = count(repair.get("reconciledCount"), "reconciledCount")
    remaining = count(
        repair.get("remainingMissedCount"), "remainingMissedCount"
    )
    fingerprints = {
        "checkpointFingerprint": str(
            replay.get("checkpointFingerprint") or ""
        ),
        "manifestFingerprint": str(
            replay.get("manifestFingerprint") or ""
        ),
        "manifestAuthorityEvidenceFingerprint": str(
            progress.get("manifestAuthorityEvidenceFingerprint") or ""
        ),
        "providerManifestFingerprint": str(
            progress.get("providerManifestFingerprint") or ""
        ),
        "atomicDurableReadSetFingerprint": str(
            progress.get("atomicDurableReadSetFingerprint") or ""
        ),
    }
    terminal_games_raw = progress.get("terminalGames")
    if (
        not isinstance(terminal_games_raw, list)
        or len(terminal_games_raw) != manifest
    ):
        raise base.ReconciliationError(
            "cooperative_completion_receipt_terminal_games_invalid"
        )
    terminal_games = []
    terminal_official_seen = set()
    terminal_states = {
        "LOCKED_CANONICAL": 0,
        "LOCKED_NO_PREDICTION_DATA": 0,
        "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED": 0,
    }
    allowed_terminal_game_keys = {
        "index",
        "officialGamePk",
        "gameIdentity",
        "durableIdentity",
        "terminalState",
        "evidenceFingerprint",
    }
    for index, entry in enumerate(terminal_games_raw):
        if (
            not isinstance(entry, Mapping)
            or set(entry) != allowed_terminal_game_keys
        ):
            raise base.ReconciliationError(
                "cooperative_completion_receipt_terminal_game_invalid"
            )
        entry_index = count(entry.get("index"), "terminalGameIndex")
        official_pk = str(entry.get("officialGamePk") or "")
        official_pk_number = count(
            entry.get("officialGamePk"),
            "terminalGameOfficialGamePk",
        )
        game_identity = str(entry.get("gameIdentity") or "")
        durable_identity = str(entry.get("durableIdentity") or "")
        terminal_state = str(entry.get("terminalState") or "")
        evidence_fingerprint = str(
            entry.get("evidenceFingerprint") or ""
        )
        if (
            entry_index != index
            or official_pk_number <= 0
            or not official_pk
            or official_pk in terminal_official_seen
            or not game_identity
            or not durable_identity
            or terminal_state not in terminal_states
            or re.fullmatch(r"[0-9a-f]{64}", evidence_fingerprint)
            is None
        ):
            raise base.ReconciliationError(
                "cooperative_completion_receipt_terminal_game_invalid"
            )
        terminal_official_seen.add(official_pk)
        terminal_states[terminal_state] += 1
        terminal_games.append(
            {
                "index": index,
                "officialGamePk": official_pk,
                "gameIdentity": game_identity,
                "durableIdentity": durable_identity,
                "terminalState": terminal_state,
                "evidenceFingerprint": evidence_fingerprint,
            }
        )
    terminal_game_set_fingerprint = hashlib.sha256(
        json.dumps(
            terminal_games,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        str(progress.get("terminalGameSetFingerprint") or "")
        != terminal_game_set_fingerprint
        or terminal_states["LOCKED_CANONICAL"] != canonical
        or terminal_states["LOCKED_NO_PREDICTION_DATA"] != no_data
        or terminal_states[
            "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
        ]
        != quarantine
    ):
        raise base.ReconciliationError(
            "cooperative_completion_receipt_terminal_game_set_invalid"
        )

    progress_copies_match = all(
        count(repair_progress.get(field), f"repair_{field}")
        == expected
        for field, expected in counts.items()
    )
    repair_header_match = all(
        count(repair.get(field), f"repair_header_{field}")
        == counts[field]
        for field in (
            "manifestGameCount",
            "processedGameCount",
            "verifiedGameCount",
            "verificationIndex",
            "atomicDurableItemCount",
            "missedLockValidPrelockQuarantineCount",
        )
    )
    cached_idempotent = bool(
        str(replay.get("reason") or "")
        == "POST_WINDOW_TERMINAL_STATUS_ALREADY_RECONCILED"
        and reconciled == 0
    )
    if (
        replay.get("ok") is not True
        or replay.get("sport") != "mlb"
        or str(replay.get("slateDateEt") or "") != slate_date
        or replay.get("terminalChunkVersion") != version
        or replay.get("cooperativeReceiptRedacted") is not True
        or replay.get("verificationPhase") != "VERIFY"
        or manifest <= 0
        or processed != manifest
        or verified != manifest
        or verification_index != manifest
        or lock_outcomes != manifest
        or canonical + no_data + quarantine != manifest
        or counts["missedCount"] != 0
        or counts["dueMissingCount"] != 0
        or replay.get("durableTerminalVerificationComplete") is not True
        or replay.get("atomicDurableProofRequired") is not True
        or progress.get("verificationComplete") is not True
        or progress.get("atomicDurableProofRequired") is not True
        or not manifest <= atomic_items <= COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS
        or count(
            replay.get("atomicDurableItemCount"),
            "top_atomicDurableItemCount",
        )
        != atomic_items
        or replay.get("completionMutationLeaseRequired") is not True
        or repair.get("ok") is not True
        or repair.get("version") != version
        or str(repair.get("slateDateEt") or "") != slate_date
        or repair.get("durableTerminalVerificationComplete") is not True
        or repair.get("atomicDurableProofRequired") is not True
        or repair.get("completionMutationLeaseRequired") is not True
        or remaining != 0
        or repair.get("unresolved") != []
        or repair.get("candidateIntegrityFailuresRelabeled") is not False
        or repair.get("postStartPredictionCreationAllowed") is not False
        or (reconciled <= 0 and not cached_idempotent)
        or not progress_copies_match
        or repair_progress.get("terminalGames") != terminal_games_raw
        or str(
            repair_progress.get("terminalGameSetFingerprint") or ""
        )
        != terminal_game_set_fingerprint
        or str(
            repair_progress.get(
                "manifestAuthorityEvidenceFingerprint"
            )
            or ""
        )
        != fingerprints["manifestAuthorityEvidenceFingerprint"]
        or str(
            repair_progress.get("providerManifestFingerprint") or ""
        )
        != fingerprints["providerManifestFingerprint"]
        or str(replay.get("providerManifestFingerprint") or "")
        != fingerprints["providerManifestFingerprint"]
        or str(
            replay.get("atomicDurableReadSetFingerprint") or ""
        )
        != fingerprints["atomicDurableReadSetFingerprint"]
        or str(
            repair.get("atomicDurableReadSetFingerprint") or ""
        )
        != fingerprints["atomicDurableReadSetFingerprint"]
        or not repair_header_match
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in fingerprints.values()
        )
        or replay.get("postStartPredictionCreationAllowed") is not False
        or replay.get("immutablePredictionRewriteAllowed") is not False
        or replay.get("directWorkflowTableWrite") is not False
        or replay.get("productionAuthorityChanged") is not False
    ):
        raise base.ReconciliationError(
            "cooperative_completion_receipt_invalid"
        )
    return {
        "version": version,
        "slateDateEt": slate_date,
        **fingerprints,
        "terminalGames": terminal_games,
        "terminalGameSetFingerprint": terminal_game_set_fingerprint,
        "manifestGameCount": manifest,
        "processedGameCount": processed,
        "verifiedGameCount": verified,
        "verificationIndex": verification_index,
        "verificationComplete": True,
        "canonicalCount": canonical,
        "noPredictionDataCount": no_data,
        "missedLockValidPrelockQuarantineCount": quarantine,
        "lockOutcomeCount": lock_outcomes,
        "reconciledCount": reconciled,
        "missedCount": 0,
        "dueMissingCount": 0,
        "durableTerminalVerificationComplete": True,
        "atomicDurableProofRequired": True,
        "atomicDurableItemCount": atomic_items,
        "completionMutationLeaseRequired": True,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
    }

def _execute_protected_terminal_replay(
    cloudformation: Any,
    lambda_client: Any,
    *,
    stack_name: str,
    request: DurableTerminalReplayRequired,
    sleep: Any = time.sleep,
    max_attempts: int = MAX_PROTECTED_REPLAY_ATTEMPTS,
) -> Dict[str, Any]:
    if max_attempts < 1:
        raise base.ReconciliationError("protected_terminal_replay_attempts_invalid")
    functions = base.resolve_stack_functions(cloudformation, stack_name)
    overlap_retry_count = 0
    cooperative_poll_count = 0
    cooperative_handoff_observed = False
    cooperative_acknowledged = False
    acknowledgement_state: Mapping[str, Any] = {}
    with _status_body_adapter():
        for attempt in range(1, max_attempts + 1):
            replay = v4.invoke_json_with_backpressure(
                lambda_client,
                functions.lock,
                {
                    "sport": "mlb",
                    "run": TERMINAL_REPLAY_RUN,
                    "slateDateEt": request.slate_date,
                    "force": True,
                },
            )
            cooperative = replay.get("cooperativeTerminalReplay") or {}
            if cooperative and not isinstance(cooperative, Mapping):
                raise base.ReconciliationError(
                    "protected_terminal_replay_cooperative_state_invalid"
                )
            cooperative_state = (
                str(cooperative.get("state") or "")
                if isinstance(cooperative, Mapping)
                else ""
            )
            cooperative_pending = bool(
                cooperative_state in {"QUEUED", "CLAIMED"}
                and replay.get("cooperativeTerminalReplayCompleted") is False
                and replay.get("mutatingRunAttempted") is False
            )
            if cooperative_pending:
                cooperative_handoff_observed = True
                cooperative_poll_count += 1
                if attempt >= max_attempts:
                    raise base.ReconciliationError(
                        "protected_terminal_replay_cooperative_retry_exhausted:"
                        + request.slate_date
                    )
                sleep(
                    PROTECTED_REPLAY_RETRY_DELAYS_SECONDS[
                        min(
                            attempt - 1,
                            len(PROTECTED_REPLAY_RETRY_DELAYS_SECONDS) - 1,
                        )
                    ]
                )
                continue
            concurrency = replay.get("lockExecutionConcurrency") or {}
            overlap = bool(
                str(replay.get("reason") or replay.get("status") or "")
                == "SKIPPED_OVERLAPPING_LOCK_EXECUTION"
                or str(replay.get("error") or "")
                == "MLB_LOCK_EXECUTION_ALREADY_RUNNING"
                or (
                    isinstance(concurrency, Mapping)
                    and concurrency.get("overlapSkipped") is True
                )
                or (
                    replay.get("skipped") is True
                    and replay.get("mutatingRunAttempted") is False
                )
            )
            if overlap:
                overlap_retry_count += 1
                if attempt >= max_attempts:
                    raise base.ReconciliationError(
                        "protected_terminal_replay_overlap_retry_exhausted:"
                        + request.slate_date
                    )
                sleep(
                    PROTECTED_REPLAY_RETRY_DELAYS_SECONDS[
                        min(
                            attempt - 1,
                            len(PROTECTED_REPLAY_RETRY_DELAYS_SECONDS) - 1,
                        )
                    ]
                )
                continue
            status = v4.read_official_status_with_consistency_retry(
                lambda_client,
                functions.lock,
                request.slate_date,
                invoke=v4.invoke_json_with_backpressure,
                retryable_errors=v4.POST_MUTATION_STATUS_ERRORS,
            )
            evidence = v3.validate_lock_result(
                replay,
                status,
                request.slate_date,
            )
            completion_receipt = (
                _validated_safe_cooperative_completion_receipt(
                    replay,
                    request.slate_date,
                )
                if replay.get("cooperativeTerminalReplayCompleted") is True
                else None
            )
            if completion_receipt is not None:
                official_lifecycle_games = evidence.get(
                    "lifecycleGames"
                )
                if not isinstance(official_lifecycle_games, list):
                    raise base.ReconciliationError(
                        "cooperative_completion_receipt_official_"
                        "lifecycle_missing"
                    )
                receipt_lifecycle_games = sorted(
                    [
                        {
                            "officialGamePk": str(
                                row.get("officialGamePk") or ""
                            ),
                            "gameIdentity": str(
                                row.get("gameIdentity") or ""
                            ),
                            "terminalState": str(
                                row.get("terminalState") or ""
                            ),
                        }
                        for row in completion_receipt["terminalGames"]
                    ],
                    key=lambda row: int(row["officialGamePk"]),
                )
                if receipt_lifecycle_games != official_lifecycle_games:
                    raise base.ReconciliationError(
                        "cooperative_completion_receipt_official_"
                        "lifecycle_mismatch"
                    )
                official_provider_manifest_fingerprint = str(
                    evidence.get("providerManifestFingerprint") or ""
                )
                if (
                    re.fullmatch(
                        r"[0-9a-f]{64}",
                        official_provider_manifest_fingerprint,
                    )
                    is None
                    or completion_receipt.get(
                        "providerManifestFingerprint"
                    )
                    != official_provider_manifest_fingerprint
                ):
                    raise base.ReconciliationError(
                        "cooperative_completion_receipt_provider_"
                        "manifest_mismatch"
                    )
                exact_receipt_bindings = {
                    "manifestGameCount": "manifestGameCount",
                    "canonicalCount": "canonicalPredictionCount",
                    "noPredictionDataCount": "terminalNoPredictionCount",
                    "missedLockValidPrelockQuarantineCount": (
                        "missedLockValidPrelockQuarantineCount"
                    ),
                    "lockOutcomeCount": "lockOutcomeCount",
                }
                if any(
                    completion_receipt.get(receipt_field)
                    != evidence.get(evidence_field)
                    for receipt_field, evidence_field
                    in exact_receipt_bindings.items()
                ):
                    raise base.ReconciliationError(
                        "cooperative_completion_receipt_official_"
                        "status_mismatch"
                    )
            if cooperative_state in {"COMPLETED", "ACKNOWLEDGED"}:
                cooperative_handoff_observed = True
                acknowledgement = v4.invoke_json_with_backpressure(
                    lambda_client,
                    functions.lock,
                    {
                        "sport": "mlb",
                        "run": TERMINAL_REPLAY_RUN,
                        "slateDateEt": request.slate_date,
                        "force": True,
                        "acknowledgeCooperativeCompletion": True,
                    },
                )
                acknowledgement_state = (
                    acknowledgement.get("cooperativeTerminalReplay") or {}
                )
                if (
                    acknowledgement.get("ok") is not True
                    or acknowledgement.get(
                        "cooperativeTerminalReplayAcknowledged"
                    )
                    is not True
                    or not isinstance(acknowledgement_state, Mapping)
                    or str(acknowledgement_state.get("state") or "")
                    != "ACKNOWLEDGED"
                    or str(acknowledgement.get("slateDateEt") or "")
                    != request.slate_date
                ):
                    raise base.ReconciliationError(
                        "protected_terminal_replay_cooperative_ack_invalid"
                    )
                cooperative_acknowledged = True
            break
        else:
            raise base.ReconciliationError(
                "protected_terminal_replay_retry_state_invalid"
            )
    final_cooperative_state = (
        acknowledgement_state
        if acknowledgement_state
        else cooperative
        if isinstance(cooperative, Mapping)
        else {}
    )
    terminal_progress = (
        final_cooperative_state.get("terminalChunkProgress") or {}
        if isinstance(final_cooperative_state, Mapping)
        else {}
    )
    try:
        progress_manifest_count = int(
            terminal_progress.get("manifestGameCount")
        )
        progress_processed_count = int(
            terminal_progress.get("processedGameCount")
        )
        progress_terminal_count = int(
            terminal_progress.get("terminalCount")
        )
        progress_verified_count = int(
            terminal_progress.get("verifiedGameCount")
        )
    except (TypeError, ValueError):
        progress_manifest_count = -1
        progress_processed_count = -1
        progress_terminal_count = -1
        progress_verified_count = -1
    cooperative_receipt_verified = bool(
        replay.get("cooperativeTerminalReplayCompleted") is True
        and isinstance(final_cooperative_state, Mapping)
        and str(final_cooperative_state.get("state") or "")
        in {"COMPLETED", "ACKNOWLEDGED"}
        and final_cooperative_state.get("version")
        == (
            "MLB-COOPERATIVE-TERMINAL-REPLAY-"
            "v1-eventbridge-owner-handoff"
        )
        and isinstance(terminal_progress, Mapping)
        and terminal_progress.get("valid") is True
        and terminal_progress.get("version")
        == (
            "MLB-COOPERATIVE-TERMINAL-CHUNK-"
            "v4-valid-prelock-quarantine"
        )
        and progress_manifest_count > 0
        and progress_processed_count == progress_manifest_count
        and progress_terminal_count == progress_manifest_count
        and progress_verified_count == progress_manifest_count
        and terminal_progress.get("verificationComplete") is True
        and final_cooperative_state.get("ownerIdentifierExposed") is False
    )
    if cooperative_handoff_observed and not cooperative_receipt_verified:
        raise base.ReconciliationError(
            "protected_terminal_replay_cooperative_receipt_invalid"
        )

    return {
        "slateDateEt": request.slate_date,
        "settlementFailure": dict(request.detail),
        "lockEvidence": evidence,
        "cooperativeCompletionReceipt": completion_receipt,
        "protectedLockReplay": True,
        "protectedLockReplayAttemptCount": attempt,
        "protectedLockReplayOverlapRetryCount": overlap_retry_count,
        "protectedLockReplayCooperativePollCount": cooperative_poll_count,
        "protectedLockReplayCooperativeHandoffObserved": (
            cooperative_handoff_observed
        ),
        "protectedLockReplayCooperativeAcknowledged": cooperative_acknowledged,
        "protectedLockReplayCooperativeReceiptVerified": (
            cooperative_receipt_verified
        ),
        "protectedLockReplayCooperativeReceipt": dict(
            final_cooperative_state
        ),
        "protectedLockReplayAutomaticExecutionOwner": (
            "eventbridge_daily_lock_schedule"
            if cooperative_handoff_observed
            else None
        ),
        "protectedLockReplayLeaseSeconds": PROTECTED_REPLAY_LEASE_SECONDS,
        "protectedLockReplayRetryHorizonSeconds": (
            PROTECTED_REPLAY_RETRY_HORIZON_SECONDS
        ),
        "protectedLockReplayRetryScheduleDephased": True,
        "protectedLockReplayRetryDistinctMinutePhaseCount": len(
            set(PROTECTED_REPLAY_RETRY_PHASES_SECONDS)
        ),
        "settlement409TreatedAsSuccess": False,
        "directTableWrite": False,
        "directWorkflowTableWrite": False,
        "activeLeaseMutationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "postStartPredictionCreationAllowed": False,
    }


def _terminal_only_target_needs_durable_receipt(
    value: Mapping[str, Any],
    target_slate_date: str,
) -> bool:
    """Detect a closed target whose exact protected receipt must be refreshed."""

    slates = value.get("slates")
    if not isinstance(slates, list) or len(slates) != 1:
        return False
    row = slates[0]
    if (
        not isinstance(row, Mapping)
        or str(row.get("slateDateEt") or "") != target_slate_date
    ):
        return False
    try:
        manifest = int(row.get("manifestGameCount"))
        canonical = int(row.get("canonicalPredictionCount"))
        no_data = int(row.get("terminalNoPredictionCount"))
        quarantine = int(
            row.get("missedLockValidPrelockQuarantineCount")
        )
        lock_outcomes = int(row.get("lockOutcomeCount"))
    except (TypeError, ValueError):
        return False
    return bool(
        manifest > 0
        and canonical == 0
        and no_data + quarantine == manifest
        and lock_outcomes == manifest
    )


def reconcile(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    target_slate_date = str(kwargs.pop("target_slate_date", "") or "").strip()
    if target_slate_date:
        if kwargs.get("slate_dates") is not None:
            raise base.ReconciliationError("target_and_slate_dates_are_mutually_exclusive")
        kwargs["slate_dates"] = [target_slate_date]
    stack_name = str(kwargs.get("stack_name") or "")
    if len(args) < 2 or not stack_name:
        raise base.ReconciliationError("reconcile_arguments_invalid")
    cloudformation, lambda_client = args[0], args[1]
    max_replays = int(kwargs.get("max_slate_days") or base.DEFAULT_MAX_SLATE_DAYS)
    repaired: Dict[str, Dict[str, Any]] = {}
    # V5 alone may defer a complete read-only status that preserves historical
    # MISSED_NOT_BACKFILLED telemetry to canonical settlement. Standalone V4
    # retains its default protected-replay gate. Settlement remains fail closed:
    # only the exact conflict-free 409 shape can trigger the validated replay.
    kwargs["settlement_authoritative_persistent_missed"] = True

    for _ in range(max_replays + 1):
        try:
            with _status_body_adapter():
                value = v4.reconcile(*args, **kwargs)
            break
        except DurableTerminalReplayRequired as request:
            if request.slate_date in repaired:
                raise base.ReconciliationError(
                    "settlement_terminal_replay_failed_to_close_gap:"
                    + request.slate_date
                ) from request
            repaired[request.slate_date] = _execute_protected_terminal_replay(
                cloudformation,
                lambda_client,
                stack_name=stack_name,
                request=request,
            )
    else:
        raise base.ReconciliationError("settlement_terminal_replay_bound_exhausted")

    # A prior invocation may have committed one target and crashed before the
    # next. Aggregate terminal status is not an acceptable rerun proof. Refresh
    # the exact owner-fenced v4 receipt even when settlement is already closed;
    # the protected queue reuses an ACK receipt for the same date or performs a
    # read-only durable scan after another date replaced the singleton slot.
    if (
        target_slate_date
        and target_slate_date not in repaired
        and _terminal_only_target_needs_durable_receipt(
            value,
            target_slate_date,
        )
    ):
        request = DurableTerminalReplayRequired(
            target_slate_date,
            {
                "reason": (
                    "EXACT_TARGET_DURABLE_TERMINAL_RECEIPT_REVERIFY_REQUIRED"
                ),
                "aggregateStatusAcceptedAsReceipt": False,
                "directTableWrite": False,
                "postStartPredictionCreationAllowed": False,
            },
        )
        repaired[target_slate_date] = _execute_protected_terminal_replay(
            cloudformation,
            lambda_client,
            stack_name=stack_name,
            request=request,
        )
        with _status_body_adapter():
            value = v4.reconcile(*args, **kwargs)

    value = dict(value)
    value_slates = value.get("slates")
    for repaired_slate, repaired_proof in repaired.items():
        completion_receipt = repaired_proof.get(
            "cooperativeCompletionReceipt"
        )
        if not isinstance(completion_receipt, Mapping):
            continue
        if not isinstance(value_slates, list):
            raise base.ReconciliationError(
                "cooperative_completion_receipt_settlement_missing"
            )
        matching_rows = [
            row
            for row in value_slates
            if isinstance(row, Mapping)
            and str(row.get("slateDateEt") or "") == repaired_slate
        ]
        if len(matching_rows) != 1:
            raise base.ReconciliationError(
                "cooperative_completion_receipt_settlement_missing"
            )
        final_row = matching_rows[0]
        settlement = final_row.get("settlement")
        if not isinstance(settlement, Mapping):
            raise base.ReconciliationError(
                "cooperative_completion_receipt_settlement_missing"
            )
        receipt_lifecycle_games = sorted(
            [
                {
                    "officialGamePk": str(row.get("officialGamePk") or ""),
                    "gameIdentity": str(row.get("gameIdentity") or ""),
                    "terminalState": str(row.get("terminalState") or ""),
                }
                for row in completion_receipt.get("terminalGames") or []
                if isinstance(row, Mapping)
            ],
            key=lambda row: int(row["officialGamePk"]),
        )
        official_lifecycle_games = final_row.get("lifecycleGames")
        settlement_lifecycle_games = settlement.get("lifecycleGames")
        if (
            not receipt_lifecycle_games
            or receipt_lifecycle_games != official_lifecycle_games
            or receipt_lifecycle_games != settlement_lifecycle_games
            or completion_receipt.get("providerManifestFingerprint")
            != final_row.get("providerManifestFingerprint")
        ):
            raise base.ReconciliationError(
                "cooperative_completion_receipt_settlement_"
                "lifecycle_mismatch"
            )
    value["version"] = VERSION
    value["readOnlyNonSuccessStatusBodiesPreserved"] = True
    value["semanticStatusConsistencyRetryInstalled"] = True
    value["mutatingNonSuccessStatusesStillFailClosed"] = True
    value["mutatingFailureDiagnosticsWhitelisted"] = True
    value["lambdaFunctionErrorsRedacted"] = True
    value["lambdaFunctionErrorRequestPayloadIncluded"] = False
    value["settlementAuthoritativePersistentMissed"] = True
    value["settlementTriggeredProtectedTerminalReplayCount"] = len(repaired)
    value["settlementTriggeredProtectedTerminalReplays"] = list(repaired.values())
    value["settlement409TreatedAsSuccess"] = False
    value["directTableWrite"] = False
    value["postStartPredictionCreationAllowed"] = False
    value["immutablePredictionRewriteAllowed"] = False
    value["promotionAuthorityChanged"] = False
    value["productionAuthorityChanged"] = False
    value["automaticWagerAllowed"] = False
    return value


def main() -> int:
    parser = base._parser()
    parser.add_argument(
        "--target-slate-date",
        help="Reconcile one exact date inside the bounded prospective horizon.",
    )
    args = parser.parse_args()
    session = base.boto3.session.Session(region_name=args.region)
    cloudformation = session.client("cloudformation", config=v4.control_plane_config())
    lambda_client = session.client("lambda", config=v4.durable_lambda_config())
    try:
        report = reconcile(
            cloudformation,
            lambda_client,
            stack_name=args.stack_name,
            max_slate_days=args.max_slate_days,
            target_slate_date=args.target_slate_date,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "version": VERSION,
            "stackName": args.stack_name,
            "error": f"{type(exc).__name__}:{exc}",
            "readOnlyNonSuccessStatusBodiesPreserved": True,
            "mutatingNonSuccessStatusesStillFailClosed": True,
            "mutatingFailureDiagnosticsWhitelisted": True,
            "lambdaFunctionErrorsRedacted": True,
            "lambdaFunctionErrorRequestPayloadIncluded": False,
            "settlement409TreatedAsSuccess": False,
            "directTableWrite": False,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "promotionAuthorityChanged": False,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
        }
        base._write_report(args.output, report)
        print(json.dumps(report, sort_keys=True), file=sys.stderr)
        return 1
    base._write_report(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
