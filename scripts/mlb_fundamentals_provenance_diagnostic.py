#!/usr/bin/env python3
"""Read-only diagnosis of MLB Fundamentals V2 provenance boundary failures.

This script explains *why* an already-attached, write-once pregame fundamentals
snapshot fails the existing chronology contract. It never changes the contract,
never writes DynamoDB, never rewrites a prediction/lock/ledger, and never grants
model, wagering, promotion, or production authority.
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import mlb_scoring_guard_post_persistence as post
import mlb_fundamentals_snapshot_v2 as snapshot_contract
import mlb_fundamentals_scoring_bridge_v1 as scoring_contract


VERSION = "MLB-FUNDAMENTALS-PROVENANCE-BOUNDARY-DIAGNOSTIC-v1"
REPORT_TYPE = "MLB_FUNDAMENTALS_PROVENANCE_READ_ONLY_DIAGNOSTIC"


def _parse(value: Any):
    return post.base._parse_dt(value)


def _text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _after(left: Any, right: Any) -> bool:
    left_dt = _parse(left)
    right_dt = _parse(right)
    return bool(left_dt and right_dt and left_dt > right_dt)


def diagnose_row(row: Mapping[str, Any], *, persisted_at: Any) -> Dict[str, Any]:
    """Return reason-coded chronology diagnostics without changing eligibility."""
    working = copy.deepcopy(dict(row))
    working["predictionPersistedAtUtc"] = _text(persisted_at)
    snapshot = working.get("fundamentalsSnapshotV2")
    snapshot = snapshot if isinstance(snapshot, Mapping) else None
    lock_at = scoring_contract._lock_at(working)
    violations: list[str] = []
    group_boundaries: list[Dict[str, Any]] = []

    if snapshot is None:
        violations.append("snapshot_missing")
        return {
            "contractSafe": False,
            "violations": violations,
            "predictionPersistedAtUtc": _text(persisted_at),
            "lockAtUtc": _text(lock_at),
            "snapshot": None,
            "groups": [],
        }

    validation_errors = list(snapshot_contract.validate(dict(snapshot)))
    violations.extend(f"snapshot_invalid:{reason}" for reason in validation_errors)

    persisted = _parse(persisted_at)
    locked = _parse(lock_at)
    if persisted is None:
        violations.append("prediction_persisted_timestamp_missing_or_invalid")
    if locked is None:
        violations.append("lock_timestamp_missing_or_invalid")
    if persisted and locked and persisted > locked:
        violations.append("prediction_persisted_after_lock")

    created_at = snapshot.get("createdAtUtc")
    source_pull_at = snapshot.get("sourcePullAtUtc")
    evidence_cutoff = snapshot.get("evidenceCutoffUtc")
    for name, value in (
        ("snapshot_created", created_at),
        ("source_pull", source_pull_at),
        ("evidence_cutoff", evidence_cutoff),
    ):
        parsed = _parse(value)
        if name != "evidence_cutoff" and parsed is None:
            violations.append(f"{name}_timestamp_missing_or_invalid")
            continue
        if parsed is None:
            continue
        if persisted and parsed > persisted:
            violations.append(f"{name}_after_prediction_persistence")
        if locked and parsed > locked:
            violations.append(f"{name}_after_lock")

    for group_name, raw_group in sorted((snapshot.get("groups") or {}).items()):
        group = raw_group if isinstance(raw_group, Mapping) else {}
        status = str(group.get("status") or "")
        if status not in snapshot_contract.SOURCE_PRESENT_STATUSES:
            continue
        retrieved = group.get("retrievedAtUtc")
        effective = group.get("sourceEffectiveAtUtc")
        group_violations: list[str] = []
        retrieved_dt = _parse(retrieved)
        effective_dt = _parse(effective)
        if retrieved_dt is None:
            group_violations.append("retrieved_timestamp_missing_or_invalid")
        if retrieved_dt and persisted and retrieved_dt > persisted:
            group_violations.append("retrieved_after_prediction_persistence")
        if retrieved_dt and locked and retrieved_dt > locked:
            group_violations.append("retrieved_after_lock")
        if effective not in (None, "") and effective_dt is None:
            group_violations.append("effective_timestamp_invalid")
        if effective_dt and retrieved_dt and effective_dt > retrieved_dt:
            group_violations.append("effective_after_retrieval")
        if effective_dt and persisted and effective_dt > persisted:
            group_violations.append("effective_after_prediction_persistence")
        if effective_dt and locked and effective_dt > locked:
            group_violations.append("effective_after_lock")
        violations.extend(f"group:{group_name}:{reason}" for reason in group_violations)
        group_boundaries.append(
            {
                "group": group_name,
                "status": status,
                "retrievedAtUtc": _text(retrieved),
                "sourceEffectiveAtUtc": _text(effective),
                "violations": group_violations,
            }
        )

    try:
        contract_safe = snapshot_contract.provenance_is_lock_safe(
            dict(snapshot),
            prediction_persisted_at=persisted_at,
            lock_at=lock_at,
        )
    except Exception as exc:
        contract_safe = False
        violations.append(f"contract_evaluation_error:{type(exc).__name__}")

    return {
        "contractSafe": bool(contract_safe),
        "violations": sorted(set(violations)),
        "predictionPersistedAtUtc": _text(persisted_at),
        "lockAtUtc": _text(lock_at),
        "snapshot": {
            "createdAtUtc": _text(created_at),
            "sourcePullAtUtc": _text(source_pull_at),
            "evidenceCutoffUtc": _text(evidence_cutoff),
            "sourcePullId": _text(snapshot.get("sourcePullId")),
            "fingerprint": _text(snapshot.get("fingerprint")),
            "validationErrors": validation_errors,
        },
        "groups": group_boundaries,
    }


def _identity(item: Mapping[str, Any]) -> str:
    row = post._prediction_data(item)
    return str(
        row.get("officialGamePk")
        or row.get("gameIdentity")
        or row.get("gameId")
        or item.get("SK")
        or "unknown"
    )


def build_live_report(*, slate_date: str, region: str, snapshots_table: str) -> Dict[str, Any]:
    resource = post.base.boto3.resource("dynamodb", region_name=region)
    table = resource.Table(snapshots_table)
    partition = f"GAME_WINNERS#mlb#{slate_date}"
    predictions = post.base._query_partition(table, partition, "GAME#")
    proofs = post.base._query_partition(table, partition, "PREGAME#GAME#")

    games: list[Dict[str, Any]] = []
    counts: collections.Counter[str] = collections.Counter()
    for prediction in predictions:
        candidates = post._proof_candidates(post.base._object_tokens(prediction), proofs)
        proof = candidates[0] if candidates else None
        if proof is None:
            diagnostic = {
                "contractSafe": False,
                "violations": ["write_once_pregame_persistence_proof_missing"],
                "predictionPersistedAtUtc": None,
                "lockAtUtc": None,
                "snapshot": None,
                "groups": [],
            }
        else:
            diagnostic = diagnose_row(
                post._prediction_data(proof),
                persisted_at=proof.get("prediction_persisted_at_utc"),
            )
        for reason in diagnostic.get("violations") or []:
            counts[str(reason)] += 1
        games.append(
            {
                "gameIdentity": _identity(prediction),
                "proofPresent": proof is not None,
                **diagnostic,
            }
        )

    safe_count = sum(game.get("contractSafe") is True for game in games)
    now = datetime.now(timezone.utc)
    return {
        "ok": True,
        "version": VERSION,
        "reportType": REPORT_TYPE,
        "createdAtUtc": now.isoformat().replace("+00:00", "Z"),
        "slateDateEt": slate_date,
        "readOnly": True,
        "mutatedPersistence": False,
        "immutablePredictionRewriteAllowed": False,
        "postStartPredictionCreationAllowed": False,
        "modelPromotionAllowed": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "gameCount": len(games),
        "contractSafeGameCount": safe_count,
        "contractBlockedGameCount": len(games) - safe_count,
        "dominantViolations": [
            {"reason": reason, "gameCount": count}
            for reason, count in counts.most_common(12)
        ],
        "games": games,
        "sourceOfTruth": {
            "snapshotsTable": snapshots_table,
            "partition": partition,
            "predictionPrefix": "GAME#",
            "immutablePregameProofPrefix": "PREGAME#GAME#",
        },
    }


def _write(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slate-date", default=datetime.now(post.base.SLATE_TZ).date().isoformat())
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1",
    )
    parser.add_argument(
        "--snapshots-table",
        default=os.environ.get("SNAPSHOTS_TABLE", "parlay_platform_snapshots"),
    )
    parser.add_argument(
        "--output",
        default="runtime_reports/mlb_fundamentals_provenance_diagnostic_latest.json",
    )
    args = parser.parse_args(argv)
    report = build_live_report(
        slate_date=args.slate_date,
        region=args.region,
        snapshots_table=args.snapshots_table,
    )
    _write(Path(args.output), report)
    print(
        json.dumps(
            {
                "gameCount": report["gameCount"],
                "contractSafeGameCount": report["contractSafeGameCount"],
                "contractBlockedGameCount": report["contractBlockedGameCount"],
                "dominantViolations": report["dominantViolations"],
                "output": args.output,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
