"""Prespecified regularization search using only a purged development tail."""
import argparse
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss

from ks1.inventory import encode
from ks1.train import PARAMS

HOLDOUT = Path(__file__).with_name('qualification_holdout_20260914.json')
# Freeze this small search space before examining a repaired final holdout.
# No custom objective, new serving format, calibration or holdout early stopping.
TRIALS = {
    'baseline': {},
    'shallow': {'num_leaves': 3, 'n_estimators': 100, 'reg_lambda': 30.0},
    'regularized': {'num_leaves': 7, 'min_child_samples': 150,
                    'n_estimators': 100, 'reg_lambda': 50.0},
    'shallow_long': {'num_leaves': 3, 'min_child_samples': 100,
                     'n_estimators': 200, 'reg_lambda': 50.0},
    # Selected for inclusion from development-only exploration after v4
    # coverage recovery; qualification predictions remain inaccessible here.
    'shallow_shrink': {'num_leaves': 3, 'n_estimators': 150,
                       'learning_rate': .02, 'reg_lambda': 100.0},
}


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def frozen_split(frame, manifest):
    """Reserve exact identities; new games cannot move the evaluation window."""
    from ks1.retrain_recent import completion_times, MIN_TRAIN
    ids = manifest['game_ids']
    if len(ids) != 300 or len(set(ids)) != 300 or frame.game_id.duplicated().any():
        raise ValueError('frozen holdout requires 300 unique games')
    indexed = frame.set_index('game_id', drop=False)
    if not set(ids).issubset(indexed.index):
        raise ValueError('frozen holdout game unavailable')
    test = indexed.loc[ids].copy().reset_index(drop=True)
    if (test.home_win.isna().any() or not set(test.home_win.unique()).issubset({0, 1, True, False})
            or test.home_score.isna().any() or test.away_score.isna().any()):
        raise ValueError('frozen holdout labels unavailable')
    for column, key, values in (
        ('home_win', 'labels_sha256', test.home_win.astype(int).tolist()),
        ('as_of_timestamp', 'as_of_sha256', test.as_of_timestamp.tolist()),
        ('label_completed_at', 'completed_sha256', test.label_completed_at.tolist()),
    ):
        if digest(values) != manifest[key]:
            raise ValueError('frozen holdout changed: ' + column)
    boundary = completion_times(test.as_of_timestamp).min()
    completed = completion_times(frame.label_completed_at)
    predicted = completion_times(frame.as_of_timestamp)
    labeled = frame.home_win.notna() & frame.home_score.notna() & frame.away_score.notna()
    valid = labeled & completed.notna() & predicted.notna() & (predicted < completed)
    train = frame.loc[valid & (completed < boundary) & ~frame.game_id.isin(ids)].copy()
    if not set(train.home_win.unique()).issubset({0, 1, True, False}):
        raise ValueError('frozen training labels must be binary')
    train = train.assign(_completed=completed.loc[train.index]).sort_values(['_completed', 'game_id'])
    if len(train) < MIN_TRAIN:
        raise ValueError('insufficient frozen-cohort training history')
    return train.drop(columns='_completed'), test


def select(train):
    """This interface intentionally accepts no final evaluation frame."""
    from ks1.retrain_recent import (split_development, choose_features,
                                    lineup_feature, bullpen_context_feature)
    fit, validation = split_development(train)
    admitted, omitted, coverage = choose_features(fit)
    baseline = [c for c in admitted if not lineup_feature(c) and not bullpen_context_feature(c)]
    recipes = {'starter': baseline,
               'starter_plus_batters': baseline + [c for c in admitted if lineup_feature(c)],
               'starter_plus_batters_and_bullpen': admitted}
    selected, evidence = {}, {}
    for name, columns in recipes.items():
        # All admission rules, including non-null coverage thresholds, were
        # recomputed on fit above. Validation covariates cannot admit a feature.
        if not columns:
            raise ValueError('no varying development fit features')
        trials = {}
        for trial, updates in TRIALS.items():
            params = {**PARAMS, **updates}
            model = lgb.LGBMClassifier(**params).fit(fit[columns].astype(float), fit.home_win.astype(int))
            p = model.predict_proba(validation[columns].astype(float))[:, 1]
            trials[trial] = {
                'brier': float(brier_score_loss(validation.home_win.astype(int), p)),
                'logloss': float(log_loss(validation.home_win.astype(int), p, labels=[0, 1])),
                'parameters': params,
                'features_used_in_splits': [c for c, count in zip(columns, model.booster_.feature_importance()) if count > 0],
            }
        baseline = trials['baseline']
        eligible = ['baseline'] + [key for key, value in trials.items() if key != 'baseline'
                    and value['brier'] < baseline['brier'] and value['logloss'] <= baseline['logloss']]
        winner = min(eligible, key=lambda key: (trials[key]['brier'], key))
        selected[name] = trials[winner]['parameters']
        evidence[name] = {'selected_trial': winner, 'trials': trials, 'features': columns}
    best = {name: values['trials'][values['selected_trial']] for name, values in evidence.items()}
    baseline = best['starter']
    eligible = ['starter'] + [name for name in best if name != 'starter'
                and best[name]['brier'] < baseline['brier'] and best[name]['logloss'] <= baseline['logloss']]
    recipe = min(eligible, key=lambda name: (best[name]['brier'], name))
    report = {'method': 'purged_development_regularization_v1',
              'fit_games': len(fit), 'games': len(validation), 'selected': recipe,
              'metrics': {name: {key: value[key] for key in ('brier', 'logloss')}
                          for name, value in best.items()},
              'trials': evidence, 'final_holdout_used_for_selection': False,
              'feature_admission_games': len(fit), 'omitted_features': omitted,
              'feature_admission_starter_coverage': coverage,
              'fit_game_ids_sha256': digest(fit.game_id.tolist()),
              'development_game_ids_sha256': digest(validation.game_id.tolist()),
              'search_space_sha256': digest(TRIALS)}
    return selected, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--proof', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    from ks1.retrain_recent import qualified_training_population
    proof = json.loads(args.proof.read_bytes())
    if hashlib.sha256(args.input.read_bytes()).hexdigest() != proof['input_table_sha256']:
        raise ValueError('input table checksum mismatch')
    train, _ = frozen_split(pd.read_parquet(args.input), json.loads(HOLDOUT.read_bytes()))
    train, population = qualified_training_population(train, proof['source_receipts'])
    _, report = select(train)
    report.update(training_population=population, input_table_sha256=proof['input_table_sha256'],
                  holdout_manifest_sha256=hashlib.sha256(HOLDOUT.read_bytes()).hexdigest(),
                  qualification_run=False, accepted=False)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'development_selection.json').write_bytes(encode(report))
    print(json.dumps({'selected': report['selected'], 'metrics': report['metrics'],
                      'qualification_run': False}, indent=2))


if __name__ == '__main__':
    main()
