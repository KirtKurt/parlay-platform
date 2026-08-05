#!/usr/bin/env python3
"""Build the source-honest hourly MLB V8 lifecycle report."""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

VERSION = "MLB-V8-HOURLY-STATUS-v1-source-honest"
MARKER = "MLB_V8_HOURLY_STATE"
MARKER_RE = re.compile(r"<!--\s*MLB_V8_HOURLY_STATE:(\{.*?\})\s*-->", re.S)
STALE_SECONDS = 7200


def load_json(path: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.exists():
        return {}, {"label": label, "status": "UNAVAILABLE", "path": str(path)}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, {"label": label, "status": "INVALID", "path": str(path), "error": f"{type(exc).__name__}:{exc}"}
    if not isinstance(value, dict):
        return {}, {"label": label, "status": "INVALID", "path": str(path), "error": "top_level_not_object"}
    return value, {"label": label, "status": "AVAILABLE", "path": str(path)}


def at(value: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def first(value: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        item = at(value, path)
        if item is not None:
            return item
    return None


def number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if not isinstance(value, float) or math.isfinite(value) else None
    try:
        text = str(value).strip()
        result = float(text) if any(token in text.lower() for token in (".", "e")) else int(text)
        return result if not isinstance(result, float) or math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def explicit_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def timestamp(value: Any) -> str | None:
    stamp = parse_time(value)
    return stamp.isoformat() if stamp else (str(value) if value not in (None, "") else None)


def freshness(value: Any, now: datetime) -> str:
    stamp = parse_time(value)
    if not stamp:
        return "UNVERIFIED"
    return "STALE" if (now - stamp).total_seconds() > STALE_SECONDS else "CURRENT"


def errors(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                result.append(value.strip())
        elif isinstance(value, Mapping):
            result.extend(errors(value.get("error") or value.get("reason") or value.get("code")))
        elif isinstance(value, Iterable):
            result.extend(errors(*list(value)))
    return sorted(set(result))


def metric(value: Any, previous: Any, *, status: str | None = None, comparable: bool = True, note: str | None = None) -> dict[str, Any]:
    current_number, previous_number = number(value), number(previous)
    delta = current_number - previous_number if comparable and current_number is not None and previous_number is not None else None
    return {
        "value": value,
        "delta": delta,
        "status": status or ("AVAILABLE" if value is not None else "UNAVAILABLE"),
        "comparable": comparable,
        "note": note,
    }


def workflow_rows(workflows: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = workflows.get("workflow_runs") or at(workflows, "runs.workflow_runs") or []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def artifact_rows(workflows: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = workflows.get("artifacts") or []
    rows = rows.get("artifacts") if isinstance(rows, Mapping) else rows
    return [dict(row) for row in rows or [] if isinstance(row, Mapping)]


def latest_run(workflows: Mapping[str, Any], names: Sequence[str], tokens: Sequence[str]) -> dict[str, Any] | None:
    wanted = {name.lower() for name in names}
    candidates = []
    for row in workflow_rows(workflows):
        name, path = str(row.get("name") or "").lower(), str(row.get("path") or "").lower()
        if name in wanted or any(token.lower() in path for token in tokens):
            candidates.append(row)
    candidates.sort(key=lambda row: str(row.get("updated_at") or row.get("run_started_at") or row.get("created_at") or ""), reverse=True)
    return candidates[0] if candidates else None


def run_summary(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"runId": None, "name": None, "status": "UNAVAILABLE", "conclusion": None, "timestamp": None, "headSha": None}
    return {
        "runId": row.get("id"),
        "name": row.get("name"),
        "status": str(row.get("status") or "UNVERIFIED").upper(),
        "conclusion": str(row.get("conclusion")).upper() if row.get("conclusion") else None,
        "timestamp": timestamp(row.get("updated_at") or row.get("run_started_at") or row.get("created_at")),
        "headSha": row.get("head_sha"),
    }


def artifact_summary(workflows: Mapping[str, Any], run_id: Any) -> dict[str, Any]:
    if run_id is None:
        return {"count": None, "bytes": None, "status": "UNAVAILABLE", "names": []}
    rows = [row for row in artifact_rows(workflows) if str(at(row, "workflow_run.id") or "") == str(run_id)]
    return {
        "count": len(rows),
        "bytes": sum(int(number(row.get("size_in_bytes")) or 0) for row in rows),
        "status": "AVAILABLE",
        "names": sorted(str(row.get("name") or "") for row in rows),
    }


def evaluation(value: Mapping[str, Any], *prefixes: str) -> dict[str, Any]:
    source = next((at(value, prefix) for prefix in prefixes if isinstance(at(value, prefix), Mapping)), None)
    if not isinstance(source, Mapping):
        return {"sample": None, "correct": None, "accuracy": None, "calibrationEce": None, "status": "UNAVAILABLE"}
    return {
        "sample": first(source, "gameCount", "sampleCount", "count", "n"),
        "correct": first(source, "correct", "correctCount", "wins"),
        "accuracy": first(source, "sourceReportedAccuracy", "overallAccuracy", "accuracy", "selectedPickAccuracy"),
        "calibrationEce": first(source, "expectedCalibrationError", "calibrationEce", "ece"),
        "status": "SOURCE_REPORTED",
    }


def extract_previous_state(markdown: str) -> dict[str, Any]:
    match = MARKER_RE.search(markdown or "")
    if not match:
        return {}
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def show(value: Any) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.8f}".rstrip("0").rstrip(".")
    return str(value)


def delta(value: Any) -> str:
    value = number(value)
    return "—" if value is None else (f"+{show(value)}" if value > 0 else show(value))


def metric_row(label: str, item: Mapping[str, Any]) -> str:
    value = show(item.get("value"))
    status = str(item.get("status") or "AVAILABLE")
    if status not in {"AVAILABLE", "SOURCE_REPORTED"}:
        value += f" — {status.lower()}"
    if item.get("note"):
        value += f" ({item['note']})"
    return f"| {label} | **{value}** | **{delta(item.get('delta'))}** |"


def build_report(*, historical: Mapping[str, Any], training: Mapping[str, Any], validation: Mapping[str, Any], controller: Mapping[str, Any], prospective: Mapping[str, Any], context: Mapping[str, Any], shadow: Mapping[str, Any], promotion: Mapping[str, Any], workflows: Mapping[str, Any], previous: Mapping[str, Any], source_status: Mapping[str, Any], now: datetime, issue_number: int) -> dict[str, Any]:
    hist = historical.get("state") if isinstance(historical.get("state"), Mapping) else historical
    eligible = first(hist, "eligibleGameCount") or first(training, "historicalState.eligibleGameCount", "recordCountLoaded")
    slates = first(hist, "completeSlateCount") or first(training, "historicalState.completeSlateCount")
    date_reached, cursor = first(hist, "endDate", "plannedThroughDate"), first(hist, "currentDate")
    target = first(hist, "targetSettledGames")
    remaining = first(hist, "gamesUntilNextOptimization", "remainingGameCount", "gamesRemaining")
    remaining_slates = first(hist, "slatesUntilNextOptimization", "remainingSlateCount", "slatesRemaining")
    revision, requests, credits = first(hist, "revision"), first(hist, "networkRequestCount"), first(hist, "creditsConsumed")
    hist_time = first(hist, "stateUpdatedAtUtc", "updatedAtUtc", "checkedAt")
    hist_metrics = {
        "eligibleGames": metric(eligible, previous.get("historicalEligibleGames")),
        "completedSlates": metric(slates, previous.get("completedSlateCount")),
        "dateReached": metric(date_reached, previous.get("historicalDateReached"), comparable=False),
        "cursorDate": metric(cursor, previous.get("historicalCursorDate"), comparable=False),
        "targetGames": metric(target, previous.get("historicalTargetGames")),
        "remainingGames": metric(remaining, previous.get("historicalRemainingGames"), note="source-reported; not recomputed"),
        "remainingSlates": metric(remaining_slates, previous.get("historicalRemainingSlates"), status="AVAILABLE" if remaining_slates is not None else "UNAVAILABLE", note="not calculated when omitted"),
        "revision": metric(revision, previous.get("optimizerRevision")),
        "networkRequests": metric(requests, previous.get("networkRequests")),
        "creditsConsumed": metric(credits, previous.get("creditsConsumed")),
    }

    training_run = run_summary(latest_run(workflows, ("MLB V8 Autonomous Controller", "MLB V8 Supervised Shadow Training"), ("mlb-v8-autonomous-controller", "mlb-supervised-shadow-v2")))
    shadow_run = run_summary(latest_run(workflows, ("MLB V8 Shadow Realtime 72", "MLB V8 Shadow Evaluation"), ("mlb-v8-shadow", "supervised-shadow-v2")))
    deploy_run = run_summary(latest_run(workflows, (), ("deploy.yml", "post-deploy", "mlb-v7-settled-horizon-resume-deploy")))
    learning = training.get("learningExecution") or {}
    training_rows = first(training, "trainingRowCount", "sampleCounts.training", "partitions.train.gameCount", "partitions.train.sampleCount", "partitions.train.count", "trainingMetrics.gameCount")
    training_rows = training_rows if training_rows is not None else first(validation, "trainingRowCount", "partitions.train.gameCount", "trainingMetrics.gameCount")
    validation_samples = first(training, "validationSampleCount", "sampleCounts.validation", "partitions.untouchedAudit.gameCount", "partitions.validation.gameCount", "untouchedAuditMetrics.gameCount")
    validation_samples = validation_samples if validation_samples is not None else first(validation, "validationSampleCount", "partitions.untouchedAudit.gameCount", "untouchedAuditMetrics.gameCount")
    walk_samples = first(training, "walkForwardSampleCount", "sampleCounts.walkForward", "partitions.walkForward.gameCount", "walkForwardMetrics.gameCount")
    walk_samples = walk_samples if walk_samples is not None else first(validation, "walkForwardSampleCount", "partitions.walkForward.gameCount", "walkForwardMetrics.gameCount")
    settled = first(hist, "latestAccuracy.settledGameCount", "settledGameCount")
    settled = settled if settled is not None else first(training, "settledGameCount", "recordCountSettled")
    graded = first(prospective, "modelMetrics.gameCount", "prospectiveGradedPredictionCount", "gradedPredictionCount")
    training_metrics = {
        "trainingRows": metric(training_rows, previous.get("trainingRows")),
        "validationSamples": metric(validation_samples, previous.get("validationSamples")),
        "walkForwardSamples": metric(walk_samples, previous.get("walkForwardSamples")),
        "settledGames": metric(settled, previous.get("settledGames")),
        "gradedPredictions": metric(graded, previous.get("gradedPredictions"), status="AVAILABLE" if graded is not None else "UNAVAILABLE_UNVERIFIED", note="prospective V8 ledger only"),
    }
    retrospective = {
        "untouchedValidation": evaluation(training, "untouchedAuditMetrics", "metrics.untouchedAudit", "evaluation.untouchedAudit"),
        "walkForward": evaluation(training, "walkForwardMetrics", "metrics.walkForward", "evaluation.walkForward"),
        "selectedOutOfFold": evaluation(training, "selectedOutOfFoldMetrics", "selection.selectedOutOfFoldMetrics", "metrics.selectedOutOfFold"),
    }
    fallbacks = {
        "untouchedValidation": ("untouchedAuditMetrics", "metrics.untouchedAudit", "evaluation.untouchedAudit"),
        "walkForward": ("walkForwardMetrics", "metrics.walkForward", "evaluation.walkForward"),
        "selectedOutOfFold": ("selectedOutOfFoldMetrics", "selection.selectedOutOfFoldMetrics", "metrics.selectedOutOfFold"),
    }
    for key, prefixes in fallbacks.items():
        if retrospective[key]["status"] == "UNAVAILABLE":
            retrospective[key] = evaluation(validation, *prefixes)

    source_times = {
        "historical": timestamp(hist_time),
        "training": timestamp(first(training, "createdAtUtc", "evaluatedAtUtc", "timestamp")),
        "validation": timestamp(first(validation, "createdAtUtc", "evaluatedAtUtc", "timestamp")),
        "prospective": timestamp(first(prospective, "createdAtUtc", "evaluatedAtUtc", "timestamp")),
        "context": timestamp(first(context, "createdAtUtc", "updatedAtUtc", "timestamp")),
        "shadow": timestamp(first(shadow, "createdAtUtc", "evaluatedAtUtc", "timestamp")),
        "promotion": timestamp(first(promotion, "createdAtUtc", "updatedAtUtc", "timestamp")),
    }
    fresh = {key: freshness(value, now) for key, value in source_times.items()}
    runtime_blockers = errors(first(hist, "lastError"))
    quality_blockers = errors(first(controller, "blockers"), first(prospective, "blockers", "errors"), first(training, "promotionGate.errors"), first(hist, "championValidation.errors"), first(hist, "cutoverValidation.errors"))
    context_blockers, shadow_blockers, promotion_blockers = errors(first(context, "blockers", "errors")), errors(first(shadow, "blockers", "errors")), errors(first(promotion, "blockers", "errors"))
    active_blockers = sorted(set(runtime_blockers + quality_blockers + context_blockers + shadow_blockers + promotion_blockers))

    state = {
        "historicalEligibleGames": eligible, "completedSlateCount": slates, "historicalDateReached": date_reached,
        "historicalCursorDate": cursor, "historicalTargetGames": target, "historicalRemainingGames": remaining,
        "historicalRemainingSlates": remaining_slates, "optimizerRevision": revision, "networkRequests": requests,
        "creditsConsumed": credits, "trainingRows": training_rows, "validationSamples": validation_samples,
        "walkForwardSamples": walk_samples, "settledGames": settled, "gradedPredictions": graded,
        "learningSteps": first(learning, "totalOptimizationSteps"), "learnedCandidateCount": first(learning, "learnedCandidateCount"),
        "learnedEligibleCandidateCount": first(learning, "learnedEligibleCandidateCount"),
        "contextProcessedGames": first(context, "processedGameCount", "recordCount"), "contextEligibleGames": first(context, "eligibleGameCount"),
        "contextNewEligibleGames": first(context, "newEligibleGameCount"), "contextRemainingGames": first(context, "remainingGameCount"),
        "contextProviderCalls": first(context, "providerCallsMade"), "contextPointerRevision": first(context, "activePointerRevision", "pointerRevision"),
        "shadowSample": first(shadow, "gradedPredictionCount", "sampleSize", "modelMetrics.gameCount", "historicalEvaluationSample"),
        "prospectiveSample": graded,
    }
    training_artifacts, shadow_artifacts, deploy_artifacts = artifact_summary(workflows, training_run["runId"]), artifact_summary(workflows, shadow_run["runId"]), artifact_summary(workflows, deploy_run["runId"])
    state.update({"trainingArtifactCount": training_artifacts["count"], "shadowArtifactCount": shadow_artifacts["count"], "deploymentArtifactCount": deploy_artifacts["count"]})

    report = {
        "proofType": "MLB_V8_HOURLY_NUMERICAL_STATUS", "version": VERSION, "createdAtUtc": now.isoformat(), "ok": True, "issueNumber": issue_number,
        "sourcePolicy": {"actualRetrievedValuesOnly": True, "accuracyDerivedByReporter": False, "pushesAndVoidsExcludedFromDerivedAccuracy": True, "lifecyclePopulationsKeptSeparate": True},
        "sources": dict(source_status), "sourceTimestamps": source_times, "freshness": fresh,
        "historicalBackfill": {"metrics": hist_metrics, "phase": first(hist, "phase"), "stateTimestamp": timestamp(hist_time), "runtimeBlockers": runtime_blockers, "eligibleAndSettledPopulationsComparable": False},
        "trainer": {
            "workflow": training_run, "reportStatus": "SUCCESS" if training.get("ok") is True else ("FAILURE" if training else "UNAVAILABLE"),
            "reportTimestamp": source_times["training"], "metrics": training_metrics, "recordCountLoaded": first(training, "recordCountLoaded"),
            "learningStatus": first(training, "learningStatus"), "selectedFeatureGroup": first(learning, "selectedFeatureGroup") or first(training, "selection.selectedFeatureGroup"),
            "totalOptimizationSteps": first(learning, "totalOptimizationSteps"), "learnedCandidateCount": first(learning, "learnedCandidateCount"),
            "learnedEligibleCandidateCount": first(learning, "learnedEligibleCandidateCount"), "learnedCandidateSelected": explicit_bool(first(learning, "learnedCandidateSelected")),
            "marketBaselineRetainedByGuard": explicit_bool(first(learning, "marketBaselineRetainedByGuard")), "promotionGatePassed": explicit_bool(first(training, "promotionGate.passed")),
            "retrospectiveValidation": retrospective,
        },
        "prospectiveAudit": {
            "status": first(prospective, "status", "prospectiveStatus", "proofType") or "UNAVAILABLE", "timestamp": source_times["prospective"], "sampleSize": graded,
            "wins": first(prospective, "wins", "modelMetrics.wins", "correct"), "losses": first(prospective, "losses", "modelMetrics.losses", "wrong"),
            "pushes": first(prospective, "pushes", "modelMetrics.pushes"), "voids": first(prospective, "voids", "modelMetrics.voids"),
            "sourceReportedOverallAccuracy": first(prospective, "sourceReportedAccuracy", "overallAccuracy", "modelMetrics.overallAccuracy"),
            "sourceReportedSelectedPickAccuracy": first(prospective, "selectedPickAccuracy", "modelMetrics.selectedPickAccuracy"),
            "sourceReportedConfidenceBandAccuracy": first(prospective, "confidenceBandAccuracy", "modelMetrics.confidenceBandAccuracy"),
            "sourceReportedCalibrationEce": first(prospective, "expectedCalibrationError", "modelMetrics.expectedCalibrationError", "calibrationEce"),
            "blockers": errors(first(prospective, "blockers", "errors")),
        },
        "shadowEvaluation": {
            "workflow": shadow_run, "status": first(shadow, "status", "authority", "proofType") or "UNAVAILABLE", "timestamp": source_times["shadow"],
            "sampleSize": state["shadowSample"], "wins": first(shadow, "wins", "modelMetrics.wins", "correct"), "losses": first(shadow, "losses", "modelMetrics.losses", "wrong"),
            "pushes": first(shadow, "pushes", "modelMetrics.pushes"), "voids": first(shadow, "voids", "modelMetrics.voids"),
            "sourceReportedOverallAccuracy": first(shadow, "sourceReportedAccuracy", "overallAccuracy", "modelMetrics.overallAccuracy"),
            "sourceReportedSelectedPickAccuracy": first(shadow, "selectedPickAccuracy", "modelMetrics.selectedPickAccuracy"),
            "sourceReportedConfidenceBandAccuracy": first(shadow, "confidenceBandAccuracy", "modelMetrics.confidenceBandAccuracy"),
            "sourceReportedCalibrationEce": first(shadow, "expectedCalibrationError", "modelMetrics.expectedCalibrationError", "calibrationEce"), "blockers": shadow_blockers,
        },
        "historicalContext": {
            "status": first(context, "status", "authority", "proofType") or "UNAVAILABLE", "timestamp": source_times["context"],
            "processedGameCount": state["contextProcessedGames"], "eligibleGameCount": state["contextEligibleGames"], "newEligibleGameCount": state["contextNewEligibleGames"],
            "ineligibleGameCount": first(context, "ineligibleGameCount"), "remainingGameCount": state["contextRemainingGames"], "providerCallsMade": state["contextProviderCalls"],
            "pointerRevision": state["contextPointerRevision"], "progressMade": explicit_bool(first(context, "progressMade")), "blockers": context_blockers,
        },
        "artifactsAndPromotion": {
            "trainingArtifacts": training_artifacts, "shadowArtifacts": shadow_artifacts, "deploymentArtifacts": deploy_artifacts, "deploymentWorkflow": deploy_run,
            "newLearnedModelArtifactCreated": first(promotion, "newModelArtifactCreated", "modelArtifactCreated", "learnedModelArtifactCreated"),
            "newV8ModelPromoted": first(promotion, "promoted", "modelPromoted", "newModelPromoted"),
            "productionAuthorityChanged": first(promotion, "productionAuthorityChanged") if first(promotion, "productionAuthorityChanged") is not None else first(controller, "productionAuthorityChanged"),
            "promotionBlockers": promotion_blockers,
        },
        "controller": {
            "fullyAutonomous": explicit_bool(first(controller, "fullyAutonomous")), "normalOperationManualInterventionRequired": explicit_bool(first(controller, "normalOperationManualInterventionRequired")),
            "nextAction": first(controller, "nextAction", "autonomyDecision"), "promotionRequested": explicit_bool(first(controller, "promotionRequested")), "blockers": errors(first(controller, "blockers")),
        },
        "activeBlockers": active_blockers, "state": state,
    }
    report["markdown"] = render_markdown(report)
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    h, t, p, s, c, a, ctl, fresh = report["historicalBackfill"], report["trainer"], report["prospectiveAudit"], report["shadowEvaluation"], report["historicalContext"], report["artifactsAndPromotion"], report["controller"], report["freshness"]
    lines = [
        "# MLB V8 Hourly Numerical Status", "", f"**Updated:** {report['createdAtUtc']}", "",
        "Accuracy is shown only when a source explicitly publishes it. This reporter never derives accuracy from wins and losses. Backfill, training, retrospective validation, prospective audit, shadow evaluation, and production promotion remain separate.", "",
        "## Historical backfill / optimizer", "", "| Metric | Current | Change |", "|---|---:|---:|",
    ]
    for label, key in (("Historical eligible games", "eligibleGames"), ("Completed slates", "completedSlates"), ("Historical date reached", "dateReached"), ("Current historical cursor", "cursorDate"), ("Configured recovery target", "targetGames"), ("Remaining games", "remainingGames"), ("Remaining slates", "remainingSlates"), ("Optimizer revision", "revision"), ("Network requests", "networkRequests"), ("Credits consumed", "creditsConsumed")):
        lines.append(metric_row(label, h["metrics"][key]))
    lines += [f"| Optimizer phase | **{show(h.get('phase'))}** | — |", f"| Latest state timestamp | **{show(h.get('stateTimestamp'))}** ({fresh.get('historical', 'UNVERIFIED').lower()}) | — |", "", "Historical eligible games and settled trainer games are separate, **incomparable** populations unless a source explicitly defines otherwise.", "", "## V8 trainer / retrospective validation", "", "| Metric | Current | Change |", "|---|---:|---:|", f"| Latest trainer workflow | **{show(t['workflow'].get('runId'))} / {show(t['workflow'].get('conclusion') or t['workflow'].get('status'))}** | — |", f"| Trainer report status | **{show(t.get('reportStatus'))}** | — |", f"| Trainer timestamp | **{show(t.get('reportTimestamp'))}** ({fresh.get('training', 'UNVERIFIED').lower()}) | — |"]
    for label, key in (("Training rows", "trainingRows"), ("Validation samples", "validationSamples"), ("Walk-forward samples", "walkForwardSamples"), ("Settled games", "settledGames"), ("Prospective graded predictions", "gradedPredictions")):
        lines.append(metric_row(label, t["metrics"][key]))
    for label, key in (("Records loaded", "recordCountLoaded"), ("Learning status", "learningStatus"), ("Selected feature group", "selectedFeatureGroup"), ("Optimization steps", "totalOptimizationSteps"), ("Learned candidates", "learnedCandidateCount"), ("Gate-eligible learned candidates", "learnedEligibleCandidateCount"), ("Learned candidate selected", "learnedCandidateSelected"), ("Promotion gate passed", "promotionGatePassed")):
        lines.append(f"| {label} | **{show(t.get(key))}** | — |")
    lines += ["", "### Retrospective validation only", "", "| Evaluation | Sample | Correct | Source-reported accuracy | Calibration ECE |", "|---|---:|---:|---:|---:|"]
    for label, key in (("Untouched validation", "untouchedValidation"), ("Walk-forward", "walkForward"), ("Selected out-of-fold", "selectedOutOfFold")):
        row = t["retrospectiveValidation"][key]
        lines.append(f"| {label} | {show(row.get('sample'))} | {show(row.get('correct'))} | {show(row.get('accuracy'))} | {show(row.get('calibrationEce'))} |")
    lines += ["", "These are retrospective historical measurements, not prospective shadow-pick wins and losses."]

    def simple_section(title: str, rows: Sequence[tuple[str, Any]]) -> None:
        lines.extend(["", f"## {title}", "", "| Metric | Value |", "|---|---:|"])
        lines.extend(f"| {label} | **{show(value)}** |" for label, value in rows)

    simple_section("Frozen prospective audit", (("Status", p.get("status")), ("Timestamp", f"{show(p.get('timestamp'))} ({fresh.get('prospective', 'UNVERIFIED').lower()})"), ("Sample size", p.get("sampleSize")), ("Wins", p.get("wins")), ("Losses", p.get("losses")), ("Pushes", p.get("pushes")), ("Voids", p.get("voids")), ("Source-reported overall accuracy", p.get("sourceReportedOverallAccuracy")), ("Source-reported selected-pick accuracy", p.get("sourceReportedSelectedPickAccuracy")), ("Source-reported confidence-band accuracy", p.get("sourceReportedConfidenceBandAccuracy")), ("Source-reported calibration ECE", p.get("sourceReportedCalibrationEce"))))
    simple_section("V8 shadow simulation / evaluation", (("Workflow run", f"{show(s['workflow'].get('runId'))} / {show(s['workflow'].get('conclusion') or s['workflow'].get('status'))}"), ("Status", s.get("status")), ("Timestamp", f"{show(s.get('timestamp'))} ({fresh.get('shadow', 'UNVERIFIED').lower()})"), ("Sample size", s.get("sampleSize")), ("Wins", s.get("wins")), ("Losses", s.get("losses")), ("Pushes", s.get("pushes")), ("Voids", s.get("voids")), ("Source-reported overall accuracy", s.get("sourceReportedOverallAccuracy")), ("Source-reported selected-pick accuracy", s.get("sourceReportedSelectedPickAccuracy")), ("Source-reported confidence-band accuracy", s.get("sourceReportedConfidenceBandAccuracy")), ("Source-reported calibration ECE", s.get("sourceReportedCalibrationEce"))))
    simple_section("Official historical context backfill", (("Status / authority", c.get("status")), ("Timestamp", f"{show(c.get('timestamp'))} ({fresh.get('context', 'UNVERIFIED').lower()})"), ("Processed games", c.get("processedGameCount")), ("Eligible games", c.get("eligibleGameCount")), ("New eligible games", c.get("newEligibleGameCount")), ("Ineligible games", c.get("ineligibleGameCount")), ("Remaining games", c.get("remainingGameCount")), ("Provider calls", c.get("providerCallsMade")), ("Pointer revision", c.get("pointerRevision")), ("Progress made", c.get("progressMade"))))
    simple_section("Artifacts and production promotion", (("Trainer artifacts", f"{show(a['trainingArtifacts'].get('count'))} / {show(a['trainingArtifacts'].get('bytes'))} bytes"), ("Shadow artifacts", f"{show(a['shadowArtifacts'].get('count'))} / {show(a['shadowArtifacts'].get('bytes'))} bytes"), ("Deployment artifacts", f"{show(a['deploymentArtifacts'].get('count'))} / {show(a['deploymentArtifacts'].get('bytes'))} bytes"), ("Latest deployment workflow", f"{show(a['deploymentWorkflow'].get('runId'))} / {show(a['deploymentWorkflow'].get('conclusion') or a['deploymentWorkflow'].get('status'))}"), ("New learned V8 model artifact created", a.get("newLearnedModelArtifactCreated")), ("New V8 model promoted", a.get("newV8ModelPromoted")), ("Production authority changed", a.get("productionAuthorityChanged"))))
    lines += ["", "## Autonomous controller and active blockers", "", f"- Fully autonomous: **{show(ctl.get('fullyAutonomous'))}**", f"- Normal-operation manual intervention required: **{show(ctl.get('normalOperationManualInterventionRequired'))}**", f"- Next action: **{show(ctl.get('nextAction'))}**", f"- Promotion requested: **{show(ctl.get('promotionRequested'))}**", ""]
    blockers = report.get("activeBlockers") or []
    lines.append("**Active blockers/gates:** " + (", ".join(f"`{item}`" for item in blockers) if blockers else "none reported."))
    lines += ["", "A quality gate is not a runtime failure. Promotion remains separate from collection, backfill, training, validation, prospective auditing, and shadow evaluation.", ""]
    marker = json.dumps(report.get("state") or {}, sort_keys=True, separators=(",", ":"))
    lines.append(f"<!-- {MARKER}:{marker} -->")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("historical-status", "training-report", "validation-report", "controller-report", "prospective-audit", "context-report", "shadow-report", "promotion-report", "workflows", "previous-markdown"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--source-status", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--issue-number", type=int, required=True)
    args = parser.parse_args()
    key_paths = {
        "historical": args.historical_status, "training": args.training_report, "validation": args.validation_report,
        "controller": args.controller_report, "prospective": args.prospective_audit, "context": args.context_report,
        "shadow": args.shadow_report, "promotion": args.promotion_report, "workflows": args.workflows,
    }
    documents, statuses = {}, {}
    for key, path in key_paths.items():
        documents[key], statuses[key] = load_json(path, key)
    if args.source_status:
        source, meta = load_json(args.source_status, "live_source")
        statuses["liveSource"] = source or meta
    previous = extract_previous_state(args.previous_markdown.read_text(encoding="utf-8") if args.previous_markdown.exists() else "")
    report = build_report(**documents, previous=previous, source_status=statuses, now=datetime.now(timezone.utc), issue_number=args.issue_number)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps({k: v for k, v in report.items() if k != "markdown"}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output_markdown.write_text(report["markdown"], encoding="utf-8")
    print(json.dumps({"ok": True, "createdAtUtc": report["createdAtUtc"], "historicalEligibleGames": report["state"].get("historicalEligibleGames"), "completedSlateCount": report["state"].get("completedSlateCount"), "trainingRows": report["state"].get("trainingRows"), "gradedPredictions": report["state"].get("gradedPredictions"), "activeBlockerCount": len(report["activeBlockers"])}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
