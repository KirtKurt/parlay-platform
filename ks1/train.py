"""Phase 2: fixed LightGBM experiment, chronological holdout, no deployment."""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss

from ks1.features import utc
from ks1.inventory import encode
from ks1.table import contract

SPLIT_DATE = '2026-01-01'
PARAMS = {'objective': 'binary', 'n_estimators': 200, 'learning_rate': 0.03,
          'num_leaves': 7, 'min_child_samples': 60, 'reg_lambda': 10.0,
          'random_state': 1729, 'n_jobs': 2, 'deterministic': True,
          'force_col_wise': True, 'verbosity': -1}


def split(frame):
    if frame.game_id.duplicated().any():
        raise ValueError('duplicate game IDs')
    labeled = frame.loc[frame.home_win.notna()].copy()
    if not set(labeled.home_win.unique()).issubset({True, False, 0, 1}):
        raise ValueError('home_win is not binary')
    train = labeled.loc[labeled.date < SPLIT_DATE].sort_values(['date', 'game_id'])
    test = labeled.loc[labeled.date >= SPLIT_DATE].sort_values(['date', 'game_id'])
    if train.empty or test.empty or train.date.max() >= test.date.min():
        raise ValueError('invalid chronological split')
    return train, test


def select_features(train, dictionary):
    eligible = [r['column'] for r in dictionary if r['role'] == 'feature']
    selected = [c for c in eligible if pd.api.types.is_numeric_dtype(train[c]) and train[c].nunique(dropna=True) > 1]
    if not selected:
        raise ValueError('no varying training features')
    return sorted(selected), sorted(set(eligible)-set(selected))


def record_baseline(targets, history):
    """Beta(1,1) season win percentages; only independently completed prior days."""
    records = defaultdict(list)
    for r in history.to_dict('records'):
        for side in ('home', 'away'):
            records[str(r[side+'_id'])].append((int(r['season']), str(r['date']), utc(r['completed_at']),
                                               str(r['game_id']), int(r['home_win']) if side == 'home' else 1-int(r['home_win'])))
    rows = []
    for r in targets.to_dict('records'):
        percentages, counts = [], []
        cutoff = utc(r['as_of_timestamp'])
        for side in ('home', 'away'):
            prior = [g for g in records[str(r[side+'_id'])] if g[0] == int(r['season'])
                     and g[1] < r['date'] and g[2] < cutoff and g[3] != str(r['game_id'])]
            counts.append(len(prior))
            percentages.append((sum(g[4] for g in prior)+1)/(len(prior)+2))
        rows.append({'game_id': str(r['game_id']),
                     'better_record_p_home': percentages[0]/sum(percentages),
                     'home_record_games': counts[0], 'away_record_games': counts[1]})
    return pd.DataFrame(rows)


def reliability(y, probabilities):
    y, probabilities = np.asarray(y), np.asarray(probabilities)
    if not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
        raise ValueError('invalid probabilities')
    bins = np.minimum((probabilities*10).astype(int), 9)
    return [{'bucket': f'{i/10:.1f}–{(i+1)/10:.1f}', 'count': int((bins == i).sum()),
             'mean_p_home': float(probabilities[bins == i].mean()) if (bins == i).any() else None,
             'home_win_rate': float(y[bins == i].mean()) if (bins == i).any() else None}
            for i in range(10)]


def metrics(y, p):
    return {'accuracy': float(accuracy_score(y, np.asarray(p) >= 0.5)),
            'brier': float(brier_score_loss(y, p)), 'games': len(y)}


def train_model(frame, baseline_history, output, input_proof):
    train, test = split(frame)
    if len(train) < 100 or len(test) < 100:
        raise ValueError('insufficient train/test games')
    _, dictionary = contract(frame.iloc[0].to_dict())
    features, omitted = select_features(train, dictionary)
    x_train, x_test = train[features].astype(float), test[features].astype(float)
    if np.isinf(x_train.to_numpy()).any() or np.isinf(x_test.to_numpy()).any():
        raise ValueError('infinite feature value')
    y_train, y_test = train.home_win.astype(int), test.home_win.astype(int)
    model = lgb.LGBMClassifier(**PARAMS)
    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, 1]
    baseline = record_baseline(test, baseline_history)
    assert baseline.game_id.tolist() == test.game_id.astype(str).tolist()
    predictions = test[['game_id', 'date', 'season', 'home_team', 'away_team', 'home_win']].reset_index(drop=True)
    predictions['p_home'] = probabilities
    for column in baseline.columns.drop('game_id'):
        predictions[column] = baseline[column]
    report = {'system': 'KS1', 'phase': 2, 'split_date': SPLIT_DATE,
              'train': {'start': train.date.min(), 'end': train.date.max(), 'games': len(train)},
              'test': {'start': test.date.min(), 'end': test.date.max(), 'games': len(test)},
              'unlabeled_excluded': int(frame.home_win.isna().sum()),
              'features': features, 'omitted_unavailable_or_constant_in_training': omitted,
              'parameters': PARAMS, 'lightgbm': metrics(y_test, probabilities),
              'better_record': metrics(y_test, baseline.better_record_p_home),
              'reliability_buckets': reliability(y_test, probabilities),
              'better_record_reliability_buckets': reliability(y_test, baseline.better_record_p_home),
              'baseline_definition': 'Available archived regular-season results, completed before cutoff and on earlier ET dates. Beta(1,1) win percentages; pick higher percentage, ties home. Probability is home percentage/(home+away percentages), a heuristic rather than a fitted calibration.',
              'baseline_history_games': len(baseline_history),
              'test_games_with_both_record_histories': int(((baseline.home_record_games > 0) & (baseline.away_record_games > 0)).sum()),
              'test_used_for_tuning': False, 'post_holdout_refit': False,
              'deployment': False, 'provider_calls': 0,
              'limitations': ['Historical retrospective features may contain later corrections.',
                              'The baseline uses retained results, not a claim of complete official standings.',
                              'Individual starter, weather and park-factor features unavailable in 2025 are omitted.',
                              'Market home probability is included; this is not a market-independent model.']}
    output.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(output/'model.txt'))
    loaded = lgb.Booster(model_file=str(output/'model.txt'))
    np.testing.assert_allclose(loaded.predict(x_test), probabilities, atol=1e-12, rtol=0)
    report['model_reload_verified'] = True
    report['model_sha256'] = hashlib.sha256((output/'model.txt').read_bytes()).hexdigest()
    (output/'metrics.json').write_bytes(encode(report))
    (output/'input_proof.json').write_bytes(encode(input_proof))
    (output/'feature_list.json').write_bytes(encode(features))
    pd.DataFrame(report['reliability_buckets']).to_csv(output/'reliability.csv', index=False)
    predictions.to_parquet(output/'test_predictions.parquet', index=False)
    pd.DataFrame({'feature': features, 'gain': model.booster_.feature_importance('gain')}).to_csv(output/'feature_importance.csv', index=False)
    (output/'metadata.json').write_bytes(encode({'system': 'KS1', 'phase': 2,
        'model_format': 'LightGBM native text', 'lightgbm_version': lgb.__version__,
        'training_git_sha': os.environ.get('GITHUB_SHA'), 'model_sha256': report['model_sha256'],
        'features': features, 'split_date': SPLIT_DATE, 'deployed': False}))
    print(json.dumps(report, indent=2))
    return report


def save_artifact(s3, bucket, output):
    if not (os.environ.get('GITHUB_ACTIONS') == 'true' and
            os.environ.get('GITHUB_REPOSITORY') == 'KirtKurt/parlay-platform' and
            os.environ.get('GITHUB_EVENT_NAME') == 'pull_request'):
        raise ValueError('model artifact writes require the authorized repository PR training job')
    files = sorted(p for p in output.iterdir() if p.is_file() and p.name != 'artifact.json')
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    run_id = hashlib.sha256(encode(hashes)).hexdigest()
    # This root is the existing artifact location discovered in Phase 1.
    prefix = 'mlb/experiments/ks1-phase2/'+run_id+'/'
    receipts = []
    for path in files:
        body, key = path.read_bytes(), prefix+path.name
        result = s3.put_object(Bucket=bucket, Key=key, Body=body, Metadata={'sha256': hashes[path.name], 'system': 'KS1'})
        args = {'Bucket': bucket, 'Key': key}
        if result.get('VersionId'):
            args['VersionId'] = result['VersionId']
        stored = s3.get_object(**args)['Body'].read()
        if hashlib.sha256(stored).hexdigest() != hashes[path.name]:
            raise ValueError('model artifact readback mismatch')
        receipts.append({'key': key, 'version_id': result.get('VersionId'), 'sha256': hashes[path.name], 'readback_verified': True})
    receipt = {'bucket': bucket, 'prefix': prefix, 'files': receipts, 'deployed': False,
               'registered_for_serving': False}
    (output/'artifact.json').write_bytes(encode(receipt))
    print(json.dumps({'artifact': f's3://{bucket}/{prefix}model.txt', 'readback_verified': True, 'deployed': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--aws', action='store_true')
    parser.add_argument('--save-artifact', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.aws:
        from ks1.sources import aws_clients
        from ks1.phase2_data import load_deployed
        _, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
        table, baseline, proof = load_deployed(s3, bucket)
        frame = table.to_pandas()
        args.output.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(args.output/'input_table.parquet', index=False)
        baseline.to_csv(args.output/'baseline_games.csv', index=False)
    else:
        if not args.input or not args.baseline or args.save_artifact:
            parser.error('local mode requires --input and --baseline; artifact saving requires --aws')
        frame = pd.read_parquet(args.input)
        baseline = pd.read_csv(args.baseline, dtype={'game_id': str, 'home_id': str, 'away_id': str})
        proof = {'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
                 'baseline_sha256': hashlib.sha256(args.baseline.read_bytes()).hexdigest()}
    train_model(frame, baseline, args.output, proof)
    if args.save_artifact:
        save_artifact(s3, bucket, args.output)


if __name__ == '__main__':
    main()
