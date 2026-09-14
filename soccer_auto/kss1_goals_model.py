"""Dependency-free fitted Poisson goals models and chronological evaluation."""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from .canonical import digest, iso_utc, parse_utc
from .kss1_features import SCHEMA
from .kss1_markets import markets_from_grid, score_matrix

FAMILY = "kss1-fitted-poisson-v1"
BASE_NAMES = ["intercept", "log_attack", "log_opponent_defence"]
XG_NAMES = ["log_xg_attack", "log_xg_opponent_defence", "attack_xg_missing", "opponent_xg_missing"]


def vector(features: dict[str, Any], side: str, use_xg: bool) -> list[float]:
    if features.get("schema") != SCHEMA or side not in {"home", "away"}:
        raise ValueError("incompatible goals feature schema")
    values = features["values"]
    opponent = "away" if side == "home" else "home"
    def log(value):
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("invalid goals feature")
        return math.log(max(.05, min(5.5, value)))
    result = [1.0, log(values[side + "_attack"]), log(values[opponent + "_defence"])]
    if use_xg:
        a, d = values[side + "_xg_attack"], values[opponent + "_xg_defence"]
        result += [0.0 if a is None else log(a), 0.0 if d is None else log(d), float(a is None), float(d is None)]
    return result


def _fit_side(rows, side, use_xg, ridge):
    xs = [vector(r["features"], side, use_xg) for r in rows]
    ys = [r[side + "_score"] for r in rows]
    offsets = [math.log(r["features"]["values"]["league_" + side]) for r in rows]
    coefficients = [0.0] * len(xs[0])
    eta = offsets[:]
    # Coordinate Newton updates, with bounded steps. This also runs in Lambda
    # without adding numpy/scipy to the isolated deployment package.
    for _ in range(120):
        largest = 0.0
        for j in range(len(coefficients)):
            penalty = ridge * len(rows) if j else 0.0
            grad = penalty * coefficients[j]
            curvature = penalty + 1e-9
            for x, y, log_rate in zip(xs, ys, eta):
                mu = math.exp(max(-7, min(5, log_rate)))
                grad += (mu - y) * x[j]
                curvature += mu * x[j] * x[j]
            step = max(-.25, min(.25, grad / curvature))
            new = max(-5.0, min(5.0, coefficients[j] - step))
            change = new - coefficients[j]
            coefficients[j] = new
            eta = [value + change * x[j] for value, x in zip(eta, xs)]
            largest = max(largest, abs(change))
        if largest < 1e-7:
            break
    return coefficients


def fit(rows: list[dict[str, Any]], *, use_xg: bool, ridge: float, fitted_as_of: str) -> dict[str, Any]:
    cutoff = parse_utc(fitted_as_of)
    if not rows or any(parse_utc(r["label_available_at"]) >= cutoff for r in rows):
        raise ValueError("training labels must be available before model fit cutoff")
    if any(parse_utc(r["commence_time"]) >= cutoff for r in rows):
        raise ValueError("model cannot train on future events")
    model = {
        "family": FAMILY, "feature_schema": SCHEMA,
        "feature_names": BASE_NAMES + (XG_NAMES if use_xg else []),
        "use_xg": use_xg, "ridge": ridge,
        "home_coefficients": _fit_side(rows, "home", use_xg, ridge),
        "away_coefficients": _fit_side(rows, "away", use_xg, ridge),
        "fitted_as_of": iso_utc(cutoff),
        "training_rows": len(rows),
        "training_manifest": digest([{k: r[k] for k in ("event_key", "label_receipt")} | {"feature_digest": r["features"]["feature_digest"]} for r in rows]),
        "training_end": max(r["commence_time"] for r in rows),
        "research_only": any(r["research_only"] for r in rows),
        "automatic_prediction_allowed": False,
    }
    model["model_digest"] = digest(model)
    return model


def rates(model: dict[str, Any], features: dict[str, Any]) -> tuple[float, float]:
    if features.get("feature_digest") != digest({k: v for k, v in features.items() if k != "feature_digest"}):
        raise ValueError("goals feature digest mismatch")
    if model.get("family") != FAMILY or model.get("feature_schema") != SCHEMA:
        raise ValueError("incompatible goals model")
    if model.get("model_digest") != digest({k: v for k, v in model.items() if k != "model_digest"}):
        raise ValueError("goals model digest mismatch")
    if parse_utc(model["fitted_as_of"]) > parse_utc(features["as_of"]):
        raise ValueError("model fitted after prediction cutoff")
    output = []
    for side in ("home", "away"):
        x = vector(features, side, model["use_xg"])
        beta = model[side + "_coefficients"]
        if len(x) != len(beta) or any(not math.isfinite(float(v)) for v in beta):
            raise ValueError("invalid goals coefficients")
        eta = math.log(features["values"]["league_" + side]) + sum(a * b for a, b in zip(x, beta))
        output.append(max(.05, min(5.5, math.exp(max(-7, min(5, eta))))))
    return tuple(output)


def evaluate(model: dict[str, Any] | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
    briers, losses = [], []
    correct = 0
    binary = {key: {"brier": 0.0, "log_loss": 0.0, "correct": 0} for key in ("over_2_5", "btts", "1X", "12", "X2")}
    for row in rows:
        lam, mu = rates(model, row["features"]) if model else (1.45, 1.15)
        markets = markets_from_grid(score_matrix(lam, mu))
        p = [markets["p_home"], markets["p_draw"], markets["p_away"]]
        actual = 0 if row["home_score"] > row["away_score"] else 2 if row["away_score"] > row["home_score"] else 1
        briers.append(sum((prob - int(i == actual)) ** 2 for i, prob in enumerate(p)))
        losses.append(-math.log(max(p[actual], 1e-12)))
        correct += max(range(3), key=p.__getitem__) == actual
        for name, prob, truth in (
            ("over_2_5", markets["p_over_25"], row["home_score"] + row["away_score"] >= 3),
            ("btts", markets["p_btts_yes"], min(row["home_score"], row["away_score"]) > 0),
            ("1X", markets["p_1x"], actual != 2), ("12", markets["p_12"], actual != 1), ("X2", markets["p_x2"], actual != 0),
        ):
            binary[name]["brier"] += (prob - int(truth)) ** 2
            binary[name]["log_loss"] -= math.log(max(prob if truth else 1-prob, 1e-12))
            binary[name]["correct"] += (prob >= .5) == truth
    n = len(rows)
    if not n:
        raise ValueError("empty evaluation cohort")
    return {"count": n, "correct": correct, "accuracy": correct / n,
            "brier": sum(briers) / n, "log_loss": sum(losses) / n,
            "markets": {name: {"count": n, "brier": values["brier"]/n, "log_loss": values["log_loss"]/n, "accuracy": values["correct"]/n} for name, values in binary.items()},
            "brier_definition": "mean sum of squared errors across home/draw/away; range 0..2",
            "event_manifest": digest([r["event_key"] for r in rows])}


def train_and_validate(table: list[dict[str, Any]], *, min_train: int = 100, min_test: int = 50) -> dict[str, Any]:
    rows = sorted([r for r in table if r["features"]["team_strength_complete"]], key=lambda r: (r["commence_time"], r["event_key"]))
    if len(rows) < min_train + 2 * min_test:
        return {"trained": False, "reason": "INSUFFICIENT_TEAM_HISTORY", "eligible_rows": len(rows), "required_minimum": min_train + 2 * min_test}
    # Boundaries are fixed from time ordering before evaluating any candidate.
    validation_start = parse_utc(rows[int(len(rows) * .60)]["commence_time"]).replace(hour=0, minute=0, second=0, microsecond=0)
    test_start = parse_utc(rows[int(len(rows) * .80)]["commence_time"]).replace(hour=0, minute=0, second=0, microsecond=0)
    train_cutoff = validation_start - timedelta(days=2)
    refit_cutoff = test_start - timedelta(days=2)
    train = [r for r in rows if parse_utc(r["label_available_at"]) < train_cutoff and parse_utc(r["commence_time"]) < train_cutoff]
    valid = [r for r in rows if validation_start <= parse_utc(r["commence_time"]) < refit_cutoff and parse_utc(r["label_available_at"]) < refit_cutoff]
    test = [r for r in rows if parse_utc(r["commence_time"]) >= test_start]
    if len(train) < min_train or min(len(valid), len(test)) < min_test:
        return {"trained": False, "reason": "INSUFFICIENT_CHRONOLOGICAL_SPLITS", "split_counts": [len(train), len(valid), len(test)]}
    candidates = []
    xg_count = sum(r["features"]["xg_complete"] for r in train)
    for use_xg in (False, True):
        if use_xg and xg_count < min_train:
            continue
        for ridge in (.01, .1, 1.0):
            model = fit(train, use_xg=use_xg, ridge=ridge, fitted_as_of=iso_utc(train_cutoff))
            candidates.append({"use_xg": use_xg, "ridge": ridge, "validation": evaluate(model, valid)})
    selected = min(candidates, key=lambda c: (c["validation"]["log_loss"], c["validation"]["brier"], c["use_xg"], c["ridge"]))
    refit = train + valid
    model = fit(refit, use_xg=selected["use_xg"], ridge=selected["ridge"], fitted_as_of=iso_utc(refit_cutoff))
    baseline, test_metrics = evaluate(None, test), evaluate(model, test)
    report = {
        "trained": True, "model": model, "selection_basis": "validation log loss only; holdout never selects model",
        "input_rows": len(table), "eligible_rows": len(rows), "excluded_insufficient_team_history": len(table) - len(rows),
        "baseline_definition": "existing untrained goals grid (1.45 home, 1.15 away); no market odds supplied",
        "split_counts": {"train": len(train), "validation": len(valid), "holdout": len(test)},
        "split_boundaries": {"train_cutoff": iso_utc(train_cutoff), "validation_start": iso_utc(validation_start), "refit_cutoff": iso_utc(refit_cutoff), "holdout_start": iso_utc(test_start)},
        "candidates": candidates, "selected": selected,
        "holdout": test_metrics, "baseline": baseline,
        "lower_brier_no_worse_log_loss": test_metrics["brier"] < baseline["brier"] and test_metrics["log_loss"] <= baseline["log_loss"],
        "xg_training_rows": xg_count,
        "xg_candidate_skipped": None if xg_count >= min_train else "INSUFFICIENT_PRIOR_MATCH_XG",
        "research_only": any(r["research_only"] for r in rows),
        "automatic_prediction_allowed": False,
        "prospective_qualified": False,
        "qualification_blockers": ["PROSPECTIVE_GOALS_GRADES_REQUIRED", "INCUMBENT_MARKET_MODEL_COMPARISON_NOT_RUN"],
    }
    report["feature_usage"] = {
        side: {name: {"coefficient": coefficient,
                      "holdout_mean_absolute_log_rate_contribution": sum(abs(coefficient * vector(r["features"], side, model["use_xg"])[i]) for r in test) / len(test)}
               for i, (name, coefficient) in enumerate(zip(model["feature_names"], model[side + "_coefficients"]))}
        for side in ("home", "away")
    }
    if report["research_only"]:
        report["qualification_blockers"].append("HISTORICAL_AVAILABILITY_ASSUMED_OR_RESEARCH_SOURCE")
    if not report["lower_brier_no_worse_log_loss"]:
        report["qualification_blockers"].append("HOLDOUT_DID_NOT_BEAT_BASELINE")
    return report
