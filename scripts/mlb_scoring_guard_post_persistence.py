#!/usr/bin/env python3
"""Extend the MLB scoring guard with read-only post-persistence shadow evidence.

Live scoring deliberately evaluates Fundamentals V2 before a durable prediction
write exists. That pre-write evaluation must remain passive because a sampled
clock value is not proof of persistence. This companion reads the write-once
PREGAME persistence record after the write, verifies that it fingerprints the
exact mutable prediction row, and only then re-evaluates the already-attached
Fundamentals V2 snapshot on an isolated in-memory copy.

This module never writes DynamoDB, never rewrites a prediction or ledger row,
and never grants fundamentals live scoring, promotion, wagering, or production
authority. Invalid or missing persistence proof simply cannot create evaluated
shadow evidence.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import mlb_scoring_guard_status as base

# Import after the base guard adds the repository-owned hello_world directory to
# sys.path. These contracts are used only for deterministic validation/evaluation.
import inqsi_pull_history as history_contract
import mlb_fundamentals_scoring_bridge_v1 as shadow_contract


PROOF_TYPE = "MLB_SCORING_GUARD_POST_PERSISTENCE_SHADOW_READ_ONLY_PROOF"
PROOF_VERSION = "MLB-SCORING-GUARD-POST-PERSISTENCE-SHADOW-v1"
PREGAME_RECORD_TYPE = "mlb_immutable_prelock_prediction_snapshot"
PREGAME_SNAPSHOT_VERSION = (
    "MLB-PREGAME-PREDICTION-SNAPSHOT-v3-user-visible-platform-prelock"
)
PERSISTENCE_PROOF_TYPE = "DDB_LIVE_PREDICTION_PUT_SUCCESS_ACK-v1"
PAYLOAD_FINGERPRINT_VERSION = history_contract.CANONICAL_PAYLOAD_FINGERPRINT_VERSION


def _prediction_data(item: Mapping[str, Any]) -> Dict[str, Any]:
    value = item.get("data")
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _proof_candidates(
    tokens: Set[str],
    items: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    matches = [
        item
        for item in items
        if item.get("record_type") == PREGAME_RECORD_TYPE
        and tokens & base._object_tokens(item)
    ]
    return sorted(
        matches,
        key=lambda item: (
            base._parse_dt(item.get("prediction_persisted_at_utc"))
            or datetime.min.replace(tzinfo=timezone.utc),
            str(item.get("SK") or ""),
        ),
        reverse=True,
    )


def _proof_errors(
    prediction_item: Mapping[str, Any],
    proof_item: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> List[str]:
    errors: List[str] = []
    live_row = _prediction_data(prediction_item)
    proof_row = _prediction_data(proof_item)

    expected_fields = {
        "record_type": PREGAME_RECORD_TYPE,
        "snapshot_version": PREGAME_SNAPSHOT_VERSION,
        "prediction_persistence_proof_type": PERSISTENCE_PROOF_TYPE,
        "prediction_payload_fingerprint_version": PAYLOAD_FINGERPRINT_VERSION,
    }
    for field, expected in expected_fields.items():
        if proof_item.get(field) != expected:
            errors.append(f"post_persistence_{field}_mismatch")
    for field in ("immutable_pregame", "write_once"):
        if proof_item.get(field) is not True:
            errors.append(f"post_persistence_{field}_missing")

    if not live_row:
        errors.append("post_persistence_live_prediction_data_missing")
    if not proof_row:
        errors.append("post_persistence_snapshot_data_missing")
    if prediction_item.get("record_type") != base.PREDICTION_RECORD_TYPE:
        errors.append("post_persistence_live_record_type_mismatch")

    live_pk = prediction_item.get("PK")
    live_sk = prediction_item.get("SK")
    if live_pk not in (None, "") and proof_item.get("prediction_persistence_write_pk") != live_pk:
        errors.append("post_persistence_write_pk_mismatch")
    if live_sk not in (None, "") and proof_item.get("prediction_persistence_write_sk") != live_sk:
        errors.append("post_persistence_write_sk_mismatch")

    if live_row and proof_row:
        expected_fingerprint = history_contract.canonical_payload_fingerprint(proof_row)
        if proof_item.get("prediction_payload_fingerprint") != expected_fingerprint:
            errors.append("post_persistence_snapshot_payload_fingerprint_mismatch")
        if history_contract.canonical_payload_fingerprint(live_row) != expected_fingerprint:
            errors.append("post_persistence_mutable_row_changed_after_snapshot")

    persisted_at = base._parse_dt(proof_item.get("prediction_persisted_at_utc"))
    created_at = base._parse_dt(proof_item.get("prediction_created_at_utc"))
    commence = base._parse_dt(
        proof_row.get("commenceTime")
        or proof_row.get("commence_time")
        or live_row.get("commenceTime")
        or live_row.get("commence_time")
    )
    if persisted_at is None:
        errors.append("post_persistence_persisted_at_invalid")
    elif persisted_at > observed_at:
        errors.append("post_persistence_persisted_after_observation")
    if created_at is None:
        errors.append("post_persistence_created_at_invalid")
    elif persisted_at is not None and created_at > persisted_at:
        errors.append("post_persistence_created_after_persistence")
    if commence is None:
        errors.append("post_persistence_commence_time_invalid")
    elif persisted_at is not None and persisted_at > commence - timedelta(minutes=45):
        errors.append("post_persistence_snapshot_persisted_after_cutoff")

    return sorted(set(errors))


def _diagnostic_shadow(
    prediction_item: Mapping[str, Any],
    proof_item: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], List[str]]:
    errors = _proof_errors(prediction_item, proof_item, observed_at=observed_at)
    if errors:
        return None, None, errors

    row = _prediction_data(proof_item)
    persisted_at = str(proof_item.get("prediction_persisted_at_utc") or "").strip()
    if not persisted_at:
        return None, None, ["post_persistence_persisted_at_missing"]

    # This is an isolated diagnostic copy. The persisted timestamp comes only
    # from the write-once proof record created after the live DynamoDB write.
    row["predictionPersistedAtUtc"] = persisted_at
    shadow = shadow_contract.evaluate_shadow(row)
    row[shadow_contract.SHADOW_FIELD] = copy.deepcopy(shadow)
    attestation_errors = list(
        shadow_contract.validate_shadow_attestation(shadow, row)
    )
    if shadow.get("liveScoringAuthority") is not False:
        attestation_errors.append("post_persistence_shadow_live_authority_not_false")
    if shadow.get("canInfluenceLivePick") is not False:
        attestation_errors.append("post_persistence_shadow_can_influence_live_pick")
    if attestation_errors:
        return None, shadow, sorted(set(attestation_errors))

    details = base._fundamentals_details({"data": row})
    return details, shadow, []


def enhance_report(
    report: Dict[str, Any],
    *,
    prediction_items: Sequence[Dict[str, Any]],
    pregame_snapshot_items: Sequence[Dict[str, Any]],
    observed_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    observed = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out = copy.deepcopy(report)
    games = [game for game in (out.get("games") or []) if isinstance(game, dict)]
    summary = dict(out.get("summary") or {})

    proof_count = 0
    proof_invalid_count = 0
    shadow_evaluated_count = 0
    shadow_not_evaluated_count = 0
    invalid_proofs: Dict[str, List[str]] = {}

    for game in games:
        tokens = base._object_tokens(game)
        identity = str(game.get("gameIdentity") or "unknown")
        prediction = base._matching_row(tokens, prediction_items)
        proofs = _proof_candidates(tokens, pregame_snapshot_items)
        proof = proofs[0] if proofs else None
        game["fundamentalsPostPersistenceProofPresent"] = proof is not None
        game["fundamentalsPostPersistenceReadOnly"] = True
        game["fundamentalsPostPersistenceProductionAuthorityChanged"] = False
        if prediction is None or proof is None:
            continue

        proof_count += 1
        before = base._fundamentals_details(prediction)
        details, shadow, errors = _diagnostic_shadow(
            prediction,
            proof,
            observed_at=observed,
        )
        game["fundamentalsPostPersistenceProofSk"] = proof.get("SK")
        if errors:
            proof_invalid_count += 1
            invalid_proofs[identity] = errors
            game["fundamentalsPostPersistenceProofValid"] = False
            game["fundamentalsPostPersistenceErrors"] = errors
            continue

        game["fundamentalsPostPersistenceProofValid"] = True
        game["fundamentalsPostPersistenceErrors"] = []
        if not details or not shadow:
            shadow_not_evaluated_count += 1
            continue
        if details.get("shadowEvaluated") is not True:
            shadow_not_evaluated_count += 1
            game["fundamentalsPostPersistenceShadowReason"] = shadow.get("reason")
            game["fundamentalsPostPersistenceShadowValidationErrors"] = list(
                shadow.get("validationErrors") or []
            )
            continue

        shadow_evaluated_count += 1
        old_state = str(before.get("state") or "")
        new_state = str(details.get("state") or "")
        if old_state != new_state:
            field_by_state = {
                "APPLIED": "fundamentalsAppliedCount",
                "NEUTRAL_OR_SOURCE_MISSING": "fundamentalsNeutralOrSourceMissingCount",
                "NOT_ACTIVE": "fundamentalsNotActiveCount",
                "SHADOW_ONLY": "fundamentalsShadowOnlyCount",
            }
            old_field = field_by_state.get(old_state)
            new_field = field_by_state.get(new_state)
            if old_field:
                summary[old_field] = max(int(summary.get(old_field) or 0) - 1, 0)
            if new_field:
                summary[new_field] = int(summary.get(new_field) or 0) + 1

        if before.get("shadowEvaluated") is not True:
            summary["fundamentalsShadowEvaluatedCount"] = int(
                summary.get("fundamentalsShadowEvaluatedCount") or 0
            ) + 1
        if (
            before.get("shadowWouldApply") is not True
            and details.get("shadowWouldApply") is True
        ):
            summary["fundamentalsShadowWouldApplyCount"] = int(
                summary.get("fundamentalsShadowWouldApplyCount") or 0
            ) + 1
        if (
            before.get("shadowSourceIncomplete") is not True
            and details.get("shadowSourceIncomplete") is True
        ):
            summary["fundamentalsSourceIncompleteCount"] = int(
                summary.get("fundamentalsSourceIncompleteCount") or 0
            ) + 1

        game["fundamentalsState"] = new_state
        game["fundamentalsReason"] = details.get("reason")
        game["fundamentalsShadowEvaluated"] = True
        game["fundamentalsShadowWouldApply"] = bool(
            details.get("shadowWouldApply") is True
        )
        game["fundamentalsShadowMode"] = details.get("shadowMode")
        game["fundamentalsConnectedGroups"] = list(
            details.get("connectedGroups") or []
        )
        game["fundamentalsMissingGroups"] = list(details.get("missingGroups") or [])
        game["fundamentalsPostPersistenceShadowOnly"] = True
        game["fundamentalsPostPersistenceLiveScoringAuthority"] = False
        game["fundamentalsPostPersistenceCanInfluenceLivePick"] = False

    summary["fundamentalsShadowOnlyNotActiveCount"] = int(
        summary.get("fundamentalsShadowOnlyCount") or 0
    ) + int(summary.get("fundamentalsNotActiveCount") or 0)
    summary["fundamentalsPostPersistenceProofCount"] = proof_count
    summary["fundamentalsPostPersistenceProofInvalidCount"] = proof_invalid_count
    summary["fundamentalsPostPersistenceShadowEvaluatedCount"] = shadow_evaluated_count
    summary["fundamentalsPostPersistenceShadowNotEvaluatedCount"] = (
        shadow_not_evaluated_count
    )

    out["summary"] = summary
    out["games"] = games
    out["postPersistenceShadowDiagnostic"] = {
        "ok": proof_invalid_count == 0,
        "version": PROOF_VERSION,
        "proofType": PROOF_TYPE,
        "readOnly": True,
        "mutatedPersistence": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "proofCount": proof_count,
        "invalidProofCount": proof_invalid_count,
        "shadowEvaluatedCount": shadow_evaluated_count,
        "shadowNotEvaluatedCount": shadow_not_evaluated_count,
        "invalidProofs": invalid_proofs,
    }
    out["postPersistenceShadowProofVersion"] = PROOF_VERSION
    return out


def build_live_report(
    *,
    slate_date: str,
    region: str,
    snapshots_table: str,
    signal_ledger_table: str,
) -> Dict[str, Any]:
    resource = base.boto3.resource("dynamodb", region_name=region)
    snapshots = resource.Table(snapshots_table)
    ledger = resource.Table(signal_ledger_table)
    pull_items = base._query_partition(snapshots, f"PULLS#mlb#{slate_date}")
    prediction_items = base._query_partition(
        snapshots,
        f"GAME_WINNERS#mlb#{slate_date}",
        "GAME#",
    )
    pregame_snapshot_items = base._query_partition(
        snapshots,
        f"GAME_WINNERS#mlb#{slate_date}",
        "PREGAME#GAME#",
    )
    movement_items = base._query_partition(
        ledger,
        f"ML_FEATURE#mlb#{slate_date}",
    )
    observed = datetime.now(timezone.utc)
    report = base.evaluate_slate(
        slate_date=slate_date,
        pull_items=pull_items,
        prediction_items=prediction_items,
        movement_items=movement_items,
        created_at=observed,
    )
    report = enhance_report(
        report,
        prediction_items=prediction_items,
        pregame_snapshot_items=pregame_snapshot_items,
        observed_at=observed,
    )
    source = dict(report.get("sourceOfTruth") or {})
    source["immutablePregamePredictionPrefix"] = (
        f"GAME_WINNERS#mlb#{slate_date} / PREGAME#GAME#"
    )
    report["sourceOfTruth"] = source
    return report


def _write_report(path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slate-date",
        default=datetime.now(base.SLATE_TZ).date().isoformat(),
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1",
    )
    parser.add_argument(
        "--snapshots-table",
        default=os.environ.get("SNAPSHOTS_TABLE", "parlay_platform_snapshots"),
    )
    parser.add_argument(
        "--signal-ledger-table",
        default=os.environ.get(
            "SIGNAL_LEDGER_TABLE", "parlay_platform_signal_ledger"
        ),
    )
    parser.add_argument(
        "--output",
        default="runtime_reports/mlb_scoring_guard_status_latest.json",
    )
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output)
    try:
        report = build_live_report(
            slate_date=args.slate_date,
            region=args.region,
            snapshots_table=args.snapshots_table,
            signal_ledger_table=args.signal_ledger_table,
        )
    except Exception as exc:
        now = datetime.now(timezone.utc)
        report = {
            "ok": False,
            "guardPassed": False,
            "proofType": PROOF_TYPE,
            "version": PROOF_VERSION,
            "createdAtUtc": now.isoformat().replace("+00:00", "Z"),
            "createdAtEt": now.astimezone(base.SLATE_TZ).isoformat(),
            "slateDateEt": args.slate_date,
            "readOnly": True,
            "blockers": ["AWS_SCORING_POST_PERSISTENCE_READ_FAILED"],
            "error": f"{type(exc).__name__}: {exc}",
            "secretExposed": False,
            "productionAuthorityChanged": False,
        }
    _write_report(output, report)
    print(
        json.dumps(
            {
                "guardPassed": report.get("guardPassed"),
                "slateDateEt": report.get("slateDateEt"),
                "summary": report.get("summary"),
                "blockers": report.get("blockers"),
                "postPersistenceShadowDiagnostic": report.get(
                    "postPersistenceShadowDiagnostic"
                ),
                "output": str(output),
            },
            indent=2,
            default=str,
        )
    )
    return 1 if args.enforce and report.get("guardPassed") is not True else 0


if __name__ == "__main__":
    raise SystemExit(main())
