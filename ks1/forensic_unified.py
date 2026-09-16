"""Train a provenance-safe KS1 candidate that learns from loss-forensic signal regimes.

The candidate uses only existing point-in-time KS1 features that daily inference already
serves.  The forensic layer changes training emphasis, not the serving schema: games
with large pregame starter-regime, workload, lineup, bullpen, or market signals receive
more weight.  Alpha and LightGBM regularization are selected only on the purged
chronological development split.  The frozen 300-game holdout remains evaluation-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from ks1.development import HOLDOUT, TRIALS, frozen_split, select
from ks1.inventory import encode
from ks1.prior_pitcher_context import PriorPitcherContext, pregame_identity_index
from ks1.retrain_recent import (
    EVALUATION_GAMES,
    accepted,
    bullpen_context_feature,
    choose_features,
    lineup_feature,
    metrics,
    pitcher_context_feature,
    qualified_context_coverage,
    qualified_team_context_coverage,
    qualified_training_population,
    split_development,
)
from ks1.sources import aws_clients, load_existing
from ks1.features import normalize
from ks1.train import PARAMS, save_artifact

ALPHAS = (0.25, 0.5, 1.0)
CONTRACT = "KS1-unified-forensic-training-v1"


def forensic_groups(features):
    """Map admitted raw features to the five predeclared forensic signal groups."""
    result = {name: [] for name in (
        "market", "starter_regime", "starter_workload", "lineup", "bullpen")}
    for column in features:
        if column == "market_home_prob":
            result["market"].append(column)
        if any(column.endswith(suffix) for suffix in (
                "_starter_era_7d", "_starter_era_30d",
                "_starter_fip_7d", "_starter_fip_30d",
                "_starter_xwoba_7d", "_starter_xwoba_30d")):
            result["starter_regime"].append(column)
        if column.endswith("_pitcher_context_expected_innings"):
            result["starter_workload"].append(column)
        if lineup_feature(column) and not column.endswith("_missing") and any(token in column for token in (
                "lineup_ops_7d", "lineup_ops_30d", "lineup_xwoba_7d",
                "lineup_xwoba_30d", "lineup_top4_ops")):
            result["lineup"].append(column)
        if bullpen_context_feature(column) and not column.endswith("_missing") and any(token in column for token in (
                "bullpen_context_fip_7d", "bullpen_context_fip_30d",
                "bullpen_context_era_7d", "bullpen_context_era_30d",
                "bullpen_context_available_count", "bullpen_context_depth",
                "bullpen_context_fatigue_score")):
            result["bullpen"].append(column)
    return {key: sorted(value) for key, value in result.items()}


def _numeric(frame, column):
    if column not in frame:
        return None
    return pd.to_numeric(frame[column], errors="coerce")


def _add_component(parts, values):
    if values is None:
        return
    values = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    parts.append(values.clip(lower=0.0, upper=2.0))


def forensic_intensity(frame, admitted):
    """Continuous, label-free pregame intensity used only for training weights."""
    admitted = set(admitted)
    parts = []

    # Short-vs-medium starter regime shifts.  Direction is left to the model;
    # weighting only emphasizes games where the pregame regime changed materially.
    for side in ("home", "away"):
        for metric, scale in (("era", 2.0), ("fip", 1.5), ("xwoba", 0.040)):
            short = f"{side}_starter_{metric}_7d"
            long = f"{side}_starter_{metric}_30d"
            if short in admitted and long in admitted:
                a, b = _numeric(frame, short), _numeric(frame, long)
                _add_component(parts, (a-b).abs()/scale)

    # Starter workload/early-bullpen exposure.
    home_ip, away_ip = (f"{side}_pitcher_context_expected_innings" for side in ("home", "away"))
    if home_ip in admitted and away_ip in admitted:
        h, a = _numeric(frame, home_ip), _numeric(frame, away_ip)
        _add_component(parts, (h-a).abs()/2.0)
        _add_component(parts, (4.5-h).clip(lower=0.0)/2.0)
        _add_component(parts, (4.5-a).clip(lower=0.0)/2.0)

    # Confirmed-lineup strength differences.
    for metric, scale in (("ops_7d", 0.080), ("xwoba_7d", 0.030), ("top4_ops", 0.080)):
        home, away = f"home_lineup_{metric}", f"away_lineup_{metric}"
        if home in admitted and away in admitted:
            _add_component(parts, (_numeric(frame, home)-_numeric(frame, away)).abs()/scale)

    # Bullpen quality/depth differences.
    for metric, scale in (("fip_7d", 1.0), ("era_7d", 1.5), ("available_count", 2.0)):
        home, away = f"home_bullpen_context_{metric}", f"away_bullpen_context_{metric}"
        if home in admitted and away in admitted:
            _add_component(parts, (_numeric(frame, home)-_numeric(frame, away)).abs()/scale)

    # Market strength is a constituent signal, not a model-vs-market residual;
    # no incumbent prediction is injected into candidate training.
    if "market_home_prob" in admitted:
        market = _numeric(frame, "market_home_prob")
        _add_component(parts, (market-0.5).abs()/0.10)

    if not parts:
        raise ValueError("no admitted forensic intensity components")
    matrix = pd.concat(parts, axis=1)
    # Require observed evidence rather than replacing absent components with zero.
    count = matrix.notna().sum(axis=1)
    total = matrix.fillna(0.0).sum(axis=1)
    return (total/count.where(count > 0)).clip(lower=0.0, upper=2.0)


def sample_weights(frame, admitted, alpha):
    intensity = forensic_intensity(frame, admitted)
    weights = 1.0 + float(alpha)*intensity.fillna(0.0)
    if not np.isfinite(weights.to_numpy()).all() or (weights <= 0).any():
        raise ValueError("invalid forensic sample weights")
    return weights


def _trial_model(fit, validation, columns, params, alpha):
    model = lgb.LGBMClassifier(**params).fit(
        fit[columns].astype(float), fit.home_win.astype(int),
        sample_weight=sample_weights(fit, columns, alpha))
    probability = model.predict_proba(validation[columns].astype(float))[:, 1]
    result = metrics(validation.home_win.astype(int), probability)
    counts = dict(zip(columns, map(int, model.booster_.feature_importance())))
    result.update(alpha=float(alpha), parameters=params,
                  features_used_in_splits=[c for c in columns if counts[c] > 0],
                  feature_split_counts=counts)
    return model, result


def development_select(train):
    """Select a forensic-weighted full recipe using only the purged development tail."""
    fit, validation = split_development(train)
    admitted, omitted, coverage = choose_features(fit)
    groups = forensic_groups(admitted)
    missing_groups = sorted(name for name, columns in groups.items() if not columns)
    baseline_parameters, baseline_report = select(train)
    baseline_name = baseline_report["selected"]
    baseline_metrics = baseline_report["metrics"][baseline_name]
    if missing_groups:
        return None, {
            "contract": CONTRACT, "accepted_for_final_holdout": False,
            "reason": "forensic_feature_group_unavailable_on_development",
            "missing_groups": missing_groups, "groups": groups,
            "baseline_selection": baseline_report, "omitted_features": omitted,
            "feature_admission_starter_coverage": coverage,
        }

    trials = {}
    best = None
    for trial_name, updates in TRIALS.items():
        params = {**PARAMS, **updates}
        for alpha in ALPHAS:
            key = f"{trial_name}:alpha={alpha}"
            _, result = _trial_model(fit, validation, admitted, params, alpha)
            used = set(result["features_used_in_splits"])
            result["forensic_group_usage"] = {
                name: sorted(used.intersection(columns)) for name, columns in groups.items()}
            result["all_forensic_groups_used"] = all(result["forensic_group_usage"].values())
            result["beats_selected_baseline"] = bool(
                result["brier"] < baseline_metrics["brier"]
                and result["logloss"] <= baseline_metrics["logloss"])
            trials[key] = result
            if result["all_forensic_groups_used"] and result["beats_selected_baseline"]:
                if best is None or (result["brier"], result["logloss"], key) < (
                        best[1]["brier"], best[1]["logloss"], best[0]):
                    best = (key, result)

    report = {
        "contract": CONTRACT, "fit_games": len(fit), "development_games": len(validation),
        "baseline_selected": baseline_name, "baseline_metrics": baseline_metrics,
        "baseline_selection": baseline_report, "admitted_features": admitted,
        "omitted_features": omitted, "groups": groups, "trials": trials,
        "final_holdout_used_for_selection": False,
        "accepted_for_final_holdout": best is not None,
        "selected_trial": best[0] if best else None,
    }
    if best is None:
        report["reason"] = "forensic_weighted_candidate_not_superior_on_development"
        return None, report
    return best[1], report


def qualify(input_path, proof_path, output):
    output.mkdir(parents=True, exist_ok=True)
    proof = json.loads(Path(proof_path).read_bytes())
    input_bytes = Path(input_path).read_bytes()
    if hashlib.sha256(input_bytes).hexdigest() != proof["input_table_sha256"]:
        raise ValueError("input table checksum mismatch")
    frame = pd.read_parquet(input_path)
    manifest = json.loads(HOLDOUT.read_bytes())
    train, test = frozen_split(frame, manifest)
    train, population = qualified_training_population(train, proof.get("source_receipts", []))

    selected, development = development_select(train)
    (output/"development_selection.json").write_bytes(encode(development))
    if selected is None:
        report = {
            "contract": CONTRACT, "accepted": False, "qualification_run": False,
            "reason": development["reason"], "reserved_holdout_games": len(test),
            "training_population": population, "development": development,
            "prediction_writes": 0, "official_ledger_writes": 0,
            "input_table_sha256": proof["input_table_sha256"],
        }
        (output/"metrics.json").write_bytes(encode(report))
        (output/"input_proof.json").write_bytes(encode(proof))
        return report

    columns = development["admitted_features"]
    params = selected["parameters"]
    alpha = selected["alpha"]
    model = lgb.LGBMClassifier(**params).fit(
        train[columns].astype(float), train.home_win.astype(int),
        sample_weight=sample_weights(train, columns, alpha))
    probability = model.predict_proba(test[columns].astype(float))[:, 1]
    model.booster_.save_model(str(output/"model.txt"))
    loaded = lgb.Booster(model_file=str(output/"model.txt"))
    np.testing.assert_allclose(loaded.predict(test[columns].astype(float)), probability,
                               atol=1e-12, rtol=0)
    split_counts = dict(zip(columns, map(int, model.booster_.feature_importance())))
    used = [c for c in columns if split_counts[c] > 0]
    groups = forensic_groups(columns)
    group_usage = {name: sorted(set(used).intersection(values)) for name, values in groups.items()}

    cf, s3, bucket = aws_clients("us-east-1", "parlay-platform-dev")
    ref = proof["incumbent_ref"]
    incumbent_bytes = s3.get_object(
        Bucket=bucket, Key=ref["key"], VersionId=ref["version_id"])["Body"].read()
    if hashlib.sha256(incumbent_bytes).hexdigest() != ref["sha256"]:
        raise ValueError("incumbent hash mismatch")
    incumbent = lgb.Booster(model_str=incumbent_bytes.decode())
    incumbent_probability = incumbent.predict(test[incumbent.feature_name()].astype(float))
    candidate_metrics = metrics(test.home_win.astype(int), probability)
    incumbent_metrics = metrics(test.home_win.astype(int), incumbent_probability)

    # Reconstruct point-in-time pitcher context independently from the retained AWS bundle.
    bundle = load_existing(cf, s3, bucket)
    reconstruction = PriorPitcherContext(
        normalize(bundle.get("full", [])), proof.get("official_history_source", {}),
        pregame_identity_index(bundle))
    used_pitcher = [c for c in used if pitcher_context_feature(c)]
    pitcher_qualification = qualified_context_coverage(
        test, used_pitcher, reconstruction=reconstruction)
    team_used = [c for c in used if lineup_feature(c) or bullpen_context_feature(c)]
    team_test = qualified_team_context_coverage(
        test, team_used, source_receipts=proof.get("source_receipts"))
    team_train = qualified_team_context_coverage(
        train, team_used, source_receipts=proof.get("source_receipts"))

    statistical = accepted(candidate_metrics, incumbent_metrics)
    forensic_usage = all(group_usage.values())
    provenance = bool(
        used_pitcher and pitcher_qualification["qualified_rows"] == EVALUATION_GAMES
        and (not team_used or (team_test["qualified_rows"] == EVALUATION_GAMES
                              and team_train["qualified_rows"] == len(train))))
    qualified = bool(statistical and forensic_usage and provenance)

    rows = test[["date", "game_id", "home_win"]].copy()
    rows["candidate_p_home"] = probability
    rows["incumbent_p_home"] = incumbent_probability
    rows.to_parquet(output/"test_predictions.parquet", index=False)
    (output/"feature_list.json").write_bytes(encode(columns))
    report = {
        "contract": CONTRACT, "accepted": qualified, "qualification_run": True,
        "candidate_metrics": candidate_metrics, "incumbent_metrics": incumbent_metrics,
        "statistical_gate_passed": statistical, "forensic_group_usage_passed": forensic_usage,
        "provenance_gate_passed": provenance, "forensic_group_usage": group_usage,
        "pitcher_context_qualification": pitcher_qualification,
        "team_context_qualification": team_test,
        "team_context_training_qualification": team_train,
        "selected_alpha": alpha, "selected_parameters": params,
        "features": columns, "features_used_in_splits": used,
        "feature_split_counts": split_counts,
        "development": development, "training_population": population,
        "holdout_games": len(test), "holdout_manifest_sha256": hashlib.sha256(HOLDOUT.read_bytes()).hexdigest(),
        "model_sha256": hashlib.sha256((output/"model.txt").read_bytes()).hexdigest(),
        "model_reload_verified": True, "incumbent_sha256": hashlib.sha256(incumbent_bytes).hexdigest(),
        "input_table_sha256": proof["input_table_sha256"],
        "prediction_writes": 0, "official_ledger_writes": 0,
        "promotion_ready": qualified,
        "promotion_rule": "lower Brier, no-worse log loss, all forensic groups used, exact-300 point-in-time provenance",
    }
    (output/"metrics.json").write_bytes(encode(report))
    (output/"input_proof.json").write_bytes(encode(proof))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--proof", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = qualify(args.input, args.proof, args.output)
    cf, s3, bucket = aws_clients("us-east-1", "parlay-platform-dev")
    save_artifact(s3, bucket, args.output)
    print(json.dumps({
        "accepted": report["accepted"], "qualification_run": report["qualification_run"],
        "reason": report.get("reason"), "candidate_metrics": report.get("candidate_metrics"),
        "incumbent_metrics": report.get("incumbent_metrics"),
        "promotion_ready": report.get("promotion_ready", False),
    }, indent=2))


if __name__ == "__main__":
    main()
