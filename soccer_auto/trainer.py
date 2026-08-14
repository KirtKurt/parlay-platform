"""Chronological challenger training, prospective audit, and atomic promotion."""
from __future__ import annotations

import os
from typing import Any, Iterable, Mapping, Sequence

from boto3.dynamodb.conditions import Key

from .canonical import digest, iso_utc, schedule_identity
from .config import PUBLIC_DECISION_HORIZON
from .market_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from .llm_analyst import BASELINE_TRIALS, latest_llm_trials
from .historical_materializer import (
    historical_training_manifest,
    training_candidate,
)
from .model import (
    CLASSES,
    TrainingRow,
    chronological_split,
    multiclass_metrics,
    paired_skill_lower_bound_from_probabilities,
    select_candidate,
)
from .settlement import (
    settlement_conflict_blocks_training,
    settlement_training_admissible,
    settlement_training_views,
)
from .storage import SoccerStore, ddb_safe, now_utc


MIN_TRAINING_ROWS = int(os.getenv("SOCCER_AUTO_MIN_TRAINING_ROWS", "500"))
MIN_AUDIT_ROWS = int(os.getenv("SOCCER_AUTO_MIN_AUDIT_ROWS", "100"))
MIN_PROSPECTIVE_ROWS = int(os.getenv("SOCCER_AUTO_MIN_PROSPECTIVE_ROWS", "200"))
MAX_TRAINING_ROWS = int(os.getenv("SOCCER_AUTO_MAX_TRAINING_ROWS", "5000"))
MAX_AUDIT_ECE = float(os.getenv("SOCCER_AUTO_MAX_AUDIT_ECE", "0.08"))
MAX_PROSPECTIVE_ECE = float(os.getenv("SOCCER_AUTO_MAX_PROSPECTIVE_ECE", "0.08"))
TARGET = "result_1x2"
SCOPE = "global"
MODEL_PK = f"MODEL#{TARGET}#{SCOPE}"


def _schedule_identity(row: Mapping[str, Any]) -> tuple[str, int, str, str] | None:
    try:
        revision = int(row.get("schedule_revision") or 0)
        if revision <= 0:
            return None
        identity = str(row.get("schedule_identity") or "")
        if not identity:
            try:
                identity = schedule_identity(row)
            except (KeyError, TypeError, ValueError):
                identity = "LEGACY_IDENTITY_UNAVAILABLE"
        return (
            str(row["event_key"]),
            revision,
            iso_utc(str(row["commence_time"])),
            identity,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _settlements(store: SoccerStore) -> dict[str, dict[str, Any]]:
    rows = list(store.scan_all(store.settlements, ConsistentRead=True))
    return {
        row["event_key"]: row
        for row in settlement_training_views(rows)
    }


def _settlement_conflict_events(store: SoccerStore) -> set[str]:
    if not hasattr(store.ops, "query"):
        return {
            str(row.get("event_key") or "")
            for row in store.scan_all(store.ops)
            if settlement_conflict_blocks_training(row) and row.get("event_key")
        }
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq("SETTLEMENT_CONFLICT"),
        "ConsistentRead": True,
    }
    rows: list[Mapping[str, Any]] = []
    while True:
        response = store.ops.query(**kwargs)
        rows.extend(response.get("Items") or [])
        cursor = response.get("LastEvaluatedKey")
        if not cursor:
            break
        kwargs["ExclusiveStartKey"] = cursor
    return {
        str(row.get("event_key") or "")
        for row in rows
        if settlement_conflict_blocks_training(row) and row.get("event_key")
    }


def _training_rows_with_proof(
    store: SoccerStore,
) -> tuple[list[TrainingRow], dict[str, int], dict[str, Any]]:
    settlements = _settlements(store)
    conflicted_events = _settlement_conflict_events(store) if hasattr(store, "ops") else set()
    rows: list[TrainingRow] = []
    historical_entries: list[Mapping[str, Any]] = []
    excluded = {
        "no_settlement": 0,
        "settlement_ineligible": 0,
        "lock_ineligible": 0,
        "schedule_mismatch": 0,
        "schema_mismatch": 0,
        "historical_provenance": 0,
        "live_provenance": 0,
        "invalid": 0,
        "settlement_conflict": 0,
    }
    for lock in store.scan_all(store.locks, ConsistentRead=True):
        # The T10 lock is a final-decision artifact, not a second training row.
        # Retaining a single declared T45 training horizon prevents duplicate
        # labels and keeps retrospective/prospective evaluation comparable.
        if not str(lock.get("SK") or "").startswith("LOCK#T45#"):
            continue
        settlement = settlements.get(lock.get("event_key"))
        if not settlement:
            excluded["no_settlement"] += 1
            continue
        candidate, reason = training_candidate(
            lock,
            settlement,
            conflicted=str(lock.get("event_key") or "") in conflicted_events,
        )
        if candidate is None:
            excluded[str(reason or "invalid")] += 1
            continue
        rows.append(candidate.row)
        if candidate.historical_manifest_entry is not None:
            historical_entries.append(candidate.historical_manifest_entry)
    return (
        sorted(rows, key=lambda row: (row.timestamp, row.event_key)),
        excluded,
        historical_training_manifest(historical_entries),
    )


def training_rows(store: SoccerStore) -> tuple[list[TrainingRow], dict[str, int]]:
    rows, excluded, _historical_manifest = _training_rows_with_proof(store)
    return rows, excluded


def _model_versions(store: SoccerStore) -> list[dict[str, Any]]:
    return [row for row in store.model_items(TARGET, SCOPE) if str(row.get("SK", "")).startswith("VERSION#")]


def _active_candidate(store: SoccerStore) -> dict[str, Any] | None:
    rows = [row for row in _model_versions(store) if row.get("authority_state") == "PROSPECTIVE_SHADOW"]
    return max(rows, key=lambda row: row.get("created_at") or "") if rows else None


def _champion(store: SoccerStore) -> dict[str, Any] | None:
    return next((row for row in store.model_items(TARGET, SCOPE) if row.get("SK") == "CHAMPION"), None)


def _prediction_rows(
    store: SoccerStore, model_digest: str
) -> dict[tuple[str, int, str], dict[str, Any]]:
    response = store.predictions.query(
        IndexName="ByModel",
        KeyConditionExpression=Key("GSI2PK").eq(f"MODEL#{model_digest}"),
    )
    result = {}
    for row in response.get("Items") or []:
        schedule = _schedule_identity(row)
        if (
            row.get("target") == TARGET
            and row.get("horizon") == PUBLIC_DECISION_HORIZON
            and schedule is not None
        ):
            result[schedule] = row
    return result


def _probability_vector(row: Mapping[str, Any]) -> list[float]:
    return [float((row.get("probabilities") or {})[name]) for name in CLASSES]


def _prior_vector(row: Mapping[str, Any]) -> list[float]:
    return [float((row.get("market_prior") or {})[name]) for name in CLASSES]


def evaluate_prospective_candidate(store: SoccerStore, candidate: Mapping[str, Any]) -> dict[str, Any]:
    conflicted_events = _settlement_conflict_events(store)
    settlements = {
        schedule: row
        for row in _settlements(store).values()
        if (schedule := _schedule_identity(row)) is not None
        and str(row.get("event_key") or "") not in conflicted_events
    }
    candidate_predictions = _prediction_rows(store, candidate["model_digest"])
    champion = _champion(store)
    champion_predictions = _prediction_rows(store, champion["model_digest"]) if champion else {}
    event_keys = sorted(
        key
        for key in candidate_predictions
        if key in settlements and settlement_training_admissible(settlements[key])
    )
    labels = [CLASSES.index(settlements[key]["result_1x2"]) for key in event_keys]
    candidate_probs = [_probability_vector(candidate_predictions[key]) for key in event_keys]
    market_probs = [_prior_vector(candidate_predictions[key]) for key in event_keys]
    metrics = {
        "count": len(event_keys),
        "candidate": multiclass_metrics(candidate_probs, labels),
        "market_baseline": multiclass_metrics(market_probs, labels),
        "market_skill_lower_bound_95": paired_skill_lower_bound_from_probabilities(
            candidate_probs, market_probs, labels
        ),
        "event_manifest_digest": digest(event_keys),
    }
    overlapping = [key for key in event_keys if key in champion_predictions]
    if champion and overlapping:
        overlap_labels = [CLASSES.index(settlements[key]["result_1x2"]) for key in overlapping]
        candidate_overlap = [_probability_vector(candidate_predictions[key]) for key in overlapping]
        champion_overlap = [_probability_vector(champion_predictions[key]) for key in overlapping]
        metrics["champion_comparison"] = {
            "champion_digest": champion["model_digest"],
            "count": len(overlapping),
            "candidate": multiclass_metrics(candidate_overlap, overlap_labels),
            "champion": multiclass_metrics(champion_overlap, overlap_labels),
            "skill_lower_bound_95": paired_skill_lower_bound_from_probabilities(
                candidate_overlap, champion_overlap, overlap_labels, seed=9187
            ),
        }
    return metrics


def _prospective_gate(metrics: Mapping[str, Any], champion_exists: bool) -> tuple[bool, list[str]]:
    failures = []
    if int(metrics.get("count") or 0) < MIN_PROSPECTIVE_ROWS:
        failures.append("INSUFFICIENT_PROSPECTIVE_ROWS")
    market_skill = metrics.get("market_skill_lower_bound_95")
    if market_skill is None or float(market_skill) <= 0:
        failures.append("MARKET_SKILL_LOWER_BOUND_NOT_POSITIVE")
    ece = (metrics.get("candidate") or {}).get("ece")
    if ece is None or float(ece) > MAX_PROSPECTIVE_ECE:
        failures.append("PROSPECTIVE_CALIBRATION_FAILED")
    comparison = metrics.get("champion_comparison")
    if champion_exists:
        if not comparison or int(comparison.get("count") or 0) < MIN_PROSPECTIVE_ROWS:
            failures.append("INSUFFICIENT_SAME_EVENT_CHAMPION_COMPARISON")
        elif comparison.get("skill_lower_bound_95") is None or float(comparison["skill_lower_bound_95"]) <= 0:
            failures.append("CHAMPION_SKILL_LOWER_BOUND_NOT_POSITIVE")
    return not failures, failures


def maybe_promote(store: SoccerStore, candidate: Mapping[str, Any]) -> dict[str, Any]:
    metrics = evaluate_prospective_candidate(store, candidate)
    champion = _champion(store)
    eligible, performance_failures = _prospective_gate(metrics, champion is not None)
    failures = list(performance_failures)
    autonomy = store.ops.get_item(
        Key={"PK": "AUTONOMY", "SK": "STATE"}, ConsistentRead=True
    ).get("Item")
    if not autonomy:
        failures.append("AUTONOMY_HEALTH_UNVERIFIED")
        eligible = False
    elif autonomy.get("promotion_blocked"):
        failures.append("AUTONOMY_HEALTH_BLOCKED_PROMOTION")
        eligible = False
    observed_at = iso_utc(now_utc())
    store.models.update_item(
        Key={"PK": candidate["PK"], "SK": candidate["SK"]},
        UpdateExpression="SET prospective_metrics=:metrics, prospective_evaluated_at=:at, prospective_gate_failures=:failures",
        ExpressionAttributeValues={
            ":metrics": ddb_safe(metrics),
            ":at": observed_at,
            ":failures": failures,
        },
    )
    if eligible:
        store.promote_candidate(
            candidate=candidate,
            expected_champion_digest=champion.get("model_digest") if champion else None,
            promoted_at=observed_at,
        )
    terminal_rejected = False
    # A transient operational health block may delay promotion but can never
    # permanently reject a statistically eligible candidate.
    terminal_performance_failure = any(
        failure not in {"INSUFFICIENT_PROSPECTIVE_ROWS"}
        for failure in performance_failures
    )
    if (
        not eligible
        and terminal_performance_failure
        and int(metrics.get("count") or 0) >= MIN_PROSPECTIVE_ROWS
    ):
        store.models.update_item(
            Key={"PK": candidate["PK"], "SK": candidate["SK"]},
            UpdateExpression="SET authority_state=:state, rejected_at=:at",
            ConditionExpression="authority_state=:expected",
            ExpressionAttributeValues={
                ":state": "PROSPECTIVE_REJECTED",
                ":at": observed_at,
                ":expected": "PROSPECTIVE_SHADOW",
            },
        )
        terminal_rejected = True
    return {
        "eligible": eligible,
        "promoted": eligible,
        "terminal_rejected": terminal_rejected,
        "metrics": metrics,
        "failures": failures,
    }


def train_challenger(store: SoccerStore, rows: Sequence[TrainingRow]) -> dict[str, Any]:
    if len(rows) < MIN_TRAINING_ROWS:
        return {"trained": False, "reason": "INSUFFICIENT_TRAINING_ROWS", "rows": len(rows)}
    split = chronological_split(rows)
    if len(split.audit) < MIN_AUDIT_ROWS:
        return {
            "trained": False,
            "reason": "INSUFFICIENT_UNTOUCHED_AUDIT_ROWS",
            "split_counts": {"train": len(split.train), "validation": len(split.validation), "audit": len(split.audit)},
        }
    llm_trials, llm_analysis_digest = latest_llm_trials(store)
    search: list[Mapping[str, Any]] = list(BASELINE_TRIALS)
    for trial in llm_trials:
        if trial not in search:
            search.append(trial)
    model, report = select_candidate(split, FEATURE_NAMES, search=search)
    report["search_trial_count"] = len(search)
    report["llm_analysis_digest"] = llm_analysis_digest
    report["llm_validated_trial_count"] = len(llm_trials)
    versions = _model_versions(store)
    if any(row.get("data_manifest_digest") == report["data_manifest_digest"] for row in versions):
        return {"trained": False, "reason": "DATA_MANIFEST_ALREADY_EVALUATED", "data_manifest_digest": report["data_manifest_digest"]}
    gate_failures = []
    if float(report["audit_log_loss_skill_lower_bound_95"]) <= 0:
        gate_failures.append("AUDIT_MARKET_SKILL_LOWER_BOUND_NOT_POSITIVE")
    if float(report["audit"]["candidate"]["ece"]) > MAX_AUDIT_ECE:
        gate_failures.append("AUDIT_CALIBRATION_FAILED")
    authority_state = "PROSPECTIVE_SHADOW" if not gate_failures else "RETROSPECTIVE_REJECTED"
    created_at = iso_utc(now_utc())
    model_payload = model.to_dict()
    artifact = {
        "artifact_version": "soccer-auto-model-artifact-v1",
        "created_at": created_at,
        "target": TARGET,
        "scope": SCOPE,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model": model_payload,
        "retrospective_report": report,
        "llm_analysis_digest": llm_analysis_digest,
        "authority_state": authority_state,
        "gate_failures": gate_failures,
    }
    artifact_digest = digest(artifact)
    artifact_uri = store.write_artifact("models/result_1x2/global", artifact, artifact_digest)
    row = {
        "PK": MODEL_PK,
        "SK": f"VERSION#{created_at}#{model_payload['model_digest']}",
        "entity_type": "SOCCER_MODEL_VERSION",
        "target": TARGET,
        "scope": SCOPE,
        "created_at": created_at,
        "model_digest": model_payload["model_digest"],
        "artifact_digest": artifact_digest,
        "artifact_uri": artifact_uri,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "data_manifest_digest": report["data_manifest_digest"],
        "authority_state": authority_state,
        "automatic_prediction_allowed": False,
        "retrospective_report": report,
        "retrospective_gate_failures": gate_failures,
    }
    written = store.put_model_version(row)
    return {
        "trained": written,
        "model_digest": model_payload["model_digest"],
        "artifact_uri": artifact_uri,
        "authority_state": authority_state,
        "gate_failures": gate_failures,
        "report": report,
    }


def trainer_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    store = SoccerStore()
    candidate = _active_candidate(store)
    prospective = maybe_promote(store, candidate) if candidate else {"promoted": False, "reason": "NO_ACTIVE_CHALLENGER"}
    rows, excluded, historical_manifest = _training_rows_with_proof(store)
    total_eligible_rows = len(rows)
    rows = rows[-MAX_TRAINING_ROWS:]
    # One sealed prospective experiment at a time.  New labels continue to
    # accumulate but cannot rewrite the candidate being prospectively audited.
    training = (
        {"trained": False, "reason": "ACTIVE_PROSPECTIVE_CHALLENGER", "model_digest": candidate["model_digest"]}
        if candidate and not prospective.get("promoted") and not prospective.get("terminal_rejected")
        else train_challenger(store, rows)
    )
    return {
        "ok": True,
        "system": "soccer_auto",
        "training_rows": len(rows),
        "total_eligible_rows": total_eligible_rows,
        "historical_training_rows": historical_manifest["count"],
        "historical_training_manifest_version": historical_manifest["version"],
        "historical_training_manifest_digest": historical_manifest["digest"],
        "rolling_rows_trimmed": max(0, total_eligible_rows - len(rows)),
        "excluded": excluded,
        "prospective": prospective,
        "training": training,
    }
