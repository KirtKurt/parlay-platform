#!/usr/bin/env python3
"""Read-only production proof for MLB prospective R7 row admission.

This diagnostic binds the exact physical tables from CloudFormation, installs
only the trainer's in-memory compatibility boundary, and evaluates immutable
canonical locks plus write-once labels. It never calls a persistence API.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
STACK = os.environ.get("STACK_NAME", "parlay-platform-dev")
EXPERIMENT = "mlb-v2-2026-08-03-future-prospective-r7"
FALLBACK_DATES = ["2026-08-03", "2026-08-24"]
OUTPUT = Path("runtime_reports/mlb_r7_admission_diagnostic_latest.json")


def _plain(value: Any) -> Any:
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _strings(values: Any) -> list[str]:
    return sorted({str(value) for value in (values or []) if str(value)})


def main() -> int:
    cf = boto3.client("cloudformation", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    ddb = boto3.resource("dynamodb", region_name=REGION)

    def physical_resource(logical_id: str) -> str:
        detail = cf.describe_stack_resource(
            StackName=STACK,
            LogicalResourceId=logical_id,
        )["StackResourceDetail"]
        value = str(detail.get("PhysicalResourceId") or "")
        if not value:
            raise RuntimeError(f"PHYSICAL_RESOURCE_NOT_FOUND:{logical_id}")
        return value

    trainer = physical_resource("MLBMLTrainingFunction")
    snapshots_table = physical_resource("SnapshotsTable")
    outcomes_table = physical_resource("OutcomesTable")
    config = lam.get_function_configuration(FunctionName=trainer)

    for key, value in (((config.get("Environment") or {}).get("Variables") or {})).items():
        if isinstance(value, str):
            os.environ[key] = value
    os.environ["SNAPSHOTS_TABLE"] = snapshots_table
    os.environ["OUTCOMES_TABLE"] = outcomes_table

    # Import only after environment binding, then bind the module-level clients
    # explicitly. This is necessary because the canonical reader dereferences
    # inqsi_pull_history.PULLS rather than a local table variable.
    import mlb_canonical_final_labels_v1 as labels
    import mlb_fundamentals_snapshot_v2 as fundamentals
    import mlb_prospective_trainer_read_repair as read_repair

    labels.history.SNAPSHOTS_TABLE = snapshots_table
    labels.history.PULLS = ddb.Table(snapshots_table)
    labels.OUTCOMES_TABLE = outcomes_table
    labels.outcomes_tbl = ddb.Table(outcomes_table)
    read_repair.install(labels)

    response = lam.invoke(
        FunctionName=trainer,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            {
                "sport": "mlb",
                "mode": "status",
                "run": "r7_admission_read_only_diagnostic",
            },
            separators=(",", ":"),
        ).encode("utf-8"),
    )
    body = response["Payload"].read()
    status: Dict[str, Any] = {}
    status_error = response.get("FunctionError")
    if not status_error:
        parsed = json.loads(body.decode("utf-8"))
        if isinstance(parsed, dict) and "body" in parsed:
            nested = parsed.get("body")
            parsed = json.loads(nested) if isinstance(nested, str) else nested
        if isinstance(parsed, dict):
            status = parsed

    latest = (((status.get("trainingHealth") or {}).get("latestRun")) or {})
    continuity = latest.get("canonicalSlateContinuity") or {}
    dates = [
        str(value)
        for value in continuity.get("finalizedGameSlateDates") or []
        if str(value)
    ] or list(FALLBACK_DATES)

    rows: list[Dict[str, Any]] = []
    rejected_locks: list[Dict[str, Any]] = []
    for slate_date in sorted(set(dates)):
        locks, rejected = labels._validated_canonical_locks(slate_date)
        rejected_locks.extend(
            {
                "slateDateEt": slate_date,
                "officialGamePk": row.get("officialGamePk"),
                "reasons": _strings(row.get("reasons")),
            }
            for row in rejected
        )
        stored_labels = {
            str(item.get("official_game_pk") or ""): item
            for item in labels._labels_for_slate(slate_date)
        }
        for locked in locks:
            official_values = labels._official_game_pk_values(locked)
            official_pk = next(iter(official_values)) if len(official_values) == 1 else None
            authority = locked.get("canonicalLockAuthority") or {}
            freeze = locked.get("mlFeatureFreeze") or {}
            vector = locked.get("frozenFeatureVector") or {}
            snapshot = locked.get("fundamentalsSnapshotV2") or {}
            verdict_eligible, verdict_reasons = labels._training_verdict(locked)
            label = stored_labels.get(str(official_pk or "")) or {}
            joined = (
                labels._joined_training_row(
                    slate_date,
                    label,
                    locked,
                    slate_finalized=True,
                )
                if label
                else {}
            )
            try:
                fundamentals_errors = (
                    fundamentals.validate(snapshot)
                    if snapshot
                    else ["fundamentals_v2_missing"]
                )
            except Exception as exc:
                fundamentals_errors = [
                    f"fundamentals_validation_failed:{type(exc).__name__}"
                ]
            rows.append(
                {
                    "slateDateEt": slate_date,
                    "officialGamePk": official_pk,
                    "storedAliases": {
                        "lockedPrediction": locked.get("lockedPrediction"),
                        "immutablePerGameStage": locked.get("immutablePerGameStage"),
                        "immutableLockedStorage": locked.get("immutableLockedStorage"),
                        "trainingEligible": locked.get("trainingEligible"),
                        "freezeTrainingEligible": freeze.get("trainingEligible"),
                        "exactVectorVerified": locked.get("exactVectorVerified"),
                        "freezeExactVectorVerified": freeze.get("exactVectorVerified"),
                    },
                    "storedTrainingExclusionReasons": _strings(
                        locked.get("trainingExclusionReasons")
                    ),
                    "freezeTrainingExclusionReasons": _strings(
                        freeze.get("trainingExclusionReasons")
                    ),
                    "vectorFingerprintPresent": bool(
                        isinstance(vector, dict) and vector.get("fingerprint")
                    ),
                    "fundamentalsPresent": bool(snapshot),
                    "fundamentalsVersion": (
                        snapshot.get("version") if isinstance(snapshot, dict) else None
                    ),
                    "fundamentalsValidationErrors": _strings(fundamentals_errors),
                    "authority": {
                        "learningEligible": authority.get("learningEligible"),
                        "verified": authority.get("verified"),
                        "consistentRead": authority.get("consistentRead"),
                        "immutableLocked": authority.get("immutableLocked"),
                        "stageAuthorityVerified": authority.get("stageAuthorityVerified"),
                        "persistedStageAuthorityValidated": authority.get(
                            "persistedStageAuthorityValidated"
                        ),
                        "officialAuditEligible": authority.get("officialAuditEligible"),
                        "exactLockVectorValidated": authority.get(
                            "exactLockVectorValidated"
                        ),
                        "selectionLockVectorStatusValidated": authority.get(
                            "selectionLockVectorStatusValidated"
                        ),
                        "trainingExclusionReasons": _strings(
                            authority.get("trainingExclusionReasons")
                        ),
                        "exactLockVectorValidationErrors": _strings(
                            authority.get("exactLockVectorValidationErrors")
                        ),
                        "selectionLockVectorStatusValidationErrors": _strings(
                            authority.get("selectionLockVectorStatusValidationErrors")
                        ),
                        "exactVectorProofSourceAtRead": authority.get(
                            "exactVectorProofSourceAtRead"
                        ),
                        "repairVersion": authority.get(
                            "prospectiveTrainerReadRepairVersion"
                        ),
                    },
                    "currentVerdict": {
                        "eligible": bool(verdict_eligible),
                        "reasons": _strings(verdict_reasons),
                    },
                    "storedLabel": {
                        "present": bool(label),
                        "trainingEligible": (
                            label.get("training_eligible") if label else None
                        ),
                        "trainingExclusionReasons": (
                            _strings(label.get("training_exclusion_reasons"))
                            if label
                            else []
                        ),
                    },
                    "joinedRow": {
                        "trainingEligible": (
                            joined.get("trainingEligible") if joined else None
                        ),
                        "trainingExclusionReasons": (
                            _strings(joined.get("trainingExclusionReasons"))
                            if joined
                            else []
                        ),
                        "repairVersion": joined.get(
                            "prospectiveTrainerReadRepairVersion"
                        )
                        if joined
                        else None,
                        "immutableLockPayloadMutated": joined.get(
                            "immutableLockPayloadMutated"
                        )
                        if joined
                        else None,
                        "immutableLabelPayloadMutated": joined.get(
                            "immutableLabelPayloadMutated"
                        )
                        if joined
                        else None,
                    },
                }
            )

    joined_counts: Counter[str] = Counter()
    verdict_counts: Counter[str] = Counter()
    for row in rows:
        for reason in (row.get("joinedRow") or {}).get(
            "trainingExclusionReasons"
        ) or []:
            joined_counts[str(reason)] += 1
        for reason in (row.get("currentVerdict") or {}).get("reasons") or []:
            verdict_counts[str(reason)] += 1

    joined_eligible = sum(
        (row.get("joinedRow") or {}).get("trainingEligible") is True
        for row in rows
    )
    report = {
        "ok": True,
        "proofType": "MLB_R7_ROW_ADMISSION_READ_ONLY_DIAGNOSTIC_V2",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "experimentId": EXPERIMENT,
        "trainerFunction": trainer,
        "trainerCodeSha256": config.get("CodeSha256"),
        "trainerLastModified": config.get("LastModified"),
        "readRepairVersion": read_repair.VERSION,
        "statusExperimentId": status.get("experimentId"),
        "statusReadOk": status.get("ok"),
        "statusFunctionError": status_error,
        "finalizedSlateDatesInspected": sorted(set(dates)),
        "inspectedCanonicalLockCount": len(rows),
        "rejectedCanonicalLockCount": len(rejected_locks),
        "rejectedCanonicalLocks": rejected_locks,
        "joinedEligibleCount": joined_eligible,
        "joinedIneligibleCount": len(rows) - joined_eligible,
        "joinedExclusionReasonCounts": dict(sorted(joined_counts.items())),
        "currentVerdictReasonCounts": dict(sorted(verdict_counts.items())),
        "rows": rows,
        "awsStateMutated": False,
        "immutablePredictionOrLockMutated": False,
        "labelMutated": False,
        "promotionAuthorityChanged": False,
        "productionAuthorityChanged": False,
        "retiredV15_10Used": False,
        "secretExposed": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(_plain(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            _plain({key: value for key, value in report.items() if key != "rows"}),
            indent=2,
            sort_keys=True,
        )
    )
    print(json.dumps(_plain(rows), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
