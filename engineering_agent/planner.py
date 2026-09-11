from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import boto3

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "engineering_agent/policy.json").read_text())
DECISION_HISTORY_PATH = ROOT / "engineering_agent/decision_history.json"

DEFAULT_MISSION = (
    "Continuously improve the MLB research and successor-learning platform. "
    "Prioritize source-honest point-in-time data capture, autonomous research, diagnostics, "
    "feature/model evaluation, calibration, and reliability. Preserve immutable evidence, "
    "chronological validation, fail-closed production authority, and sport isolation."
)

_STOP_WORDS = {
    "a", "an", "and", "for", "from", "in", "into", "of", "on", "the", "to", "with",
    "implement", "implementation", "update", "fix", "repair", "add", "ensure", "make",
}

_PROSPECTIVE_UNDERPOWERED_SIGNALS = (
    "insufficient prospective selected recommendation",
    "insufficient_prospective_selected_recommendation",
    "lacks sufficient prospective selected recommendation",
    "prospective selected recommendation count below",
    "insufficient fresh test rows",
    "insufficient_fresh_test_rows",
    "insufficient prospective test rows",
    "insufficient_prospective_test_rows",
)

_CALIBRATION_MUTATION_SIGNALS = (
    "adjust calibration",
    "adjust the calibration",
    "adjust model output",
    "recalibrat",
    "tune calibrat",
    "refit calibrat",
    "fit calibrat",
    "calibration technique",
    "platt scaling",
    "isotonic regression",
    "retrain model",
    "retrain the model",
    "update calibration parameter",
    "change calibration parameter",
    "adjusted calibration parameter",
)

_PROSPECTIVE_MUTATION_SIGNALS = (
    "fabricate prospective",
    "synthesize prospective",
    "backfill prospective",
    "rewrite prospective",
    "reclassify prospective",
    "manufacture prospective",
)

_SOURCE_OBSERVED_PREGAME_SIGNALS = (
    "observed starter",
    "starter rate",
    "starter_rates",
    "observed team context",
    "team_context",
    "observed lineup",
    "confirmed lineup",
    "bullpen availability",
    "pregame context",
    "pre-lock context",
    "prelock context",
)

_SYNTHETIC_OBSERVATION_SIGNALS = (
    "data augmentation",
    "augment starter",
    "augment observed",
    "synthetic starter",
    "synthetic pregame",
    "synthesize starter",
    "simulate starter",
    "fabricate starter",
    "impute observed",
    "manufacture observed",
)

_HISTORICAL_EXPANSION_SIGNALS = (
    "more historical games",
    "historical training window",
    "historical data window",
    "expand historical",
    "extend historical",
    "backfill historical",
    "historical starter",
    "historical pregame",
)

_POINT_IN_TIME_ARCHIVE_PROOF_SIGNALS = (
    "immutable pregame",
    "point-in-time pregame",
    "point in time pregame",
    "original observation",
    "archived pregame",
    "source provenance",
    "sourceprovenance",
    "retrievedatutc",
    "payloadfingerprint",
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
                    system=[{"text": "You are the InQsi autonomous senior MLB engineering planner. You plan one small, testable engineering task at a time. You never weaken fail-closed safety, chronology, immutable evidence, source-honest pregame provenance, prospective holdouts, or production authority."}],
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


def load_decision_history(path: Path | None = None) -> dict[str, Any]:
    target = path or DECISION_HISTORY_PATH
    if not target.exists():
        return {"decisions": []}
    value = json.loads(target.read_text())
    if not isinstance(value, dict) or not isinstance(value.get("decisions", []), list):
        raise ValueError("engineering decision history must contain a decisions array")
    return value


def normalize_task_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _stem_token(token: str) -> str:
    if token == "binding":
        return "bind"
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3].rstrip("n")
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def task_title_tokens(value: Any) -> set[str]:
    return {
        _stem_token(token)
        for token in normalize_task_title(value).split()
        if token and token not in _STOP_WORDS
    }


def title_similarity(left: Any, right: Any) -> float:
    a, b = task_title_tokens(left), task_title_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def blocked_task_titles(decision_history: dict[str, Any] | None) -> set[str]:
    blocked: set[str] = set()
    for decision in (decision_history or {}).get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        status = str(decision.get("status") or "").upper()
        if status not in {"COMPLETED", "REJECTED_AS_UNSAFE_OR_INAPPLICABLE"}:
            continue
        title = normalize_task_title(decision.get("title"))
        if title:
            blocked.add(title)
    return blocked


def validate_against_history(value: dict[str, Any], decision_history: dict[str, Any] | None = None) -> dict[str, Any]:
    history = decision_history if decision_history is not None else load_decision_history()
    title = normalize_task_title(value.get("title"))
    for decision in history.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        status = str(decision.get("status") or "").upper()
        if status not in {"COMPLETED", "REJECTED_AS_UNSAFE_OR_INAPPLICABLE"}:
            continue
        prior = normalize_task_title(decision.get("title"))
        if not title or not prior:
            continue
        similarity = title_similarity(title, prior)
        shared = len(task_title_tokens(title) & task_title_tokens(prior))
        if title == prior or (similarity >= 0.60 and shared >= 3):
            raise ValueError(
                f"task duplicates engineering decision history: {decision.get('title')} "
                f"(similarity={similarity:.2f})"
            )
    return value


def _require_string_list(value: dict[str, Any], key: str) -> list[str]:
    raw = value.get(key)
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{key} must be a non-empty array")
    result = [str(item).strip() for item in raw if str(item).strip()]
    if not result:
        raise ValueError(f"{key} must contain non-empty strings")
    value[key] = result
    return result


def validate_prospective_holdout_safety(value: dict[str, Any]) -> dict[str, Any]:
    """Reject planner work that would tune against an incomplete prospective holdout."""
    evidence = " ".join(str(x) for x in value.get("evidenceBasis") or []).lower()
    implementation = " ".join(str(x) for x in value.get("implementation") or []).lower()
    task_text = " ".join(
        [
            str(value.get("title") or ""),
            str(value.get("objective") or ""),
            implementation,
            evidence,
        ]
    ).lower()
    underpowered = any(signal in task_text for signal in _PROSPECTIVE_UNDERPOWERED_SIGNALS)
    mutates_calibration = any(signal in implementation for signal in _CALIBRATION_MUTATION_SIGNALS)
    mutates_prospective_rows = any(signal in task_text for signal in _PROSPECTIVE_MUTATION_SIGNALS)
    if mutates_prospective_rows:
        raise ValueError("prospective evaluation rows are immutable holdout evidence")
    if underpowered and mutates_calibration:
        raise ValueError(
            "prospective holdout is underpowered; do not tune calibration against incomplete prospective evaluation"
        )
    return value


def validate_source_honest_pregame_safety(value: dict[str, Any]) -> dict[str, Any]:
    """Do not let planning convert unavailable historical context into observed evidence."""
    evidence = " ".join(str(x) for x in value.get("evidenceBasis") or []).lower()
    implementation = " ".join(str(x) for x in value.get("implementation") or []).lower()
    task_text = " ".join(
        [
            str(value.get("title") or ""),
            str(value.get("objective") or ""),
            implementation,
            evidence,
        ]
    ).lower()
    targets_source_observed = any(signal in task_text for signal in _SOURCE_OBSERVED_PREGAME_SIGNALS)
    if not targets_source_observed:
        return value
    if any(signal in implementation for signal in _SYNTHETIC_OBSERVATION_SIGNALS):
        raise ValueError(
            "source-observed pregame inputs cannot be synthesized or data-augmented into observed evidence"
        )
    expands_history = any(signal in implementation for signal in _HISTORICAL_EXPANSION_SIGNALS)
    archive_proven = any(signal in evidence for signal in _POINT_IN_TIME_ARCHIVE_PROOF_SIGNALS)
    if expands_history and not archive_proven:
        raise ValueError(
            "historical pregame expansion requires explicit immutable point-in-time source provenance"
        )
    return value


def validate(value: dict[str, Any], *, decision_history: dict[str, Any] | None = None) -> dict[str, Any]:
    for key in ("title", "objective"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    for key in ("implementation", "acceptanceTests", "safetyReceipts", "evidenceBasis"):
        _require_string_list(value, key)
    if "likelyFiles" in value and value["likelyFiles"] is not None and not isinstance(value["likelyFiles"], list):
        raise ValueError("likelyFiles must be an array")

    validate_prospective_holdout_safety(value)
    validate_source_honest_pregame_safety(value)

    required_receipts = {str(x) for x in POLICY.get("required_negative_authority_receipts") or []}
    supplied_receipts = set(value["safetyReceipts"])
    missing_receipts = sorted(required_receipts - supplied_receipts)
    if missing_receipts:
        raise ValueError("missing required safety receipts: " + ",".join(missing_receipts))

    files = [str(x).strip() for x in value.get("likelyFiles") or [] if str(x).strip()][:10]
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
    return validate_against_history(value, decision_history)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--decision-history")
    args = parser.parse_args()
    evidence = Path(args.evidence).read_text(encoding="utf-8", errors="replace")[-50000:]
    history = load_decision_history(Path(args.decision_history) if args.decision_history else None)
    mission = os.environ.get("INQSI_ENGINEERING_MISSION", "").strip() or DEFAULT_MISSION
    prompt = json.dumps({
        "mission": mission,
        "latestOperationalEvidence": evidence,
        "decisionHistory": history,
        "blockedTaskTitles": sorted(blocked_task_titles(history)),
        "policy": POLICY,
        "instruction": (
            "Choose exactly one highest-value unresolved engineering task that is actionable from current evidence and can be implemented in a small reviewed PR. "
            "Never return a title in blockedTaskTitles or a cosmetic rewording of one. Do not propose weakening thresholds merely to pass. "
            "Prospective evaluation rows are immutable holdout evidence, never training or calibration material. If the prospective minimum is not met, do not tune a model or calibrator against that incomplete set; treat chronological accumulation as a wait condition unless current evidence proves a distinct capture defect. Never fabricate, backfill, rewrite, or reclassify prospective evaluation rows. "
            "Source-observed pregame features such as starter rates, lineups, team context, and bullpen availability may count as observed only when supported by pre-lock source provenance. Never data-augment or synthesize them into observed rows. Historical expansion is allowed only when evidence identifies immutable point-in-time pregame observations with source provenance; final boxes or current-season totals cannot be reconstructed into past locks. "
            "Return strict JSON only with: title (string), objective (string), implementation (array of concrete steps), likelyFiles (array), acceptanceTests (array), safetyReceipts (array containing every required_negative_authority_receipts value), evidenceBasis (array)."
        ),
    })
    raw, route = invoke(prompt)
    task = validate(parse_json(raw), decision_history=history)
    task["plannerRoute"] = route
    Path(args.output).write_text(json.dumps(task, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"ok": True, "title": task["title"], "plannerRoute": route}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
