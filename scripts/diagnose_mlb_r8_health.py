"""Bounded read-only MLB runtime diagnosis; prints no credentials or feature data."""
import hashlib
import json
import re
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config

CONFIG = Config(retries={"max_attempts": 0}, read_timeout=120)


def main():
    cf = boto3.client("cloudformation", config=CONFIG)
    lam = boto3.client("lambda", config=CONFIG)
    logs = boto3.client("logs", config=CONFIG)
    def resolve(stack, logical):
        return cf.describe_stack_resource(StackName=stack, LogicalResourceId=logical)["StackResourceDetail"]["PhysicalResourceId"]
    trainer = resolve("parlay-platform-dev", "MLBMLTrainingFunction")
    auto = resolve("parlay-platform-mlb-auto-llm", "MLBAutoLLMFunction")
    response = lam.invoke(FunctionName=trainer, Payload=json.dumps({"sport": "mlb", "mode": "status"}).encode())
    if response.get("FunctionError"):
        raise RuntimeError("MLB_TRAINER_STATUS_FAILED")
    status = json.loads(response["Payload"].read())
    latest = (status.get("trainingHealth") or {}).get("latestRun") or status.get("latestStatus") or {}
    candidate = status.get("latestCandidate") or {}
    result = {
        "observedAtUtc": datetime.now(timezone.utc).isoformat(),
        "experimentId": status.get("experimentId"),
        "candidateDigest": candidate.get("artifactDigest"),
        "promotionGate": candidate.get("promotionGate"),
        "latestTraining": {k: v for k, v in latest.items() if k in {"status", "createdAtUtc", "completedAtUtc", "startedAtUtc", "processedThroughSlateDate", "acceptedRowCount", "partitionCounts", "modelTrained", "validation", "prospectiveTest", "promotion"}},
        "health": {k: {a: b for a,b in (status.get(k) or {}).items() if a != "latestRun"} for k in ["trainingHealth", "selectionCaptureHealth"]},
        "runtimeAuthorityActivationAvailable": status.get("runtimeAuthorityActivationAvailable"),
        "successorDevelopment": latest.get("successorDevelopment"),
        "successorCapture": (((status.get("selectionCaptureHealth") or {}).get("latestRun") or {}).get("selectionCapture") or {}).get("successorCapture"),
    }
    pointer = (candidate.get("artifacts") or {}).get("evaluation") or {}
    if pointer:
        try:
            response = boto3.client("s3", config=CONFIG).get_object(Bucket=pointer["bucket"], Key=pointer["key"], VersionId=pointer["versionId"])
            body = response["Body"].read()
            assert hashlib.sha256(body).hexdigest() == pointer["sha256"]
            assert response.get("Metadata", {}).get("sha256") == pointer["sha256"]
            evaluation = json.loads(body)
            result["evaluation"] = {k: evaluation.get(k) for k in ["validation", "prospectiveTest", "split"]}
        except Exception as exc:
            result["evaluationReadError"] = type(exc).__name__
    result["runtime"] = {}
    for name, function in [("trainer", trainer), ("auto", auto)]:
        cfg = lam.get_function_configuration(FunctionName=function)
        result["runtime"][name] = {k: cfg.get(k) for k in ["State", "LastUpdateStatus", "LastModified", "CodeSha256", "Timeout", "MemorySize", "Handler"]}
        try:
            events = logs.filter_log_events(logGroupName="/aws/lambda/" + function, startTime=int((datetime.now(timezone.utc)-timedelta(hours=8)).timestamp()*1000), filterPattern='?ERROR ?Task ?Traceback', limit=100).get("events", [])
            errors = []
            for event in events:
                message = event.get("message", "")
                error_types = re.findall(r'\b([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception|Unavailable))\b', message)
                frames = re.findall(r'File "(/var/task/[A-Za-z0-9_./-]+)", line (\d+), in ([A-Za-z0-9_]+)', message)
                undefined = re.findall(r"name '([A-Za-z_][A-Za-z_0-9]*)' is not defined", message)
                errors.append({"timestamp":event.get("timestamp"), "types":sorted(set(error_types)), "frames":frames, "undefinedNames":undefined, "codes": re.findall(r"(?:RuntimeError: |HTTP Error )([A-Z][A-Z_0-9]+|[0-9]{3})", message), "timeout":"Task timed out" in message})
            result["runtime"][name]["errors"] = errors
        except Exception as exc:
            result["runtime"][name]["logReadError"] = type(exc).__name__
    try:
        table = boto3.resource("dynamodb", config=CONFIG).Table("parlay_platform_snapshots")
        item = table.get_item(Key={"PK":"GAME_WINNERS#mlb#2026-09-07", "SK":"LOCKED#GAME#2026-09-07T18:10:00+00:00#mlb_statsapi:824062"}, ConsistentRead=True).get("Item") or {}
        row = item.get("data") or {}
        fields = ["record_type", "slate_date", "immutable_locked", "stage_authority_verified", "selection_lock_verified", "stage_authority_version", "immutable_locked_storage_version", "stage_fingerprint", "game_id", "game_identity", "predicted_winner"]
        result["auditMetadata"] = {k:item.get(k) for k in fields}
        result["auditRow"] = {k:row.get(k) for k in ["gameId", "gameIdentity", "providerEventId", "commenceTime", "predictedWinner", "canonicalPerGameStageAuthority"]}
    except Exception as exc:
        result["auditReadError"] = type(exc).__name__
    try:
        os.environ.setdefault("SNAPSHOTS_TABLE", "parlay_platform_snapshots")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hello_world"))
        import mlb_yesterday_audit as audit
        import mlb_yesterday_audit_lock_card_resolver_patch as resolver
        resolver.apply(audit)
        verified = audit.load_locked_predictions("2026-09-07")
        result["auditVerification"] = {"rowCount": len(verified["rows"]), "authority": verified["authority"]}
    except Exception as exc:
        result["auditVerificationError"] = str(exc)[:500]
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
