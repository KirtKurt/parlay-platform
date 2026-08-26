#!/usr/bin/env python3
"""Deploy and verify the isolated MLB R7 stable-feature repair.

The script mutates only two pre-resolved MLB Lambda resources:
MLBAuditedPullFunction code and MLBMLTrainingFunction memory. It rebuilds only
movement-feature ledger rows from immutable pregame snapshots, then runs the
read-only scoring guard and the fail-closed R7 trainer/status flow. It never
writes predictions, locks, labels, champions, promotion state, or another sport.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import boto3
from botocore.config import Config


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STACK = "parlay-platform-dev"
DEFAULT_REGION = "us-east-1"
PULL_LOGICAL_ID = "MLBAuditedPullFunction"
TRAINER_LOGICAL_ID = "MLBMLTrainingFunction"
PULL_HANDLER = "mlb_manual_pull.lambda_handler"
TRAINER_HANDLER = "mlb_ml_aws_training_v1_compat.lambda_handler"


class RepairError(RuntimeError):
    pass


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")


def _resolve_function(cfn: Any, stack: str, logical_id: str) -> str:
    response = cfn.describe_stack_resource(
        StackName=stack,
        LogicalResourceId=logical_id,
    )
    value = str(
        ((response.get("StackResourceDetail") or {}).get("PhysicalResourceId"))
        or ""
    ).strip()
    if not value or value == "None":
        raise RepairError(f"could_not_resolve:{logical_id}")
    return value


def _assert_active(cfg: Mapping[str, Any], *, handler: str) -> None:
    if cfg.get("Handler") != handler:
        raise RepairError(f"handler_mismatch:{cfg.get('FunctionName')}")
    if cfg.get("State") != "Active":
        raise RepairError(f"function_not_active:{cfg.get('FunctionName')}")
    if cfg.get("LastUpdateStatus") != "Successful":
        raise RepairError(f"function_update_not_successful:{cfg.get('FunctionName')}")


def _download_existing_package(lambda_client: Any, function_name: str, target: Path) -> None:
    response = lambda_client.get_function(FunctionName=function_name)
    location = str(((response.get("Code") or {}).get("Location")) or "")
    if not location.startswith("https://"):
        raise RepairError("existing_lambda_package_url_missing")
    urllib.request.urlretrieve(location, target)
    if not target.is_file() or target.stat().st_size <= 0:
        raise RepairError("existing_lambda_package_download_failed")


def _zip_tree(source: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())


def _build_overlay_package(
    lambda_client: Any,
    function_name: str,
    work: Path,
) -> tuple[Path, str]:
    original = work / "existing.zip"
    package = work / "package"
    output = work / "mlb-audited-pull-repaired.zip"
    _download_existing_package(lambda_client, function_name, original)
    package.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(original, "r") as archive:
        archive.extractall(package)

    overlays = (
        "mlb_manual_pull.py",
        "mlb_movement_feature_identity_v2.py",
    )
    for name in overlays:
        source = ROOT / "hello_world" / name
        if not source.is_file():
            raise RepairError(f"overlay_source_missing:{name}")
        shutil.copy2(source, package / name)

    for stale in package.rglob("*.pyc"):
        stale.unlink(missing_ok=True)
    for cache in list(package.rglob("__pycache__")):
        if cache.is_dir():
            shutil.rmtree(cache)

    _zip_tree(package, output)
    digest = base64.b64encode(hashlib.sha256(output.read_bytes()).digest()).decode("ascii")
    return output, digest


def _wait_updated(lambda_client: Any, function_name: str) -> None:
    lambda_client.get_waiter("function_updated_v2").wait(
        FunctionName=function_name,
        WaiterConfig={"Delay": 5, "MaxAttempts": 120},
    )


def _invoke(lambda_client: Any, function_name: str, payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=_json_bytes(payload),
    )
    stream = response.get("Payload")
    if stream is None:
        raise RepairError("lambda_payload_stream_missing")
    try:
        raw = stream.read()
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    metadata = {
        key: response.get(key)
        for key in ("StatusCode", "FunctionError", "ExecutedVersion")
        if key in response
    }
    if metadata.get("FunctionError"):
        raise RepairError(f"lambda_function_error:{metadata.get('FunctionError')}")
    try:
        outer = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RepairError("lambda_response_json_invalid") from exc
    if not isinstance(outer, dict):
        raise RepairError("lambda_response_not_object")
    if "statusCode" in outer:
        try:
            status_code = int(outer.get("statusCode"))
        except Exception as exc:
            raise RepairError("lambda_api_status_invalid") from exc
        body = outer.get("body")
        if isinstance(body, str):
            try:
                parsed = json.loads(body)
            except Exception as exc:
                raise RepairError("lambda_api_body_json_invalid") from exc
        else:
            parsed = body
        if not isinstance(parsed, dict):
            raise RepairError("lambda_api_body_not_object")
        if status_code != 200:
            raise RepairError(f"lambda_api_status_not_200:{status_code}:{parsed.get('error')}")
        return parsed, metadata
    return outer, metadata


def _run(command: Sequence[str], *, env: Mapping[str, str], timeout: int = 2400) -> None:
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        env=dict(env),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise RepairError(f"command_failed:{command[0]}:{result.returncode}")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"evidence_not_object:{path.name}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", DEFAULT_REGION))
    parser.add_argument("--stack-name", default=os.environ.get("MLB_ROOT_STACK", DEFAULT_STACK))
    parser.add_argument("--slate-date", default="2026-08-25")
    parser.add_argument("--evidence-dir", type=Path, default=Path("/tmp/mlb-r7-repair/evidence"))
    args = parser.parse_args(argv)

    evidence = args.evidence_dir
    evidence.mkdir(parents=True, exist_ok=True)
    session = boto3.Session(region_name=args.region)
    cfn = session.client("cloudformation")
    lambda_client = session.client(
        "lambda",
        config=Config(
            connect_timeout=10,
            read_timeout=930,
            tcp_keepalive=True,
            retries={"total_max_attempts": 4, "mode": "standard"},
        ),
    )

    pull_fn = _resolve_function(cfn, args.stack_name, PULL_LOGICAL_ID)
    trainer_fn = _resolve_function(cfn, args.stack_name, TRAINER_LOGICAL_ID)
    if pull_fn == trainer_fn:
        raise RepairError("resolved_function_identity_collision")

    pull_before = lambda_client.get_function_configuration(FunctionName=pull_fn)
    trainer_before = lambda_client.get_function_configuration(FunctionName=trainer_fn)
    _assert_active(pull_before, handler=PULL_HANDLER)
    _assert_active(trainer_before, handler=TRAINER_HANDLER)
    _write(evidence / "pull-before.json", pull_before)
    _write(evidence / "trainer-before.json", trainer_before)

    with tempfile.TemporaryDirectory(prefix="mlb-r7-overlay-") as tmp:
        package, desired_digest = _build_overlay_package(lambda_client, pull_fn, Path(tmp))
        update = lambda_client.update_function_code(
            FunctionName=pull_fn,
            ZipFile=package.read_bytes(),
            Publish=False,
        )
        _write(evidence / "pull-update.json", update)
    _wait_updated(lambda_client, pull_fn)
    pull_after = lambda_client.get_function_configuration(FunctionName=pull_fn)
    _assert_active(pull_after, handler=PULL_HANDLER)
    if pull_after.get("CodeSha256") != desired_digest:
        raise RepairError("deployed_pull_code_digest_mismatch")
    _write(evidence / "pull-after.json", pull_after)

    if int(trainer_before.get("MemorySize") or 0) != 4096:
        update = lambda_client.update_function_configuration(
            FunctionName=trainer_fn,
            MemorySize=4096,
        )
        _write(evidence / "trainer-capacity-update.json", update)
        _wait_updated(lambda_client, trainer_fn)
    trainer_after = lambda_client.get_function_configuration(FunctionName=trainer_fn)
    _assert_active(trainer_after, handler=TRAINER_HANDLER)
    if int(trainer_after.get("MemorySize") or 0) != 4096:
        raise RepairError("trainer_memory_not_4096")
    _write(evidence / "trainer-after.json", trainer_after)

    rebuild, rebuild_meta = _invoke(
        lambda_client,
        pull_fn,
        {
            "mode": "movement_identity_rebuild",
            "game_date_et": args.slate_date,
            "run": "stable_identity_rebuild",
        },
    )
    _write(evidence / "rebuild.json", rebuild)
    _write(evidence / "rebuild-invocation.json", rebuild_meta)
    required_rebuild = {
        "ok": True,
        "immutablePregameOnly": True,
        "outcomeDataUsed": False,
        "postStartObservationUsed": False,
        "predictionsWritten": 0,
        "locksWritten": 0,
        "labelsWritten": 0,
        "otherSportChanged": False,
    }
    for field, expected in required_rebuild.items():
        if rebuild.get(field) != expected:
            raise RepairError(f"rebuild_contract_failed:{field}")
    if int(rebuild.get("stored") or 0) != 15:
        raise RepairError(f"movement_rebuild_incomplete:{rebuild.get('stored')}")

    env = os.environ.copy()
    env["AWS_REGION"] = args.region
    env["AWS_DEFAULT_REGION"] = args.region
    env["PYTHONPATH"] = str(ROOT)
    scoring_path = evidence / "scoring-guard.json"
    _run(
        [
            sys.executable,
            "scripts/mlb_scoring_guard_status.py",
            "--slate-date",
            args.slate_date,
            "--output",
            str(scoring_path),
            "--enforce",
        ],
        env=env,
        timeout=600,
    )
    scoring = _load(scoring_path)
    summary = scoring.get("summary") or {}
    if not (
        scoring.get("guardPassed") is True
        and scoring.get("readOnly") is True
        and int(summary.get("officialGameCount") or 0) == 15
        and int(summary.get("movementFeatureGameCount") or 0) == 15
        and int(summary.get("missingMovementCount") or 0) == 0
        and int(summary.get("persistedPredictionGameCount") or 0) == 15
    ):
        raise RepairError("scoring_guard_postrepair_failed")

    training = evidence / "training.json"
    training_invocation = evidence / "training-invocation.json"
    selection = evidence / "selection.json"
    selection_invocation = evidence / "selection-invocation.json"
    status = evidence / "status.json"
    status_invocation = evidence / "status-invocation.json"
    common = [
        sys.executable,
        "scripts/invoke_mlb_trainer_with_retry.py",
        "--function-name",
        trainer_fn,
        "--region",
        args.region,
    ]
    _run(
        common
        + [
            "--payload",
            '{"sport":"mlb","mode":"scheduled","run":"stable_feature_identity_postrepair"}',
            "--response",
            str(training),
            "--invocation",
            str(training_invocation),
            "--retry-execution-lease",
            "--deadline-seconds",
            "1800",
            "--retry-delay-seconds",
            "30",
        ],
        env=env,
        timeout=2400,
    )
    _run(
        common
        + [
            "--payload",
            '{"sport":"mlb","mode":"selection_capture","run":"stable_feature_identity_postrepair_selection"}',
            "--response",
            str(selection),
            "--invocation",
            str(selection_invocation),
            "--retry-execution-lease",
            "--deadline-seconds",
            "1800",
            "--retry-delay-seconds",
            "30",
        ],
        env=env,
        timeout=2400,
    )
    _run(
        common
        + [
            "--status-training-result",
            str(training),
            "--status-selection-capture-result",
            str(selection),
            "--response",
            str(status),
            "--invocation",
            str(status_invocation),
        ],
        env=env,
        timeout=1200,
    )

    training_body = _load(training)
    selection_body = _load(selection)
    status_body = _load(status)
    health = status_body.get("trainingHealth") or {}
    selection_health = status_body.get("selectionCaptureHealth") or {}
    latest = health.get("latestRun") or training_body
    acceptance = {
        "ok": bool(
            training_body.get("ok") is True
            and selection_body.get("ok") is True
            and status_body.get("ok") is True
            and int(latest.get("acceptedRowCount") or 0) >= 18
            and int(latest.get("rejectedRowCount") or 0) == 0
            and health.get("deploymentIdentityMatches") is True
            and selection_health.get("deploymentIdentityMatches") is True
            and latest.get("liveInferenceAuthority") is not True
            and latest.get("productionAuthorityChanged") is not True
            and latest.get("automaticPromotionEnabled") is not True
            and latest.get("championChanged") is not True
        ),
        "acceptedRowCount": latest.get("acceptedRowCount"),
        "rejectedRowCount": latest.get("rejectedRowCount"),
        "partitionCounts": latest.get("partitionCounts"),
        "processedThroughSlateDate": (
            latest.get("canonicalSlateContinuity") or {}
        ).get("processedThroughSlateDate"),
        "trainingDeploymentIdentityMatches": health.get("deploymentIdentityMatches"),
        "selectionDeploymentIdentityMatches": selection_health.get("deploymentIdentityMatches"),
        "liveInferenceAuthority": latest.get("liveInferenceAuthority"),
        "productionAuthorityChanged": latest.get("productionAuthorityChanged"),
        "automaticPromotionEnabled": latest.get("automaticPromotionEnabled"),
        "championChanged": latest.get("championChanged"),
        "movementCoverage": {
            "officialGameCount": summary.get("officialGameCount"),
            "movementFeatureGameCount": summary.get("movementFeatureGameCount"),
            "missingMovementCount": summary.get("missingMovementCount"),
        },
        "pullFunction": pull_fn,
        "trainerFunction": trainer_fn,
        "trainerMemoryMb": trainer_after.get("MemorySize"),
        "sharedRootStackDeployed": False,
        "otherSportChanged": False,
        "immutablePredictionHistoryRewritten": False,
        "postStartPredictionCreated": False,
        "predictionOrLockWritePerformed": False,
        "promotionGateChanged": False,
    }
    _write(evidence / "acceptance.json", acceptance)
    print(json.dumps(acceptance, indent=2, sort_keys=True))
    if not acceptance["ok"]:
        raise RepairError("r7_postrepair_acceptance_failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
