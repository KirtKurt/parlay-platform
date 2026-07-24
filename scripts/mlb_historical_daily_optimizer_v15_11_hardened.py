#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

from mlb_historical_evidence_v1 import (  # noqa: E402
    VERSION as EVIDENCE_VERSION,
    assert_evidence_chain,
    build_audit_claim,
    build_dataset_manifest,
    build_evidence_chain,
    claim_audit_once_local,
    claim_audit_once_s3,
    evidence_blockers,
    validate_dataset_manifest,
    validate_ledger,
    validate_request_plan,
)
from mlb_historical_policy_v1 import (  # noqa: E402
    canonical_digest,
    evaluate_promotion_gate,
    sha256_file,
)

CORE_PATH = Path(__file__).with_name("mlb_historical_daily_optimizer_v15_11.py")
SPEC = importlib.util.spec_from_file_location("mlb_historical_core_v15_11", CORE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load MLB V15.11.1 core command module")
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)

VERSION = "MLB-HISTORICAL-HARDENED-CLI-v15.11.1"


def _read(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def _write(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def _require_manifest_binding(
    artifact: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    binding = artifact.get("evidence_binding") or {}
    if binding.get("dataset_manifest_sha256") != manifest.get("manifest_sha256"):
        raise PermissionError("candidate artifact is not bound to this dataset manifest")
    if binding.get("dataset_sha256") != manifest.get("dataset_sha256"):
        raise PermissionError("candidate artifact is not bound to this dataset")


def _combined_gate(report: Mapping[str, Any]) -> dict[str, Any]:
    statistical = evaluate_promotion_gate(report).to_dict()
    blockers = sorted(set(statistical["blockers"]) | set(evidence_blockers(report)))
    statistical["approved"] = not blockers
    statistical["decision"] = "PROMOTE" if not blockers else "BLOCK"
    statistical["blockers"] = blockers
    statistical["evidence_version"] = EVIDENCE_VERSION
    return statistical


def command_plan(args: argparse.Namespace) -> None:
    schedule_path = Path(args.schedule)
    plan = CORE.build_request_plan(
        CORE.read_json(schedule_path),
        credits_per_request=args.credits_per_request,
    )
    plan.pop("plan_sha256", None)
    plan["schedule_sha256"] = sha256_file(schedule_path)
    plan["plan_sha256"] = canonical_digest(plan)
    result = validate_request_plan(plan)
    if not result["ok"]:
        raise ValueError(result)
    _write(args.output, plan)
    print(
        json.dumps(
            {
                "request_count": plan["request_count"],
                "estimated_credits": plan["estimated_credits"],
                "plan_sha256": plan["plan_sha256"],
                "schedule_sha256": plan["schedule_sha256"],
            },
            indent=2,
        )
    )


def command_backfill(args: argparse.Namespace) -> None:
    plan = _read(args.plan)
    result = validate_request_plan(plan)
    if not result["ok"]:
        raise ValueError(result)
    ledger = CORE.execute_backfill(
        plan,
        output_dir=args.output_dir,
        api_key=os.environ.get("ODDS_API_KEY", ""),
        confirmation=args.confirmation,
        max_credits=args.max_credits,
        timeout=args.timeout,
    )
    print(json.dumps(ledger, indent=2, sort_keys=True))


def command_dataset(args: argparse.Namespace) -> None:
    plan = _read(args.plan)
    ledger = _read(args.ledger)
    ledger_result = validate_ledger(plan, ledger, args.snapshot_dir)
    if not ledger_result["ok"]:
        raise ValueError(ledger_result)
    rows = CORE.build_dataset(
        CORE.read_json(args.schedule),
        CORE.read_json(args.outcomes),
        args.snapshot_dir,
    )
    CORE.write_jsonl(args.output, rows)
    manifest = build_dataset_manifest(
        schedule_path=args.schedule,
        outcomes_path=args.outcomes,
        plan_path=args.plan,
        ledger_path=args.ledger,
        snapshot_dir=args.snapshot_dir,
        dataset_path=args.output,
    )
    manifest["dataset_rows"] = len(rows)
    material = dict(manifest)
    material.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = canonical_digest(material)
    _write(args.manifest_output, manifest)
    print(
        json.dumps(
            {
                "rows": len(rows),
                "dataset_sha256": manifest["dataset_sha256"],
                "manifest_sha256": manifest["manifest_sha256"],
            },
            indent=2,
        )
    )


def command_train(args: argparse.Namespace) -> None:
    manifest = _read(args.dataset_manifest)
    validation = validate_dataset_manifest(manifest, dataset_path=args.dataset)
    if not validation["ok"]:
        raise ValueError(validation)
    artifact = CORE.train_candidate(
        CORE.read_jsonl(args.dataset),
        max_candidates=args.max_candidates,
        seed=args.seed,
    )
    artifact["evidence_binding"] = {
        "evidence_version": EVIDENCE_VERSION,
        "dataset_manifest_sha256": manifest["manifest_sha256"],
        "dataset_sha256": manifest["dataset_sha256"],
        "plan_sha256": manifest["plan_sha256"],
        "ledger_sha256": manifest["ledger_sha256"],
        "snapshot_manifest_sha256": manifest["snapshot_manifest_sha256"],
    }
    _write(args.output, artifact)
    print(
        json.dumps(
            {
                "candidate_id": artifact["candidate"]["candidate_id"],
                "candidates_evaluated": artifact["candidates_evaluated"],
                "dataset_manifest_sha256": manifest["manifest_sha256"],
                "audit_opened": False,
            },
            indent=2,
        )
    )


def command_audit(args: argparse.Namespace) -> None:
    artifact = _read(args.artifact)
    manifest = _read(args.dataset_manifest)
    validation = validate_dataset_manifest(manifest, dataset_path=args.dataset)
    if not validation["ok"]:
        raise ValueError(validation)
    _require_manifest_binding(artifact, manifest)
    artifact_sha256 = sha256_file(args.artifact)
    claim = build_audit_claim(
        experiment_id=args.experiment_id,
        candidate_id=str((artifact.get("candidate") or {}).get("candidate_id") or ""),
        artifact_sha256=artifact_sha256,
        dataset_sha256=str(manifest["dataset_sha256"]),
        dataset_manifest_sha256=str(manifest["manifest_sha256"]),
    )
    if bool(args.audit_claim_file) == bool(args.audit_claim_s3_uri):
        raise ValueError("provide exactly one of --audit-claim-file or --audit-claim-s3-uri")
    if args.audit_claim_file:
        stored_claim = claim_audit_once_local(args.audit_claim_file, claim)
    else:
        stored_claim = claim_audit_once_s3(
            args.audit_claim_s3_uri,
            claim,
            region=args.region,
        )
    report = CORE.audit_candidate(
        CORE.read_jsonl(args.dataset),
        artifact,
        artifact_path=args.artifact,
    )
    report["evidence_chain"] = build_evidence_chain(
        dataset_manifest=manifest,
        artifact_sha256=artifact_sha256,
        audit_claim=stored_claim,
    )
    report["audit_claim"] = stored_claim
    report["gate"] = _combined_gate(report)
    _write(args.output, report)
    print(json.dumps(report["gate"], indent=2, sort_keys=True))


def command_promote(args: argparse.Namespace) -> None:
    report = _read(args.report)
    assert_evidence_chain(report)
    final_gate = _combined_gate(report)
    if final_gate["approved"] is not True:
        result = {"executed": False, "gate": final_gate}
    else:
        if str((report.get("artifact") or {}).get("sha256") or "") != str(
            (report.get("evidence_chain") or {}).get("artifact_sha256") or ""
        ):
            raise PermissionError("report artifact digest does not match the evidence chain")
        result = CORE.execute_promotion(
            report,
            experiment_id=args.experiment_id,
            confirmation=args.confirmation,
            execute=args.execute,
            table_name=args.table_name,
            region=args.region,
        )
        result["gate"] = final_gate
    _write(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Hardened MLB V15.11.1 historical daily-slate optimizer"
    )
    root.add_argument("--version", action="version", version=VERSION)
    commands = root.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan")
    plan.add_argument("--schedule", required=True)
    plan.add_argument("--credits-per-request", type=int, default=10)
    plan.add_argument("--output", required=True)
    plan.set_defaults(func=command_plan)

    backfill = commands.add_parser("backfill")
    backfill.add_argument("--plan", required=True)
    backfill.add_argument("--output-dir", required=True)
    backfill.add_argument("--max-credits", type=int, required=True)
    backfill.add_argument("--confirmation", required=True)
    backfill.add_argument("--timeout", type=int, default=30)
    backfill.set_defaults(func=command_backfill)

    dataset = commands.add_parser("build-dataset")
    dataset.add_argument("--schedule", required=True)
    dataset.add_argument("--outcomes", required=True)
    dataset.add_argument("--plan", required=True)
    dataset.add_argument("--ledger", required=True)
    dataset.add_argument("--snapshot-dir", required=True)
    dataset.add_argument("--output", required=True)
    dataset.add_argument("--manifest-output", required=True)
    dataset.set_defaults(func=command_dataset)

    train = commands.add_parser("train")
    train.add_argument("--dataset", required=True)
    train.add_argument("--dataset-manifest", required=True)
    train.add_argument("--max-candidates", type=int, default=25000)
    train.add_argument("--seed", type=int, default=15111)
    train.add_argument("--output", required=True)
    train.set_defaults(func=command_train)

    audit = commands.add_parser("audit")
    audit.add_argument("--dataset", required=True)
    audit.add_argument("--dataset-manifest", required=True)
    audit.add_argument("--artifact", required=True)
    audit.add_argument("--experiment-id", required=True)
    audit.add_argument("--audit-claim-file")
    audit.add_argument("--audit-claim-s3-uri")
    audit.add_argument("--region", default="us-east-1")
    audit.add_argument("--output", required=True)
    audit.set_defaults(func=command_audit)

    promote = commands.add_parser("promote")
    promote.add_argument("--report", required=True)
    promote.add_argument("--experiment-id", required=True)
    promote.add_argument("--confirmation", required=True)
    promote.add_argument("--execute", action="store_true")
    promote.add_argument("--table-name")
    promote.add_argument("--region", default="us-east-1")
    promote.add_argument("--output", required=True)
    promote.set_defaults(func=command_promote)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
