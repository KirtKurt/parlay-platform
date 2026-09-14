"""Fixed September Brier challenger benchmark; no serving or model-ref writes."""
import argparse
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

from ks1.brier_objective import _probability, booster_params, brier_metric
from ks1.inventory import encode
from ks1.retrain_recent import choose_features
from ks1.train import PARAMS

SPLIT_DATE = '2026-09-01'
MIN_TRAIN, MIN_TEST = 500, 100


def split_september(frame):
    """Freeze the requested split independently of the rolling production trainer."""
    if frame.game_id.duplicated().any():
        raise ValueError('duplicate game IDs')
    labeled = frame.loc[frame.home_win.notna() & frame.home_score.notna() & frame.away_score.notna()].copy()
    if not set(labeled.home_win.unique()).issubset({True, False, 0, 1}):
        raise ValueError('invalid binary labels')
    completed = pd.to_datetime(labeled.label_completed_at, format='ISO8601', utc=True, errors='raise')
    predicted = pd.to_datetime(labeled.as_of_timestamp, format='ISO8601', utc=True, errors='raise')
    if completed.isna().any() or predicted.isna().any() or (predicted >= completed).any():
        raise ValueError('missing or invalid prediction/label chronology')
    boundary = pd.Timestamp(SPLIT_DATE, tz='America/New_York').tz_convert('UTC')
    test = labeled.loc[(labeled.date >= SPLIT_DATE) & (labeled.date < '2026-10-01')].sort_values(['date', 'game_id'])
    if len(test) < MIN_TEST:
        raise ValueError('insufficient September holdout games')
    cutoff = min(boundary, predicted.loc[test.index].min())
    train = labeled.loc[(labeled.date < SPLIT_DATE) & (completed < cutoff)].sort_values(['date', 'game_id'])
    if len(train) < MIN_TRAIN:
        raise ValueError('insufficient pre-September training games')
    return train, test


def metrics(y, p):
    p = np.asarray(p, dtype=float)
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError('invalid probability')
    return {'games': int(len(y)), 'accuracy': float(accuracy_score(y, p >= .5)),
            'brier': float(brier_score_loss(y, p)),
            'logloss': float(log_loss(y, p, labels=[0, 1]))}


def fit_brier(x_train, y_train, x_test, features, rounds=None):
    dataset = lgb.Dataset(x_train, label=np.asarray(y_train, dtype=float),
                         feature_name=list(features), free_raw_data=False)
    booster = lgb.train(booster_params(PARAMS), dataset,
                        num_boost_round=int(rounds or PARAMS['n_estimators']), feval=brier_metric)
    return booster, _probability(booster.predict(x_test, raw_score=True))


def export_probability_model(booster, path, validation_frame):
    """Keep the learned trees and persist LightGBM's native sigmoid output link.

    Custom-objective text has no objective header. The binary header controls
    only inference; training remains Brier, as recorded in metrics.json.
    Fail closed if the text format changes or either prediction check fails.
    """
    text = booster.model_to_string()
    header, trees = text.split('\nTree=0\n', 1)
    if not header.startswith('tree\nversion=v4\n') or any(
            line.startswith('objective=') for line in header.splitlines()):
        raise ValueError('unexpected custom-objective LightGBM format')
    if '[objective: custom]' not in trees:
        raise ValueError('expected a custom-objective booster')
    text = header.rstrip('\n') + '\nobjective=binary sigmoid:1\n\nTree=0\n' + trees.replace(
        '[objective: custom]', '[objective: binary]', 1)
    loaded = lgb.Booster(model_str=text)
    raw = booster.predict(validation_frame, raw_score=True)
    np.testing.assert_allclose(loaded.predict(validation_frame, raw_score=True), raw, atol=1e-12, rtol=0)
    np.testing.assert_allclose(loaded.predict(validation_frame), _probability(raw), atol=1e-12, rtol=0)
    loaded.save_model(str(path))
    reloaded = lgb.Booster(model_file=str(path))
    np.testing.assert_allclose(reloaded.predict(validation_frame), _probability(raw), atol=1e-12, rtol=0)
    return reloaded


def evaluate(frame, incumbent_bytes, output, input_hash):
    refs = json.loads((Path(__file__).parent / 'model_refs.json').read_bytes())
    incumbent_hash = hashlib.sha256(incumbent_bytes).hexdigest()
    if incumbent_hash != refs['lightgbm']['sha256']:
        raise ValueError('incumbent does not match unchanged model_refs.json')
    train, test = split_september(frame)
    features, omitted, coverage = choose_features(train)
    x_train, x_test = train[features].astype(float), test[features].astype(float)
    if np.isinf(x_train.to_numpy()).any() or np.isinf(x_test.to_numpy()).any():
        raise ValueError('infinite feature value')
    incumbent = lgb.Booster(model_str=incumbent_bytes.decode())
    incumbent_p = incumbent.predict(test[incumbent.feature_name()].astype(float))
    y_train, y_test = train.home_win.astype(int), test.home_win.astype(int)
    challenger, p = fit_brier(x_train, y_train, x_test, features)
    challenger_metrics, incumbent_metrics = metrics(y_test, p), metrics(y_test, incumbent_p)
    output.mkdir(parents=True, exist_ok=True)
    model_path = output / 'model.txt'
    export_probability_model(challenger, model_path, pd.concat([x_train, x_test]))
    predictions = test[['date', 'game_id', 'home_win']].copy()
    predictions['challenger_p_home'], predictions['incumbent_p_home'] = p, incumbent_p
    predictions.to_parquet(output / 'test_predictions.parquet', index=False)
    report = {
        'system': 'KS1', 'kind': 'brier_objective_challenger', 'serving_authority': False,
        'training_loss': 'brier', 'incumbent_training_loss': 'binary_logloss',
        'split_date': SPLIT_DATE,
        'train': {'start': train.date.min(), 'end': train.date.max(), 'games': len(train)},
        'test': {'start': test.date.min(), 'end': test.date.max(), 'games': len(test)},
        'features': features, 'omitted_features': omitted, 'individual_starter_training_rows': coverage,
        'challenger': challenger_metrics, 'incumbent': incumbent_metrics,
        'passes_metric_comparison': challenger_metrics['brier'] < incumbent_metrics['brier'] and challenger_metrics['logloss'] <= incumbent_metrics['logloss'],
        'promotion_eligible': False, 'promotion_requires_separate_review': True,
        'model_format': 'LightGBM native text', 'prediction_link': 'sigmoid',
        'native_probability_reload_verified': True, 'lightgbm_version': lgb.__version__,
        'model_sha256': hashlib.sha256(model_path.read_bytes()).hexdigest(),
        'incumbent_sha256': incumbent_hash, 'input_table_sha256': input_hash,
        'holdout_predictions_sha256': hashlib.sha256((output / 'test_predictions.parquet').read_bytes()).hexdigest(),
        'test_used_for_tuning': False, 'holdout_refit': False, 'hyperparameter_search': False,
        'deployment': False, 'prediction_writes': 0, 'model_reference_writes': 0,
        'limitations': ['Retrospective comparison on retained data, not prospective official grades.',
                       'Training period and eligible features differ from the incumbent; this does not isolate loss alone.'],
    }
    (output / 'metrics.json').write_bytes(encode(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--incumbent-model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Never overwrite an existing artifact, model directory, or the incumbent.
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('output must be a new or empty experiment directory')
    report = evaluate(pd.read_parquet(args.input), args.incumbent_model.read_bytes(), args.output,
                      hashlib.sha256(args.input.read_bytes()).hexdigest())
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
