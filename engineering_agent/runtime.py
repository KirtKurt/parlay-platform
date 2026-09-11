from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import boto3

from engineering_agent.planner import (
    DEFAULT_MISSION,
    POLICY,
    blocked_task_titles,
    load_decision_history,
    normalize_task_title,
    parse_json,
    title_similarity,
    validate,
    validate_against_history,
)

_SUPERSEDED_HEALTH_CUTOFF_EPOCH = 1789120225.0  # 2026-09-11T09:50:25Z, merge of PR #765
_SUPERSEDED_HEALTH_TOKENS = (
    "mlb_successor_runtime_health",
    "mlb_trainer_deploy_health_diagnostic",
    "mlb_trainer_health_diagnostic",
    "mlb_trainer_runtime_error",
    "mlb_trainer_function_error",
)
_RETIRED_DIAGNOSTIC_REPORTS = (
    "mlb_ml_optimization_status_latest.json",
    "mlb_ml_clean_cohort_latest.json",
    "mlb_ml_outcome_challenger_latest.json",
    "mlb_ml_reliability_challenger_latest.json",
    "mlb_ml_challenger_bundle_latest.json",
)

FOCUS_DOMAINS = (
    "clean_cohort",
    "challenger_model",
    "calibration",
    "data_capture",
    "reliability",
)

_FOCUS_SIGNAL_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "clean_cohort",
        (
            "insufficient_clean_rows",
            "insufficient_clean_official_evidence",
            "clean cohort",
            "cleancohort",
            "data_admission",
            "data admission",
            "invalid immutable fundamentals snapshot",
            "quarantinedrowcount",
            "admittedrows",
        ),
    ),
    (
        "challenger_model",
        (
            "insufficient_observed_starter",
            "insufficient_observed_team_context",
            "insufficient_whole_slate_development_rows",
            "whole_slate_development",
            "successor",
            "challenger",
            "development train",
        ),
    ),
    (
        "calibration",
        (
            "calibration_error_too_high",
            "selected_reliability_calibration_too_high",
            "no_positive_brier_skill",
            "log_loss_not_lower",
            "brier",
            "calibrationerror",
            "\"ece\"",
        ),
    ),
    (
        "data_capture",
        (
            "missing_t10",
            "coverage_mismatch",
            "statcast",
            "ingestion",
            "snapshotwrites",
            "source coverage",
            "pregame",
        ),
    ),
    (
        "reliability",
        (
            "functionerror",
            "function error",
            "timeout",
            "scheduled failure",
            "runtime error",
            "health failed",
            "execution failure",
        ),
    ),
)

_TASK_DOMAIN_SIGNALS: dict[str, tuple[str, ...]] = {
    "deployment_identity": (
        "deployment identity",
        "deploymentidentity",
        "runtime identity",
        "implementation identity",
        "git sha mismatch",
        "template sha",
    ),
    "clean_cohort": (
        "clean cohort",
        "clean_cohort",
        "admission",
        "admitted rows",
        "quarantine",
        "immutable fundamentals snapshot",
        "clean rows",
    ),
    "challenger_model": (
        "successor",
        "challenger",
        "observed starter",
        "team context",
        "whole slate development",
        "development row",
    ),
    "calibration": (
        "calibration",
        "brier",
        "log loss",
        "log_loss",
        "ece",
        "reliability threshold",
    ),
    "data_capture": (
        "capture",
        "ingestion",
        "t10",
        "statcast",
        "snapshot",
        "source coverage",
        "pregame source",
    ),
    "reliability": (
        "runtime reliability",
        "function error",
        "functionerror",
        "timeout",
        "schedule failure",
        "heartbeat",
        "execution health",
    ),
}


def _filter_superseded_evidence(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except Exception:
            kept.append(line)
            continue
        path = str(item.get("path") or "").lower()
        observed = float(item.get("observedEpoch") or 0.0)
        if any(path.endswith(name) for name in _RETIRED_DIAGNOSTIC_REPORTS):
            continue
        if (
            any(token in path for token in _SUPERSEDED_HEALTH_TOKENS)
            and observed <= _SUPERSEDED_HEALTH_CUTOFF_EPOCH
        ):
            continue
        kept.append(line)
    return "\n".join(kept) + ("\n" if kept else "")


def _task_text(value: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("title", "objective"):
        raw = value.get(key)
        if raw:
            chunks.append(str(raw))
    for key in ("implementation", "likelyFiles", "acceptanceTests", "evidenceBasis"):
        raw = value.get(key)
        if isinstance(raw, list):
            chunks.extend(str(item) for item in raw if item)
    return " ".join(chunks).lower()


def classify_task_domain(value: dict[str, Any]) -> str:
    text = _task_text(value)
    scores: dict[str, int] = {}
    for domain, signals in _TASK_DOMAIN_SIGNALS.items():
        scores[domain] = sum(1 for signal in signals if signal in text)
    best_score = max(scores.values(), default=0)
    if best_score <= 0:
        return "other"
    for domain in (
        "deployment_identity",
        "clean_cohort",
        "challenger_model",
        "calibration",
        "data_capture",
        "reliability",
    ):
        if scores.get(domain) == best_score:
            return domain
    return "other"


def prioritized_focus_domains(evidence: str) -> list[str]:
    lowered = evidence.lower()
    ranked: list[str] = []
    for domain, signals in _FOCUS_SIGNAL_GROUPS:
        if any(signal in lowered for signal in signals):
            ranked.append(domain)
    return ranked


def _rejected_domains(rejected: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("domain"))
        for item in rejected
        if str(item.get("domain") or "") in FOCUS_DOMAINS
    }


def next_focus_domain(
    evidence: str,
    rejected: list[dict[str, Any]],
) -> str | None:
    ranked = prioritized_focus_domains(evidence)
    if not ranked:
        return None
    rejected_domains = _rejected_domains(rejected)
    for domain in ranked:
        if domain not in rejected_domains:
            return domain
    # If each evidenced domain has already produced a rejected task, retry the
    # highest-priority unresolved domain rather than drifting into a completed
    # or non-evidenced area.
    return ranked[0]


def _planner_payload(
    evidence: str,
    history: dict[str, Any],
    rejected: list[dict[str, Any]],
    *,
    required_focus_domain: str | None,
) -> str:
    required_receipts = [
        str(x) for x in POLICY.get("required_negative_authority_receipts") or []
    ]
    excluded_domains = sorted(
        {
            "deployment_identity",
            *(
                str(item.get("domain"))
                for item in rejected
                if str(item.get("domain") or "") == "deployment_identity"
            ),
        }
    )
    focus_instruction = ""
    if required_focus_domain:
        focus_instruction = (
            f" This retry is constrained to focus domain {required_focus_domain!r}. "
            "The returned task MUST materially address that domain using current evidence; "
            "do not return deployment-identity work or drift to another domain."
        )
    return json.dumps(
        {
            "mission": DEFAULT_MISSION,
            "latestOperationalEvidence": evidence,
            "decisionHistory": history,
            "blockedTaskTitles": sorted(blocked_task_titles(history)),
            "rejectedProposalsThisCycle": rejected,
            "requiredSafetyReceipts": required_receipts,
            "requiredFocusDomain": required_focus_domain,
            "excludedFocusDomains": excluded_domains,
            "requiredOutputSchema": {
                "title": "string",
                "objective": "string",
                "implementation": ["step 1", "step 2"],
                "likelyFiles": ["allowed/path.py"],
                "acceptanceTests": ["test 1", "test 2"],
                "safetyReceipts": required_receipts,
                "evidenceBasis": ["specific current evidence"],
            },
            "instruction": (
                "Choose exactly one highest-value unresolved MLB engineering task actionable from current evidence "
                "and suitable for a small reviewed PR. Never return a title in blockedTaskTitles or a semantic/cosmetic "
                "rewording of one. Never repeat any rejectedProposalsThisCycle title. Recent main-history evidence is "
                "authoritative for whether work is already merged. Historical-training-only rows with intentionally "
                "unavailable label-observation times are not a reason to fabricate timestamps or weaken chronology. "
                "Prefer active current blockers over expected fail-closed diagnostic errors. Do not weaken thresholds, "
                "chronology, immutable evidence, qualification, calibration, promotion, or production authority merely "
                "to pass. Return ONE strict JSON object matching requiredOutputSchema. implementation, likelyFiles, "
                "acceptanceTests, safetyReceipts, and evidenceBasis MUST be JSON arrays of strings. safetyReceipts MUST "
                "contain every string in requiredSafetyReceipts exactly. Do not return prose outside the JSON object."
                + focus_instruction
            ),
        },
        separators=(",", ":"),
    )


def _invoke(function_name: str, prompt: str) -> dict[str, Any]:
    client = boto3.client("lambda", region_name="us-east-1")
    response = client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            {"mode": "engineering_plan", "prompt": prompt}
        ).encode("utf-8"),
    )
    if response.get("FunctionError"):
        raise RuntimeError(
            "planner Lambda FunctionError: " + str(response.get("FunctionError"))
        )
    value = json.loads(response["Payload"].read())
    if not isinstance(value, dict):
        raise ValueError("planner Lambda response must be an object")
    return value


def _function_name(stack_name: str, logical_id: str) -> str:
    cf = boto3.client("cloudformation", region_name="us-east-1")
    detail = cf.describe_stack_resource(
        StackName=stack_name,
        LogicalResourceId=logical_id,
    )["StackResourceDetail"]
    value = str(detail.get("PhysicalResourceId") or "").strip()
    if not value or value == "None":
        raise RuntimeError("planner Lambda physical resource ID unavailable")
    return value


def _reject_cycle_repeat(
    title: str,
    rejected: list[dict[str, Any]],
) -> None:
    normalized = normalize_task_title(title)
    if not normalized:
        return
    for prior in rejected:
        prior_title = str(prior.get("title") or "")
        if prior_title.startswith("<"):
            continue
        similarity = title_similarity(normalized, prior_title)
        if (
            normalized == normalize_task_title(prior_title)
            or similarity >= 0.60
        ):
            raise ValueError(
                "task repeats a proposal already rejected this cycle: "
                f"{prior_title} (similarity={similarity:.2f})"
            )


def _validate_focus_domain(
    task: dict[str, Any],
    required_focus_domain: str | None,
) -> str:
    domain = classify_task_domain(task)
    if required_focus_domain and domain != required_focus_domain:
        raise ValueError(
            "task does not satisfy required focus domain: "
            f"required={required_focus_domain} observed={domain}"
        )
    return domain


def run(
    *,
    evidence_path: Path,
    output_path: Path,
    response_path: Path,
    attempts_path: Path,
    stack_name: str,
    logical_id: str,
    max_attempts: int,
) -> dict[str, Any]:
    history = load_decision_history()
    raw_evidence = evidence_path.read_text(encoding="utf-8", errors="replace")
    evidence = _filter_superseded_evidence(raw_evidence)[-28000:]
    function_name = _function_name(stack_name, logical_id)
    rejected: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []

    for attempt in range(1, max(1, max_attempts) + 1):
        required_focus_domain = (
            None if attempt == 1 else next_focus_domain(evidence, rejected)
        )
        prompt = _planner_payload(
            evidence,
            history,
            rejected,
            required_focus_domain=required_focus_domain,
        )
        prompt_bytes = len(prompt.encode("utf-8"))
        if prompt_bytes > 50000:
            raise ValueError(
                f"planner request exceeded 50KB contract: {prompt_bytes}"
            )
        response = _invoke(function_name, prompt)
        summary: dict[str, Any] = {
            "attempt": attempt,
            "promptBytes": prompt_bytes,
            "requiredFocusDomain": required_focus_domain,
            "ok": response.get("ok"),
            "mode": response.get("mode"),
            "routeId": response.get("routeId"),
            "region": response.get("region"),
            "modelId": response.get("modelId"),
            "endpointFamily": response.get("endpointFamily"),
        }
        if response.get("ok") is not True or response.get("mode") != "engineering_plan":
            reason = "planner Lambda failed: " + json.dumps(
                response, default=str
            )
            summary["rejected"] = reason
            attempts.append(summary)
            rejected.append(
                {
                    "title": "<lambda-failure>",
                    "domain": "other",
                    "reason": reason[:1000],
                }
            )
            continue

        text = str(response.get("text") or "").strip()
        if not text:
            reason = "planner Lambda returned empty task text"
            summary["rejected"] = reason
            attempts.append(summary)
            rejected.append(
                {"title": "<empty>", "domain": "other", "reason": reason}
            )
            continue

        proposed_title = "<unparsed>"
        proposal_domain = "other"
        try:
            parsed = parse_json(text)
            proposed_title = str(parsed.get("title") or "<untitled>")
            proposal_domain = classify_task_domain(parsed)
            _reject_cycle_repeat(proposed_title, rejected)
            _validate_focus_domain(parsed, required_focus_domain)
            validate_against_history(parsed, history)
            task = validate(parsed, decision_history=history)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            summary.update(
                {
                    "title": proposed_title,
                    "domain": proposal_domain,
                    "rejected": reason,
                }
            )
            attempts.append(summary)
            rejected.append(
                {
                    "title": proposed_title,
                    "domain": proposal_domain,
                    "reason": reason[:1000],
                }
            )
            continue

        task["focusDomain"] = _validate_focus_domain(
            task, required_focus_domain
        )
        task["plannerRoute"] = {
            key: response.get(key)
            for key in ("routeId", "region", "modelId", "endpointFamily")
        }
        task["runtimeDecisionAuthority"] = response.get("decisionAuthority")
        task["bedrockAvailable"] = response.get("bedrockAvailable")
        task["plannerAttempt"] = attempt
        task["rejectedProposalsThisCycle"] = rejected
        output_path.write_text(
            json.dumps(task, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        response_path.write_text(
            json.dumps(response, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary.update(
            {
                "title": task["title"],
                "domain": task["focusDomain"],
                "accepted": True,
            }
        )
        attempts.append(summary)
        attempts_path.write_text(
            json.dumps(attempts, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return task

    attempts_path.write_text(
        json.dumps(attempts, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    raise RuntimeError(
        f"no acceptable planner task after {max_attempts} bounded attempts"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--attempts", type=Path, required=True)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--logical-function", required=True)
    parser.add_argument("--max-attempts", type=int, default=5)
    args = parser.parse_args()
    task = run(
        evidence_path=args.evidence,
        output_path=args.output,
        response_path=args.response,
        attempts_path=args.attempts,
        stack_name=args.stack,
        logical_id=args.logical_function,
        max_attempts=args.max_attempts,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "title": task["title"],
                "attempt": task["plannerAttempt"],
                "focusDomain": task["focusDomain"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
