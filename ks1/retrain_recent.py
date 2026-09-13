"""Rolling chronological holdout for seven-day KS1 features; never publish predictions."""
import argparse
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

from ks1.features import Features, MATCHUP_METRICS
from ks1.inventory import encode
from ks1.sources import aws_clients, load_existing
from ks1.table import build, contract
from ks1.train import PARAMS, select_features, save_artifact

# Feature-contract construction needs a valid timestamp but does not inspect data.
FEATURE_CONTRACT_DATE = '2026-09-01'
MIN_TRAIN = 500
EVALUATION_GAMES = 300
MIN_TEST = EVALUATION_GAMES
MIN_STARTER_ROWS = EVALUATION_GAMES


def completion_times(values):
    """Parse retained completion timestamps, accepting valid mixed ISO-8601 forms."""
    return pd.to_datetime(values, format='ISO8601', utc=True, errors='raise')


def split_recent(frame):
    if frame.game_id.duplicated().any():
        raise ValueError('duplicate game IDs')
    labeled = frame.loc[frame.home_win.notna() & frame.home_score.notna() & frame.away_score.notna()].copy()
    if not set(labeled.home_win.unique()).issubset({True, False, 0, 1}):
        raise ValueError('invalid labels')
    completed = completion_times(labeled.label_completed_at)
    predicted = completion_times(labeled.as_of_timestamp)
    valid = completed.notna() & predicted.notna() & (predicted < completed)
    eligible = labeled.loc[valid].assign(
        _label_completed_at=completed.loc[valid],
        _prediction_at=predicted.loc[valid],
    ).sort_values(['_label_completed_at', 'game_id'])
    if len(eligible) < MIN_TRAIN + MIN_TEST:
        raise ValueError('insufficient chronological train/test games')
    test = eligible.tail(EVALUATION_GAMES)
    boundary = test['_prediction_at'].min()
    # No training label may become available after the first held-out feature
    # vector was frozen, including overlapping games from the same slate.
    train = eligible.loc[eligible['_label_completed_at'] < boundary]
    if len(train) < MIN_TRAIN:
        raise ValueError('insufficient chronological train/test games')
    train = train.drop(columns=['_label_completed_at', '_prediction_at'])
    test = test.drop(columns=['_label_completed_at', '_prediction_at'])
    return train, test


def individual_feature(column):
    return any(column.startswith(side+'_starter_') for side in ('home', 'away'))


def pitcher_context_feature(column):
    return any(column.startswith(side+'_pitcher_context_') for side in ('home', 'away'))


def choose_features(train):
    _, dictionary = contract(train.iloc[0].to_dict())
    features, omitted = select_features(train, dictionary)
    # Identity must be recorded before the game's cutoff; actual-starter labels
    # are never substituted. Require genuine prior pitcher history, not a prior
    # computed for an ID with zero recorded appearances.
    coverage = {side: int((train[side+'_starter_id'].notna() &
                           (pd.to_numeric(train[side+'_starter_bf_30d'], errors='coerce') > 0)).sum())
                for side in ('home', 'away')}
    if min(coverage.values()) < MIN_STARTER_ROWS:
        rejected = [c for c in features if individual_feature(c)]
        features = [c for c in features if c not in rejected]
        omitted = sorted(set(omitted) | set(rejected))
    # An observed starter ID does not prove that a particular box/Statcast
    # metric is covered. Do not let LightGBM learn an advanced field from a
    # handful of non-null rows while silently treating the rest as missing.
    sparse = [c for c in features if individual_feature(c)
              and int(train[c].notna().sum()) < MIN_STARTER_ROWS]
    features = [c for c in features if c not in sparse]
    omitted = sorted(set(omitted) | set(sparse))
    # Historical point-in-time summaries can accelerate shadow learning without
    # claiming confirmed identity.  Each learned field still needs the same
    # 300-row floor on both sides.
    context_sparse = [c for c in features if pitcher_context_feature(c)
                      and int(train[c].notna().sum()) < MIN_STARTER_ROWS]
    features = [c for c in features if c not in context_sparse]
    omitted = sorted(set(omitted) | set(context_sparse))
    supported = {side+'_'+key for side in ('home', 'away')
                 for key in Features([]).at(FEATURE_CONTRACT_DATE+'T04:00:00Z', '0')}
    supported.update(side+'_starter_'+metric for side in ('home', 'away') for metric in MATCHUP_METRICS)
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


def pitcher_promotion_ready(candidate, incumbent, context_features, prospective_rows):
    return bool(accepted(candidate, incumbent) and context_features
                and prospective_rows >= EVALUATION_GAMES)


def prospective_context_coverage(frame, context_features):
    prospective = frame.pitcher_context_evidence.eq(
        'frozen_versioned_ks1_profile') & frame.historical_pitcher_context_mode.isna()
    per_feature = {column: int((prospective & frame[column].notna()).sum())
                   for column in context_features}
    complete = frame[context_features].notna().all(axis=1) if context_features else False
    return per_feature, int((prospective & complete).sum())


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
    context_features = [c for c in features if pitcher_context_feature(c)]
    prospective_feature_coverage, prospective_context_rows = prospective_context_coverage(
        test, context_features)
    statistical_gate = accepted(candidate_metrics, incumbent_metrics)
    promotion_ready = pitcher_promotion_ready(
        candidate_metrics, incumbent_metrics, context_features, prospective_context_rows)
    output.mkdir(parents=True, exist_ok=True)
    candidate.booster_.save_model(str(output/'model.txt'))
    loaded = lgb.Booster(model_file=str(output/'model.txt'))
    np.testing.assert_allclose(loaded.predict(test[features].astype(float)), predictions, atol=1e-12, rtol=0)
    report = {'system': 'KS1', 'split_date': test.date.min(),
              'split_completed_at': min(completion_times(test.label_completed_at)).isoformat(),
              'split_prediction_at': min(completion_times(test.as_of_timestamp)).isoformat(),
              'train': {'start': train.date.min(), 'end': train.date.max(), 'games': len(train)},
              'test': {'start': test.date.min(), 'end': test.date.max(), 'games': len(test)},
              'candidate': candidate_metrics, 'incumbent': incumbent_metrics,
              'without_seven_day': metrics(y_test, ablated),
              'accepted': promotion_ready,
              'statistical_gate_passed': statistical_gate,
              'promotion_rule': 'strictly lower Brier and no worse logloss than incumbent on identical trailing 300-game holdout',
              'pitcher_promotion_rule': 'candidate must learn verified pitcher context and all 300 holdout games must carry prospective pregame pitcher context',
              'evaluation_window_games': EVALUATION_GAMES,
              'features': features, 'omitted_features': omitted,
              'individual_starter_training_rows': coverage,
              'individual_starter_features_learned': [c for c in features if individual_feature(c)],
              'individual_feature_training_rows': {
                  c: int(train[c].notna().sum()) for c in features if individual_feature(c)},
              'historical_pitcher_context_training_rows': {
                  side: int(train[side+'_pitcher_context_quality'].notna().sum())
                  for side in ('home', 'away')},
              'pitcher_context_features_learned': context_features,
              'pitcher_context_feature_training_rows': {
                  c: int(train[c].notna().sum()) for c in context_features},
              'prospective_pitcher_context_feature_rows': prospective_feature_coverage,
              'prospective_pitcher_context_test_rows': prospective_context_rows,
              'minimum_individual_starter_rows_per_side': MIN_STARTER_ROWS,
              'parameters': PARAMS, 'test_used_for_tuning': False, 'holdout_refit': False,
              'model_reload_verified': True,
              'model_sha256': hashlib.sha256((output/'model.txt').read_bytes()).hexdigest(),
              'incumbent_sha256': hashlib.sha256(incumbent_bytes).hexdigest(),
              'input_table_sha256': proof['input_table_sha256'],
              'provider_calls': 0, 'prediction_writes': 0, 'official_ledger_writes': 0,
              'limitations': ['Rolling retrospective evaluation, not official live grades.',
                             'Prior box scores can include later scoring corrections.',
                             'Individual starter inputs require retained pregame identity and earlier pitcher boxes.',
                             'The trailing 300-game holdout does not establish future performance.']}
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
             'optional_reads': source_report['optional_reads'],
             'incumbent_ref': ref, 'provider_calls': 0}
    report = evaluate(frame, body, args.output, proof)
    # Only isolated experiment artifacts are saved. A separate reviewed model
    # reference change is required for serving; no authority or ledger write.
    save_artifact(s3, bucket, args.output)


if __name__ == '__main__':
    main()
