"""Leakage-safe prospective audit for autonomous MLB V8 challengers.

A challenger is frozen only after a learned residual clears the retrospective
chronological gate. Its model, standardizer, calibrator, and full training report
are content-addressed with a corpus boundary. Only complete settled slates after
that boundary may qualify the challenger for automatic promotion.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Mapping, Sequence

import mlb_supervised_features_v2 as features
import mlb_supervised_model_v2 as supervised
import mlb_v8_autonomy_v1 as autonomy
import mlb_v8_model_runtime as runtime

VERSION = "MLB-V8-PROSPECTIVE-AUDIT-v1-frozen-candidate"
MIN_PROSPECTIVE_GAMES = 200
MIN_PROSPECTIVE_DAYS = 15
DAILY_TARGET = 0.80
MAX_ECE = 0.08


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _last_corpus_day(training: Mapping[str, Any]) -> str:
    values = []
    for partition in (training.get("partitions") or {}).values():
        if not isinstance(partition, Mapping):
            continue
        if partition.get("lastDate"):
            values.append(str(partition["lastDate"]))
        values.extend(str(day) for day in partition.get("dates") or [] if day)
    if not values:
        raise ValueError("V8 candidate has no frozen corpus boundary")
    return max(values)


def candidate_eligibility(training: Mapping[str, Any]) -> Dict[str, Any]:
    """Describe whether one learned challenger can enter prospective audit.

    A retained market baseline is a valid autonomous *decision*, but it is not a
    deployable residual challenger.  Therefore no residual runtime bundle is
    required or built for that state.  This prevents a healthy baseline retention
    from being mislabeled as a broken model while still blocking promotion until a
    learned candidate clears every gate.
    """

    errors = []
    learning = training.get("learningExecution") or {}
    learned_selected = learning.get("learnedCandidateSelected") is True
    baseline_retained = learning.get("marketBaselineRetainedByGuard") is True

    if training.get("ok") is not True:
        errors.append("training_report_unhealthy")
    if learning.get("learningExecuted") is not True:
        errors.append("learning_execution_unproven")
    if not learned_selected:
        errors.append("learned_candidate_not_selected")
    if baseline_retained:
        errors.append("market_baseline_retained")
    if (training.get("promotionGate") or {}).get("passed") is not True:
        errors.append("retrospective_promotion_gate_not_passed")
    if training.get("automaticWagerAllowed") is not False:
        errors.append("automatic_wager_must_remain_disabled")

    bundle = None
    bundle_required = learned_selected
    if bundle_required:
        bundle_status = "REQUIRED_PENDING_VALIDATION"
        try:
            bundle = runtime.build_bundle(training)
            runtime.verify_bundle(bundle)
        except Exception as exc:
            bundle_status = "INVALID_LEARNED_CANDIDATE_BUNDLE"
            errors.append(f"runtime_bundle_invalid:{type(exc).__name__}:{exc}")
        else:
            bundle_status = "VALID_LEARNED_CANDIDATE_BUNDLE"
    elif baseline_retained:
        bundle_status = "NOT_APPLICABLE_MARKET_BASELINE_RETAINED"
    else:
        bundle_status = "NOT_APPLICABLE_NO_LEARNED_CANDIDATE"

    boundary = None
    try:
        boundary = _last_corpus_day(training)
    except Exception as exc:
        errors.append(f"corpus_boundary_invalid:{type(exc).__name__}:{exc}")
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "modelBundle": bundle,
        "runtimeBundleRequired": bundle_required,
        "runtimeBundleStatus": bundle_status,
        "frozenCorpusLastDate": boundary,
    }


def build_candidate(training: Mapping[str, Any]) -> Dict[str, Any]:
    eligibility = candidate_eligibility(training)
    if eligibility["ok"] is not True:
        raise ValueError(
            "V8 candidate is not eligible for prospective freeze:"
            + ",".join(eligibility["errors"])
        )
    candidate = {
        "proofType": "MLB_V8_FROZEN_PROSPECTIVE_CANDIDATE",
        "version": VERSION,
        "authority": "SHADOW_ONLY",
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
        "frozenCorpusLastDate": eligibility["frozenCorpusLastDate"],
        "trainingResultDigest": training.get("resultDigest"),
        "modelDigest": eligibility["modelBundle"]["modelDigest"],
        "sourceModelDigest": eligibility["modelBundle"].get(
            "sourceModelDigest"
        ),
        "featureSchemaVersion": eligibility["modelBundle"][
            "featureSchemaVersion"
        ],
        "modelBundle": eligibility["modelBundle"],
        "trainingReport": copy.deepcopy(dict(training)),
    }
    candidate["candidateDigest"] = _sha(candidate)
    return candidate


def verify_candidate(candidate: Mapping[str, Any]) -> None:
    if candidate.get("version") != VERSION:
        raise ValueError("V8 prospective candidate version mismatch")
    if (
        candidate.get("authority") != "SHADOW_ONLY"
        or candidate.get("productionAuthorityChanged") is not False
        or candidate.get("automaticWagerAllowed") is not False
    ):
        raise ValueError("V8 prospective candidate changed authority")
    material = {
        key: item for key, item in candidate.items() if key != "candidateDigest"
    }
    if candidate.get("candidateDigest") != _sha(material):
        raise ValueError("V8 prospective candidate digest mismatch")
    runtime.verify_bundle(candidate.get("modelBundle") or {})
    if (
        candidate.get("modelDigest")
        != (candidate.get("modelBundle") or {}).get("modelDigest")
    ):
        raise ValueError("V8 prospective model digest mismatch")
    if not candidate.get("frozenCorpusLastDate"):
        raise ValueError("V8 prospective candidate boundary is missing")


def _prospective_examples(
    candidate: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
):
    # Compile the complete corpus first so every prospective team-history feature
    # uses the same strictly-prior-slate ledger as training. Filter only after
    # compilation; same-day results remain excluded by the feature compiler.
    compiled = features.prepare_examples(records)
    boundary = str(candidate["frozenCorpusLastDate"])
    return [row for row in compiled if str(row.day) > boundary]


def evaluate_candidate(
    candidate: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    verify_candidate(candidate)
    bundle = candidate["modelBundle"]
    examples = _prospective_examples(candidate, records)
    probabilities = []
    for row in examples:
        vector = dict(row.features)
        vector[str(bundle.get("marketProbabilityFeature"))] = row.market_probability
        probabilities.append(runtime.score(bundle, vector)["probability"])
    model_metrics = supervised.evaluate_probabilities(examples, probabilities)
    market_metrics = supervised.evaluate_probabilities(
        examples, [row.market_probability for row in examples]
    )
    errors = []
    if model_metrics["gameCount"] < MIN_PROSPECTIVE_GAMES:
        errors.append("prospective_game_floor_not_met")
    if model_metrics["dayCount"] < MIN_PROSPECTIVE_DAYS:
        errors.append("prospective_day_floor_not_met")
    evidence_complete = not {
        "prospective_game_floor_not_met",
        "prospective_day_floor_not_met",
    }.intersection(errors)
    if evidence_complete:
        if model_metrics["dailyPassRate"] < 1.0 - 1e-12:
            errors.append("prospective_contains_day_below_80_percent")
        if model_metrics["meanDailyAccuracy"] < DAILY_TARGET - 1e-12:
            errors.append("prospective_mean_daily_accuracy_below_80_percent")
        if model_metrics["minimumDailyAccuracy"] < DAILY_TARGET - 1e-12:
            errors.append("prospective_minimum_daily_accuracy_below_80_percent")
        if model_metrics["expectedCalibrationError"] > MAX_ECE + 1e-12:
            errors.append("prospective_calibration_error_above_0_08")
        if model_metrics["brierScore"] > market_metrics["brierScore"] + 1e-12:
            errors.append("prospective_brier_worse_than_market")
        if model_metrics["logLoss"] > market_metrics["logLoss"] + 1e-12:
            errors.append("prospective_log_loss_worse_than_market")
        if model_metrics["overallAccuracy"] < market_metrics["overallAccuracy"]:
            errors.append("prospective_accuracy_worse_than_market")
    passed = evidence_complete and not errors
    rejected = evidence_complete and not passed
    result = {
        "proofType": "MLB_V8_FROZEN_PROSPECTIVE_AUDIT",
        "version": VERSION,
        "candidateDigest": candidate["candidateDigest"],
        "modelDigest": candidate["modelDigest"],
        "sourceModelDigest": candidate.get("sourceModelDigest"),
        "frozenCorpusLastDate": candidate["frozenCorpusLastDate"],
        "prospectiveFirstDate": min((row.day for row in examples), default=None),
        "prospectiveLastDate": max((row.day for row in examples), default=None),
        "prospectiveEvidenceComplete": evidence_complete,
        "prospectiveAuditPassed": passed,
        "prospectiveAuditRejected": rejected,
        "minimumProspectiveGames": MIN_PROSPECTIVE_GAMES,
        "minimumProspectiveDays": MIN_PROSPECTIVE_DAYS,
        "dailyAccuracyRequirement": DAILY_TARGET,
        "maximumCalibrationError": MAX_ECE,
        "modelMetrics": model_metrics,
        "sameTimeMarketBaseline": market_metrics,
        "errors": sorted(set(errors)),
        "selectionUsedProspectiveOutcomes": False,
        "modelRefitDuringProspectiveAudit": False,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
    }
    result["auditDigest"] = _sha(result)
    return result


def augment_training_for_promotion(
    candidate: Mapping[str, Any], audit: Mapping[str, Any]
) -> Dict[str, Any]:
    verify_candidate(candidate)
    if audit.get("candidateDigest") != candidate.get("candidateDigest"):
        raise ValueError("prospective audit candidate digest mismatch")
    if audit.get("modelDigest") != candidate.get("modelDigest"):
        raise ValueError("prospective audit model digest mismatch")
    if audit.get("prospectiveAuditPassed") is not True:
        raise ValueError("prospective audit has not passed")
    effective = copy.deepcopy(dict(candidate["trainingReport"]))
    effective.update(
        {
            "retrospectiveArchitectureEvaluation": False,
            "freshProspectiveAuditRequired": False,
            "productionPromotionEligible": True,
            "prospectiveAudit": copy.deepcopy(dict(audit)),
            "prospectiveCandidateDigest": candidate["candidateDigest"],
            "prospectiveAuditDigest": audit.get("auditDigest"),
            "frozenModelBundle": copy.deepcopy(candidate["modelBundle"]),
            "frozenModelBundleDigest": candidate["modelDigest"],
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
        }
    )
    return autonomy.decorate_result(effective)
