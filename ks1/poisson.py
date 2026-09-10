"""Phase 3: two regularized run regressions on the frozen Phase 2 experiment."""
import argparse
import hashlib
import json
from pathlib import Path
import warnings

import numpy as np
import pandas as pd
import scipy
from scipy.stats import skellam
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_poisson_deviance, root_mean_squared_error

from ks1.inventory import encode
from ks1.table import contract
from ks1.train import metrics, reliability, select_features, split

PHASE2_RUN = 34431359524
RECEIPT_SHA = '9281b750aad0821ccfb5090a56fd37ce83077688ca3735b37548737af274f06d'
PARAMETERS = {'alpha': 1.0, 'max_iter': 2000, 'tol': 1e-8, 'solver': 'lbfgs'}


def verify_reference(folder):
    receipt = folder / 'artifact.json'
    if hashlib.sha256(receipt.read_bytes()).hexdigest() != RECEIPT_SHA:
        raise ValueError('not the accepted Phase 2 artifact receipt')
    value = json.loads(receipt.read_bytes())
    hashes = {}
    for item in value['files']:
        name = Path(item['key']).name
        body = (folder / name).read_bytes()
        if hashlib.sha256(body).hexdigest() != item['sha256']:
            raise ValueError('Phase 2 artifact hash mismatch: ' + name)
        hashes[name] = item['sha256']
    return {'phase2_run': PHASE2_RUN, 'receipt_sha256': RECEIPT_SHA,
            'verified_files': hashes, 'same_table_verified': True}


def candidates(side):
    opponent = 'away' if side == 'home' else 'home'
    fields = [f'{side}_offense_{stat}_{d}d' for stat in ('ops', 'iso', 'pa', 'games') for d in (10, 30, 75)]
    fields += [f'{opponent}_{kind}_{stat}_{d}d' for kind in ('starter', 'team_starter')
               for stat in ('k_bb_pct', 'whip', 'bf', 'appearances') for d in (10, 30, 75)]
    fields += [f'{opponent}_bullpen_{stat}_{d}d' for stat in ('pitches', 'outs') for d in (1, 3, 5)]
    fields += [f'{s}_{stat}' for s in (side, opponent) for stat in ('history_games', 'rest_days', 'travel_km')]
    return sorted(fields + ['park_run_factor', 'park_hr_factor', 'temp', 'wind_speed'])


def make_estimator():
    return make_pipeline(SimpleImputer(strategy='median', add_indicator=True),
                         StandardScaler(), PoissonRegressor(**PARAMETERS))


def export_estimator(estimator, features):
    imputer, scaler, regressor = estimator.steps[0][1], estimator.steps[1][1], estimator.steps[2][1]
    return {'features': features, 'medians': imputer.statistics_.tolist(),
            'missing_indicator_indices': imputer.indicator_.features_.tolist(),
            'mean': scaler.mean_.tolist(), 'scale': scaler.scale_.tolist(),
            'coefficients': regressor.coef_.tolist(), 'intercept': float(regressor.intercept_)}


def predict_exported(model, frame):
    x = frame[model['features']].astype(float).to_numpy()
    missing = np.isnan(x)
    x = np.where(missing, np.asarray(model['medians']), x)
    x = np.column_stack([x, missing[:, model['missing_indicator_indices']]])
    x = (x - np.asarray(model['mean'])) / np.asarray(model['scale'])
    return np.exp(x @ np.asarray(model['coefficients']) + model['intercept'])


def home_probability(lambda_home, lambda_away):
    home, away = np.broadcast_arrays(np.asarray(lambda_home, dtype=float), np.asarray(lambda_away, dtype=float))
    if not (np.isfinite(home).all() and np.isfinite(away).all() and (home > 0).all() and (away > 0).all()):
        raise ValueError('Poisson rates must be finite and positive')
    tie = skellam.pmf(0, home, away)
    p = skellam.sf(0, home, away) + 0.5 * tie
    if not (np.isfinite(p).all() and (p >= 0).all() and (p <= 1).all()):
        raise ValueError('invalid Poisson win probabilities')
    return p, tie


def align_reference(test, reference):
    if reference.game_id.duplicated().any() or set(reference.game_id) != set(test.game_id):
        raise ValueError('Phase 2 and Phase 3 test game IDs differ')
    aligned = reference.set_index('game_id').loc[test.game_id].reset_index()
    for field in ('date', 'season', 'home_team', 'away_team', 'home_win'):
        if aligned[field].tolist() != test[field].tolist():
            raise ValueError('Phase 2 and Phase 3 test data differ: ' + field)
    return aligned


def run(folder, output):
    proof = verify_reference(folder)
    frame = pd.read_parquet(folder / 'input_table.parquet')
    phase2 = json.loads((folder / 'metrics.json').read_bytes())
    train, test = split(frame)
    for name, part in [('train', train), ('test', test)]:
        observed = {'start': part.date.min(), 'end': part.date.max(), 'games': len(part)}
        if observed != phase2[name]:
            raise ValueError('Phase 2 split changed')
        scores = part[['home_score', 'away_score']].astype(float).to_numpy()
        if not (np.isfinite(scores).all() and (scores >= 0).all() and (scores == np.floor(scores)).all()):
            raise ValueError('missing or invalid run labels')
        if not np.array_equal(scores[:, 0] > scores[:, 1], part.home_win.astype(bool)):
            raise ValueError('score labels disagree with winner')
    reference = align_reference(test, pd.read_parquet(folder / 'test_predictions.parquet'))
    _, dictionary = contract(train.iloc[0].to_dict())
    available, _ = select_features(train, dictionary)
    fitted, rates, omitted = {}, {}, {}
    for side in ('home', 'away'):
        features = sorted(set(candidates(side)) & set(available))
        if not features:
            raise ValueError('no run-model features')
        omitted[side] = sorted(set(candidates(side)) - set(features))
        x_train, x_test = train[features].astype(float), test[features].astype(float)
        if np.isinf(x_train.to_numpy()).any() or np.isinf(x_test.to_numpy()).any():
            raise ValueError('infinite feature value')
        estimator = make_estimator()
        with warnings.catch_warnings():
            warnings.simplefilter('error', ConvergenceWarning)
            estimator.fit(x_train, train[side + '_score'].astype(float))
        rates[side] = estimator.predict(x_test)
        fitted[side] = export_estimator(estimator, features)
    model = {'system': 'KS1', 'phase': 3, 'kind': 'dual Poisson log-link regression',
             'parameters': PARAMETERS, 'home': fitted['home'], 'away': fitted['away'],
             'tie_resolution': 'P(H>A) + 0.5*P(H=A)', 'deployed': False}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'poisson_model.json').write_bytes(encode(model))
    loaded = json.loads((output / 'poisson_model.json').read_bytes())
    for side in ('home', 'away'):
        np.testing.assert_allclose(predict_exported(loaded[side], test), rates[side], atol=1e-12, rtol=0)
    p, tie = home_probability(rates['home'], rates['away'])
    predictions = reference.rename(columns={'p_home': 'lightgbm_p_home'}).copy()
    predictions['lambda_home'] = rates['home']
    predictions['lambda_away'] = rates['away']
    predictions['proj_total'] = rates['home'] + rates['away']
    predictions['p_home'] = p
    predictions['poisson_tie_prob'] = tie
    for side in ('home', 'away'):
        predictions[side + '_score'] = test[side + '_score'].to_numpy()
    y = test.home_win.astype(int)
    comparison = {name: metrics(y, predictions[column]) for name, column in
                  [('poisson', 'p_home'), ('lightgbm', 'lightgbm_p_home'), ('better_record', 'better_record_p_home')]}
    for name in ('lightgbm', 'better_record'):
        for field in ('accuracy', 'brier', 'games'):
            if abs(comparison[name][field] - phase2[name][field]) > 1e-12:
                raise ValueError('accepted Phase 2 metrics changed')
    run_metrics = {}
    for side, predicted, actual in [('home', rates['home'], test.home_score),
                                    ('away', rates['away'], test.away_score),
                                    ('total', predictions.proj_total, test.home_score + test.away_score)]:
        run_metrics[side] = {'mean_predicted': float(np.mean(predicted)), 'mean_observed': float(np.mean(actual)),
                             'mae': float(mean_absolute_error(actual, predicted)),
                             'rmse': float(root_mean_squared_error(actual, predicted)),
                             'poisson_deviance': float(mean_poisson_deviance(actual, predicted))}
    report = {'system': 'KS1', 'phase': 3, 'train': phase2['train'], 'test': phase2['test'],
              'unlabeled_excluded': phase2['unlabeled_excluded'], 'comparison': comparison,
              'run_metrics': run_metrics, 'features': {s: fitted[s]['features'] for s in fitted},
              'omitted_unavailable_or_constant_in_training': omitted, 'parameters': PARAMETERS,
              'reliability_buckets': {n: reliability(y, predictions[c]) for n, c in
                  [('poisson', 'p_home'), ('lightgbm', 'lightgbm_p_home'), ('better_record', 'better_record_p_home')]},
              'tie_resolution': model['tie_resolution'], 'mean_poisson_tie_probability': float(tie.mean()),
              'same_test_games_verified': True, 'phase2_metrics_unchanged': True, 'model_reload_verified': True,
              'test_used_for_tuning': False, 'lightgbm_refitted': False, 'deployed': False, 'provider_calls': 0,
              'versions': {'numpy': np.__version__, 'pandas': pd.__version__, 'scikit_learn': sklearn.__version__, 'scipy': scipy.__version__},
              'limitations': ['Team offense is used because historical confirmed lineups are unavailable.',
                  'Opponent team-starter process features substitute for individual starters unavailable in training.',
                  'Park and weather columns unavailable in training are omitted; neutral environment is implicit.',
                  'Run targets are final full-game scores, including extra innings. Independent Poisson rates and 50/50 tie allocation are approximations, not an extra-inning or PA simulation.',
                  'LightGBM includes market probability; Poisson uses baseball features without market inputs.',
                  *phase2['limitations']]}
    (output / 'comparison.json').write_bytes(encode(report))
    (output / 'input_receipt.json').write_bytes(encode(proof))
    predictions.to_parquet(output / 'predictions.parquet', index=False)
    predictions.to_csv(output / 'predictions.csv', index=False)
    pd.DataFrame([{'model': name, **row} for name, rows in report['reliability_buckets'].items()
                  for row in rows]).to_csv(output / 'reliability.csv', index=False)
    print(json.dumps(report, indent=2))
    print(predictions[['date', 'away_team', 'home_team', 'p_home', 'lambda_home', 'lambda_away', 'proj_total']].head(20).to_string(index=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase2-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve() == args.phase2_dir.resolve():
        parser.error('output must differ from the accepted Phase 2 directory')
    run(args.phase2_dir, args.output)


if __name__ == '__main__':
    main()
