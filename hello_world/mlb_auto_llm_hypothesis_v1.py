from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "MLB-AUTO-LLM-HYPOTHESIS-v1-bounded-walk-forward-shadow"
SCHEMA_VERSION = "MLB-AUTO-HYPOTHESIS-SCHEMA-v1"
TEST_VERSION = "MLB-AUTO-HYPOTHESIS-WALK-FORWARD-v1-whole-slate-untouched"
MAX_HYPOTHESES = 12
APPROVED_OPERATIONS = frozenset(
    {"single", "difference", "absolute_gap", "sum", "product", "ratio"}
)
APPROVED_MODEL_FAMILIES = frozenset(
    {"threshold_regime", "logistic_interaction", "tree_stump"}
)
APPROVED_WINDOWS = frozenset({15, 30, 45, 60, 90, 120, 180, 360, 720})
APPROVED_FEATURES = frozenset(
    {
        "homeMarketDeVigProbability",
        "awayMarketDeVigProbability",
        "selectedMarketDeVigProbability",
        "deltaGapHome",
        "bookAgreementGapHome",
        "reversalGapHome",
        "homeAwayVelocityPpHr15mDiff",
        "homeAwayVelocityPpHr60mDiff",
        "homeAwayVelocityPpHr180mDiff",
        "homeAwayAccelerationPpHr2Diff",
        "bookDivergenceGapHome",
        "consensusPersistenceGapHome",
        "compressionGapHome",
        "runLineMovementGapHome",
        "steamGapHome",
        "resistanceGapHome",
        "lateInstabilityGapHome",
        "selectedScore",
        "selectedDelta",
        "selectedBookDivergence",
        "selectedReversalCountFull",
        "selectedCoverageRatioFull",
        "selectedVolatilityPpPerPull180m",
        "starterCompositeGapHome",
        "bullpenCompositeGapHome",
        "lineupWrcPlusGapHome",
        "fundamentalPitchingMissing",
        "fundamentalOffenseLineupMissing",
    }
)


class HypothesisContractError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HypothesisContractError("non-finite hypothesis value")
        return format(value, ".17g")
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def digest(value: Any) -> str:
    encoded = json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_object_from_text(value: str) -> Dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise HypothesisContractError("LLM response did not contain a JSON object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise HypothesisContractError("LLM hypothesis response is not an object")
    return parsed


def _validated_hypothesis(raw: Mapping[str, Any], index: int) -> Dict[str, Any]:
    features = [str(value) for value in (raw.get("features") or [])]
    operation = str(raw.get("operation") or "")
    family = str(raw.get("modelFamily") or "")
    windows = [int(value) for value in (raw.get("timingWindowsMinutes") or [])]
    if not 1 <= len(features) <= 3:
        raise HypothesisContractError("hypothesis feature count must be 1-3")
    if len(set(features)) != len(features):
        raise HypothesisContractError("hypothesis features must be unique")
    if any(feature not in APPROVED_FEATURES for feature in features):
        raise HypothesisContractError("hypothesis uses a non-approved feature")
    if operation not in APPROVED_OPERATIONS:
        raise HypothesisContractError("hypothesis operation is not approved")
    if operation != "single" and len(features) < 2:
        raise HypothesisContractError("interaction hypothesis needs at least two features")
    if family not in APPROVED_MODEL_FAMILIES:
        raise HypothesisContractError("hypothesis model family is not approved")
    if any(window not in APPROVED_WINDOWS for window in windows):
        raise HypothesisContractError("hypothesis timing window is not approved")
    rationale = str(raw.get("rationale") or "").strip()
    if not rationale or len(rationale) > 600:
        raise HypothesisContractError("hypothesis rationale is missing or too long")
    expected = str(raw.get("expectedDirection") or "nonlinear")
    if expected not in {"home_positive", "home_negative", "nonlinear"}:
        raise HypothesisContractError("hypothesis expected direction is invalid")
    value = {
        "version": SCHEMA_VERSION,
        "hypothesisId": str(raw.get("hypothesisId") or f"H{index:02d}"),
        "features": features,
        "operation": operation,
        "modelFamily": family,
        "timingWindowsMinutes": sorted(set(windows)),
        "expectedDirection": expected,
        "rationale": rationale,
        "productionAuthority": False,
        "automaticWagerAllowed": False,
        "requiresSupervisedWalkForward": True,
        "requiresUntouchedHoldout": True,
    }
    value["hypothesisDigest"] = digest(value)
    return value


def validate_hypotheses(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise HypothesisContractError("hypothesis response must be an object")
    rows = value.get("hypotheses")
    if not isinstance(rows, list) or not rows:
        raise HypothesisContractError("hypothesis response must contain hypotheses")
    if len(rows) > MAX_HYPOTHESES:
        raise HypothesisContractError("too many hypotheses returned")
    hypotheses = [
        _validated_hypothesis(row, index)
        for index, row in enumerate(rows, 1)
        if isinstance(row, Mapping)
    ]
    if len(hypotheses) != len(rows):
        raise HypothesisContractError("hypothesis list contains a non-object")
    digests = [row["hypothesisDigest"] for row in hypotheses]
    if len(set(digests)) != len(digests):
        raise HypothesisContractError("duplicate hypotheses are not allowed")
    return hypotheses


def prompt_payload(research_summary: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "task": (
            "Propose bounded MLB pregame predictive hypotheses. Do not choose "
            "today's winners, write code, change production weights, or use "
            "postgame/current-game outcomes. Every proposal will be tested by "
            "chronological whole-slate walk-forward and untouched holdout."
        ),
        "schema": {
            "hypotheses": [
                {
                    "hypothesisId": "short stable id",
                    "features": ["one to three approved feature names"],
                    "operation": sorted(APPROVED_OPERATIONS),
                    "modelFamily": sorted(APPROVED_MODEL_FAMILIES),
                    "timingWindowsMinutes": sorted(APPROVED_WINDOWS),
                    "expectedDirection": [
                        "home_positive",
                        "home_negative",
                        "nonlinear",
                    ],
                    "rationale": "under 600 characters",
                }
            ]
        },
        "maximumHypotheses": MAX_HYPOTHESES,
        "approvedFeatures": sorted(APPROVED_FEATURES),
        "researchSummary": copy.deepcopy(dict(research_summary or {})),
        "constraints": {
            "productionAuthority": False,
            "currentGamePredictionAllowed": False,
            "codeGenerationAllowed": False,
            "unknownFeaturesAllowed": False,
            "walkForwardRequired": True,
            "untouchedHoldoutRequired": True,
        },
    }


def deterministic_hypotheses() -> List[Dict[str, Any]]:
    payload = {
        "hypotheses": [
            {
                "hypothesisId": "movement_reversal_regime",
                "features": ["deltaGapHome", "reversalGapHome"],
                "operation": "product",
                "modelFamily": "threshold_regime",
                "timingWindowsMinutes": [60, 180],
                "expectedDirection": "nonlinear",
                "rationale": "Test whether movement is useful only when reversal pressure remains low.",
            },
            {
                "hypothesisId": "agreement_divergence_regime",
                "features": ["bookAgreementGapHome", "bookDivergenceGapHome"],
                "operation": "difference",
                "modelFamily": "logistic_interaction",
                "timingWindowsMinutes": [60, 180],
                "expectedDirection": "home_positive",
                "rationale": "Test agreement only after explicitly subtracting cross-book divergence.",
            },
            {
                "hypothesisId": "steam_resistance_conflict",
                "features": ["steamGapHome", "resistanceGapHome"],
                "operation": "difference",
                "modelFamily": "threshold_regime",
                "timingWindowsMinutes": [15, 60],
                "expectedDirection": "home_positive",
                "rationale": "Test whether steam has value only when the opposing side shows no resistance.",
            },
            {
                "hypothesisId": "late_instability_volatility",
                "features": [
                    "lateInstabilityGapHome",
                    "selectedVolatilityPpPerPull180m",
                ],
                "operation": "absolute_gap",
                "modelFamily": "tree_stump",
                "timingWindowsMinutes": [15, 60, 180],
                "expectedDirection": "nonlinear",
                "rationale": "Test whether late instability and sustained volatility define a reject regime.",
            },
            {
                "hypothesisId": "market_fundamental_gap",
                "features": [
                    "homeMarketDeVigProbability",
                    "starterCompositeGapHome",
                    "bullpenCompositeGapHome",
                ],
                "operation": "sum",
                "modelFamily": "logistic_interaction",
                "timingWindowsMinutes": [45],
                "expectedDirection": "home_positive",
                "rationale": "Test whether starter and bullpen context improve the market probability baseline.",
            },
        ]
    }
    return validate_hypotheses(payload)


def generate_hypotheses(
    research_summary: Mapping[str, Any],
    *,
    bedrock_client: Optional[Any] = None,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    model = str(
        model_id
        or os.environ.get("MLB_AUTO_LLM_MODEL_ID")
        or "amazon.nova-lite-v1:0"
    )
    prompt = prompt_payload(research_summary)
    if bedrock_client is None:
        try:
            import boto3

            bedrock_client = boto3.client("bedrock-runtime")
        except Exception:
            bedrock_client = None
    if bedrock_client is None:
        hypotheses = deterministic_hypotheses()
        return {
            "ok": True,
            "version": VERSION,
            "source": "DETERMINISTIC_QUOTA_SAFE_FALLBACK",
            "modelId": None,
            "hypotheses": hypotheses,
            "bedrockAuthoritativeForProduction": False,
        }
    try:
        response = bedrock_client.converse(
            modelId=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": json.dumps(
                                prompt,
                                sort_keys=True,
                                separators=(",", ":"),
                                default=str,
                            )
                        }
                    ],
                }
            ],
            inferenceConfig={
                "maxTokens": 1800,
                "temperature": 0.15,
                "topP": 0.8,
            },
        )
        content = (((response.get("output") or {}).get("message") or {}).get("content") or [])
        text = "\n".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, Mapping)
        )
        hypotheses = validate_hypotheses(_json_object_from_text(text))
        source = "BEDROCK_BOUNDED_HYPOTHESIS_GENERATOR"
        error = None
    except Exception as exc:
        hypotheses = deterministic_hypotheses()
        source = "DETERMINISTIC_FALLBACK_AFTER_BEDROCK_FAILURE"
        error = {"type": type(exc).__name__, "redacted": True}
    return {
        "ok": True,
        "version": VERSION,
        "source": source,
        "modelId": model if source.startswith("BEDROCK") else None,
        "hypotheses": hypotheses,
        "generationError": error,
        "bedrockAuthoritativeForProduction": False,
    }


def _feature(row: Mapping[str, Any], name: str) -> Optional[float]:
    candidates = [
        row.get(name),
        (row.get("features") or {}).get(name)
        if isinstance(row.get("features"), Mapping)
        else None,
        (row.get("derivedFeatures") or {}).get(name)
        if isinstance(row.get("derivedFeatures"), Mapping)
        else None,
        (row.get("featureVector") or {}).get(name)
        if isinstance(row.get("featureVector"), Mapping)
        else None,
    ]
    for value in candidates:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def _label(row: Mapping[str, Any]) -> Optional[int]:
    for key in ("homeWon", "home_won"):
        if row.get(key) in {0, 1, False, True}:
            return int(bool(row.get(key)))
    winner = str(row.get("winner") or "").strip().lower()
    home = str(row.get("homeTeam") or row.get("home_team") or "").strip().lower()
    away = str(row.get("awayTeam") or row.get("away_team") or "").strip().lower()
    if winner and winner == home:
        return 1
    if winner and winner == away:
        return 0
    return None


def _slate(row: Mapping[str, Any]) -> str:
    return str(row.get("slateDateEt") or row.get("slate_date") or "")


def _signal(row: Mapping[str, Any], hypothesis: Mapping[str, Any]) -> Optional[float]:
    values = [_feature(row, name) for name in hypothesis.get("features") or []]
    if not values or any(value is None for value in values):
        return None
    numbers = [float(value) for value in values if value is not None]
    operation = hypothesis.get("operation")
    if operation == "single":
        return numbers[0]
    if operation == "difference":
        return numbers[0] - numbers[1]
    if operation == "absolute_gap":
        return abs(numbers[0] - numbers[1])
    if operation == "sum":
        return sum(numbers)
    if operation == "product":
        value = 1.0
        for number in numbers:
            value *= number
        return value
    if operation == "ratio":
        return numbers[0] / numbers[1] if abs(numbers[1]) > 1e-12 else None
    return None


def _quantiles(values: Sequence[float]) -> List[float]:
    ordered = sorted(values)
    if not ordered:
        return []
    indexes = {0, len(ordered) - 1}
    for fraction in (0.2, 0.35, 0.5, 0.65, 0.8):
        indexes.add(min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return sorted({ordered[index] for index in indexes})


def _daily_accuracy(rows: Sequence[Mapping[str, Any]], predictions: Sequence[int]) -> Dict[str, Any]:
    by_slate: Dict[str, List[int]] = defaultdict(list)
    correct = 0
    for row, prediction in zip(rows, predictions):
        label = _label(row)
        if label is None:
            continue
        hit = int(prediction == label)
        correct += hit
        by_slate[_slate(row)].append(hit)
    daily = [sum(values) / len(values) for values in by_slate.values() if values]
    count = sum(len(values) for values in by_slate.values())
    return {
        "rowCount": count,
        "dayCount": len(daily),
        "correct": correct,
        "overallAccuracy": correct / count if count else None,
        "meanDailyAccuracy": sum(daily) / len(daily) if daily else None,
        "minimumDailyAccuracy": min(daily) if daily else None,
        "completeSlateCoverage": 1.0 if count else 0.0,
    }


def _market_baseline(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    predictions = []
    retained = []
    for row in rows:
        value = _feature(row, "homeMarketDeVigProbability")
        if value is None or _label(row) is None:
            continue
        retained.append(row)
        predictions.append(1 if value >= 0.5 else 0)
    return _daily_accuracy(retained, predictions)


def _split_whole_slates(rows: Iterable[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    cleaned = [
        copy.deepcopy(dict(row))
        for row in rows
        if _slate(row) and _label(row) is not None
    ]
    dates = sorted({_slate(row) for row in cleaned})
    if len(dates) < 15:
        return cleaned, [], []
    train_end = max(1, int(len(dates) * 0.60))
    validation_end = max(train_end + 1, int(len(dates) * 0.80))
    validation_end = min(validation_end, len(dates) - 1)
    train_dates = set(dates[:train_end])
    validation_dates = set(dates[train_end:validation_end])
    test_dates = set(dates[validation_end:])
    return (
        [row for row in cleaned if _slate(row) in train_dates],
        [row for row in cleaned if _slate(row) in validation_dates],
        [row for row in cleaned if _slate(row) in test_dates],
    )


def evaluate_hypothesis(
    rows: Iterable[Mapping[str, Any]], hypothesis: Mapping[str, Any]
) -> Dict[str, Any]:
    train, validation, untouched = _split_whole_slates(rows)
    if not validation or not untouched:
        return {
            "ok": False,
            "version": TEST_VERSION,
            "hypothesisId": hypothesis.get("hypothesisId"),
            "status": "INSUFFICIENT_WHOLE_SLATE_HISTORY",
            "productionAuthority": False,
        }
    train_pairs = [
        (row, _signal(row, hypothesis))
        for row in train
        if _signal(row, hypothesis) is not None
    ]
    if len(train_pairs) < 100:
        return {
            "ok": False,
            "version": TEST_VERSION,
            "hypothesisId": hypothesis.get("hypothesisId"),
            "status": "INSUFFICIENT_TRAINING_ROWS",
            "trainingRowCount": len(train_pairs),
            "productionAuthority": False,
        }
    thresholds = _quantiles([float(value) for _row, value in train_pairs])
    candidates = []
    for threshold in thresholds:
        for direction in (-1, 1):
            predictions = [
                1 if direction * (float(value) - threshold) >= 0 else 0
                for _row, value in train_pairs
            ]
            metrics = _daily_accuracy(
                [row for row, _value in train_pairs], predictions
            )
            candidates.append((metrics.get("meanDailyAccuracy") or 0.0, threshold, direction, metrics))
    _score, threshold, direction, train_metrics = max(candidates, key=lambda item: item[0])

    def score_partition(partition: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        pairs = [
            (row, _signal(row, hypothesis))
            for row in partition
            if _signal(row, hypothesis) is not None
        ]
        predictions = [
            1 if direction * (float(value) - threshold) >= 0 else 0
            for _row, value in pairs
        ]
        return _daily_accuracy([row for row, _value in pairs], predictions)

    validation_metrics = score_partition(validation)
    untouched_metrics = score_partition(untouched)
    validation_baseline = _market_baseline(validation)
    untouched_baseline = _market_baseline(untouched)
    validation_accuracy = validation_metrics.get("overallAccuracy")
    untouched_accuracy = untouched_metrics.get("overallAccuracy")
    validation_baseline_accuracy = validation_baseline.get("overallAccuracy")
    untouched_baseline_accuracy = untouched_baseline.get("overallAccuracy")
    passed = bool(
        validation_metrics.get("rowCount", 0) >= 100
        and untouched_metrics.get("rowCount", 0) >= 100
        and validation_metrics.get("dayCount", 0) >= 5
        and untouched_metrics.get("dayCount", 0) >= 5
        and validation_accuracy is not None
        and untouched_accuracy is not None
        and validation_baseline_accuracy is not None
        and untouched_baseline_accuracy is not None
        and validation_accuracy >= validation_baseline_accuracy + 0.01
        and untouched_accuracy >= untouched_baseline_accuracy + 0.01
        and (validation_metrics.get("meanDailyAccuracy") or 0.0) >= 0.60
        and (untouched_metrics.get("meanDailyAccuracy") or 0.0) >= 0.60
        and (untouched_metrics.get("minimumDailyAccuracy") or 0.0) >= 0.40
    )
    return {
        "ok": True,
        "version": TEST_VERSION,
        "hypothesisId": hypothesis.get("hypothesisId"),
        "hypothesisDigest": hypothesis.get("hypothesisDigest"),
        "status": "WALK_FORWARD_PASSED" if passed else "WALK_FORWARD_REJECTED",
        "selectedOnTrainingOnly": {
            "threshold": threshold,
            "direction": direction,
            "training": train_metrics,
        },
        "validation": validation_metrics,
        "validationMarketBaseline": validation_baseline,
        "untouchedHoldout": untouched_metrics,
        "untouchedMarketBaseline": untouched_baseline,
        "wholeSlateChronologyPreserved": True,
        "untouchedHoldoutUsedForSelection": False,
        "eligibleForOptimizerSearchExpansion": passed,
        "productionAuthority": False,
        "automaticWagerAllowed": False,
    }


def run_shadow_cycle(
    rows: Iterable[Mapping[str, Any]],
    *,
    research_summary: Optional[Mapping[str, Any]] = None,
    bedrock_client: Optional[Any] = None,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    generated = generate_hypotheses(
        research_summary or {},
        bedrock_client=bedrock_client,
        model_id=model_id,
    )
    tests = [
        evaluate_hypothesis(rows, hypothesis)
        for hypothesis in generated.get("hypotheses") or []
    ]
    passed = [
        result
        for result in tests
        if result.get("status") == "WALK_FORWARD_PASSED"
    ]
    report = {
        "ok": True,
        "version": VERSION,
        "createdAtUtc": _now(),
        "generation": generated,
        "tests": tests,
        "passedHypothesisCount": len(passed),
        "passedHypothesisDigests": [
            row.get("hypothesisDigest") for row in passed
        ],
        "productionAuthority": False,
        "productionWeightMutation": False,
        "winnerSelectionMutation": False,
        "automaticWagerAllowed": False,
        "nextAuthority": (
            "Existing supervised optimizer may include only WALK_FORWARD_PASSED "
            "hypotheses in a new candidate; normal immutable prospective and "
            "calibration promotion gates still apply."
        ),
    }
    report["reportDigest"] = digest(report)
    return report
