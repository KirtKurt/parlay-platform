from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import boto3

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "engineering_agent/policy.json").read_text())

DEFAULT_MISSION = (
    "Continuously improve the MLB research and successor-learning platform. "
    "Prioritize source-honest point-in-time data capture, autonomous research, diagnostics, "
    "feature/model evaluation, calibration, and reliability. Preserve immutable evidence, "
    "chronological validation, fail-closed production authority, and sport isolation."
)


def invoke(prompt: str) -> tuple[str, str]:
    regions = [x.strip() for x in os.environ.get("INQSI_ENGINEERING_REGIONS", "us-east-1,us-east-2,us-west-2").split(",") if x.strip()]
    models = [x.strip() for x in os.environ.get("INQSI_ENGINEERING_MODEL_IDS", "us.amazon.nova-2-lite-v1:0,global.amazon.nova-2-lite-v1:0,amazon.nova-pro-v1:0").split(",") if x.strip()]
    errors: list[str] = []
    for region in regions:
        client = boto3.client("bedrock-runtime", region_name=region)
        for model in models:
            try:
                response = client.converse(
                    modelId=model,
                    system=[{"text": "You are the InQsi autonomous senior MLB engineering planner. You plan one small, testable engineering task at a time. You never weaken fail-closed safety, chronology, immutable evidence, or production authority."}],
                    messages=[{"role": "user", "content": [{"text": prompt}]}],
                    inferenceConfig={"temperature": 0.1, "maxTokens": 5000},
                )
                text = "".join(str(x.get("text") or "") for x in response.get("output", {}).get("message", {}).get("content", []))
                if text.strip():
                    return text, f"{region}::{model}"
            except Exception as exc:
                errors.append(f"{region}::{model}:{type(exc).__name__}")
    raise RuntimeError("all Bedrock routes failed: " + ",".join(errors))


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("planner response must be an object")
    return value


def validate(value: dict[str, Any]) -> dict[str, Any]:
    required = ("title", "objective", "implementation", "acceptanceTests", "safetyReceipts")
    for key in required:
        if not value.get(key):
            raise ValueError(f"missing {key}")
    files = [str(x) for x in value.get("likelyFiles") or []][:10]
    forbidden = [str(x).lower() for x in POLICY["forbidden_path_fragments"]]
    exact = set(POLICY["forbidden_exact_paths"])
    allowed = []
    for path in files:
        if path in exact or any(token in path.lower() for token in forbidden):
            continue
        if any(path.startswith(prefix) for prefix in POLICY["allowed_prefixes"]):
            allowed.append(path)
    value["likelyFiles"] = allowed
    value["noDirectProductionDeploy"] = True
    value["noMainBranchWrite"] = True
    value["noModelPromotion"] = True
    value["noSecretMutation"] = True
    value["noOtherSportChange"] = True
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    evidence = Path(args.evidence).read_text(encoding="utf-8", errors="replace")[-50000:]
    mission = os.environ.get("INQSI_ENGINEERING_MISSION", "").strip() or DEFAULT_MISSION
    prompt = json.dumps({
        "mission": mission,
        "latestOperationalEvidence": evidence,
        "policy": POLICY,
        "instruction": (
            "Choose exactly one highest-value engineering task that is actionable from current evidence and can be implemented in a small draft PR. "
            "Do not repeat a task already shown as fixed. Do not propose weakening thresholds merely to pass. Return strict JSON only with: "
            "title (string), objective (string), implementation (array of concrete steps), likelyFiles (array), acceptanceTests (array), safetyReceipts (array), evidenceBasis (array)."
        ),
    })
    raw, route = invoke(prompt)
    task = validate(parse_json(raw))
    task["plannerRoute"] = route
    Path(args.output).write_text(json.dumps(task, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"ok": True, "title": task["title"], "plannerRoute": route}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
