#!/usr/bin/env python3
"""Train a fundamentals-aware, shadow-only MLB recovery challenger.

Candidate selection uses only the chronological training and validation blocks.
The final 20% block is evaluated once after candidate selection. Historical
probable-pitcher identities may reflect later schedule resolution, so even a
strong research result cannot promote this model. The artifact is a seed for
prospective T-minus-45 validation only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import boto3
from botocore.exceptions import ClientError

VERSION = "MLB-RECOVERY-FUNDAMENTALS-SHADOW-v1"
TARGET_SELECTED_ACCURACY_PCT = 80.0
SIGNAL_FEATURES: Tuple[str, ...] = (
    "marketHomeProbability",
    "currentHomeProbability",
    "v10HomeProbability",
    "v11HomeProbability",
    "lineMovementHomeProbability",
    "homeVoteFraction",
    "pullDepthLog",
)
FUNDAMENTAL_FEATURES: Tuple[str, ...] = (
    "offenseOpsGapHome",
    "offenseRunsPerGameGapHome",
    "offenseWalkRateGapHome",
    "offenseStrikeoutRateGapHome",
    "recentOffenseOpsGapHome",
    "recentOffenseRunsPerGameGapHome",
    "staffEraGapHome",
    "staffWhipGapHome",
    "staffStrikeoutWalkGapHome",
    "staffHomeRunsPer9GapHome",
    "recentStaffEraGapHome",
    "recentStaffWhipGapHome",
    "starterEraGapHome",
    "starterWhipGapHome",
    "starterStrikeoutWalkGapHome",
    "starterHomeRunsPer9GapHome",
    "starterInningsPerStartGapHome",
    "starterRecentEraGapHome",
    "starterRecentWhipGapHome",
    "starterRecentStrikeoutWalkGapHome",
    "starterDaysRestGapHome",
    "homeField",
)


class FundamentalsTrainingError(RuntimeError):
    pass


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        return float(f"{value:.12g}") if math.isfinite(value) else str(value)
    return value


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_canonical(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _side_home_probability(model: Mapping[str, Any]) -> float:
    probability = min(max(float(model.get("probability") or 0.5), 0.001), 0.999)
    return probability if str(model.get("side")) == "home" else 1.0 - probability


def _record(row: Mapping[str, Any]) -> Dict[str, Any]:
    models = row.get("models") or {}
    required = ("market", "current", "v10", "v11", "lineMovement", "ensemble")
    if any(not isinstance(models.get(name), dict) for name in required):
        raise FundamentalsTrainingError("replay model component missing")
    winner = str(row.get("winner") or "").strip().lower()
    home = str(row.get("homeTeam") or "").strip().lower()
    if not winner or not home:
        raise FundamentalsTrainingError("outcome identity missing")
    ensemble = models["ensemble"]
    model_count = max(int(ensemble.get("modelCount") or 0), 1)
    signal = {
        "marketHomeProbability": _side_home_probability(models["market"]),
        "currentHomeProbability": _side_home_probability(models["current"]),
        "v10HomeProbability": _side_home_probability(models["v10"]),
        "v11HomeProbability": _side_home_probability(models["v11"]),
        "lineMovementHomeProbability": _side_home_probability(models["lineMovement"]),
        "homeVoteFraction": int((ensemble.get("votes") or {}).get("home") or 0) / model_count,
        "pullDepthLog": math.log1p(max(int(row.get("pullCountBeforeCutoff") or 0), 0)),
    }
    standard = row.get("standardFundamentals") or {}
    fundamentals = standard.get("features") if isinstance(standard.get("features"), dict) else {}
    masks = standard.get("missingMasks") if isinstance(standard.get("missingMasks"), dict) else {}
    return {
        "gamePk": str(row.get("gamePk") or ""),
        "commenceTime": str(row.get("commenceTime") or ""),
        "targetHomeWon": 1 if winner == home else 0,
        "signal": signal,
        "fundamentals": {key: _number(fundamentals.get(key)) for key in FUNDAMENTAL_FEATURES},
        "fundamentalMasks": {
            f"{key}Missing": 1.0 if masks.get(f"{key}Missing") is True or fundamentals.get(key) is None else 0.0
            for key in FUNDAMENTAL_FEATURES
            if key != "homeField"
        },
        "fundamentalsCompletenessPct": float(standard.get("completenessPct") or 0.0),
    }


def _feature_names(kind: str) -> Tuple[str, ...]:
    if kind == "signal_only":
        return SIGNAL_FEATURES
    if kind == "signal_plus_fundamentals":
        masks = tuple(f"{key}Missing" for key in FUNDAMENTAL_FEATURES if key != "homeField")
        return SIGNAL_FEATURES + FUNDAMENTAL_FEATURES + masks
    raise FundamentalsTrainingError(f"unknown candidate kind: {kind}")


def _raw_feature(record: Mapping[str, Any], name: str) -> Optional[float]:
    if name in record.get("signal", {}):
        return _number(record["signal"].get(name))
    if name in record.get("fundamentals", {}):
        return _number(record["fundamentals"].get(name))
    return _number((record.get("fundamentalMasks") or {}).get(name))


def _prepare(records: Sequence[Mapping[str, Any]], feature_names: Sequence[str]) -> Dict[str, Any]:
    impute: Dict[str, float] = {}
    means: Dict[str, float] = {}
    scales: Dict[str, float] = {}
    for name in feature_names:
        observed = [value for value in (_raw_feature(row, name) for row in records) if value is not None]
        imputed = sum(observed) / len(observed) if observed else 0.0
        impute[name] = imputed
        values = [(_raw_feature(row, name) if _raw_feature(row, name) is not None else imputed) for row in records]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / max(len(values) - 1, 1)
        means[name] = mean
        scales[name] = math.sqrt(variance) or 1.0
    return {"impute": impute, "means": means, "scales": scales}


def _sigmoid(value: float) -> float:
    if value >= 35:
        return 1.0
    if value <= -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def _fit(records: Sequence[Mapping[str, Any]], kind: str, l2: float, balanced: bool) -> Dict[str, Any]:
    names = _feature_names(kind)
    preparation = _prepare(records, names)
    targets = [int(row["targetHomeWon"]) for row in records]
    positives = sum(targets)
    negatives = len(targets) - positives
    if positives <= 0 or negatives <= 0:
        raise FundamentalsTrainingError("one-class training target")
    positive_weight = len(targets) / (2 * positives) if balanced else 1.0
    negative_weight = len(targets) / (2 * negatives) if balanced else 1.0
    weights = {name: 0.0 for name in names}
    bias = math.log((positives + 1.0) / (negatives + 1.0))
    epochs = 1400
    learning_rate = 0.028
    for epoch in range(epochs):
        grad_bias = 0.0
        gradient = {name: 0.0 for name in names}
        for row in records:
            z = bias
            normalized: Dict[str, float] = {}
            for name in names:
                raw = _raw_feature(row, name)
                value = preparation["impute"][name] if raw is None else raw
                normalized[name] = (value - preparation["means"][name]) / preparation["scales"][name]
                z += weights[name] * normalized[name]
            target = int(row["targetHomeWon"])
            sample_weight = positive_weight if target else negative_weight
            error = (_sigmoid(z) - target) * sample_weight
            grad_bias += error
            for name in names:
                gradient[name] += error * normalized[name]
        rate = learning_rate / (1.0 + epoch / 700.0)
        bias -= rate * grad_bias / len(records)
        for name in names:
            weights[name] -= rate * (gradient[name] / len(records) + l2 * weights[name])
    return {
        "version": "MLB-RECOVERY-PORTABLE-LOGISTIC-v2",
        "kind": kind,
        "features": list(names),
        "bias": bias,
        "weights": weights,
        **preparation,
        "training": {
            "epochs": epochs,
            "learningRate": learning_rate,
            "l2": l2,
            "classBalanced": balanced,
            "rowCount": len(records),
        },
    }


def _score(record: Mapping[str, Any], model: Mapping[str, Any]) -> float:
    z = float(model.get("bias") or 0.0)
    for name in model.get("features") or []:
        raw = _raw_feature(record, name)
        value = float((model.get("impute") or {}).get(name) or 0.0) if raw is None else raw
        mean = float((model.get("means") or {}).get(name) or 0.0)
        scale = float((model.get("scales") or {}).get(name) or 1.0) or 1.0
        z += float((model.get("weights") or {}).get(name) or 0.0) * ((value - mean) / scale)
    return _sigmoid(z)


def _wilson_lower(correct: int, count: int, z: float = 1.0) -> float:
    if count <= 0:
        return 0.0
    p = correct / count
    return (p + z*z/(2*count) - z*math.sqrt(p*(1-p)/count + z*z/(4*count*count))) / (1 + z*z/count)


def _metrics(records: Sequence[Mapping[str, Any]], model: Mapping[str, Any], threshold: float) -> Dict[str, Any]:
    rows = []
    for record in records:
        probability = _score(record, model)
        prediction = 1 if probability >= 0.5 else 0
        rows.append({
            "probability": probability,
            "prediction": prediction,
            "target": int(record["targetHomeWon"]),
            "selected": max(probability, 1 - probability) >= threshold,
        })
    selected = [row for row in rows if row["selected"]]
    all_correct = sum(row["prediction"] == row["target"] for row in rows)
    selected_correct = sum(row["prediction"] == row["target"] for row in selected)
    brier = sum((row["probability"] - row["target"]) ** 2 for row in rows) / len(rows)
    return {
        "rowCount": len(rows),
        "allCorrect": all_correct,
        "allAccuracyPct": round(100 * all_correct / len(rows), 2),
        "brierScore": round(brier, 6),
        "selectedThreshold": threshold,
        "selectedCount": len(selected),
        "selectedCorrect": selected_correct,
        "selectedWrong": len(selected) - selected_correct,
        "selectedAccuracyPct": round(100 * selected_correct / len(selected), 2) if selected else None,
        "selectedCoveragePct": round(100 * len(selected) / len(rows), 2),
        "selectedWilsonLower68Pct": round(100 * _wilson_lower(selected_correct, len(selected)), 2),
    }


def _best_threshold(validation: Sequence[Mapping[str, Any]], model: Mapping[str, Any]) -> Dict[str, Any]:
    candidates = []
    for step in range(50, 76):
        threshold = step / 100
        metrics = _metrics(validation, model, threshold)
        if metrics["selectedCount"] < 10:
            continue
        metrics["targetMet"] = float(metrics.get("selectedAccuracyPct") or -1) >= TARGET_SELECTED_ACCURACY_PCT
        candidates.append(metrics)
    if not candidates:
        raise FundamentalsTrainingError("candidate has no validation threshold with ten selections")
    target = [row for row in candidates if row["targetMet"]]
    pool = target or candidates
    return max(pool, key=lambda row: (
        row["targetMet"],
        float(row.get("selectedWilsonLower68Pct") or 0),
        float(row.get("selectedAccuracyPct") or 0),
        int(row.get("selectedCount") or 0),
    ))


def _candidate(train: Sequence[Mapping[str, Any]], validation: Sequence[Mapping[str, Any]], kind: str, l2: float, balanced: bool) -> Dict[str, Any]:
    model = _fit(train, kind, l2, balanced)
    threshold = _best_threshold(validation, model)
    return {
        "kind": kind,
        "l2": l2,
        "classBalanced": balanced,
        "model": model,
        "threshold": threshold["selectedThreshold"],
        "validationMetrics": threshold,
        "trainMetrics": _metrics(train, model, threshold["selectedThreshold"]),
    }


def _select(candidates: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return dict(max(candidates, key=lambda row: (
        bool((row.get("validationMetrics") or {}).get("targetMet")),
        float((row.get("validationMetrics") or {}).get("selectedWilsonLower68Pct") or 0),
        float((row.get("validationMetrics") or {}).get("selectedAccuracyPct") or 0),
        int((row.get("validationMetrics") or {}).get("selectedCount") or 0),
        float((row.get("validationMetrics") or {}).get("allAccuracyPct") or 0),
    )))


def _put_write_once(s3: Any, bucket: str, key: str, payload: bytes, digest: str) -> Dict[str, Any]:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if str((exc.response.get("Error") or {}).get("Code")) not in {"404", "NoSuchKey", "NotFound"}:
            raise
        head = None
    if head:
        if str((head.get("Metadata") or {}).get("artifact-digest") or "") != digest:
            raise FundamentalsTrainingError("S3 recovery artifact collision")
        return {"created": False, "bucket": bucket, "key": key}
    result = s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload,
        ContentType="application/json",
        ServerSideEncryption="AES256",
        Metadata={
            "schema-version": VERSION,
            "artifact-digest": digest,
            "shadow-only": "true",
            "production-authority": "false",
        },
    )
    return {"created": True, "bucket": bucket, "key": key, "versionId": result.get("VersionId")}


def train(replay: Mapping[str, Any]) -> Dict[str, Any]:
    records = [_record(row) for row in (replay.get("rows") or []) if isinstance(row, dict)]
    records.sort(key=lambda row: (row["commenceTime"], row["gamePk"]))
    if len(records) < 250:
        raise FundamentalsTrainingError("at least 250 enriched rows are required")
    train_end = int(len(records) * 0.60)
    validation_end = int(len(records) * 0.80)
    train_rows = records[:train_end]
    validation_rows = records[train_end:validation_end]
    test_rows = records[validation_end:]
    candidates = []
    for kind in ("signal_only", "signal_plus_fundamentals"):
        for l2 in (0.01, 0.03, 0.10):
            for balanced in (False, True):
                candidates.append(_candidate(train_rows, validation_rows, kind, l2, balanced))
    selected = _select(candidates)
    threshold = float(selected["threshold"])
    research_test = _metrics(test_rows, selected["model"], threshold)
    final_model = _fit(records, selected["kind"], float(selected["l2"]), bool(selected["classBalanced"]))
    backfill = replay.get("standardFundamentalsBackfill") or {}
    artifact: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "recordType": "mlb_recovery_fundamentals_shadow_seed",
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "shadowOnly": True,
        "productionAuthority": False,
        "officialPickOverrideAllowed": False,
        "prospectivePromotionRequired": True,
        "model": final_model,
        "noPlayGate": {
            "threshold": threshold,
            "targetSelectedAccuracyPct": TARGET_SELECTED_ACCURACY_PCT,
            "appliesTo": "playable_subset_only",
        },
        "candidateSelection": {
            "protocol": "chronological_60_train_20_validation_then_single_research_test",
            "selectedKind": selected["kind"],
            "selectedL2": selected["l2"],
            "selectedClassBalanced": selected["classBalanced"],
            "trainCount": len(train_rows),
            "validationCount": len(validation_rows),
            "researchTestCount": len(test_rows),
            "selectedTrainMetrics": selected["trainMetrics"],
            "selectedValidationMetrics": selected["validationMetrics"],
            "selectedResearchTestMetrics": research_test,
            "candidateSummaries": [
                {
                    "kind": row["kind"],
                    "l2": row["l2"],
                    "classBalanced": row["classBalanced"],
                    "threshold": row["threshold"],
                    "trainMetrics": row["trainMetrics"],
                    "validationMetrics": row["validationMetrics"],
                }
                for row in candidates
            ],
            "researchTestIsNotPromotionEvidence": True,
        },
        "fundamentalsEvidence": {
            "backfill": backfill,
            "historicalProbablePitcherIdentityMayReflectPostgameScheduleResolution": True,
            "validForPromotionEvidence": False,
            "prospectiveTMinus45CaptureRequired": True,
        },
        "replayEvidence": {
            "version": replay.get("version"),
            "range": replay.get("range"),
            "rowCount": len(records),
            "fingerprint": _fingerprint(replay),
        },
        "prospectiveReviewGate": {
            "minimumTotalRows": 250,
            "minimumSelectedRows": 100,
            "minimumSelectedAccuracyPct": 80.0,
            "minimumWilsonLower95Pct": 70.0,
            "mustBeatMarketBaseline": True,
            "brierMustNotBeWorseThanMarket": True,
            "automaticPromotionEnabled": False,
            "manualReviewRequired": True,
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
            raise FundamentalsTrainingError("--bucket is required with --upload")
        digest = artifact["artifactDigest"]
        key = f"mlb/recovery-shadow/v1/fundamentals-candidates/{digest}.json"
        payload = output.read_bytes()
        s3 = boto3.client("s3")
        upload = _put_write_once(s3, args.bucket, key, payload, digest)
        pointer = {
            "version": "MLB-RECOVERY-FUNDAMENTALS-LATEST-v1",
            "updatedAtUtc": artifact["createdAtUtc"],
            "artifactDigest": digest,
            "artifactKey": key,
            "shadowOnly": True,
            "productionAuthority": False,
            "prospectivePromotionRequired": True,
        }
        s3.put_object(
            Bucket=args.bucket,
            Key="mlb/recovery-shadow/v1/fundamentals-latest.json",
            Body=(json.dumps(pointer, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256",
            Metadata={
                "schema-version": pointer["version"],
                "artifact-digest": digest,
                "shadow-only": "true",
                "production-authority": "false",
            },
        )
    print(json.dumps({
        "ok": True,
        "artifactDigest": artifact["artifactDigest"],
        "selectedKind": artifact["candidateSelection"]["selectedKind"],
        "selectedValidationMetrics": artifact["candidateSelection"]["selectedValidationMetrics"],
        "selectedResearchTestMetrics": artifact["candidateSelection"]["selectedResearchTestMetrics"],
        "productionAuthority": False,
        "upload": upload,
        "output": str(output),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
