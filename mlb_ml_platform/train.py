from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import (
    ExtraTreesClassifier,
    GradientBoostingClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

FEATURES: Tuple[str, ...] = (
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
)
LABEL = "homeWon"
TIME = "commenceTime"
EVENT_ID = "gameId"
RANDOM_SEED = 7609


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _clip(values: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(values, dtype=float), 1e-6, 1.0 - 1e-6)


def _ece(prob: Sequence[float], labels: Sequence[int], bins: int = 10) -> float:
    p = np.asarray(prob, dtype=float)
    y = np.asarray(labels, dtype=int)
    total = max(1, len(y))
    result = 0.0
    for idx in range(bins):
        lo, hi = idx / bins, (idx + 1) / bins
        mask = (p >= lo) & ((p < hi) if idx < bins - 1 else (p <= hi))
        if mask.any():
            conf = float(p[mask].mean())
            acc = float(y[mask].mean())
            result += float(mask.sum()) / total * abs(conf - acc)
    return float(result)


def metrics(prob: Sequence[float], labels: Sequence[int]) -> Dict[str, float | int]:
    p = _clip(np.asarray(prob, dtype=float))
    y = np.asarray(labels, dtype=int)
    return {
        "count": int(len(y)),
        "log_loss": float(log_loss(y, np.column_stack([1.0 - p, p]), labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "accuracy": float(accuracy_score(y, p >= 0.5)),
        "ece": _ece(p, y),
    }


def _bootstrap_skill_lower_bound(
    candidate: Sequence[float], baseline: Sequence[float], labels: Sequence[int], samples: int = 2000
) -> float:
    cp = _clip(np.asarray(candidate, dtype=float))
    bp = _clip(np.asarray(baseline, dtype=float))
    y = np.asarray(labels, dtype=int)
    if not len(y):
        return float("-inf")
    c_loss = -(y * np.log(cp) + (1 - y) * np.log(1 - cp))
    b_loss = -(y * np.log(bp) + (1 - y) * np.log(1 - bp))
    deltas = b_loss - c_loss
    rng = random.Random(RANDOM_SEED)
    values = []
    for _ in range(samples):
        values.append(sum(float(deltas[rng.randrange(len(deltas))]) for _ in range(len(deltas))) / len(deltas))
    values.sort()
    return float(values[max(0, min(len(values) - 1, int(0.05 * len(values))))])


def _read_input(path: str) -> pd.DataFrame:
    root = Path(path)
    files = sorted([*root.rglob("*.parquet"), *root.rglob("*.csv"), *root.rglob("*.jsonl")])
    if not files:
        raise RuntimeError(f"NO_TRAINING_FILES:{root}")
    frames: List[pd.DataFrame] = []
    for file in files:
        if file.suffix == ".parquet":
            frames.append(pd.read_parquet(file))
        elif file.suffix == ".csv":
            frames.append(pd.read_csv(file))
        else:
            frames.append(pd.read_json(file, lines=True))
    data = pd.concat(frames, ignore_index=True)
    missing = [name for name in (*FEATURES, LABEL, TIME, EVENT_ID) if name not in data.columns]
    if missing:
        raise RuntimeError("TRAINING_SCHEMA_MISSING:" + ",".join(missing))
    data[TIME] = pd.to_datetime(data[TIME], utc=True, errors="raise")
    data = data.sort_values([TIME, EVENT_ID]).drop_duplicates(EVENT_ID, keep="last")
    data = data[data[LABEL].isin([0, 1, True, False])].copy()
    data[LABEL] = data[LABEL].astype(int)
    for name in FEATURES:
        data[name] = pd.to_numeric(data[name], errors="coerce")
    # Missingness is permitted only for numeric feature values whose explicit
    # immutable missingness masks are included in the vector. Impute with the
    # training-only median later to prevent future information leakage.
    return data.reset_index(drop=True)


def _split(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if len(data) < 500:
        raise RuntimeError(f"INSUFFICIENT_ROWS:{len(data)}<500")
    train_end = int(len(data) * 0.60)
    validation_end = int(len(data) * 0.80)
    train = data.iloc[:train_end].copy()
    validation = data.iloc[train_end:validation_end].copy()
    audit = data.iloc[validation_end:].copy()
    if min(len(train), len(validation), len(audit)) <= 0:
        raise RuntimeError("EMPTY_CHRONOLOGICAL_PARTITION")
    if train[TIME].max() >= validation[TIME].min() or validation[TIME].max() >= audit[TIME].min():
        raise RuntimeError("CHRONOLOGY_VIOLATION")
    return train, validation, audit


def _impute(train: pd.DataFrame, *others: pd.DataFrame) -> tuple[pd.DataFrame, ...]:
    medians = train.loc[:, FEATURES].median(numeric_only=True)
    if medians.isna().any():
        bad = [name for name in FEATURES if pd.isna(medians.get(name))]
        raise RuntimeError("FEATURE_HAS_NO_TRAINING_VALUES:" + ",".join(bad))
    output = []
    for frame in (train, *others):
        copy = frame.copy()
        copy.loc[:, FEATURES] = copy.loc[:, FEATURES].fillna(medians)
        output.append(copy)
    return tuple(output)


def _optional_models() -> Dict[str, Any]:
    models: Dict[str, Any] = {}
    try:
        from xgboost import XGBClassifier  # type: ignore
        models["xgboost"] = XGBClassifier(
            n_estimators=350, max_depth=3, learning_rate=0.03, subsample=0.85,
            colsample_bytree=0.85, reg_lambda=2.0, objective="binary:logistic",
            eval_metric="logloss", random_state=RANDOM_SEED, n_jobs=-1,
        )
    except Exception:
        pass
    try:
        from lightgbm import LGBMClassifier  # type: ignore
        models["lightgbm"] = LGBMClassifier(
            n_estimators=350, learning_rate=0.03, num_leaves=15, max_depth=4,
            reg_lambda=2.0, random_state=RANDOM_SEED, n_jobs=-1, verbosity=-1,
        )
    except Exception:
        pass
    try:
        from catboost import CatBoostClassifier  # type: ignore
        models["catboost"] = CatBoostClassifier(
            iterations=350, depth=4, learning_rate=0.03, l2_leaf_reg=4.0,
            loss_function="Logloss", verbose=False, random_seed=RANDOM_SEED,
        )
    except Exception:
        pass
    return models


def _models() -> Dict[str, Any]:
    models: Dict[str, Any] = {
        "logistic_l2": Pipeline([
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=0.35, penalty="l2", max_iter=3000, random_state=RANDOM_SEED)),
        ]),
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            learning_rate=0.04, max_iter=300, max_leaf_nodes=15,
            l2_regularization=2.0, random_state=RANDOM_SEED,
        ),
        "gradient_boosting": GradientBoostingClassifier(
            n_estimators=300, learning_rate=0.025, max_depth=2,
            min_samples_leaf=15, subsample=0.85, random_state=RANDOM_SEED,
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=600, max_depth=6, min_samples_leaf=10,
            max_features="sqrt", class_weight="balanced_subsample",
            random_state=RANDOM_SEED, n_jobs=-1,
        ),
        "extra_trees": ExtraTreesClassifier(
            n_estimators=600, max_depth=7, min_samples_leaf=8,
            max_features="sqrt", class_weight="balanced",
            random_state=RANDOM_SEED, n_jobs=-1,
        ),
    }
    models.update(_optional_models())
    return models


@dataclass
class PlattCalibrator:
    model: LogisticRegression

    def transform(self, probability: Sequence[float]) -> np.ndarray:
        p = _clip(np.asarray(probability, dtype=float))
        logits = np.log(p / (1.0 - p)).reshape(-1, 1)
        return self.model.predict_proba(logits)[:, 1]


def _fit_calibrator(probability: Sequence[float], labels: Sequence[int]) -> PlattCalibrator:
    p = _clip(np.asarray(probability, dtype=float))
    logits = np.log(p / (1.0 - p)).reshape(-1, 1)
    model = LogisticRegression(C=1.0, penalty="l2", max_iter=2000, random_state=RANDOM_SEED)
    model.fit(logits, np.asarray(labels, dtype=int))
    return PlattCalibrator(model)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/opt/ml/input/data/training")
    parser.add_argument("--model-dir", default=os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    parser.add_argument("--output", default="/opt/ml/output/data")
    args = parser.parse_args()

    data = _read_input(args.input)
    train, validation, audit = _split(data)
    train, validation, audit = _impute(train, validation, audit)
    x_train, y_train = train.loc[:, FEATURES], train[LABEL].to_numpy()
    x_val, y_val = validation.loc[:, FEATURES], validation[LABEL].to_numpy()
    x_audit, y_audit = audit.loc[:, FEATURES], audit[LABEL].to_numpy()

    market_val = _clip(validation["homeMarketDeVigProbability"].to_numpy(dtype=float))
    market_audit = _clip(audit["homeMarketDeVigProbability"].to_numpy(dtype=float))

    fitted: Dict[str, Any] = {}
    calibrators: Dict[str, PlattCalibrator] = {}
    validation_rows: List[Dict[str, Any]] = []
    for name, estimator in _models().items():
        model = clone(estimator)
        model.fit(x_train, y_train)
        raw_val = model.predict_proba(x_val)[:, 1]
        calibrator = _fit_calibrator(raw_val, y_val)
        calibrated_val = calibrator.transform(raw_val)
        row = {"name": name, "validation": metrics(calibrated_val, y_val)}
        validation_rows.append(row)
        fitted[name] = model
        calibrators[name] = calibrator

    validation_rows.sort(key=lambda row: (float(row["validation"]["log_loss"]), row["name"]))
    finalists = [row["name"] for row in validation_rows[: min(3, len(validation_rows))]]
    if not finalists:
        raise RuntimeError("NO_MODEL_CANDIDATES")

    # Weight the strongest validation finalists by inverse calibrated log loss.
    inverse = np.array([1.0 / max(1e-9, next(r for r in validation_rows if r["name"] == name)["validation"]["log_loss"]) for name in finalists])
    weights = inverse / inverse.sum()
    ensemble_val = np.zeros(len(validation), dtype=float)
    ensemble_audit = np.zeros(len(audit), dtype=float)
    for weight, name in zip(weights, finalists):
        ensemble_val += weight * calibrators[name].transform(fitted[name].predict_proba(x_val)[:, 1])
        ensemble_audit += weight * calibrators[name].transform(fitted[name].predict_proba(x_audit)[:, 1])

    best_single = finalists[0]
    best_single_val = calibrators[best_single].transform(fitted[best_single].predict_proba(x_val)[:, 1])
    use_ensemble = metrics(ensemble_val, y_val)["log_loss"] < metrics(best_single_val, y_val)["log_loss"]
    champion_type = "calibrated_ensemble" if use_ensemble else "calibrated_single"
    champion_names = finalists if use_ensemble else [best_single]
    champion_weights = weights.tolist() if use_ensemble else [1.0]
    champion_audit = ensemble_audit if use_ensemble else calibrators[best_single].transform(fitted[best_single].predict_proba(x_audit)[:, 1])

    audit_metrics = metrics(champion_audit, y_audit)
    market_metrics = metrics(market_audit, y_audit)
    lower_bound = _bootstrap_skill_lower_bound(champion_audit, market_audit, y_audit)
    promotion_failures = []
    if audit_metrics["count"] < 100:
        promotion_failures.append("INSUFFICIENT_AUDIT_ROWS")
    if float(audit_metrics["log_loss"]) >= float(market_metrics["log_loss"]):
        promotion_failures.append("AUDIT_DOES_NOT_BEAT_MARKET")
    if lower_bound <= 0:
        promotion_failures.append("AUDIT_MARKET_SKILL_LOWER_BOUND_NOT_POSITIVE")
    if float(audit_metrics["ece"]) > 0.08:
        promotion_failures.append("AUDIT_CALIBRATION_FAILED")

    bundle = {
        "artifact_version": "inqsi-mlb-managed-ensemble-v1",
        "features": list(FEATURES),
        "label": LABEL,
        "champion_type": champion_type,
        "model_names": champion_names,
        "weights": champion_weights,
        "models": {name: fitted[name] for name in champion_names},
        "calibrators": {name: calibrators[name] for name in champion_names},
    }
    manifest = {
        "artifact_version": bundle["artifact_version"],
        "feature_schema_digest": _sha(list(FEATURES)),
        "data_manifest_digest": _sha([(str(row[EVENT_ID]), str(row[TIME]), int(row[LABEL])) for _, row in data.iterrows()]),
        "split_counts": {"train": len(train), "validation": len(validation), "audit": len(audit)},
        "candidate_validation": validation_rows,
        "champion_type": champion_type,
        "champion_names": champion_names,
        "champion_weights": champion_weights,
        "audit": audit_metrics,
        "market_baseline": market_metrics,
        "audit_market_skill_lower_bound_95": lower_bound,
        "promotion_eligible": not promotion_failures,
        "promotion_failures": promotion_failures,
    }
    manifest["manifest_digest"] = _sha(manifest)

    model_dir = Path(args.model_dir)
    output_dir = Path(args.output)
    model_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, model_dir / "model.joblib")
    (model_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "metrics.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
