#!/usr/bin/env python3
"""Gap-tolerant MLB prospective reconciliation and trainer recovery.

Every slate date is processed independently through the protected lock and
settlement Lambdas.  An unresolved or unrepairable date is quarantined and can
never emit training rows, but it no longer prevents later exact finalized slates
from being reconciled.  No direct DynamoDB mutation, post-start prediction
creation, immutable prediction rewrite, model promotion, production authority,
or wagering authority is available in this script.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v3 as v3
import reconcile_mlb_prospective_backlog_v4 as v4
import reconcile_mlb_prospective_backlog_v5 as v5


VERSION = "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v6-gap-tolerant-trainer-recovery"


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}:{str(exc)[:500]}"


def _status_event(slate_date: str) -> Dict[str, Any]:
    return {
        "httpMethod": "GET",
        "path": "/v1/mlb/locks/status",
        "queryStringParameters": {"date": slate_date},
    }


def _classify_gap(exc: BaseException) -> str:
    value = str(exc)
    if "not fully finalized" in value or "not_complete" in value:
        return "DEFERRED_NOT_YET_FINAL"
    if "official_status_terminal_coverage_incomplete" in value:
        return "QUARANTINED_TERMINAL_COVERAGE_INCOMPLETE"
    if "official schedule" in value.lower() or "official_status" in value:
        return "QUARANTINED_OFFICIAL_AUTHORITY_UNPROVEN"
    return "QUARANTINED_RECONCILIATION_FAILED"


def _invoke_trainer(
    lambda_client: Any,
    function_name: str,
    *,
    invoke: Any,
) -> Dict[str, Any]:
    selection = invoke(
        lambda_client,
        function_name,
        {"mode": "selection_capture", "run": "prospective_reconcile_v6"},
    )
    training = invoke(
        lambda_client,
        function_name,
        {"mode": "scheduled", "run": "prospective_reconcile_v6"},
    )
    training_run_id = str(training.get("runId") or "")
    selection_run_id = str(selection.get("runId") or "")
    status_event: Dict[str, Any] = {"mode": "status"}
    if training_run_id:
        status_event["trainingRunId"] = training_run_id
    if selection_run_id:
        status_event["selectionCaptureRunId"] = selection_run_id
    status = invoke(lambda_client, function_name, status_event)
    return {
        "selectionCapture": selection,
        "training": training,
        "status": status,
        "trainingRunId": training_run_id or None,
        "selectionCaptureRunId": selection_run_id or None,
    }


def reconcile(
    cloudformation: Any,
    lambda_client: Any,
    *,
    stack_name: str,
    now_utc: Optional[datetime] = None,
    max_slate_days: int = base.DEFAULT_MAX_SLATE_DAYS,
    invoke: Any = v4.invoke_json_with_backpressure,
) -> Dict[str, Any]:
    functions = base.resolve_stack_functions(cloudformation, stack_name)
    cutoff = base.release_cutoff(lambda_client, functions.trainer)
    slate_dates = base.prospective_slate_dates(
        cutoff,
        now_utc=now_utc,
        max_slate_days=max_slate_days,
    )
    reconciled: List[Dict[str, Any]] = []
    quarantined: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []

    with v5._status_body_adapter():
        for slate_date in slate_dates:
            try:
                official_status = invoke(
                    lambda_client,
                    functions.lock,
                    _status_event(slate_date),
                )
                mutation_executed = False
                mutation_payload: Optional[Dict[str, Any]] = None
                try:
                    lock_evidence = v4._official_evidence(
                        official_status, slate_date
                    )
                except base.ReconciliationError as exc:
                    if not v4._incomplete_status_error(exc):
                        raise
                    mutation_executed = True
                    mutation_payload = invoke(
                        lambda_client,
                        functions.lock,
                        {
                            "sport": "mlb",
                            "run": "prospective_terminal_backlog_reconciliation_v6",
                            "slateDateEt": slate_date,
                            "force": True,
                        },
                    )
                    official_status = invoke(
                        lambda_client,
                        functions.lock,
                        _status_event(slate_date),
                    )
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
                        "run": "prospective_backlog_settlement_v6",
                        "slate_date": slate_date,
                        "days_from": 0,
                    },
                )
                settlement = base.validate_settlement_result(
                    settlement_payload, slate_date
                )
                reconciled.append(
                    {
                        **lock_evidence,
                        "settlement": settlement,
                        "protectedLockReplay": mutation_executed,
                        "mutationSkippedBecauseOfficialStatusComplete": (
                            not mutation_executed
                        ),
                        "readOnlyOfficialStatusProof": True,
                        "directTableWrite": False,
                        "postStartPredictionCreationAllowed": False,
                    }
                )
            except Exception as exc:
                row = {
                    "slateDateEt": slate_date,
                    "classification": _classify_gap(exc),
                    "error": _safe_error(exc),
                    "trainingRowsAuthorized": 0,
                    "directTableWrite": False,
                    "postStartPredictionCreationAllowed": False,
                    "immutablePredictionRewriteAllowed": False,
                }
                if row["classification"].startswith("DEFERRED"):
                    deferred.append(row)
                else:
                    quarantined.append(row)
                continue

    trainer = _invoke_trainer(
        lambda_client,
        functions.trainer,
        invoke=invoke,
    )
    status = trainer.get("status") or {}
    training = trainer.get("training") or {}
    return {
        "ok": True,
        "version": VERSION,
        "stackName": stack_name,
        "releaseCutoffUtc": cutoff,
        "firstSlateDateEt": slate_dates[0] if slate_dates else None,
        "lastSlateDateEt": slate_dates[-1] if slate_dates else None,
        "evaluatedSlateCount": len(slate_dates),
        "reconciledSlateCount": len(reconciled),
        "quarantinedSlateCount": len(quarantined),
        "deferredSlateCount": len(deferred),
        "reconciledSlates": reconciled,
        "quarantinedSlates": quarantined,
        "deferredSlates": deferred,
        "laterFinalizedSlatesContinuePastGaps": True,
        "strictPerSlateTrainingAuthority": True,
        "trainerRecovery": trainer,
        "acceptedRowCount": training.get("acceptedRowCount"),
        "canonicalSlateContinuity": training.get("canonicalSlateContinuity"),
        "automaticPromotionEnabled": status.get("automaticPromotionEnabled"),
        "firstPromotionRequiresManualReview": status.get(
            "firstPromotionRequiresManualReview"
        ),
        "v2InferenceConsumerInstalled": status.get(
            "v2InferenceConsumerInstalled"
        ),
        "runtimeAuthorityActivationAvailable": status.get(
            "runtimeAuthorityActivationAvailable"
        ),
        "directTableWrite": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "promotionAuthorityChangedByReconciliation": False,
        "productionAuthorityChangedByReconciliation": False,
        "automaticWagerAllowed": False,
    }


def main() -> int:
    args = base._parser().parse_args()
    session = base.boto3.session.Session(region_name=args.region)
    cloudformation = session.client(
        "cloudformation", config=v4.control_plane_config()
    )
    lambda_client = session.client(
        "lambda", config=v4.durable_lambda_config()
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
            "error": _safe_error(exc),
            "directTableWrite": False,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "promotionAuthorityChangedByReconciliation": False,
            "productionAuthorityChangedByReconciliation": False,
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
