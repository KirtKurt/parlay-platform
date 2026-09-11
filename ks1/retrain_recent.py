"""Fixed September holdout for seven-day KS1 features; never publish predictions."""
import argparse
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

from ks1.features import ET, Features
from ks1.inventory import encode
from ks1.sources import aws_clients, load_existing
from ks1.table import build, contract
from ks1.train import PARAMS, select_features, save_artifact

# Fixed before inspecting results. No holdout refit or hyperparameter search.
SPLIT_DATE = '2026-09-01'
MIN_TRAIN = 500
MIN_TEST = 100
MIN_STARTER_ROWS = 100


def split_recent(frame):
    if frame.game_id.duplicated().any():
        raise ValueError('duplicate game IDs')
    labeled = frame.loc[frame.home_win.notna() & frame.home_score.notna() & frame.away_score.notna()].copy()
    if not set(labeled.home_win.unique()).issubset({True, False, 0, 1}):
        raise ValueError('invalid labels')
    # A resumed August game completed in September cannot supply a training
    # label that was unavailable at the start of the holdout.
    completed = pd.to_datetime(labeled.label_completed_at, utc=True, errors='raise')
    boundary = pd.Timestamp(SPLIT_DATE, tz=ET).tz_convert('UTC')
    train = labeled.loc[(labeled.date < SPLIT_DATE) & (completed < boundary)].sort_values(['date', 'game_id'])
    test = labeled.loc[labeled.date >= SPLIT_DATE].sort_values(['date', 'game_id'])
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST:
        raise ValueError('insufficient chronological train/test games')
    return train, test


def individual_feature(column):
    return any(column.startswith(side+'_starter_'+metric) for side in ('home', 'away')
               for metric in ('k_bb_pct_', 'whip_', 'bf_', 'appearances_'))


def choose_features(train):
    _, dictionary = contract(train.iloc[0].to_dict())
    features, omitted = select_features(train, dictionary)
    # Identity must be recorded before the game's cutoff; actual-starter labels
    # are never substituted. Require genuine prior pitcher history, not a prior
    # computed for an ID with zero recorded appearances.
    coverage = {side: int((train[side+'_starter_id'].notna() &
                           (train[side+'_starter_bf_30d'].fillna(0) > 0)).sum())
                for side in ('home', 'away')}
    if min(coverage.values()) < MIN_STARTER_ROWS:
        rejected = [c for c in features if individual_feature(c)]
        features = [c for c in features if c not in rejected]
        omitted = sorted(set(omitted) | set(rejected))
    supported = {side+'_'+key for side in ('home', 'away')
                 for key in Features([]).at(SPLIT_DATE+'T04:00:00Z', '0')}
    supported.update(('market_home_prob', 'market_total', 'market_spread'))
    if set(features) - supported:
        raise ValueError('training features missing from daily inference: '+','.join(sorted(set(features)-supported)))
    if not any(c.endswith('_7d') for c in features):
        raise ValueError('seven-day features unavailable in training')
    return features, omitted, coverage


def metrics(y, p):
    p = np.asarray(p)
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError('invalid probability')
    return {'games': len(y), 'accuracy': float(accuracy_score(y, p >= .5)),
            'brier': float(brier_score_loss(y, p)),
            'logloss': float(log_loss(y, p, labels=[0, 1]))}


def accepted(candidate, incumbent):
    return (candidate['games'] >= MIN_TEST and candidate['games'] == incumbent['games']
            and candidate['brier'] < incumbent['brier']
            and candidate['logloss'] <= incumbent['logloss'])


def evaluate(frame, incumbent_bytes, output, proof):
    train, test = split_recent(frame)
    features, omitted, coverage = choose_features(train)
    y_train, y_test = train.home_win.astype(int), test.home_win.astype(int)
    candidate = lgb.LGBMClassifier(**PARAMS).fit(train[features].astype(float), y_train)
    predictions = candidate.predict_proba(test[features].astype(float))[:, 1]
    incumbent = lgb.Booster(model_str=incumbent_bytes.decode())
    old = incumbent.predict(test[incumbent.feature_name()].astype(float))
    # One prespecified ablation isolates the seven-day contribution from the
    # expanded training period. It is reported, never used to tune the model.
    without_seven = [c for c in features if not c.endswith('_7d')]
    ablation = lgb.LGBMClassifier(**PARAMS).fit(train[without_seven].astype(float), y_train)
    ablated = ablation.predict_proba(test[without_seven].astype(float))[:, 1]
    candidate_metrics, incumbent_metrics = metrics(y_test, predictions), metrics(y_test, old)
    output.mkdir(parents=True, exist_ok=True)
    candidate.booster_.save_model(str(output/'model.txt'))
    loaded = lgb.Booster(model_file=str(output/'model.txt'))
    np.testing.assert_allclose(loaded.predict(test[features].astype(float)), predictions, atol=1e-12, rtol=0)
    report = {'system': 'KS1', 'split_date': SPLIT_DATE,
              'train': {'start': train.date.min(), 'end': train.date.max(), 'games': len(train)},
              'test': {'start': test.date.min(), 'end': test.date.max(), 'games': len(test)},
              'candidate': candidate_metrics, 'incumbent': incumbent_metrics,
              'without_seven_day': metrics(y_test, ablated),
              'accepted': accepted(candidate_metrics, incumbent_metrics),
              'promotion_rule': 'strictly lower Brier and no worse logloss than incumbent on identical September holdout',
              'features': features, 'omitted_features': omitted,
              'individual_starter_training_rows': coverage,
              'individual_starter_features_learned': [c for c in features if individual_feature(c)],
              'minimum_individual_starter_rows_per_side': MIN_STARTER_ROWS,
              'parameters': PARAMS, 'test_used_for_tuning': False, 'holdout_refit': False,
              'model_reload_verified': True,
              'model_sha256': hashlib.sha256((output/'model.txt').read_bytes()).hexdigest(),
              'incumbent_sha256': hashlib.sha256(incumbent_bytes).hexdigest(),
              'input_table_sha256': proof['input_table_sha256'],
              'provider_calls': 0, 'prediction_writes': 0, 'official_ledger_writes': 0,
              'limitations': ['Retrospective historical evaluation, not official live grades.',
                             'Prior box scores can include later scoring corrections.',
                             'Individual starter inputs require retained pregame identity and earlier pitcher boxes.',
                             'September holdout is small; future performance remains unproven.']}
    (output/'metrics.json').write_bytes(encode(report))
    (output/'input_proof.json').write_bytes(encode(proof))
    (output/'feature_list.json').write_bytes(encode(features))
    rows = test[['date', 'game_id', 'home_win']].copy()
    rows['candidate_p_home'], rows['incumbent_p_home'], rows['without_seven_p_home'] = predictions, old, ablated
    rows.to_parquet(output/'test_predictions.parquet', index=False)
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    cf, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
    bundle = load_existing(cf, s3, bucket)
    table, source_report, *_ = build(bundle)
    args.output.mkdir(parents=True, exist_ok=True)
    frame = table.to_pandas()
    history = {str(g['officialGamePk']): g for g in bundle.get('compact', [])}
    history.update({str(g['officialGamePk']): g for g in bundle.get('full', [])})
    frame['label_completed_at'] = frame.game_id.map(
        lambda pk: history.get(str(pk), {}).get('completedAtUtc'))
    frame.to_parquet(args.output/'input_table.parquet', index=False)
    refs = json.loads((Path(__file__).parent/'model_refs.json').read_bytes())
    ref = refs['lightgbm']
    body = s3.get_object(Bucket=bucket, Key=ref['key'], VersionId=ref['version_id'])['Body'].read()
    if hashlib.sha256(body).hexdigest() != ref['sha256']:
        raise ValueError('incumbent hash mismatch')
    proof = {'input_table_sha256': hashlib.sha256((args.output/'input_table.parquet').read_bytes()).hexdigest(),
             'source_receipts': source_report['source_receipts'], 'source_coverage': source_report['coverage'],
             'incumbent_ref': ref, 'provider_calls': 0}
    report = evaluate(frame, body, args.output, proof)
    # Only isolated experiment artifacts are saved. A separate reviewed model
    # reference change is required for serving; no authority or ledger write.
    save_artifact(s3, bucket, args.output)


if __name__ == '__main__':
    main()
