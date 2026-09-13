"""Optional KS1 LightGBM challenger that minimizes Brier.

Does not write model_refs or replace the serving binary-objective booster.
"""
import argparse
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from ks1.brier_objective import _probability, booster_params, brier_metric, brier_objective
from ks1.inventory import encode
from ks1.train import PARAMS, metrics, select_features, split
from ks1.table import contract


def fit_brier(x_train, y_train, x_test, features, rounds=None):
    dataset = lgb.Dataset(x_train, label=np.asarray(y_train, dtype=float),
                          feature_name=list(features), free_raw_data=False)
    rounds = int(rounds or PARAMS["n_estimators"])
    booster = lgb.train(booster_params(PARAMS), dataset, num_boost_round=rounds,
                        fobj=brier_objective, feval=brier_metric)
    p_test = _probability(booster.predict(x_test))
    return booster, p_test


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frame = pd.read_parquet(args.input)
    train, test = split(frame)
    _, dictionary = contract(train.iloc[0].to_dict())
    features, omitted = select_features(train, dictionary)
    x_train = train[features].astype(float)
    x_test = test[features].astype(float)
    y_train = train.home_win.astype(int)
    y_test = test.home_win.astype(int)
    booster, p_test = fit_brier(x_train, y_train, x_test, features)
    args.output.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(args.output / "model.txt"))
    report = {
        "system": "KS1",
        "kind": "brier_objective_challenger",
        "serving_authority": False,
        "training_loss": "brier",
        "incumbent_training_loss": "binary_logloss",
        "features": features,
        "omitted_unavailable_or_constant_in_training": omitted,
        "train_games": int(len(train)),
        "test_games": int(len(test)),
        "lightgbm": metrics(y_test, p_test),
        "deployment": False,
    }
    report["model_sha256"] = hashlib.sha256((args.output / "model.txt").read_bytes()).hexdigest()
    (args.output / "metrics.json").write_bytes(encode(report))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
