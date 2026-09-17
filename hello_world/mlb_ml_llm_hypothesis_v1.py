from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import boto3


VERSION = "MLB-ML-LLM-HYPOTHESIS-v1-bedrock-bounded-shadow-research"
HYPOTHESIS_SCHEMA_VERSION = "MLB-ML-LLM-HYPOTHESIS-SCHEMA-v1"
EVALUATION_ATTESTATION_VERSION = "MLB-ML-LLM-WALK-FORWARD-ATTESTATION-v1"
DEFAULT_MODEL_ID = "amazon.nova-lite-v1:0"
MAX_HYPOTHESES = 12
MAX_INTERACTIONS = 6

ALLOWED_FEATURES = frozenset(
    {
        "selected_probability",
        "starting_probability",
        "movement_15m",
        "movement_60m",
        "movement_180m",
        "movement_full",
        "velocity_15m",
        "velocity_60m",
        "velocity_180m",
        "acceleration_60m",
        "acceleration_180m",
        "acceleration_full",
        "reversal_count_60m",
        "reversal_count_180m",
        "reversal_count_full",
        "movement_per_reversal",
        "book_agreement_rate",
        "book_divergence",
        "book_leadership",
        "book_follow_through",
        "steam_strength",
        "steam_persistence",
        "run_line_movement",
        "run_line_confirmation",
        "resistance_strength",
        "market_compression",
        "compression_breakout",
        "late_instability",
        "starting_pitcher_quality",
        "starting_pitcher_recent_form",
        "starting_pitcher_expected_innings",
        "bullpen_quality",
        "bullpen_freshness",
        "lineup_quality",
        "confirmed_lineup",
        "impact_player_absence",
        "injury_uncertainty",
        "park_factor",
        "weather_uncertainty",
        "travel_rest",
        "doubleheader_game_number",
        "fundamentals_completeness",
    }
)
ALLOWED_OPERATORS = frozenset(
    {"gt", "gte", "lt", "lte", "between", "abs_gt", "same_sign", "opposite_sign", "and", "or"}
)
ALLOWED_MODEL_FAMILIES = frozenset(
    {"logistic", "elastic_net_logistic", "gradient_boosted_trees", "random_forest", "calibrated_linear", "regime_mixture"}
)
FORBIDDEN_TOKENS = frozenset(
    {
        "winner",
        "actual_winner",
        "final_score",
        "home_score",
        "away_score",
        "settled_result",
        "outcome_label",
        "postgame",
        "after_game",
    }
)


class LLMHypothesisError(ValueError):
    pass


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(text: str) -> Any:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = value.strip("`").strip()
        if value.lower().startswith("json"):
            value = value[4:].strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start = value.find("[")
        end = value.rfind("]")
        if start >= 0 and end > start:
            return json.loads(value[start : end + 1])
        raise


def _request_prompt(context: Mapping[str, Any]) -> str:
    compact = {
        "weakPatterns": context.get("weakPatterns") or [],
        "strongButUnprovenPatterns": context.get("strongButUnprovenPatterns") or [],
        "currentFeatureCoverage": context.get("currentFeatureCoverage") or {},
        "currentModelFamilies": context.get("currentModelFamilies") or [],
        "latestMetrics": context.get("latestMetrics") or {},
        "constraints": {
            "preLockOnly": True,
            "immutableT45Required": True,
            "noOutcomeOrPostgameFields": True,
            "shadowOnly": True,
            "walkForwardValidationRequired": True,
            "allowedFeatures": sorted(ALLOWED_FEATURES),
            "allowedOperators": sorted(ALLOWED_OPERATORS),
            "allowedModelFamilies": sorted(ALLOWED_MODEL_FAMILIES),
            "maximumHypotheses": MAX_HYPOTHESES,
            "maximumInteractionsPerHypothesis": MAX_INTERACTIONS,
        },
    }
    return (
        "You are the bounded research analyst for INQSI MLB AUTO. Propose novel, testable "
        "pregame hypotheses that may improve winner prediction. You may not choose games, "
        "change production, use outcomes, use post-lock data, or write code. Return ONLY a "
        "JSON array. Each object must contain name, rationale, regimePredicates, interactions, "
        "timingWindows, and modelFamilies. Each interaction must contain features and operator. "
        "All features/operators/model families must come from the supplied allowlists. Prefer "
        "hypotheses that explain known weak patterns without merely increasing a failed signal's weight.\n"
        + json.dumps(compact, sort_keys=True, separators=(",", ":"), default=str)
    )


def _invoke_bedrock(prompt: str, *, model_id: str, client: Optional[Any] = None) -> str:
    runtime = client or boto3.client("bedrock-runtime")
    if hasattr(runtime, "converse"):
        response = runtime.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 3000, "temperature": 0.2, "topP": 0.8},
        )
        content = (((response.get("output") or {}).get("message") or {}).get("content") or [])
        return "".join(str(item.get("text") or "") for item in content if isinstance(item, Mapping))
    body = {
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": 3000, "temperature": 0.2, "topP": 0.8},
    }
    response = runtime.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body).encode("utf-8"),
    )
    raw = response.get("body")
    payload = json.loads(raw.read().decode("utf-8") if hasattr(raw, "read") else raw)
    content = (((payload.get("output") or {}).get("message") or {}).get("content") or [])
    return "".join(str(item.get("text") or "") for item in content if isinstance(item, Mapping))


def _feature_names(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    output: List[str] = []
    for item in value:
        if isinstance(item, str):
            output.append(item)
        elif isinstance(item, Mapping):
            output.extend(str(name) for name in (item.get("features") or []) if isinstance(name, str))
    return output


def hypothesis_errors(value: Any) -> List[str]:
    if not isinstance(value, Mapping):
        return ["hypothesis_not_object"]
    errors: List[str] = []
    if not str(value.get("name") or "").strip():
        errors.append("name_missing")
    if not str(value.get("rationale") or "").strip():
        errors.append("rationale_missing")
    interactions = value.get("interactions")
    if not isinstance(interactions, list) or not interactions:
        errors.append("interactions_missing")
        interactions = []
    if len(interactions) > MAX_INTERACTIONS:
        errors.append("too_many_interactions")
    for index, interaction in enumerate(interactions):
        if not isinstance(interaction, Mapping):
            errors.append(f"interaction_{index}_not_object")
            continue
        features = [str(item) for item in (interaction.get("features") or [])]
        if not features:
            errors.append(f"interaction_{index}_features_missing")
        for feature in features:
            lowered = feature.lower()
            if feature not in ALLOWED_FEATURES:
                errors.append(f"interaction_{index}_feature_not_allowed:{feature}")
            if any(token in lowered for token in FORBIDDEN_TOKENS):
                errors.append(f"interaction_{index}_forbidden_feature:{feature}")
        operator = str(interaction.get("operator") or "")
        if operator not in ALLOWED_OPERATORS:
            errors.append(f"interaction_{index}_operator_not_allowed:{operator}")
    for field in ("regimePredicates", "timingWindows"):
        text = json.dumps(value.get(field) or {}, sort_keys=True).lower()
        for token in FORBIDDEN_TOKENS:
            if token in text:
                errors.append(f"{field}_contains_forbidden_token:{token}")
    model_families = [str(item) for item in (value.get("modelFamilies") or [])]
    if not model_families:
        errors.append("model_families_missing")
    for family in model_families:
        if family not in ALLOWED_MODEL_FAMILIES:
            errors.append(f"model_family_not_allowed:{family}")
    return sorted(set(errors))


def normalize_hypothesis(value: Mapping[str, Any], *, ordinal: int) -> Dict[str, Any]:
    hypothesis = {
        "schemaVersion": HYPOTHESIS_SCHEMA_VERSION,
        "name": str(value.get("name") or "").strip()[:160],
        "rationale": str(value.get("rationale") or "").strip()[:1500],
        "regimePredicates": _plain(value.get("regimePredicates") or []),
        "interactions": _plain(value.get("interactions") or []),
        "timingWindows": _plain(value.get("timingWindows") or []),
        "modelFamilies": [str(item) for item in (value.get("modelFamilies") or [])],
        "preLockOnly": True,
        "immutableT45Required": True,
        "requiresWalkForwardValidation": True,
        "requiresUntouchedHoldoutValidation": True,
        "validatedBySupervisedWalkForward": False,
        "eligibleForCandidate": False,
        "productionAuthority": False,
        "ordinal": ordinal,
    }
    hypothesis["hypothesisDigest"] = _digest(hypothesis)
    return hypothesis


def generate_hypotheses(
    context: Mapping[str, Any],
    *,
    client: Optional[Any] = None,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    selected_model = str(
        model_id
        or os.environ.get("MLB_LLM_HYPOTHESIS_MODEL_ID")
        or DEFAULT_MODEL_ID
    ).strip()
    enabled = str(os.environ.get("MLB_LLM_HYPOTHESIS_ENABLED", "true")).lower() in {
        "1",
        "true",
        "yes",
    }
    if not enabled:
        return {
            "ok": True,
            "version": VERSION,
            "status": "DISABLED_BY_CONFIGURATION",
            "hypotheses": [],
            "productionAuthority": False,
        }
    prompt = _request_prompt(context)
    try:
        raw = _invoke_bedrock(prompt, model_id=selected_model, client=client)
        parsed = _extract_json(raw)
    except Exception as exc:
        return {
            "ok": False,
            "version": VERSION,
            "status": "BEDROCK_HYPOTHESIS_GENERATION_FAILED",
            "modelId": selected_model,
            "hypotheses": [],
            "errorType": type(exc).__name__,
            "productionAuthority": False,
            "failClosed": True,
        }
    if not isinstance(parsed, list):
        parsed = [parsed]
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for ordinal, item in enumerate(parsed[:MAX_HYPOTHESES]):
        errors = hypothesis_errors(item)
        if errors:
            rejected.append({"ordinal": ordinal, "errors": errors})
            continue
        accepted.append(normalize_hypothesis(item, ordinal=ordinal))
    report = {
        "ok": True,
        "version": VERSION,
        "createdAtUtc": _now(),
        "modelId": selected_model,
        "hypothesisCount": len(accepted),
        "rejectedCount": len(rejected),
        "hypotheses": accepted,
        "rejected": rejected,
        "productionAuthority": False,
        "directCodeMutationAllowed": False,
        "directWinnerSelectionAllowed": False,
        "supervisedValidationRequired": True,
    }
    report["reportDigest"] = _digest(report)
    return report


def evaluation_attestation(
    *,
    hypothesis: Mapping[str, Any],
    walk_forward: Mapping[str, Any],
    untouched_holdout: Mapping[str, Any],
    calibration: Mapping[str, Any],
) -> Dict[str, Any]:
    walk_count = int(walk_forward.get("gameCount") or 0)
    holdout_count = int(untouched_holdout.get("gameCount") or 0)
    walk_accuracy = float(walk_forward.get("accuracy") or 0.0)
    holdout_accuracy = float(untouched_holdout.get("accuracy") or 0.0)
    brier_delta = float(calibration.get("brierDeltaVsBaseline") or 0.0)
    log_loss_delta = float(calibration.get("logLossDeltaVsBaseline") or 0.0)
    errors: List[str] = []
    if walk_count < 200:
        errors.append("walk_forward_sample_below_200")
    if holdout_count < 200:
        errors.append("untouched_holdout_sample_below_200")
    if walk_accuracy <= 0.5:
        errors.append("walk_forward_not_predictive")
    if holdout_accuracy <= 0.5:
        errors.append("untouched_holdout_not_predictive")
    if brier_delta < -0.005:
        errors.append("brier_degraded")
    if log_loss_delta < -0.01:
        errors.append("log_loss_degraded")
    attestation = {
        "version": EVALUATION_ATTESTATION_VERSION,
        "hypothesisDigest": hypothesis.get("hypothesisDigest"),
        "walkForward": _plain(walk_forward),
        "untouchedHoldout": _plain(untouched_holdout),
        "calibration": _plain(calibration),
        "passedResearchGate": not errors,
        "eligibleForCandidate": not errors,
        "productionAuthority": False,
        "errors": errors,
        "evaluatedAtUtc": _now(),
    }
    attestation["attestationDigest"] = _digest(attestation)
    return attestation


def _store(report: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    table_name = str(os.environ.get("SNAPSHOTS_TABLE") or "").strip()
    experiment_id = str(os.environ.get("MLB_ML_EXPERIMENT_ID") or "default").strip()
    if not table_name:
        return None
    table = boto3.resource("dynamodb").Table(table_name)
    created = str(report.get("createdAtUtc") or _now())
    digest = str(report.get("reportDigest") or _digest(report))
    item = {
        "PK": f"MLB_ML_EXPERIMENT#V2#{experiment_id}",
        "SK": f"LLM_HYPOTHESIS#{created}#{digest}",
        "record_type": "mlb_ml_llm_hypothesis_batch_v1",
        "created_at": created,
        "data": _plain(report),
    }
    table.put_item(Item=item)
    return {"pk": item["PK"], "sk": item["SK"], "digest": digest}


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    payload = event if isinstance(event, Mapping) else {}
    research_context = payload.get("researchContext")
    if not isinstance(research_context, Mapping):
        research_context = {
            "weakPatterns": payload.get("weakPatterns") or [],
            "strongButUnprovenPatterns": payload.get("strongButUnprovenPatterns") or [],
            "currentFeatureCoverage": payload.get("currentFeatureCoverage") or {},
            "currentModelFamilies": payload.get("currentModelFamilies") or [],
            "latestMetrics": payload.get("latestMetrics") or {},
        }
    report = generate_hypotheses(research_context)
    if report.get("ok") is True:
        try:
            report["stored"] = _store(report)
        except Exception as exc:
            report["stored"] = None
            report["storeErrorType"] = type(exc).__name__
            report["ok"] = False
    return report
