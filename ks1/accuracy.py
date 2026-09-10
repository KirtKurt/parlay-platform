"""Fixed accuracy add-on experiment; paired dates, no tuning or deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import skellam
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import mean_absolute_error

from ks1.inventory import encode
from ks1.poisson import (candidates, export_estimator, home_probability, make_estimator,
                         predict_exported, verify_reference)
from ks1.train import PARAMS, metrics, reliability, split

CORE = sorted([f'{s}_{name}' for s in ('home', 'away') for name in (
    *[f'offense_{stat}_{d}d' for stat in ('ops', 'iso') for d in (10, 30, 75)],
    'offense_pa_30d', *[f'team_starter_k_bb_pct_{d}d' for d in (10, 30, 75)],
    'team_starter_bf_30d', 'team_starter_appearances_30d',
    *[f'bullpen_pitches_{d}d' for d in (1, 3, 5)], 'rest_days', 'history_games')]+['market_home_prob'])
assert len(CORE) == 35


def agreement(y, lgb_p, poisson_p):
    y, a, b = np.asarray(y), np.asarray(lgb_p), np.asarray(poisson_p)
    eligible = ((a >= .5) == (b >= .5)) & (np.maximum(a, 1-a) >= .60) & (np.maximum(b, 1-b) >= .60)
    return {'games': int(eligible.sum()), 'coverage': float(eligible.mean()) if len(y) else 0.,
            'accuracy': float(((a[eligible] >= .5) == y[eligible]).mean()) if eligible.any() else None}, eligible


def fit_pair(train, test, features, output, *, target='full'):
    output.mkdir(parents=True, exist_ok=True)
    params = dict(PARAMS)
    if target == 'f5':
        params.update(objective='multiclass', num_class=3)
        labels = np.where(train.f5_home_runs > train.f5_away_runs, 2,
                          np.where(train.f5_home_runs == train.f5_away_runs, 1, 0))
    else:
        labels = train.home_win.astype(int)
    model = lgb.LGBMClassifier(**params).fit(train[features].astype(float), labels)
    model.booster_.save_model(str(output/'lightgbm.txt'))
    raw = model.predict_proba(test[features].astype(float))
    reloaded = lgb.Booster(model_file=str(output/'lightgbm.txt')).predict(test[features].astype(float))
    np.testing.assert_allclose(reloaded, raw if target == 'f5' else raw[:, 1], atol=1e-12, rtol=0)
    p = raw[:, 2] if target == 'f5' else raw[:, 1]
    rates, exports, used = {}, {}, {}
    for side in ('home', 'away'):
        names = sorted(set(features) & (set(candidates(side)) | {f for f in features if f not in CORE}))
        estimator = make_estimator()
        label = side+'_score' if target == 'full' else 'f5_'+side+'_runs'
        with warnings.catch_warnings():
            warnings.simplefilter('error', ConvergenceWarning)
            estimator.fit(train[names].astype(float), train[label].astype(float))
        rates[side] = estimator.predict(test[names].astype(float))
        exports[side] = export_estimator(estimator, names)
        used[side] = names
    (output/'poisson.json').write_bytes(encode({'target': target, **exports}))
    saved = json.loads((output/'poisson.json').read_bytes())
    for side in ('home', 'away'):
        np.testing.assert_allclose(predict_exported(saved[side], test), rates[side], atol=1e-12, rtol=0)
    if target == 'f5':
        pp, tie = skellam.sf(0, rates['home'], rates['away']), skellam.pmf(0, rates['home'], rates['away'])
    else:
        pp, tie = home_probability(rates['home'], rates['away'])
    prediction = {'lightgbm_p_home': p, 'poisson_p_home': pp, 'lambda_home': rates['home'],
                  'lambda_away': rates['away'], 'projected_total': rates['home']+rates['away']}
    if target == 'f5':
        prediction.update(lightgbm_p_tie=raw[:, 1], lightgbm_p_away=raw[:, 0],
                          poisson_p_tie=tie, poisson_p_away=1-pp-tie)
    metadata = {'features': features, 'poisson_features': used, 'parameters': params, 'target': target,
                'reload_verified': True, 'train_games': len(train), 'test_games': len(test), 'deployed': False,
                'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.name != 'metadata.json'}}
    (output/'metadata.json').write_bytes(encode(metadata))
    return prediction, metadata


def evaluate(y, pred):
    value = {name: metrics(y, pred[name+'_p_home']) for name in ('lightgbm', 'poisson')}
    value['agreement_p60'], _ = agreement(y, pred['lightgbm_p_home'], pred['poisson_p_home'])
    return value


def paired_delta(y, before, after, dates):
    losses = (np.asarray(after)-np.asarray(y))**2-(np.asarray(before)-np.asarray(y))**2
    grouped = pd.DataFrame({'date': np.asarray(dates), 'loss': losses}).groupby('date').loss.agg(['sum', 'count'])
    rng = np.random.default_rng(1729)
    idx = rng.integers(0, len(grouped), (1000, len(grouped)))
    samples = grouped['sum'].to_numpy()[idx].sum(axis=1)/grouped['count'].to_numpy()[idx].sum(axis=1)
    return {'after_minus_before_brier': float(losses.mean()), 'date_block_bootstrap_95pct': np.quantile(samples, [.025,.975]).tolist()}


def run(phase2, archive_dir, output):
    from ks1.accuracy_features import build, load_archive, ADDONS
    proof = verify_reference(phase2)
    original = pd.read_parquet(phase2/'input_table.parquet')
    bundle, receipt = load_archive(archive_dir)
    frame, coverage = build(original, bundle)
    # 2020 is explicitly excluded. DH/bullpen states are audit flags rather
    # than allowing outcome-dependent cohort changes in the primary comparison.
    frame = frame.loc[frame.season != 2020].copy()
    train, test = split(frame)
    coverage['by_model_split'] = {name: {c: int(part[c].notna().sum()) for c in ADDONS if not c.endswith('_missing')}
                                 for name,part in [('train',train),('test',test)]}
    available = [c for c in ADDONS if train[c].notna().any() and train[c].nunique(dropna=True) > 1]
    features = CORE + available
    output.mkdir(parents=True, exist_ok=True)
    export_columns = ['game_id','date','season','home_team','away_team','home_id','away_id','home_win','home_score','away_score',
                      'as_of_timestamp','commence_time','doubleheader_status','bullpen_game_status','season_status','f5_home_runs','f5_away_runs']+CORE+ADDONS
    frame[export_columns].to_parquet(output/'feature_table.parquet', index=False)
    refs = pd.read_parquet(phase2/'test_predictions.parquet').set_index('game_id').loc[test.game_id]
    if refs.home_win.tolist() != test.home_win.tolist():
        raise ValueError('accepted test labels changed')
    poisson_bytes = (Path(__file__).parent/'poisson_model.json').read_bytes()
    model_refs = json.loads((Path(__file__).parent/'model_refs.json').read_bytes())
    if hashlib.sha256(poisson_bytes).hexdigest() != model_refs['poisson']['sha256']:
        raise ValueError('accepted Poisson artifact hash mismatch')
    accepted_poisson = json.loads(poisson_bytes)
    ah, aa = [predict_exported(accepted_poisson[s], test) for s in ('home', 'away')]
    before = {'lightgbm_p_home': refs.p_home.to_numpy(), 'poisson_p_home': home_probability(ah, aa)[0]}
    control, control_meta = fit_pair(train, test, CORE, output/'compact_control')
    after, after_meta = fit_pair(train, test, features, output/'addons')
    y = test.home_win.astype(int).to_numpy()
    predictions = test[['game_id', 'date', 'home_team', 'away_team', 'home_win', 'as_of_timestamp',
                        'doubleheader_status', 'bullpen_game_status']].reset_index(drop=True)
    comparison = {}
    for name, value in [('accepted_before', before), ('compact_control', control), ('addons_after', after)]:
        comparison[name] = evaluate(y, value)
        for c, v in value.items(): predictions[name+'_'+c] = v
    report = {'system': 'KS1', 'experiment': 'accuracy add-ons', 'train': {'start': train.date.min(), 'end': train.date.max(), 'games': len(train)},
              'test': {'start': test.date.min(), 'end': test.date.max(), 'games': len(test)}, 'core_features': CORE,
              'added_trainable_features': available, 'omitted_no_training_variation': sorted(set(ADDONS)-set(available)),
              'comparison': comparison, 'coverage': coverage,
              'agreement_definition': 'Both models choose the same side and both assign that side >=0.60. Away confidence is 1-P(home). Counts and coverage shown; different qualifying sets are not a paired accuracy comparison.',
              'test_used_for_tuning': False, 'test_previously_reported': True, 'model_reload_verified': True,
              'same_train_and_test_for_control_and_addons': True, 'deployment': False, 'provider_calls': 0, 'aws_writes': 0}
    report['paired_brier_deltas'] = {base: {m: paired_delta(y, pred[m+'_p_home'], after[m+'_p_home'], test.date)
        for m in ('lightgbm', 'poisson')} for base, pred in [('accepted_before', before), ('compact_control', control)]}
    _, old_agree = agreement(y, before['lightgbm_p_home'], before['poisson_p_home'])
    _, new_agree = agreement(y, after['lightgbm_p_home'], after['poisson_p_home'])
    common = old_agree & new_agree
    report['common_agreement_cohort'] = {'games': int(common.sum()), **{name: float(((p[common] >= .5) == y[common]).mean()) if common.any() else None
        for name,p in [('before_accuracy',before['lightgbm_p_home']),('after_accuracy',after['lightgbm_p_home'])]}}
    report['run_rates'] = {name: {s: {'mean_expected': float(pred['lambda_'+s].mean()),
        'mae': float(mean_absolute_error(test[s+'_score'],pred['lambda_'+s]))} for s in ('home','away')}
        for name,pred in [('compact_control',control),('addons_after',after)]}
    ftrain = train.dropna(subset=['f5_home_runs','f5_away_runs']); ftest = test.dropna(subset=['f5_home_runs','f5_away_runs'])
    classes = lambda part: np.where(part.f5_home_runs > part.f5_away_runs, 2, np.where(part.f5_home_runs == part.f5_away_runs, 1, 0))
    if len(ftrain) >= 100 and len(ftest) >= 50 and len(set(classes(ftrain))) == 3:
        fp, fm = fit_pair(ftrain, ftest, features, output/'f5_addons', target='f5')
        fc, _ = fit_pair(ftrain, ftest, CORE, output/'f5_control', target='f5')
        fy = (ftest.f5_home_runs > ftest.f5_away_runs).astype(int)
        fpred = ftest[['game_id','date','f5_home_runs','f5_away_runs']].reset_index(drop=True)
        for name, value in [('before',fc),('after',fp)]:
            for c,v in value.items():fpred[name+'_'+c]=v
        fpred.to_parquet(output/'f5_predictions.parquet',index=False)
        report['f5'] = {'status': 'trained', 'train_games': len(ftrain), 'test_games': len(ftest),
            'home_win_brier': {n: metrics(fy,p['lightgbm_p_home']) for n,p in [('before',fc),('after',fp)]},
            'home_run_mae': float(mean_absolute_error(ftest.f5_home_runs, fp['lambda_home'])),
            'away_run_mae': float(mean_absolute_error(ftest.f5_away_runs, fp['lambda_away'])),
            'tie_definition': 'F5 has three outcomes. Home-win probability is unconditional; ties are a separate class, not half-wins.'}
    else:
        report['f5'] = {'status': 'blocked_no_sufficient_archived_inning_labels', 'train_games': len(ftrain), 'test_games': len(ftest),
                         'reason': 'Cannot infer first-five scores from full-game totals or manufacture F5 targets.'}
    predictions['f5_status'] = report['f5']['status']
    predictions.to_parquet(output/'test_predictions.parquet', index=False)
    predictions.to_csv(output/'test_predictions.csv', index=False)
    pd.DataFrame([{'model': name, **bucket} for name,pred in [('accepted_before',before),('compact_control',control),('addons_after',after)]
                  for family in ('lightgbm','poisson') for bucket in [{**b,'family':family} for b in reliability(y,pred[family+'_p_home'])]]).to_csv(output/'reliability.csv',index=False)
    (output/'comparison.json').write_bytes(encode(report))
    (output/'input_receipt.json').write_bytes(encode({'phase2': proof, 'archive': receipt}))
    print(json.dumps({k:v for k,v in report.items() if k not in ('coverage','core_features','omitted_no_training_variation')},indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase2-dir', type=Path, required=True)
    parser.add_argument('--archive-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.phase2_dir, args.archive_dir, args.output)
