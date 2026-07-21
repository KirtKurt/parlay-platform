from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import mlb_ml_walk_forward_v2 as walk_forward

VERSION = "MLB-ML-DUAL-MODEL-v2-persisted-cutover-prospective-fundamentals-v2"
OUTCOME_MODEL_VERSION = "MLB-OUTCOME-MODEL-v3-prospective-fundamentals-v2"
RELIABILITY_MODEL_VERSION = "MLB-RELIABILITY-MODEL-v2-prospective-selected-pick"
VALIDATION_PROTOCOL = "fixed_whole_slate_300_100_next_100_prospective"
MAX_MODEL_FEATURES = 10
FUNDAMENTALS_VERSION = "MLB-FUNDAMENTALS-SNAPSHOT-v2-immutable-source-provenance"

OUTCOME_FEATURES = [
    "homeMarketDeVigProbability",
    "deltaGapHome",
    "bookAgreementGapHome",
    "reversalGapHome",
    "homeAwayVelocityPpHr60mDiff",
    "starterCompositeGapHome",
    "bullpenCompositeGapHome",
    "lineupWrcPlusGapHome",
    "fundamentalPitchingMissing",
    "fundamentalOffenseLineupMissing",
]
RELIABILITY_FEATURES = [
    "selectedMarketDeVigProbability",
    "selectedScore",
    "selectedDelta",
    "selectedBookDivergence",
    "selectedReversalCountFull",
    "selectedCoverageRatioFull",
    "selectedVolatilityPpPerPull180m",
    "selectedHome",
    "fundamentalPitchingMissing",
    "fundamentalOffenseLineupMissing",
]

if len(OUTCOME_FEATURES) > MAX_MODEL_FEATURES or len(RELIABILITY_FEATURES) > MAX_MODEL_FEATURES:
    raise RuntimeError("MLB shadow feature list exceeds the lock-safe small-sample dimensionality limit")
if len(set(OUTCOME_FEATURES)) != len(OUTCOME_FEATURES) or len(set(RELIABILITY_FEATURES)) != len(RELIABILITY_FEATURES):
    raise RuntimeError("MLB shadow feature lists contain duplicate fields")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "": return default
        return float(value)
    except Exception: return default


def _optional_f(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _nested(mapping: Dict[str, Any], *path: str) -> Any:
    value: Any = mapping
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _difference(home: Any, away: Any) -> Optional[float]:
    home_value = _optional_f(home)
    away_value = _optional_f(away)
    if home_value is None or away_value is None:
        return None
    return home_value - away_value


def _group_missing(snapshot: Dict[str, Any], group_name: str, required_values: Sequence[str]) -> float:
    group = _nested(snapshot, "groups", group_name)
    if not isinstance(group, dict):
        return 1.0
    if str(group.get("status") or "").upper() not in {"CONNECTED", "PARTIAL"}:
        return 1.0
    values = group.get("values") or {}
    return 0.0 if all(_optional_f(values.get(name)) is not None for name in required_values) else 1.0


def _strict_features(item: Dict[str, Any], vector: Dict[str, Any]) -> Dict[str, Any]:
    """Read only exact immutable V2 values; never reconstruct or zero-fill."""
    base = dict(vector.get("features") or {})
    snapshot = item.get("fundamentalsSnapshotV2") or vector.get("fundamentalsSnapshotV2") or {}
    if not isinstance(snapshot, dict) or snapshot.get("version") != FUNDAMENTALS_VERSION:
        snapshot = {}

    home_market = _optional_f(
        item.get("homeMarketDeVigProbability", vector.get("homeMarketDeVigProbability"))
    )
    away_market = _optional_f(
        item.get("awayMarketDeVigProbability", vector.get("awayMarketDeVigProbability"))
    )
    selected_side = str(item.get("predictedSide") or vector.get("predictedSide") or "").lower()
    selected_market = home_market if selected_side == "home" else away_market if selected_side == "away" else None

    starter_values = _nested(snapshot, "groups", "starter_quality", "values") or {}
    bullpen_values = _nested(snapshot, "groups", "bullpen_availability", "values") or {}
    lineup_values = _nested(snapshot, "groups", "confirmed_lineups", "values") or {}
    starter_gap = _difference(starter_values.get("homeComposite"), starter_values.get("awayComposite"))
    bullpen_gap = _difference(bullpen_values.get("homeComposite"), bullpen_values.get("awayComposite"))
    lineup_gap = _difference(lineup_values.get("homeWrcPlus"), lineup_values.get("awayWrcPlus"))

    pitching_missing = max(
        _group_missing(snapshot, "starter_quality", ("homeComposite", "awayComposite")),
        _group_missing(snapshot, "bullpen_availability", ("homeComposite", "awayComposite")),
    )
    lineup_missing = _group_missing(snapshot, "confirmed_lineups", ("homeWrcPlus", "awayWrcPlus"))
    base.update({
        "homeMarketDeVigProbability": home_market,
        "selectedMarketDeVigProbability": selected_market,
        "starterCompositeGapHome": starter_gap,
        "bullpenCompositeGapHome": bullpen_gap,
        "lineupWrcPlusGapHome": lineup_gap,
        "fundamentalPitchingMissing": pitching_missing,
        "fundamentalOffenseLineupMissing": lineup_missing,
    })
    return base


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _sigmoid(value: float) -> float:
    if value >= 35: return 1.0
    if value <= -35: return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def _american_decimal(value: Any) -> Optional[float]:
    price = _f(value, 0.0)
    if price == 0.0: return None
    return 1.0 + (100.0 / abs(price)) if price < 0 else 1.0 + (price / 100.0)


def records_from_clean_rows(clean_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Join final labels to immutable pregame features without changing the vector."""
    records: List[Dict[str, Any]] = []
    for item in clean_rows or []:
        snapshot = item.get("featureSnapshot") or {}
        fundamentals_v2 = item.get("fundamentalsSnapshotV2") or snapshot.get("fundamentalsSnapshotV2") or {}
        features = _strict_features(item, snapshot)
        winner = item.get("winner")
        home = item.get("homeTeam")
        away = item.get("awayTeam")
        correct = item.get("correct")
        if not isinstance(features, dict) or not features:
            continue
        if not isinstance(fundamentals_v2, dict) or fundamentals_v2.get("version") != FUNDAMENTALS_VERSION:
            continue
        if correct not in {True, False} or not winner or not home or not away:
            continue
        if _norm(winner) not in {_norm(home), _norm(away)}:
            continue
        if any(name not in features for name in set(OUTCOME_FEATURES + RELIABILITY_FEATURES)):
            continue
        home_market = _optional_f(features.get("homeMarketDeVigProbability"))
        away_market = _optional_f(
            item.get("awayMarketDeVigProbability", snapshot.get("awayMarketDeVigProbability"))
        )
        if home_market is None or away_market is None or abs(home_market + away_market - 1.0) > 1e-6:
            continue
        market_source_at = item.get("marketProbabilitySourceAtUtc") or snapshot.get("marketProbabilitySourceAtUtc")
        market_version = item.get("marketProbabilityVersion") or snapshot.get("marketProbabilityVersion")
        market_fingerprint = item.get("marketProbabilityFingerprint") or snapshot.get("marketProbabilityFingerprint")
        if not market_source_at or not market_version or not market_fingerprint:
            continue
        home_won = 1 if _norm(winner) == _norm(home) else 0
        record = {
            "gameId": item.get("gameId"), "slateDateEt": item.get("slateDateEt"),
            "commenceTime": item.get("commenceTime"), "homeTeam": home, "awayTeam": away,
            "predictedSide": item.get("predictedSide"), "homeWon": home_won,
            "pickCorrect": 1 if correct is True else 0,
            "marketHomeProbability": home_market,
            "marketAwayProbability": away_market,
            "marketProbabilitySourceAtUtc": market_source_at,
            "marketProbabilityVersion": market_version,
            "marketProbabilityFingerprint": market_fingerprint,
            "lockedAmericanOdds": item.get("lockedAmericanOdds"),
            "featureFingerprint": snapshot.get("fingerprint"),
            "featureSourcePullAtUtc": snapshot.get("sourcePullAtUtc"),
            "featureLockAtUtc": snapshot.get("lockAtUtc"),
            "fundamentalsSnapshotVersion": fundamentals_v2.get("version"),
            "fundamentalsSnapshotFingerprint": fundamentals_v2.get("fingerprint"),
            "fundamentalsSnapshotRef": (
                item.get("fundamentalsSnapshotRefV2")
                or item.get("fundamentalsSnapshotV2Ref")
                or snapshot.get("fundamentalsSnapshotRefV2")
                or item.get("snapshotRef")
                or snapshot.get("fundamentalsSnapshotV2Ref")
                or snapshot.get("snapshotRef")
                or fundamentals_v2.get("snapshotRef")
            ),
            "labelSource": "final_settlement_join_not_pregame_feature_vector",
        }
        for feature in sorted(set(OUTCOME_FEATURES + RELIABILITY_FEATURES)):
            record[feature] = _optional_f(features.get(feature))
        records.append(record)
    return sorted(records, key=lambda row: (str(row.get("slateDateEt") or ""), str(row.get("commenceTime") or "")))


def record_from_unlabeled_lock(item: Dict[str, Any]) -> Dict[str, Any]:
    """Transform one immutable T-45 lock without reading an outcome."""
    snapshot = item.get("featureSnapshot") or item.get("frozenFeatureVector") or {}
    fundamentals_v2 = (
        item.get("fundamentalsSnapshotV2")
        or snapshot.get("fundamentalsSnapshotV2")
        or {}
    )
    features = _strict_features(item, snapshot)
    if (
        not isinstance(fundamentals_v2, dict)
        or fundamentals_v2.get("version") != FUNDAMENTALS_VERSION
    ):
        raise ValueError("immutable fundamentals V2 snapshot is required")
    missing = sorted(
        name
        for name in set(OUTCOME_FEATURES + RELIABILITY_FEATURES)
        if name not in features
    )
    if missing:
        raise ValueError("prespecified frozen features missing: " + ",".join(missing))
    home_market = _optional_f(features.get("homeMarketDeVigProbability"))
    away_market = _optional_f(
        item.get(
            "awayMarketDeVigProbability",
            snapshot.get("awayMarketDeVigProbability"),
        )
    )
    if (
        home_market is None
        or away_market is None
        or abs(home_market + away_market - 1.0) > 1e-6
    ):
        raise ValueError("same-time de-vigged market probability pair is required")
    record = {
        "gameId": item.get("gameId"),
        "slateDateEt": item.get("slateDateEt"),
        "commenceTime": item.get("commenceTime"),
        "homeTeam": item.get("homeTeam"),
        "awayTeam": item.get("awayTeam"),
        "predictedSide": item.get("predictedSide"),
        "marketHomeProbability": home_market,
        "marketAwayProbability": away_market,
        "lockedAmericanOdds": item.get("lockedAmericanOdds"),
        "featureFingerprint": snapshot.get("fingerprint"),
    }
    for feature in sorted(set(OUTCOME_FEATURES + RELIABILITY_FEATURES)):
        record[feature] = _optional_f(features.get(feature))
    return record


def _standardize(records: Sequence[Dict[str, Any]], features: Sequence[str]) -> Tuple[Dict[str, float], Dict[str, float], List[str]]:
    means: Dict[str, float] = {}; scales: Dict[str, float] = {}; empty: List[str] = []
    for feature in features:
        values = [value for value in (_optional_f(record.get(feature)) for record in records) if value is not None]
        if not values:
            empty.append(feature)
            means[feature] = 0.0
            scales[feature] = 1.0
            continue
        mean = sum(values) / len(values) if values else 0.0
        variance = sum((value - mean) ** 2 for value in values) / max(1, len(values) - 1)
        means[feature] = mean; scales[feature] = math.sqrt(variance) or 1.0
    return means, scales, empty


def fit_logistic(records: Sequence[Dict[str, Any]], features: Sequence[str], target: str, version: str,
                 epochs: int = 320, learning_rate: float = 0.035, l2: float = 0.02) -> Dict[str, Any]:
    rows = list(records or [])
    if not rows: return {"ok": False, "version": version, "reason": "no_training_rows"}
    positives = sum(int(row.get(target) or 0) for row in rows); negatives = len(rows) - positives
    if not positives or not negatives:
        return {"ok": False, "version": version, "reason": "target_has_single_class", "rowCount": len(rows), "positives": positives, "negatives": negatives}
    means, scales, empty_features = _standardize(rows, features)
    if empty_features:
        return {
            "ok": False,
            "version": version,
            "reason": "prespecified_feature_has_no_observed_training_values",
            "emptyFeatures": empty_features,
            "rowCount": len(rows),
        }
    weights = {feature: 0.0 for feature in features}
    bias = math.log((positives + 1.0) / (negatives + 1.0))
    for epoch in range(epochs):
        grad_bias = 0.0; grad = {feature: 0.0 for feature in features}
        for row in rows:
            z = bias; normalized: Dict[str, float] = {}
            for feature in features:
                raw = _optional_f(row.get(feature))
                # Missing V2 numeric inputs are mean-imputed only after the
                # corresponding immutable group-missing mask has been frozen.
                value = ((raw if raw is not None else means[feature]) - means[feature]) / scales[feature]
                normalized[feature] = value; z += weights[feature] * value
            probability = _sigmoid(z); error = probability - int(row.get(target) or 0); grad_bias += error
            for feature in features: grad[feature] += error * normalized[feature]
        rate = learning_rate / (1.0 + epoch / 500.0); bias -= rate * grad_bias / len(rows)
        for feature in features: weights[feature] -= rate * (grad[feature] / len(rows) + l2 * weights[feature])
    return {"ok": True, "version": version, "rowCount": len(rows), "positiveCount": positives,
            "negativeCount": negatives, "target": target, "features": list(features), "bias": bias,
            "weights": weights, "means": means, "scales": scales,
            "training": {"epochs": epochs, "learningRate": learning_rate, "l2": l2},
            "validationProtocol": VALIDATION_PROTOCOL,
            "missingValuePolicy": "train_mean_imputation_with_prespecified_frozen_missingness_masks",
            "featureLabelPolicy": "immutable_pregame_features_plus_final_settlement_labels"}


def score(record: Dict[str, Any], model: Dict[str, Any]) -> float:
    if not model or not model.get("ok"): return 0.5
    z = _f(model.get("bias")); means = model.get("means") or {}; scales = model.get("scales") or {}; weights = model.get("weights") or {}
    for feature in model.get("features") or []:
        scale = _f(scales.get(feature), 1.0) or 1.0
        raw = _optional_f(record.get(feature))
        mean = _f(means.get(feature))
        value = raw if raw is not None else mean
        z += _f(weights.get(feature)) * ((value - mean) / scale)
    return _sigmoid(z)


def _selected_reliability_test(rows: Sequence[Dict[str, Any]], threshold: float) -> Dict[str, Any]:
    selected = [row for row in rows if walk_forward.clip_probability(row.get("reliabilityProbability")) >= threshold]
    exact_odds_rows = []
    for row in selected:
        decimal = _american_decimal(row.get("lockedAmericanOdds"))
        if decimal is None: continue
        exact_odds_rows.append(row)
    exact_coverage = round(len(exact_odds_rows) / len(selected) * 100.0, 2) if selected else 0.0
    return {"count": len(selected), "correct": sum(int(row.get("pickCorrect") or 0) for row in selected),
            "coveragePct": round(len(selected) / len(rows) * 100.0, 2) if rows else 0.0,
            "accuracyPct": walk_forward.accuracy(selected, "reliabilityProbability", "pickCorrect", threshold=threshold),
            "brierScore": walk_forward.brier(selected, "reliabilityProbability", "pickCorrect"),
            "logLoss": walk_forward.log_loss(selected, "reliabilityProbability", "pickCorrect"),
            "calibrationError": walk_forward.calibration_error(selected, "reliabilityProbability", "pickCorrect"),
            "threshold": threshold, "pricedCount": len(exact_odds_rows),
            "unpricedCount": len(selected) - len(exact_odds_rows),
            "priceCoveragePct": exact_coverage, "exactOddsCoveragePct": exact_coverage}


def _data_quality(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    completeness = [_f(row.get("fundamentalsCompleteness"), 0.0) for row in records]
    priced = sum(_american_decimal(row.get("lockedAmericanOdds")) is not None for row in records)
    return {"recordCount": len(records),
            "averageFundamentalsCompletenessPct": round(sum(completeness) / len(completeness) * 100.0, 2) if completeness else 0.0,
            "rowsWithAtLeast75PctFundamentals": sum(value >= 0.75 for value in completeness),
            "priceCoveragePct": round(priced / len(records) * 100.0, 2) if records else 0.0,
            "modelScope": "FULL_BASEBALL_CONTEXT" if completeness and sum(value >= 0.75 for value in completeness) / len(completeness) >= 0.90 else "MARKET_MOVEMENT_ONLY_WITH_MISSINGNESS"}


def train(clean_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    raise RuntimeError(
        "dynamic V2 training is disabled; use train_fixed_partitions with an "
        "immutable MLB-ML-EXPERIMENT-v2 manifest"
    )


def _partition_records(
    partition_rows: Dict[str, Sequence[Dict[str, Any]]],
    experiment_manifest: Dict[str, Any],
    names: Sequence[str],
) -> Tuple[Optional[Dict[str, List[Dict[str, Any]]]], Optional[Dict[str, Any]]]:
    manifest_partitions = experiment_manifest.get("partitions") or {}
    minimums = {"train": 300, "validation": 100, "prospectiveTest": 100}
    records_by_partition: Dict[str, List[Dict[str, Any]]] = {}
    for name in names:
        source = list(partition_rows.get(name) or [])
        records = records_from_clean_rows(source)
        records_by_partition[name] = records
        declared = int((manifest_partitions.get(name) or {}).get("rowCount") or 0)
        if len(source) != declared or len(records) != declared:
            return None, {
                "ok": False,
                "version": VERSION,
                "status": "TRAINING_BLOCKED",
                "reason": "partition_row_or_v2_schema_mismatch",
                "partition": name,
                "manifestCount": declared,
                "sourceCount": len(source),
                "strictV2RecordCount": len(records),
            }
        if len(records) < minimums[name]:
            return None, {
                "ok": False,
                "version": VERSION,
                "status": "ACCUMULATING_FIXED_PROSPECTIVE_EXPERIMENT",
                "reason": "partition_minimum_not_met",
                "partition": name,
                "actual": len(records),
                "required": minimums[name],
            }
    partition_dates = {
        name: {str(row.get("slateDateEt") or "") for row in rows}
        for name, rows in records_by_partition.items()
    }
    ordered = list(names)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            overlap = partition_dates[left] & partition_dates[right]
            if overlap:
                return None, {
                    "ok": False,
                    "version": VERSION,
                    "status": "TRAINING_BLOCKED",
                    "reason": "slate_date_crosses_partitions",
                    "dates": sorted(overlap),
                }
    return records_by_partition, None


def fit_frozen_challenger(
    partition_rows: Dict[str, Sequence[Dict[str, Any]]],
    experiment_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """Fit once from frozen train and validation partitions, before test labels."""
    manifest_partitions = experiment_manifest.get("partitions") or {}
    if any(
        (manifest_partitions.get(name) or {}).get("frozen") is not True
        for name in ("train", "validation")
    ):
        return {
            "ok": False,
            "version": VERSION,
            "status": "ACCUMULATING_TRAIN_OR_VALIDATION",
        }
    if int((manifest_partitions.get("prospectiveTest") or {}).get("rowCount") or 0):
        return {
            "ok": False,
            "version": VERSION,
            "status": "TRAINING_BLOCKED",
            "reason": "prospective_rows_exist_before_challenger_cutover",
        }
    records, failure = _partition_records(
        partition_rows, experiment_manifest, ("train", "validation")
    )
    if failure:
        return failure
    assert records is not None
    train_rows = records["train"]
    validation_rows = records["validation"]
    outcome_model = fit_logistic(
        train_rows, OUTCOME_FEATURES, "homeWon", OUTCOME_MODEL_VERSION
    )
    reliability_model = fit_logistic(
        train_rows, RELIABILITY_FEATURES, "pickCorrect", RELIABILITY_MODEL_VERSION
    )
    if not outcome_model.get("ok") or not reliability_model.get("ok"):
        return {
            "ok": False,
            "version": VERSION,
            "status": "TRAINING_BLOCKED",
            "outcomeModel": outcome_model,
            "reliabilityModel": reliability_model,
        }
    validation_scored = [
        {
            **row,
            "outcomeProbability": score(row, outcome_model),
            "reliabilityProbability": score(row, reliability_model),
        }
        for row in validation_rows
    ]
    threshold = walk_forward.select_reliability_threshold(
        validation_scored,
        minimum_selected=min(30, len(validation_scored)),
        minimum_coverage=0.10,
    )
    if threshold.get("ok") is not True:
        return {
            "ok": False,
            "version": VERSION,
            "status": "TRAINING_BLOCKED",
            "reason": "validation_reliability_threshold_unavailable",
            "thresholdSelection": threshold,
        }
    reliability_model["selectedThreshold"] = dict(threshold)
    reliability_model["thresholdSelectedOnValidationOnly"] = True
    outcome_model["directionThreshold"] = 0.5
    return {
        "ok": True,
        "version": VERSION,
        "status": "FROZEN_CHALLENGER_READY_FOR_DURABLE_CUTOVER",
        "experimentId": experiment_manifest.get("experimentId"),
        "experimentManifestDigestAtFit": experiment_manifest.get("manifestDigest"),
        "featureSchemaFingerprint": experiment_manifest.get("featureSchemaFingerprint"),
        "fundamentalsSnapshotVersion": FUNDAMENTALS_VERSION,
        "features": {
            "outcome": list(OUTCOME_FEATURES),
            "reliability": list(RELIABILITY_FEATURES),
            "maximumPerModel": MAX_MODEL_FEATURES,
        },
        "partitionProof": {
            "trainRowCount": len(train_rows),
            "validationRowCount": len(validation_rows),
            "prospectiveRowsUsedForFitOrThreshold": 0,
            "trainFingerprint": (manifest_partitions.get("train") or {}).get("partitionFingerprint"),
            "validationFingerprint": (manifest_partitions.get("validation") or {}).get("partitionFingerprint"),
        },
        "outcomeModel": outcome_model,
        "reliabilityModel": reliability_model,
        "validation": {
            "outcome": walk_forward.evaluate(
                validation_scored,
                "outcomeProbability",
                "homeWon",
                baseline_probability_key="marketHomeProbability",
            ),
            "reliability": walk_forward.evaluate(
                validation_scored, "reliabilityProbability", "pickCorrect"
            ),
            "selectedReliability": threshold,
        },
        "selectedThreshold": float(threshold["threshold"]),
        "thresholdSelectionSource": "validation_only_before_prospective_cutover",
        "automaticPromotionEnabled": False,
        "liveInferenceAuthority": False,
    }


def score_unlabeled_lock(row: Dict[str, Any], challenger: Dict[str, Any]) -> Dict[str, float]:
    if challenger.get("ok") is not True:
        raise ValueError("verified frozen challenger is required")
    record = record_from_unlabeled_lock(row)
    return {
        "outcomeProbability": score(record, challenger.get("outcomeModel") or {}),
        "reliabilityProbability": score(record, challenger.get("reliabilityModel") or {}),
    }


def evaluate_frozen_challenger(
    partition_rows: Dict[str, Sequence[Dict[str, Any]]],
    experiment_manifest: Dict[str, Any],
    challenger: Dict[str, Any],
) -> Dict[str, Any]:
    """Evaluate the persisted validation challenger on the sealed future test."""
    if experiment_manifest.get("prospectiveTestSealed") is not True:
        return {
            "ok": False,
            "version": VERSION,
            "status": "ACCUMULATING_GENUINELY_FUTURE_PROSPECTIVE_TEST",
        }
    bound = experiment_manifest.get("frozenChallenger") or {}
    proof = challenger.get("partitionProof") or {}
    manifest_partitions = experiment_manifest.get("partitions") or {}
    mismatches = []
    if challenger.get("experimentId") != experiment_manifest.get("experimentId"):
        mismatches.append("experiment_id")
    if challenger.get("featureSchemaFingerprint") != experiment_manifest.get("featureSchemaFingerprint"):
        mismatches.append("feature_schema")
    if proof.get("trainFingerprint") != (manifest_partitions.get("train") or {}).get("partitionFingerprint"):
        mismatches.append("train_partition")
    if proof.get("validationFingerprint") != (manifest_partitions.get("validation") or {}).get("partitionFingerprint"):
        mismatches.append("validation_partition")
    if float(challenger.get("selectedThreshold") or 0.0) != float(bound.get("selectedThreshold") or -1.0):
        mismatches.append("selected_threshold")
    if mismatches:
        return {
            "ok": False,
            "version": VERSION,
            "status": "TRAINING_BLOCKED",
            "reason": "persisted_challenger_manifest_mismatch",
            "mismatches": mismatches,
        }
    records, failure = _partition_records(
        {"prospectiveTest": partition_rows.get("prospectiveTest") or []},
        experiment_manifest,
        ("prospectiveTest",),
    )
    if failure:
        return failure
    assert records is not None
    prospective_rows = records["prospectiveTest"]
    scored = [
        {
            **row,
            "outcomeProbability": score(row, challenger["outcomeModel"]),
            "reliabilityProbability": score(row, challenger["reliabilityModel"]),
        }
        for row in prospective_rows
    ]
    threshold = float(challenger["selectedThreshold"])
    return {
        "ok": True,
        "version": VERSION,
        "status": "PERSISTED_CHALLENGER_PROSPECTIVE_TEST_EVALUATED",
        "experimentId": experiment_manifest.get("experimentId"),
        "experimentManifestDigest": experiment_manifest.get("manifestDigest"),
        "featureSchemaFingerprint": experiment_manifest.get("featureSchemaFingerprint"),
        "split": {
            "protocol": VALIDATION_PROTOCOL,
            "counts": {
                "train": int((manifest_partitions.get("train") or {}).get("rowCount") or 0),
                "validation": int((manifest_partitions.get("validation") or {}).get("rowCount") or 0),
                "prospectiveTest": len(prospective_rows),
            },
            "partitionFingerprints": {
                name: (manifest_partitions.get(name) or {}).get("partitionFingerprint")
                for name in ("train", "validation", "prospectiveTest")
            },
        },
        "outcomeModel": challenger["outcomeModel"],
        "reliabilityModel": challenger["reliabilityModel"],
        "validation": challenger["validation"],
        "prospectiveTest": {
            "sealedBeforeEvaluation": True,
            "outcome": walk_forward.evaluate(
                scored,
                "outcomeProbability",
                "homeWon",
                baseline_probability_key="marketHomeProbability",
            ),
            "reliability": walk_forward.evaluate(
                scored, "reliabilityProbability", "pickCorrect"
            ),
            "selectedReliability": _selected_reliability_test(scored, threshold),
        },
        "prospectiveSelectedRecommendationCount": 0,
        "testWasUntouchedDuringFitAndThresholdSelection": True,
        "persistedChallengerCutoverAtUtc": experiment_manifest.get("prospectiveCutoverAtUtc"),
        "fitOrThresholdSelectionUsedProspectiveRows": False,
        "automaticPromotionEnabled": False,
        "liveInferenceAuthority": False,
    }


def evaluate_selection_ledger(
    clean_rows: Iterable[Dict[str, Any]],
    entries: Iterable[Dict[str, Any]],
    *,
    challenger_artifact_digest: str,
    experiment_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """Join frozen pre-outcome decisions to later canonical labels by identity."""
    import mlb_ml_experiment_v2 as experiment

    source_rows = list(clean_rows or [])
    by_identity: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
    official_game_pks: Dict[str, str] = {}
    conflicts: List[Dict[str, Any]] = []
    for source in source_rows:
        identity = experiment.record_identity(source)
        official_pk = str(source.get("officialGamePk") or "")
        try:
            converted = records_from_clean_rows([source])
        except Exception:
            converted = []
        if not identity:
            conflicts.append(
                {"recordIdentity": "", "reason": "canonical_label_identity_missing"}
            )
            continue
        if len(converted) != 1:
            conflicts.append(
                {
                    "recordIdentity": identity,
                    "reason": "canonical_label_not_model_eligible",
                }
            )
            continue
        if not official_pk:
            conflicts.append(
                {
                    "recordIdentity": identity,
                    "reason": "canonical_label_official_game_pk_missing",
                }
            )
            continue
        if identity in by_identity:
            conflicts.append(
                {"recordIdentity": identity, "reason": "duplicate_canonical_label_identity"}
            )
            continue
        if official_pk in official_game_pks:
            conflicts.append(
                {
                    "recordIdentity": identity,
                    "reason": "duplicate_canonical_label_official_game_pk",
                    "officialGamePk": official_pk,
                }
            )
            continue
        official_game_pks[official_pk] = identity
        by_identity[identity] = (converted[0], source)
    joined: List[Dict[str, Any]] = []
    seen = set()
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            conflicts.append(
                {
                    "recordIdentity": "",
                    "reason": "invalid_selection_contract",
                    "errors": ["selection_schema_not_object"],
                }
            )
            continue
        identity = str(entry.get("recordIdentity") or "")
        if identity in seen:
            conflicts.append({"recordIdentity": identity, "reason": "duplicate_selection"})
            continue
        seen.add(identity)
        source_pair = by_identity.get(identity)
        source = source_pair[1] if source_pair else None
        contract_errors = experiment.selection_ledger_validation_errors(
            entry,
            experiment_manifest,
            row=source,
            challenger_artifact_digest=challenger_artifact_digest,
        )
        if source is not None:
            captured = experiment._parse_dt(entry.get("capturedAtUtc"))
            commence = experiment.game_commence_at(source)
            official_label = source.get("officialLabel") or {}
            if not isinstance(official_label, Mapping):
                official_label = {}
            label_observed = experiment._parse_dt(
                source.get("labelRetrievedAtUtc")
                or official_label.get("retrievedAtUtc")
            )
            if label_observed is None:
                contract_errors.append("canonical_label_observation_time_missing")
            elif commence is None or label_observed <= commence:
                contract_errors.append("canonical_label_not_observed_after_commence")
            elif captured is None or captured >= label_observed:
                contract_errors.append(
                    "selection_not_captured_before_label_observation"
                )
        if contract_errors:
            conflicts.append(
                {
                    "recordIdentity": identity,
                    "reason": "invalid_selection_contract",
                    "errors": sorted(set(contract_errors)),
                }
            )
            continue
        if not source_pair:
            # A valid pregame entry may not have a canonical FINAL label yet.
            continue
        if entry.get("selected") is True:
            joined.append({
                **source_pair[0],
                "reliabilityProbability": float(entry.get("reliabilityProbability")),
            })
    metrics = walk_forward.evaluate(joined, "reliabilityProbability", "pickCorrect")
    return {
        "ok": not conflicts,
        "settledSelectedRecommendationCount": len(joined),
        "metrics": metrics,
        "conflicts": conflicts,
        "authority": "immutable_pre_outcome_conditional_selection_ledger",
    }


def train_fixed_partitions(
    partition_rows: Dict[str, Sequence[Dict[str, Any]]],
    experiment_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """Train/evaluate the one immutable 300/100/next-100 experiment.

    Partition membership is supplied by ``mlb_ml_experiment_v2`` and is never
    recomputed here. The prospective set is not scored until its manifest is
    sealed, and the exact trained artifact is the artifact being evaluated.
    """
    manifest_partitions = experiment_manifest.get("partitions") or {}
    if not experiment_manifest.get("prospectiveTestSealed"):
        return {
            "ok": False,
            "version": VERSION,
            "status": "ACCUMULATING_FIXED_PROSPECTIVE_EXPERIMENT",
            "reason": "prospective_test_not_sealed",
        }

    expected_minimums = {"train": 300, "validation": 100, "prospectiveTest": 100}
    records_by_partition: Dict[str, List[Dict[str, Any]]] = {}
    source_counts: Dict[str, int] = {}
    for name, minimum in expected_minimums.items():
        source = list(partition_rows.get(name) or [])
        source_counts[name] = len(source)
        records = records_from_clean_rows(source)
        records_by_partition[name] = records
        declared = int((manifest_partitions.get(name) or {}).get("rowCount") or 0)
        if len(source) != declared or len(records) != declared:
            return {
                "ok": False,
                "version": VERSION,
                "status": "TRAINING_BLOCKED",
                "reason": "partition_row_or_v2_schema_mismatch",
                "partition": name,
                "manifestCount": declared,
                "sourceCount": len(source),
                "strictV2RecordCount": len(records),
            }
        if len(records) < minimum:
            return {
                "ok": False,
                "version": VERSION,
                "status": "ACCUMULATING_FIXED_PROSPECTIVE_EXPERIMENT",
                "reason": "partition_minimum_not_met",
                "partition": name,
                "actual": len(records),
                "required": minimum,
            }

    partition_dates = {
        name: {str(row.get("slateDateEt") or "") for row in rows}
        for name, rows in records_by_partition.items()
    }
    for index, left in enumerate(("train", "validation", "prospectiveTest")):
        for right in ("train", "validation", "prospectiveTest")[index + 1:]:
            if partition_dates[left] & partition_dates[right]:
                return {
                    "ok": False,
                    "version": VERSION,
                    "status": "TRAINING_BLOCKED",
                    "reason": "slate_date_crosses_partitions",
                    "dates": sorted(partition_dates[left] & partition_dates[right]),
                }

    train_rows = records_by_partition["train"]
    validation_rows = records_by_partition["validation"]
    prospective_rows = records_by_partition["prospectiveTest"]
    outcome_model = fit_logistic(train_rows, OUTCOME_FEATURES, "homeWon", OUTCOME_MODEL_VERSION)
    reliability_model = fit_logistic(train_rows, RELIABILITY_FEATURES, "pickCorrect", RELIABILITY_MODEL_VERSION)
    if not outcome_model.get("ok") or not reliability_model.get("ok"):
        return {
            "ok": False,
            "version": VERSION,
            "status": "TRAINING_BLOCKED",
            "outcomeModel": outcome_model,
            "reliabilityModel": reliability_model,
        }

    validation_scored = [
        {
            **row,
            "outcomeProbability": score(row, outcome_model),
            "reliabilityProbability": score(row, reliability_model),
        }
        for row in validation_rows
    ]
    threshold = walk_forward.select_reliability_threshold(
        validation_scored,
        minimum_selected=min(30, len(validation_scored)),
        minimum_coverage=0.10,
    )
    if threshold.get("ok") is not True:
        return {
            "ok": False,
            "version": VERSION,
            "status": "TRAINING_BLOCKED",
            "reason": "validation_reliability_threshold_unavailable",
            "thresholdSelection": threshold,
        }
    selected_threshold = float(threshold["threshold"])
    reliability_model["selectedThreshold"] = dict(threshold)
    reliability_model["thresholdSelectedOnValidationOnly"] = True
    outcome_model["directionThreshold"] = 0.5

    prospective_scored = [
        {
            **row,
            "outcomeProbability": score(row, outcome_model),
            "reliabilityProbability": score(row, reliability_model),
        }
        for row in prospective_rows
    ]
    outcome_validation = walk_forward.evaluate(
        validation_scored,
        "outcomeProbability",
        "homeWon",
        baseline_probability_key="marketHomeProbability",
    )
    outcome_prospective = walk_forward.evaluate(
        prospective_scored,
        "outcomeProbability",
        "homeWon",
        baseline_probability_key="marketHomeProbability",
    )
    reliability_validation = walk_forward.evaluate(
        validation_scored, "reliabilityProbability", "pickCorrect"
    )
    reliability_prospective = walk_forward.evaluate(
        prospective_scored, "reliabilityProbability", "pickCorrect"
    )
    selected_prospective = _selected_reliability_test(prospective_scored, selected_threshold)
    return {
        "ok": True,
        "version": VERSION,
        "status": "PROSPECTIVE_CHALLENGER_EVALUATED_AWAITING_PROMOTION_POLICY",
        "experimentId": experiment_manifest.get("experimentId"),
        "experimentManifestDigest": experiment_manifest.get("manifestDigest"),
        "featureSchemaFingerprint": experiment_manifest.get("featureSchemaFingerprint"),
        "fundamentalsSnapshotVersion": FUNDAMENTALS_VERSION,
        "features": {
            "outcome": list(OUTCOME_FEATURES),
            "reliability": list(RELIABILITY_FEATURES),
            "maximumPerModel": MAX_MODEL_FEATURES,
        },
        "split": {
            "protocol": VALIDATION_PROTOCOL,
            "counts": {name: len(rows) for name, rows in records_by_partition.items()},
            "slateDates": {name: sorted(dates) for name, dates in partition_dates.items()},
            "partitionFingerprints": {
                name: (manifest_partitions.get(name) or {}).get("partitionFingerprint")
                for name in expected_minimums
            },
        },
        "outcomeModel": outcome_model,
        "reliabilityModel": reliability_model,
        "validation": {
            "outcome": outcome_validation,
            "reliability": reliability_validation,
            "selectedReliability": threshold,
        },
        "prospectiveTest": {
            "sealedBeforeEvaluation": True,
            "outcome": outcome_prospective,
            "reliability": reliability_prospective,
            "selectedReliability": selected_prospective,
        },
        # Compatibility alias for read-only consumers while they migrate.
        "untouchedTest": {
            "outcome": outcome_prospective,
            "reliability": reliability_prospective,
            "selectedReliability": selected_prospective,
        },
        "prospectiveSelectedRecommendationCount": selected_prospective.get("count"),
        "testWasUntouchedDuringFitAndThresholdSelection": True,
        "featureLabelPolicy": "immutable_v2_pregame_features_plus_canonical_final_labels",
        "marketBaselinePolicy": "same_time_canonical_de_vigged_market_probability_frozen_before_lock",
    }
