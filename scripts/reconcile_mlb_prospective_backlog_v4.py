#!/usr/bin/env python3
"""Reconcile prospective MLB slates without fighting the live lock schedule.

The exact official read-only status is checked first. The protected lock writer
is invoked only when that status proves terminal coverage is incomplete. All
Lambda calls use idempotent, bounded backpressure retries and a read timeout
longer than the deployed function timeout. No prediction, immutable evidence,
model authority, promotion authority, or wagering authority is rewritten.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v3 as v3


VERSION = "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v4-status-first-backpressure"
MAX_INVOKE_ATTEMPTS = 12
RETRY_DELAYS_SECONDS = (5, 10, 20, 30, 45, 60, 60, 60, 60, 60, 60)
RETRYABLE_CLIENT_CODES = frozenset(
    {
        "TooManyRequestsException",
        "ThrottlingException",
        "Throttling",
        "RequestLimitExceeded",
        "ServiceException",
        "EC2ThrottledException",
        "ResourceConflictException",
    }
)
RETRYABLE_TRANSPORT_ERRORS = (
    ConnectTimeoutError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)
MUTABLE_INCOMPLETE_STATUS_ERRORS = frozenset(
    {
        "official_status_terminal_counts_inconsistent",
        "official_status_terminal_coverage_incomplete",
        "official_status_not_complete",
    }
)


def control_plane_config() -> Config:
    """Retain bounded retries for CloudFormation resource discovery."""

    return Config(retries={"total_max_attempts": 4, "mode": "standard"})


def durable_lambda_config(*args: Any, **kwargs: Any) -> Config:
    """Use one SDK delivery per outer attempt and outlive the Lambda timeout."""

    del args, kwargs
    return Config(
        connect_timeout=10,
        read_timeout=420,
        retries={"total_max_attempts": 1, "mode": "standard"},
    )


def _client_error_code(exc: ClientError) -> str:
    return str(((exc.response or {}).get("Error") or {}).get("Code") or "")


def _retry_after_seconds(exc: ClientError) -> int:
    metadata = (exc.response or {}).get("ResponseMetadata") or {}
    headers = metadata.get("HTTPHeaders") or {}
    raw = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return max(int(float(raw)), 0)
    except (TypeError, ValueError):
        return 0


def _delay_seconds(attempt: int, exc: Optional[ClientError] = None) -> int:
    configured = RETRY_DELAYS_SECONDS[
        min(max(attempt - 1, 0), len(RETRY_DELAYS_SECONDS) - 1)
    ]
    return max(configured, _retry_after_seconds(exc) if exc else 0)


def invoke_json_with_backpressure(
    lambda_client: Any,
    function_name: str,
    event: Dict[str, Any],
    *,
    sleep: Any = time.sleep,
    max_attempts: int = MAX_INVOKE_ATTEMPTS,
) -> Dict[str, Any]:
    """Retry only idempotent Lambda delivery failures and account throttling."""

    if max_attempts < 1:
        raise base.ReconciliationError("lambda_max_attempts_invalid")
    last_error: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return base.invoke_json(lambda_client, function_name, event)
        except ClientError as exc:
            last_error = exc
            if _client_error_code(exc) not in RETRYABLE_CLIENT_CODES:
                raise
            if attempt >= max_attempts:
                raise base.ReconciliationError(
                    "lambda_backpressure_retry_exhausted"
                ) from exc
            sleep(_delay_seconds(attempt, exc))
        except RETRYABLE_TRANSPORT_ERRORS as exc:
            last_error = exc
            if attempt >= max_attempts:
                raise base.ReconciliationError(
                    "lambda_transport_retry_exhausted"
                ) from exc
            sleep(_delay_seconds(attempt))
    raise base.ReconciliationError("lambda_retry_state_invalid") from last_error


def _official_evidence(status: Mapping[str, Any], slate_date: str) -> Dict[str, Any]:
    counts = base._validate_official_status(status, slate_date)
    return {
        "slateDateEt": slate_date,
        "manifestGameCount": counts["gameCount"],
        "canonicalPredictionCount": counts["lockedPredictionCount"],
        "terminalNoPredictionCount": counts["terminalNoPredictionCount"],
        "lockOutcomeCount": counts["lockedStatusCount"],
        "offDay": counts["gameCount"] == 0,
        "officialStatusReadBound": True,
        "terminalCoverageAuthority": "official_exact_date_read_status",
    }


def _status_event(slate_date: str) -> Dict[str, Any]:
    return {
        "httpMethod": "GET",
        "path": "/v1/mlb/locks/status",
        "queryStringParameters": {"date": slate_date},
    }


def _incomplete_status_error(exc: base.ReconciliationError) -> bool:
    return str(exc) in MUTABLE_INCOMPLETE_STATUS_ERRORS


def reconcile(
    cloudformation: Any,
    lambda_client: Any,
    *,
    stack_name: str,
    now_utc: Optional[datetime] = None,
    max_slate_days: int = base.DEFAULT_MAX_SLATE_DAYS,
    invoke: Any = invoke_json_with_backpressure,
) -> Dict[str, Any]:
    functions = base.resolve_stack_functions(cloudformation, stack_name)
    cutoff = base.release_cutoff(lambda_client, functions.trainer)
    slate_dates = base.prospective_slate_dates(
        cutoff,
        now_utc=now_utc,
        max_slate_days=max_slate_days,
    )
    rows: List[Dict[str, Any]] = []
    for slate_date in slate_dates:
        status_event = _status_event(slate_date)
        official_status = invoke(lambda_client, functions.lock, status_event)
        mutation_payload: Optional[Dict[str, Any]] = None
        mutation_executed = False
        try:
            lock_evidence = _official_evidence(official_status, slate_date)
        except base.ReconciliationError as exc:
            if not _incomplete_status_error(exc):
                raise
            mutation_executed = True
            mutation_payload = invoke(
                lambda_client,
                functions.lock,
                {
                    "sport": "mlb",
                    "run": "prospective_terminal_backlog_reconciliation_v4",
                    "slateDateEt": slate_date,
                    "force": True,
                },
            )
            official_status = invoke(lambda_client, functions.lock, status_event)
            lock_evidence = v3.validate_lock_result(
                mutation_payload,
                official_status,
                slate_date,
            )

        settlement_payload = invoke(
            lambda_client,
            functions.results,
            {
                "sport": "mlb",
                "run": "prospective_backlog_settlement_v4",
                "slate_date": slate_date,
                "days_from": 0,
            },
        )
        settlement = base.validate_settlement_result(
            settlement_payload,
            slate_date,
        )
        rows.append(
            {
                **lock_evidence,
                "settlement": settlement,
                "protectedLockReplay": mutation_executed,
                "mutationSkippedBecauseOfficialStatusComplete": (
                    not mutation_executed
                ),
                "readOnlyOfficialStatusProof": True,
                "backpressureRetryInstalled": True,
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
        "statusFirst": True,
        "readOnlyOfficialStatusProof": True,
        "backpressureRetryInstalled": True,
        "directTableWrite": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "promotionAuthorityChanged": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
    }


def main() -> int:
    args = base._parser().parse_args()
    session = base.boto3.session.Session(region_name=args.region)
    cloudformation = session.client(
        "cloudformation",
        config=control_plane_config(),
    )
    lambda_client = session.client(
        "lambda",
        config=durable_lambda_config(),
    )
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
        base._write_report(args.output, report)
        print(json.dumps(report, sort_keys=True), file=sys.stderr)
        return 1
    base._write_report(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
