#!/usr/bin/env python3
"""Train a portable, shadow-only MLB recovery seed from the T-45 replay.

The historical replay has already been inspected during research, so it is not
valid promotion evidence. This script therefore creates only a recovery seed:

* fixed small feature set;
* chronological development/test reporting;
* target-oriented no-play threshold;
* final refit on all replay rows;
* immutable S3 artifact and latest pointer under a recovery-only prefix;
* explicit productionAuthority=false and prospectivePromotionRequired=true.

It never changes the production champion, runtime authority, official picks, or
DynamoDB prediction rows.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import boto3
from botocore.exceptions import ClientError

VERSION = "MLB-RECOVERY-SHADOW-SEED-v1-stacked-market-signals"
MODEL_VERSION = "MLB-RECOVERY-STACKED-LOGISTIC-v1"
FEATURES: Tuple[str, ...] = (
    "marketHomeProbability",
    "currentHomeProbability",
    "v10HomeProbability",
    "v11HomeProbability",
    "lineMovementHomeProbability",
    "homeVoteFraction",
    "pullDepthLog",
)
TARGET_SELECTED_ACCURACY_PCT = 80.0
MIN_PROSPECTIVE_SELECTED_FOR_REVIEW = 100
MIN_PROSPECTIVE_TOTAL_FOR_REVIEW = 250
FIXED_L2 = 0.02
FIXED_LEARNING_RATE = 0.035
FIXED_EPOCHS = 1200


class RecoveryTrainingError(RuntimeError):
    pass


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except Exception:
        return default


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return float(f"{value:.12g}")
    return value


def _fingerprint(value: Any) -> str:
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _side_home_probability(model: Mapping[str, Any]) -> float:
    probability = min(max(_number(model.get("probability"), 0.5), 0.001), 0.999)
    return probability if str(model.get("side")) == "home" else 1.0 - probability


def _record(row: Mapping[str, Any]) -> Dict[str, Any]:
    models = row.get("models") if isinstance(row.get("models"), dict) else {}
    required = ("market", "current", "v10", "v11", "lineMovement", "ensemble")
    if any(not isinstance(models.get(name), dict) for name in required):
        raise RecoveryTrainingError("replay row is missing a required model component")
    ensemble = models["ensemble"]
    model_count = max(int(ensemble.get("modelCount") or 0), 1)
    home_votes = int((ensemble.get("votes") or {}).get("home") or 0)
    winner = str(row.get("winner") or "")
    home = str(row.get("homeTeam") or "")
    if not winner or not home:
        raise RecoveryTrainingError("replay row is missing an outcome identity")
    return {
        "gamePk": str(row.get("gamePk") or ""),
        "slateDateEt": str(row.get("slateDateEt") or ""),
        "commenceTime": str(row.get("commenceTime") or ""),
        "targetHomeWon": 1 if winner.strip().lower() == home.strip().lower() else 0,
        "features": {
            "marketHomeProbability": _side_home_probability(models["market"]),
            "currentHomeProbability": _side_home_probability(models["current"]),
            "v10HomeProbability": _side_home_probability(models["v10"]),
            "v11HomeProbability": _side_home_probability(models["v11"]),
            "lineMovementHomeProbability": _side_home_probability(models["lineMovement"]),
            "homeVoteFraction": home_votes / model_count,
            "pullDepthLog": math.log1p(max(int(row.get("pullCountBeforeCutoff") or 0), 0)),
        },
    }


def _standardize(records: Sequence[Mapping[str, Any]]) -> Tuple[Dict[str, float], Dict[str, float]]:
    means: Dict[str, float] = {}
    scales: Dict[str, float] = {}
    for feature in FEATURES:
        values = [_number((row.get("features") or {}).get(feature)) for row in records]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / max(len(values) - 1, 1)
        means[feature] = mean
        scales[feature] = math.sqrt(variance) or 1.0
    return means, scales


def _sigmoid(value: float) -> float:
    if value >= 35:
        return 1.0
    if value <= -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def _fit(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if len(records) < 50:
        raise RecoveryTrainingError("at least 50 replay rows are required")
    means, scales = _standardize(records)
    positives = sum(int(row["targetHomeWon"]) for row in records)
    negatives = len(records) - positives
    if positives <= 0 or negatives <= 0:
        raise RecoveryTrainingError("outcome target has one class")
    positive_weight = len(records) / (2.0 * positives)
    negative_weight = len(records) / (2.0 * negatives)
    weights = {feature: 0.0 for feature in FEATURES}
    bias = math.log((positives + 1.0) / (negatives + 1.0))
    for epoch in range(FIXED_EPOCHS):
        grad_bias = 0.0
        grad = {feature: 0.0 for feature in FEATURES}
        for row in records:
            normalized: Dict[str, float] = {}
            z = bias
            for feature in FEATURES:
                value = (_number(row["features"].get(feature)) - means[feature]) / scales[feature]
                normalized[feature] = value
                z += weights[feature] * value
            probability = _sigmoid(z)
            target = int(row["targetHomeWon"])
            sample_weight = positive_weight if target else negative_weight
            error = (probability - target) * sample_weight
            grad_bias += error
            for feature in FEATURES:
                grad[feature] += error * normalized[feature]
        rate = FIXED_LEARNING_RATE / (1.0 + epoch / 600.0)
        bias -= rate * grad_bias / len(records)
        for feature in FEATURES:
            weights[feature] -= rate * (
                grad[feature] / len(records) + FIXED_L2 * weights[feature]
            )
    return {
        "ok": True,
        "version": MODEL_VERSION,
        "features": list(FEATURES),
        "rowCount": len(records),
        "positiveCount": positives,
        "negativeCount": negatives,
        "bias": bias,
        "weights": weights,
        "means": means,
        "scales": scales,
        "training": {
            "algorithm": "class_balanced_standardized_logistic_regression",
            "epochs": FIXED_EPOCHS,
            "learningRate": FIXED_LEARNING_RATE,
            "l2": FIXED_L2,
        },
    }


def score(features: Mapping[str, Any], model: Mapping[str, Any]) -> float:
    z = _number(model.get("bias"))
    means = model.get("means") or {}
    scales = model.get("scales") or {}
    weights = model.get("weights") or {}
    for feature in model.get("features") or []:
        scale = _number(scales.get(feature), 1.0) or 1.0
        z += _number(weights.get(feature)) * (
            (_number(features.get(feature)) - _number(means.get(feature))) / scale
        )
    return _sigmoid(z)


def _wilson_lower(correct: int, count: int, z: float = 1.0) -> float:
    if count <= 0:
        return 0.0
    p = correct / count
    return (
        p
        + z * z / (2 * count)
        - z * math.sqrt(p * (1.0 - p) / count + z * z / (4 * count * count))
    ) / (1.0 + z * z / count)


def _evaluate(records: Sequence[Mapping[str, Any]], model: Mapping[str, Any], threshold: float) -> Dict[str, Any]:
    scored: List[Dict[str, Any]] = []
    for row in records:
        probability = score(row["features"], model)
        prediction = 1 if probability >= 0.5 else 0
        selected = max(probability, 1.0 - probability) >= threshold
        scored.append(
            {
                "probability": probability,
                "prediction": prediction,
                "target": int(row["targetHomeWon"]),
                "selected": selected,
            }
        )
    all_correct = sum(item["prediction"] == item["target"] for item in scored)
    selected = [item for item in scored if item["selected"]]
    selected_correct = sum(item["prediction"] == item["target"] for item in selected)
    brier = sum((item["probability"] - item["target"]) ** 2 for item in scored) / len(scored)
    return {
        "rowCount": len(scored),
        "allCorrect": all_correct,
        "allAccuracyPct": round(100.0 * all_correct / len(scored), 2),
        "brierScore": round(brier, 6),
        "selectedThreshold": threshold,
        "selectedCount": len(selected),
        "selectedCorrect": selected_correct,
        "selectedWrong": len(selected) - selected_correct,
        "selectedCoveragePct": round(100.0 * len(selected) / len(scored), 2),
        "selectedAccuracyPct": (
            round(100.0 * selected_correct / len(selected), 2) if selected else None
        ),
        "selectedWilsonLower68Pct": round(
            100.0 * _wilson_lower(selected_correct, len(selected)), 2
        ),
    }


def _choose_threshold(validation: Sequence[Mapping[str, Any]], model: Mapping[str, Any]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    for step in range(50, 76):
        threshold = step / 100.0
        metrics = _evaluate(validation, model, threshold)
        if metrics["selectedCount"] < 10:
            continue
        metrics["targetMetOnValidation"] = (
            _number(metrics.get("selectedAccuracyPct"), -1.0)
            >= TARGET_SELECTED_ACCURACY_PCT
        )
        candidates.append(metrics)
    if not candidates:
        raise RecoveryTrainingError("no validation threshold selected at least 10 rows")
    target_met = [row for row in candidates if row["targetMetOnValidation"]]
    pool = target_met or candidates
    selected = max(
        pool,
        key=lambda row: (
            _number(row.get("selectedWilsonLower68Pct")),
            _number(row.get("selectedAccuracyPct")),
            int(row.get("selectedCount") or 0),
        ),
    )
    return {
        "version": "MLB-RECOVERY-NO-PLAY-THRESHOLD-v1-validation-only",
        "targetSelectedAccuracyPct": TARGET_SELECTED_ACCURACY_PCT,
        "minimumValidationSelectedCount": 10,
        "selectedThreshold": selected["selectedThreshold"],
        "validationMetrics": selected,
        "targetMetOnValidation": selected["targetMetOnValidation"],
        "candidateThresholdMetrics": candidates,
    }


def _put_write_once(s3: Any, bucket: str, key: str, payload: bytes, metadata: Dict[str, str]) -> Dict[str, Any]:
    digest = hashlib.sha256(payload).hexdigest()
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code not in {"404", "NoSuchKey", "NotFound"}:
            raise
        head = None
    if head:
        existing = str((head.get("Metadata") or {}).get("sha256") or "")
        if existing != digest:
            raise RecoveryTrainingError("shadow recovery S3 artifact collision")
        return {"created": False, "bucket": bucket, "key": key, "sha256": digest}
    response = s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload,
        ContentType="application/json",
        Metadata={**metadata, "sha256": digest},
        ServerSideEncryption="AES256",
    )
    return {
        "created": True,
        "bucket": bucket,
        "key": key,
        "sha256": digest,
        "versionId": response.get("VersionId"),
    }


def train(replay: Mapping[str, Any]) -> Dict[str, Any]:
    raw_rows = replay.get("rows") if isinstance(replay.get("rows"), list) else []
    records = [_record(row) for row in raw_rows if isinstance(row, dict)]
    records.sort(key=lambda row: (row["commenceTime"], row["gamePk"]))
    if len(records) < 250:
        raise RecoveryTrainingError("at least 250 T-45 replay rows are required")
    train_end = int(len(records) * 0.60)
    validation_end = int(len(records) * 0.80)
    train_rows = records[:train_end]
    validation_rows = records[train_end:validation_end]
    untouched_rows = records[validation_end:]
    development_model = _fit(train_rows)
    threshold = _choose_threshold(validation_rows, development_model)
    selected_threshold = _number(threshold["selectedThreshold"], 0.63)
    development = {
        "split": {
            "protocol": "chronological_60_train_20_validation_20_research_test",
            "trainCount": len(train_rows),
            "validationCount": len(validation_rows),
            "researchTestCount": len(untouched_rows),
            "trainEndUtc": train_rows[-1]["commenceTime"],
            "validationEndUtc": validation_rows[-1]["commenceTime"],
            "researchTestEndUtc": untouched_rows[-1]["commenceTime"],
        },
        "thresholdSelection": threshold,
        "trainMetrics": _evaluate(train_rows, development_model, selected_threshold),
        "validationMetrics": _evaluate(validation_rows, development_model, selected_threshold),
        "researchTestMetrics": _evaluate(untouched_rows, development_model, selected_threshold),
        "researchReplayInspectedBeforeRegistration": True,
        "researchTestIsNotPromotionEvidence": True,
    }
    final_model = _fit(records)
    artifact: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "recordType": "mlb_recovery_shadow_model_seed",
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sport": "mlb",
        "shadowOnly": True,
        "productionAuthority": False,
        "officialPickOverrideAllowed": False,
        "runtimePromotionAllowedFromReplay": False,
        "prospectivePromotionRequired": True,
        "model": final_model,
        "noPlayGate": {
            "version": threshold["version"],
            "selectedThreshold": selected_threshold,
            "targetSelectedAccuracyPct": TARGET_SELECTED_ACCURACY_PCT,
            "appliesTo": "playable_subset_only",
            "allGamesStillReceiveBaselineDirection": True,
        },
        "developmentEvidence": development,
        "replayEvidence": {
            "replayVersion": replay.get("version"),
            "replayCreatedAtUtc": replay.get("createdAtUtc"),
            "replayRowCount": len(records),
            "replayRange": replay.get("range"),
            "leakageControls": replay.get("leakageControls"),
            "replayFingerprint": _fingerprint(replay),
        },
        "prospectiveReviewGate": {
            "minimumProspectiveTotalRows": MIN_PROSPECTIVE_TOTAL_FOR_REVIEW,
            "minimumProspectiveSelectedRows": MIN_PROSPECTIVE_SELECTED_FOR_REVIEW,
            "minimumSelectedAccuracyPct": TARGET_SELECTED_ACCURACY_PCT,
            "minimumSelectedWilsonLower95Pct": 0.70,
            "mustBeatMarketBaseline": True,
            "brierMustNotBeWorseThanMarket": True,
            "negativeClvAllowed": False,
            "manualReviewRequired": True,
            "automaticPromotionEnabled": False,
        },
        "sourcePolicy": {
            "canonicalPullsOnly": True,
            "featuresAtOrBeforeTMinus45": True,
            "officialMlbFinalOutcomesOnly": True,
            "missingPlayerStatsMayNotBeZeroFilled": True,
            "fundamentalsMustRemainSeparatelyProven": True,
        },
    }
    artifact["artifactDigest"] = _fingerprint(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--bucket")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    replay = json.loads(Path(args.replay).read_text(encoding="utf-8"))
    artifact = train(replay)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_canonical(artifact), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    upload = None
    if args.upload:
        if not args.bucket:
            raise RecoveryTrainingError("--bucket is required with --upload")
        s3 = boto3.client("s3")
        digest = artifact["artifactDigest"]
        key = f"mlb/recovery-shadow/v1/candidates/{digest}.json"
        payload = output.read_bytes()
        upload = _put_write_once(
            s3,
            args.bucket,
            key,
            payload,
            {
                "schema-version": VERSION,
                "model-version": MODEL_VERSION,
                "shadow-only": "true",
                "production-authority": "false",
                "prospective-promotion-required": "true",
                "artifact-digest": digest,
            },
        )
        pointer = {
            "version": "MLB-RECOVERY-SHADOW-LATEST-v1",
            "updatedAtUtc": artifact["createdAtUtc"],
            "artifactDigest": digest,
            "artifactKey": key,
            "shadowOnly": True,
            "productionAuthority": False,
            "prospectivePromotionRequired": True,
        }
        s3.put_object(
            Bucket=args.bucket,
            Key="mlb/recovery-shadow/v1/latest.json",
            Body=(json.dumps(pointer, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            ContentType="application/json",
            Metadata={
                "schema-version": pointer["version"],
                "shadow-only": "true",
                "production-authority": "false",
                "artifact-digest": digest,
            },
            ServerSideEncryption="AES256",
        )
    print(
        json.dumps(
            {
                "ok": True,
                "artifactDigest": artifact["artifactDigest"],
                "selectedThreshold": artifact["noPlayGate"]["selectedThreshold"],
                "developmentEvidence": artifact["developmentEvidence"],
                "productionAuthority": False,
                "upload": upload,
                "output": str(output),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
