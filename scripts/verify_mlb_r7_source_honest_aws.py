#!/usr/bin/env python3
"""Read-only AWS integration proof for MLB prospective R7 admission repair."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import boto3


REGION = os.environ.get("AWS_REGION", "us-east-1")
STACK = os.environ.get("STACK_NAME", "parlay-platform-dev")
EXPECTED_DATES = ["2026-08-03", "2026-08-24"]
EXPECTED_ROW_COUNT = 18
OUTPUT = Path("runtime_reports/mlb_r7_source_honest_aws_proof_latest.json")


def plain(value: Any) -> Any:
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def main() -> int:
    cf = boto3.client("cloudformation", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    ddb = boto3.resource("dynamodb", region_name=REGION)

    def physical(logical_id: str) -> str:
        detail = cf.describe_stack_resource(
            StackName=STACK,
            LogicalResourceId=logical_id,
        )["StackResourceDetail"]
        resource = str(detail.get("PhysicalResourceId") or "")
        if not resource:
            raise RuntimeError(f"PHYSICAL_RESOURCE_NOT_FOUND:{logical_id}")
        return resource

    trainer = physical("MLBMLTrainingFunction")
    snapshots_table = physical("SnapshotsTable")
    outcomes_table = physical("OutcomesTable")
    trainer_config = lam.get_function_configuration(FunctionName=trainer)
    for key, value in (
        ((trainer_config.get("Environment") or {}).get("Variables") or {})
    ).items():
        if isinstance(value, str):
            os.environ[key] = value
    os.environ["SNAPSHOTS_TABLE"] = snapshots_table
    os.environ["OUTCOMES_TABLE"] = outcomes_table

    import mlb_canonical_final_labels_v1 as labels
    import mlb_ml_dual_model_v2 as dual_model
    import mlb_ml_experiment_v2 as experiment
    import mlb_prospective_trainer_read_repair as legacy_read_repair
    import mlb_r7_source_honest_training_repair as source_honest_repair

    labels.history.SNAPSHOTS_TABLE = snapshots_table
    labels.history.PULLS = ddb.Table(snapshots_table)
    labels.OUTCOMES_TABLE = outcomes_table
    labels.outcomes_tbl = ddb.Table(outcomes_table)

    legacy_read_repair.install(labels)
    policy = source_honest_repair.install(
        labels=labels,
        experiment=experiment,
        dual_model=dual_model,
    )

    canonical = labels.load_canonical_training_rows(slate_dates=EXPECTED_DATES)
    rows = list(canonical.get("rows") or [])
    feature_vector_versions = sorted(
        {
            str(
                (
                    row.get("featureSnapshot")
                    or row.get("frozenFeatureVector")
                    or {}
                ).get("version")
                or ""
            )
            for row in rows
            if str(
                (
                    row.get("featureSnapshot")
                    or row.get("frozenFeatureVector")
                    or {}
                ).get("version")
                or ""
            )
        }
    )
    if len(feature_vector_versions) != 1:
        raise RuntimeError(
            "R7_IMMUTABLE_VECTOR_VERSION_NOT_UNIQUE:"
            + ",".join(feature_vector_versions)
        )
    feature_vector_version = feature_vector_versions[0]

    manifest = experiment.new_manifest(
        experiment_id=experiment.PRODUCTION_EXPERIMENT_ID,
        release_contract_id=experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        release_cutoff_utc=experiment.PRODUCTION_RELEASE_CUTOFF_UTC,
        feature_vector_version=feature_vector_version,
        model_feature_schemas={
            "outcome": list(dual_model.OUTCOME_FEATURES),
            "reliability": list(dual_model.RELIABILITY_FEATURES),
        },
        created_at_utc=experiment.PRODUCTION_RELEASE_CUTOFF_UTC,
    )
    filtered = experiment.filter_records(rows, manifest)

    by_date: Dict[str, list[str]] = defaultdict(list)
    missing_group_counts: Counter[str] = Counter()
    for row in rows:
        slate_date = str(row.get("slateDateEt") or "")
        by_date[slate_date].append(str(row.get("officialGamePk") or ""))
        snapshot = row.get("fundamentalsSnapshotV2") or {}
        for group in snapshot.get("missingGroups") or []:
            missing_group_counts[str(group)] += 1

    diagnostics = {
        str(item.get("slateDateEt") or ""): item
        for item in canonical.get("slates") or []
    }
    exact_slates: Dict[str, Any] = {}
    for slate_date in EXPECTED_DATES:
        diag = diagnostics.get(slate_date) or {}
        game_pks = sorted(value for value in by_date.get(slate_date, []) if value)
        exact_slates[slate_date] = {
            "slateFinalized": diag.get("slateFinalized"),
            "officialGameCount": diag.get("officialGameCount"),
            "canonicalLockCount": diag.get("canonicalLockCount"),
            "terminalNoPredictionCount": diag.get("terminalNoPredictionCount"),
            "validLabelCount": diag.get("validLabelCount"),
            "coverageComplete": diag.get("coverageComplete"),
            "labelsComplete": diag.get("labelsComplete"),
            "admittedRowCount": len(game_pks),
            "officialGamePks": game_pks,
            "officialGameSetFingerprint": experiment.official_game_set_fingerprint(
                slate_date, game_pks
            ),
        }

    annotation_failures = []
    for row in rows:
        failures = []
        if row.get("trainingEligible") is not True:
            failures.append("trainingEligible_not_true")
        if row.get("trainingExclusionReasons") not in ([], None):
            failures.append("trainingExclusionReasons_not_empty")
        if row.get("r7SourceHonestTrainingAdmission") is not True:
            failures.append("source_honest_admission_not_true")
        if row.get("r7SourceHonestTrainingPolicyVersion") != policy["version"]:
            failures.append("source_honest_policy_version_mismatch")
        if row.get("immutablePregameVectorMutated") is not False:
            failures.append("immutable_vector_mutation_flag_not_false")
        if row.get("immutableLockPayloadMutated") is not False:
            failures.append("immutable_lock_mutation_flag_not_false")
        if row.get("immutableLabelPayloadMutated") is not False:
            failures.append("immutable_label_mutation_flag_not_false")
        if row.get("productionPickEligibilityChanged") is not False:
            failures.append("production_pick_eligibility_changed")
        if failures:
            annotation_failures.append(
                {
                    "slateDateEt": row.get("slateDateEt"),
                    "officialGamePk": row.get("officialGamePk"),
                    "failures": failures,
                }
            )

    assertions = {
        "canonicalLoaderOk": canonical.get("ok") is True,
        "exactFinalizedDates": sorted(canonical.get("finalizedSlateDates") or [])
        == EXPECTED_DATES,
        "canonicalRowCount18": len(rows) == EXPECTED_ROW_COUNT,
        "singleExactFeatureVectorVersion": len(feature_vector_versions) == 1,
        "acceptedRowCount18": int(filtered.get("acceptedRowCount") or 0)
        == EXPECTED_ROW_COUNT,
        "rejectedRowCount0": int(filtered.get("rejectedRowCount") or 0) == 0,
        "allRowsPolicyAnnotated": not annotation_failures,
        "allSlatesExactAndComplete": all(
            value.get("slateFinalized") is True
            and value.get("coverageComplete") is True
            and value.get("labelsComplete") is True
            and int(value.get("officialGameCount") or 0)
            == int(value.get("admittedRowCount") or 0)
            for value in exact_slates.values()
        ),
        "immutablePredictionOrLockMutated": False,
        "immutableLabelMutated": False,
        "productionPickEligibilityChanged": False,
        "promotionGateChanged": False,
        "retiredV15_10Used": False,
    }
    ok = all(
        value is True
        for key, value in assertions.items()
        if key
        not in {
            "immutablePredictionOrLockMutated",
            "immutableLabelMutated",
            "productionPickEligibilityChanged",
            "promotionGateChanged",
            "retiredV15_10Used",
        }
    ) and all(
        assertions[key] is False
        for key in (
            "immutablePredictionOrLockMutated",
            "immutableLabelMutated",
            "productionPickEligibilityChanged",
            "promotionGateChanged",
            "retiredV15_10Used",
        )
    )

    report = {
        "ok": ok,
        "proofType": "MLB_R7_SOURCE_HONEST_AWS_READ_ONLY_PROOF_V2",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
        "trainerFunction": trainer,
        "deployedTrainerCodeSha256BeforeRepair": trainer_config.get("CodeSha256"),
        "deployedTrainerLastModifiedBeforeRepair": trainer_config.get("LastModified"),
        "policy": policy,
        "canonicalLoader": {
            "ok": canonical.get("ok"),
            "finalizedSlateDates": canonical.get("finalizedSlateDates"),
            "rowCount": len(rows),
            "featureVectorVersions": feature_vector_versions,
        },
        "manifestFeatureVectorVersion": feature_vector_version,
        "filter": {
            "acceptedRowCount": filtered.get("acceptedRowCount"),
            "rejectedRowCount": filtered.get("rejectedRowCount"),
            "rejectionReasonCounts": filtered.get("rejectionReasonCounts"),
        },
        "exactSlates": exact_slates,
        "missingGroupCounts": dict(sorted(missing_group_counts.items())),
        "annotationFailures": annotation_failures,
        "assertions": assertions,
        "awsStateMutated": False,
        "immutablePredictionOrLockMutated": False,
        "immutableLabelMutated": False,
        "productionAuthorityChanged": False,
        "promotionAuthorityChanged": False,
        "retiredV15_10Used": False,
        "secretExposed": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(plain(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(plain(report), indent=2, sort_keys=True))
    if not ok:
        raise SystemExit("MLB_R7_SOURCE_HONEST_AWS_PROOF_FAILED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
