"""Train the existing KS1 feature set plus deterministic forensic derived features.

This is the second forensic experiment.  Unlike the rejected sample-weighting
experiment, it gives LightGBM explicit signed pregame deltas/advantages while retaining
all currently admitted KS1 raw features.  Feature engineering is label-free and shared
with daily serving.  Development selection is purged and chronological; the checksum-
bound 300-game holdout is touched only after a candidate beats the selected incumbent
recipe on development with no worse log loss and uses all five derived signal families.
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
from ks1.features import normalize
from ks1.forensic_features import (
    CONTRACT as FEATURE_CONTRACT,
    SPECS,
    admit as admit_derived,
    derive_frame,
    groups as derived_groups,
    raw_parents,
)
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
from ks1.train import PARAMS, save_artifact

CONTRACT = "KS1-unified-forensic-derived-training-v1"


def _augment(frame, derived):
    result = frame.copy()
    values = derive_frame(frame)
    for column in derived:
        result[column] = values[column]
    return result


def _trial(fit, validation, columns, params):
    model = lgb.LGBMClassifier(**params).fit(
        fit[columns].astype(float), fit.home_win.astype(int))
    probability = model.predict_proba(validation[columns].astype(float))[:, 1]
    result = metrics(validation.home_win.astype(int), probability)
    split_counts = dict(zip(columns, map(int, model.booster_.feature_importance())))
    result.update(
        parameters=params,
        features_used_in_splits=[column for column in columns if split_counts[column] > 0],
        feature_split_counts=split_counts,
    )
    return result


def development_select(train):
    """Select a derived-feature challenger without exposing the frozen holdout."""
    fit, validation = split_development(train)
    raw, omitted, coverage = choose_features(fit)
    derived, rejected, _ = admit_derived(fit, raw, EVALUATION_GAMES)
    groups = derived_groups(derived)
    missing_groups = sorted(name for name, columns in groups.items() if not columns)
    _, baseline_report = select(train)
    baseline_name = baseline_report["selected"]
    baseline_metrics = baseline_report["metrics"][baseline_name]

    report = {
        "contract": CONTRACT,
        "feature_contract": FEATURE_CONTRACT,
        "fit_games": len(fit),
        "development_games": len(validation),
        "raw_admitted_features": raw,
        "derived_admitted_features": derived,
        "derived_rejections": rejected,
        "derived_groups": groups,
        "derived_parent_map": {name: list(SPECS[name]["parents"]) for name in derived},
        "omitted_raw_features": omitted,
        "feature_admission_starter_coverage": coverage,
        "baseline_selected": baseline_name,
        "baseline_metrics": baseline_metrics,
        "baseline_selection": baseline_report,
        "final_holdout_used_for_selection": False,
        "accepted_for_final_holdout": False,
        "trials": {},
        "selected_trial": None,
    }
    if missing_groups:
        report.update(reason="derived_forensic_group_unavailable_on_development",
                      missing_groups=missing_groups)
        return None, report

    fit_aug = _augment(fit, derived)
    validation_aug = _augment(validation, derived)
    columns = raw + derived
    best = None
    for trial_name, updates in TRIALS.items():
        params = {**PARAMS, **updates}
        result = _trial(fit_aug, validation_aug, columns, params)
        used = set(result["features_used_in_splits"])
        result["derived_group_usage"] = {
            name: sorted(used.intersection(group_columns))
            for name, group_columns in groups.items()
        }
        result["all_derived_groups_used"] = all(result["derived_group_usage"].values())
        result["beats_selected_baseline"] = bool(
            result["brier"] < baseline_metrics["brier"]
            and result["logloss"] <= baseline_metrics["logloss"])
        report["trials"][trial_name] = result
        if result["all_derived_groups_used"] and result["beats_selected_baseline"]:
            if best is None or (result["brier"], result["logloss"], trial_name) < (
                    best[1]["brier"], best[1]["logloss"], best[0]):
                best = (trial_name, result)

    if best is None:
        report["reason"] = "derived_forensic_candidate_not_superior_on_development"
        return None, report
    report["accepted_for_final_holdout"] = True
    report["selected_trial"] = best[0]
    report["features"] = columns
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

    raw = development["raw_admitted_features"]
    derived = development["derived_admitted_features"]
    columns = development["features"]
    train_aug, test_aug = _augment(train, derived), _augment(test, derived)
    params = selected["parameters"]
    model = lgb.LGBMClassifier(**params).fit(
        train_aug[columns].astype(float), train_aug.home_win.astype(int))
    probability = model.predict_proba(test_aug[columns].astype(float))[:, 1]
    model.booster_.save_model(str(output/"model.txt"))
    loaded = lgb.Booster(model_file=str(output/"model.txt"))
    np.testing.assert_allclose(
        loaded.predict(test_aug[columns].astype(float)), probability, atol=1e-12, rtol=0)
    split_counts = dict(zip(columns, map(int, model.booster_.feature_importance())))
    used = [column for column in columns if split_counts[column] > 0]
    used_derived = [column for column in used if column in derived]
    group_usage = {
        name: sorted(set(used_derived).intersection(group_columns))
        for name, group_columns in derived_groups(derived).items()
    }

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

    # Trace every used derived feature back to its raw pregame parents for source
    # qualification; derived names themselves are not treated as independent evidence.
    parents = raw_parents(used_derived)
    raw_used = [column for column in used if column in raw]
    provenance_inputs = sorted(set(parents + raw_used))
    pitcher_inputs = [column for column in provenance_inputs if pitcher_context_feature(column)]
    team_inputs = [column for column in provenance_inputs
                   if lineup_feature(column) or bullpen_context_feature(column)]
    starter_inputs = [column for column in provenance_inputs
                      if "_starter_" in column and not pitcher_context_feature(column)]

    bundle = load_existing(cf, s3, bucket)
    reconstruction = PriorPitcherContext(
        normalize(bundle.get("full", [])), proof.get("official_history_source", {}),
        pregame_identity_index(bundle))
    pitcher_qualification = qualified_context_coverage(
        test, pitcher_inputs, reconstruction=reconstruction)
    team_test = qualified_team_context_coverage(
        test, team_inputs, source_receipts=proof.get("source_receipts"))
    team_train = qualified_team_context_coverage(
        train, team_inputs, source_receipts=proof.get("source_receipts"))
    starter_parent_coverage = {
        "test": {column: int(pd.to_numeric(test[column], errors="coerce").notna().sum())
                 for column in starter_inputs},
        "train": {column: int(pd.to_numeric(train[column], errors="coerce").notna().sum())
                  for column in starter_inputs},
    }

    statistical = accepted(candidate_metrics, incumbent_metrics)
    forensic_usage = bool(used_derived) and all(group_usage.values())
    # Historical starter parents were admitted only after the existing physical
    # starter/source proof and 300-row development floor.  Keep that exact admission
    # proof in the artifact rather than inventing a new source authority here.
    starter_parent_gate = bool(
        not starter_inputs or all(column in development["raw_admitted_features"] for column in starter_inputs))
    pitcher_gate = bool(
        not pitcher_inputs or pitcher_qualification["qualified_rows"] == EVALUATION_GAMES)
    team_gate = bool(
        not team_inputs or (team_test["qualified_rows"] == EVALUATION_GAMES
                            and team_train["qualified_rows"] == len(train)))
    provenance = bool(starter_parent_gate and pitcher_gate and team_gate)
    qualified = bool(statistical and forensic_usage and provenance)

    rows = test[["date", "game_id", "home_win"]].copy()
    rows["candidate_p_home"] = probability
    rows["incumbent_p_home"] = incumbent_probability
    rows.to_parquet(output/"test_predictions.parquet", index=False)
    (output/"feature_list.json").write_bytes(encode(columns))
    report = {
        "contract": CONTRACT, "feature_contract": FEATURE_CONTRACT,
        "accepted": qualified, "qualification_run": True,
        "candidate_metrics": candidate_metrics, "incumbent_metrics": incumbent_metrics,
        "statistical_gate_passed": statistical,
        "derived_group_usage_passed": forensic_usage,
        "provenance_gate_passed": provenance,
        "derived_group_usage": group_usage,
        "used_derived_features": used_derived,
        "derived_parent_features": parents,
        "starter_parent_coverage": starter_parent_coverage,
        "starter_parent_admission_gate_passed": starter_parent_gate,
        "pitcher_context_qualification": pitcher_qualification,
        "team_context_qualification": team_test,
        "team_context_training_qualification": team_train,
        "selected_parameters": params,
        "features": columns, "features_used_in_splits": used,
        "feature_split_counts": split_counts,
        "development": development, "training_population": population,
        "holdout_games": len(test),
        "holdout_manifest_sha256": hashlib.sha256(HOLDOUT.read_bytes()).hexdigest(),
        "model_sha256": hashlib.sha256((output/"model.txt").read_bytes()).hexdigest(),
        "model_reload_verified": True,
        "incumbent_sha256": hashlib.sha256(incumbent_bytes).hexdigest(),
        "input_table_sha256": proof["input_table_sha256"],
        "prediction_writes": 0, "official_ledger_writes": 0,
        "promotion_ready": qualified,
        "promotion_rule": "lower Brier, no-worse log loss, all five derived groups used, raw-parent point-in-time provenance",
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
    _, s3, bucket = aws_clients("us-east-1", "parlay-platform-dev")
    save_artifact(s3, bucket, args.output)
    print(json.dumps({
        "accepted": report["accepted"],
        "qualification_run": report["qualification_run"],
        "reason": report.get("reason"),
        "candidate_metrics": report.get("candidate_metrics"),
        "incumbent_metrics": report.get("incumbent_metrics"),
        "promotion_ready": report.get("promotion_ready", False),
    }, indent=2))


if __name__ == "__main__":
    main()
