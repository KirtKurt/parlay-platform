from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def fingerprint(template_path: str, resources_path: str, output_path: str) -> None:
    template = json.loads(Path(template_path).read_text()).get("TemplateBody")
    resources = json.loads(Path(resources_path).read_text()).get("StackResourceSummaries", [])
    material = {
        "template": template,
        "resources": sorted(
            ({k: row.get(k) for k in ("LogicalResourceId", "PhysicalResourceId", "ResourceType")} for row in resources),
            key=lambda row: (str(row.get("LogicalResourceId")), str(row.get("PhysicalResourceId"))),
        ),
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()
    Path(output_path).write_text(digest + "\n")
    print(digest)


def restrict_template(source_path: str, target_path: str) -> None:
    lines = Path(source_path).read_text(encoding="utf-8").splitlines(keepends=True)
    lines = [line for line in lines if "ReservedConcurrentExecutions:" not in line]
    remove = {
        "TennisIngestDeadLetterQueue", "TennisLockDeadLetterQueue",
        "TennisOutcomeDeadLetterQueue", "TennisTrainingDeadLetterQueue",
        "TennisIngestDeadLetterQueuePolicy", "TennisLockDeadLetterQueuePolicy",
        "TennisOutcomeDeadLetterQueuePolicy", "TennisTrainingDeadLetterQueuePolicy",
        "TennisIngestDlqAlarm", "TennisLockDlqAlarm",
        "TennisOutcomeDlqAlarm", "TennisTrainingDlqAlarm",
    }
    out: list[str] = []
    i = 0
    while i < len(lines):
        match = re.match(r"^  ([A-Za-z0-9]+):\s*$", lines[i])
        if match and match.group(1) in remove:
            i += 1
            while i < len(lines) and not re.match(r"^  [A-Za-z0-9]+:\s*$", lines[i]) and not lines[i].startswith("Outputs:"):
                i += 1
            continue
        out.append(lines[i]); i += 1
    lines = out; out = []; i = 0
    while i < len(lines):
        stripped = lines[i].lstrip(); indent = len(lines[i]) - len(stripped)
        if stripped.startswith("DeadLetterQueue:") or stripped.startswith("DeadLetterConfig:"):
            i += 1
            while i < len(lines):
                if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= indent:
                    break
                i += 1
            continue
        out.append(lines[i]); i += 1
    lines = out; out = []; i = 0
    while i < len(lines):
        if re.match(r"^        - Version:", lines[i]):
            start = i; i += 1
            while i < len(lines):
                if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= 8:
                    break
                i += 1
            block = lines[start:i]
            if any("sqs:SendMessage" in line for line in block):
                continue
            out.extend(block); continue
        out.append(lines[i]); i += 1
    text = "".join(out)
    forbidden = ("AWS::SQS", "DeadLetterQueue", "DeadLetterConfig", "sqs:SendMessage", "ReservedConcurrentExecutions")
    present = [token for token in forbidden if token in text]
    if present:
        raise SystemExit(f"restricted template retains forbidden authority: {present}")
    Path(target_path).write_text(text, encoding="utf-8")


def outputs(stack_path: str, outputs_path: str, api_path: str) -> None:
    stack = json.loads(Path(stack_path).read_text())["Stacks"][0]
    if stack["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
        raise SystemExit(f"unexpected Tennis stack status: {stack['StackStatus']}")
    values = {row["OutputKey"]: row.get("OutputValue") for row in stack.get("Outputs", [])}
    required = {
        "TennisApiUrl", "TennisSnapshotsTableName", "TennisSignalLedgerTableName",
        "TennisPredictionsTableName", "TennisOutcomesTableName", "TennisModelsTableName",
        "TennisArchiveBucketName", "TennisIngestScheduleArn", "TennisLockScheduleArn",
        "TennisOutcomeScheduleArn", "TennisTrainingScheduleArn",
    }
    missing = sorted(required - set(values))
    if missing:
        raise SystemExit(f"missing Tennis outputs: {missing}")
    for key in required - {"TennisApiUrl"}:
        if "tennis" not in str(values[key]).lower():
            raise SystemExit(f"non-Tennis output for {key}: {values[key]}")
    Path(outputs_path).write_text(json.dumps(values, indent=2, sort_keys=True))
    Path(api_path).write_text(str(values["TennisApiUrl"]).rstrip("/") + "\n")


def payload(path: str, kind: str) -> None:
    row = json.loads(Path(path).read_text())
    if row.get("ok") is not True or row.get("sport") != "tennis":
        raise SystemExit(f"invalid {kind} response")
    if kind == "model":
        if float(row.get("collectionLeadHours")) != 10.0:
            raise SystemExit("Tennis collection lead is not 10 hours")
        if row.get("predictionMode") not in {"MARKET_BOOTSTRAP", "TENNIS_ML_CHAMPION"}:
            raise SystemExit("invalid Tennis prediction mode")
        if (row.get("data_architecture") or {}).get("cross_sport_runtime_data") is not False:
            raise SystemExit("cross-sport runtime data is not disabled")
    elif kind == "status":
        if (row.get("trainingPolicy") or {}).get("crossSportInputsAllowed") is not False:
            raise SystemExit("cross-sport ML inputs are not disabled")
        if (row.get("status") or {}).get("mode") not in {"MARKET_BOOTSTRAP", "TENNIS_ML_CHAMPION"}:
            raise SystemExit("invalid ML status mode")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fingerprint"); p.add_argument("template"); p.add_argument("resources"); p.add_argument("output")
    p = sub.add_parser("restrict"); p.add_argument("source"); p.add_argument("target")
    p = sub.add_parser("outputs"); p.add_argument("stack"); p.add_argument("output"); p.add_argument("api")
    p = sub.add_parser("payload"); p.add_argument("path"); p.add_argument("kind", choices=("model", "status", "discovery"))
    args = parser.parse_args()
    if args.command == "fingerprint": fingerprint(args.template, args.resources, args.output)
    elif args.command == "restrict": restrict_template(args.source, args.target)
    elif args.command == "outputs": outputs(args.stack, args.output, args.api)
    else: payload(args.path, args.kind)


if __name__ == "__main__":
    main()
