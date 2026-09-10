"""Eight isolated add-on toggles against the hash-pinned Phase 2/3 champion.

This is retrospective feature selection on an already-reported holdout, not a
new estimate of prospective performance. No provider, AWS or deployment calls.
"""
import argparse
import hashlib
import json
import shutil
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import skellam
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

from ks1.accuracy import agreement, paired_delta
from ks1.accuracy_features import build, load_archive
from ks1.inventory import encode
from ks1.poisson import (align_reference, export_estimator, home_probability,
                         make_estimator, predict_exported, verify_reference)
from ks1.train import split


GROUPS = {
    'A': ('Expected IP + opener', ['home_starter_expected_ip', 'away_starter_expected_ip',
                                  'home_opener', 'away_opener']),
    'B': ('Lineup platoon advantage', ['platoon_advantage_count_diff']),
    'C': ('2-5 scratch flag', ['middle_order_absent_count_diff']),
    'D': ('Defense OAA/DRS', ['defense_oaa_diff', 'defense_drs_diff']),
    'E': ('Handed park + outdoor weather', ['park_hr_factor_lhb', 'park_hr_factor_rhb',
                                           'outdoor_forecast_temp_f']),
    'F': ('F5 separate head', ['f5_home_runs', 'f5_away_runs']),
    'G': ('As-of weather safeguard', ['outdoor_forecast_temp_f']),
    'H': ('DH / scheduled bullpen / 2020 flags', ['doubleheader_flag', 'scheduled_bullpen_flag',
                                               'season_2020_flag']),
}
WEATHER = ('temp', 'wind_speed', 'wind_dir', 'outdoor_forecast_temp_f')
TOLERANCE = 1e-12
# Native artifact reloads remain 1e-12. Numerical optimization/array layout
# can shift a Poisson refit around 1e-10 despite identical feature values.
REFIT_TOLERANCE = 1e-8


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(value))


def add_audit_flags(frame):
    """Experimental H values only; unknown is not a negative, and no rows drop."""
    result = frame.copy()
    result['doubleheader_flag'] = result.doubleheader_status.map({'flagged': 1., 'official_single': 0.})
    # No retained source asserts a scheduled bullpen role. Never infer from an
    # actual short start, missing starter, or realized reliever usage.
    result['scheduled_bullpen_flag'] = np.nan
    result['season_2020_flag'] = (result.season == 2020).astype(float)
    for name in GROUPS['H'][1]:
        result[name+'_missing'] = result[name].isna().astype(float)
    return result


def weather_guard(frame, safe_features):
    """Only pre-cutoff, original outdoor forecasts from accuracy_features.build.

    Wind has no qualifying retained forecast source. Never pass through a raw
    weather value. The accepted models have no weather inputs, so this policy
    is a safeguard rather than a claim that their metrics contain weather leak.
    """
    if frame.game_id.tolist() != safe_features.game_id.tolist():
        raise ValueError('weather evidence game IDs differ')
    result = frame.copy()
    for field in WEATHER:
        if field in result:
            result[field] = (safe_features.outdoor_forecast_temp_f.to_numpy()
                             if field in ('temp', 'outdoor_forecast_temp_f') else np.nan)
    return result


def coverage(train, test, columns):
    splits = {}
    for name, part in [('train', train), ('test', test)]:
        rates = part[columns].isna().mean()*100
        splits[name] = {'missing_pct': float(rates.mean()),
                        'by_column_missing_pct': {c: float(rates[c]) for c in columns},
                        'any_required_value_missing_pct': float(part[columns].isna().any(axis=1).mean()*100)}
    return {**splits, 'gate_missing_pct': max(v['missing_pct'] for v in splits.values()),
            'definition': 'Mean missing raw-value cells, excluding missingness/provenance indicators; gate uses worse of train/test. All named group members count, including unavailable ones.'}


def isolated_features(group, train):
    if group in ('F', 'G'):
        return []  # F5 labels never enter full-game X or y; G is a policy.
    values = GROUPS[group][1]
    names = values + [c+'_missing' for c in values]
    if group == 'A':
        names += ['home_starter_projection_missing', 'away_starter_projection_missing']
    return sorted(c for c in names if pd.api.types.is_numeric_dtype(train[c])
                  and train[c].nunique(dropna=True) > 1)


def metric_pair(y, prediction):
    result = {}
    for family in ('lightgbm', 'poisson'):
        p = np.asarray(prediction[family+'_p_home'])
        if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
            raise ValueError('invalid probabilities')
        result[family] = {'n_test': len(y), 'brier': float(brier_score_loss(y, p)),
                          'logloss': float(log_loss(y, p, labels=[0, 1])),
                          'accuracy': float(accuracy_score(y, p >= .5))}
    result['agreement_p60'], _ = agreement(y, prediction['lightgbm_p_home'], prediction['poisson_p_home'])
    return result


def decision(group, missing, before, after):
    if group == 'G':
        return True, 'keep_as_of_weather_safeguard_by_instruction'
    if missing > 40:
        return False, 'drop_more_than_40_percent_missing'
    if group == 'F':
        return False, 'separate_head_cannot_qualify_as_full_game_feature'
    old, new = before['lightgbm'], after['lightgbm']
    if new['brier'] > old['brier'] and new['logloss'] > old['logloss']:
        return False, 'drop_worse_brier_and_logloss'
    if new['brier'] < old['brier']-TOLERANCE:
        return True, 'keep_improved_primary_lightgbm_brier'
    return False, 'drop_no_primary_lightgbm_brier_improvement'


def fit_frozen_pair(train, test, lgb_features, poisson_features, params, output, *, f5=False):
    """Always preserve the champion's exact ordered base lists and parameters."""
    output.mkdir(parents=True, exist_ok=True)
    parameters = dict(params)
    if f5:
        parameters.update(objective='multiclass', num_class=3)
        labels = np.where(train.f5_home_runs > train.f5_away_runs, 2,
                          np.where(train.f5_home_runs == train.f5_away_runs, 1, 0))
    else:
        labels = train.home_win.astype(int)
    model = lgb.LGBMClassifier(**parameters).fit(train[lgb_features].astype(float), labels)
    model.booster_.save_model(str(output/'lightgbm.txt'))
    raw = model.predict_proba(test[lgb_features].astype(float))
    np.testing.assert_allclose(lgb.Booster(model_file=str(output/'lightgbm.txt')).predict(test[lgb_features].astype(float)),
                               raw if f5 else raw[:, 1], atol=TOLERANCE, rtol=0)
    rates, exports = {}, {}
    for side in ('home', 'away'):
        names = poisson_features[side]
        target = 'f5_'+side+'_runs' if f5 else side+'_score'
        estimator = make_estimator()
        with warnings.catch_warnings():
            warnings.simplefilter('error', ConvergenceWarning)
            estimator.fit(train[names].astype(float), train[target].astype(float))
        rates[side] = estimator.predict(test[names].astype(float))
        exports[side] = export_estimator(estimator, names)
    write_json(output/'poisson.json', exports)
    saved = json.loads((output/'poisson.json').read_bytes())
    for side in ('home', 'away'):
        np.testing.assert_allclose(predict_exported(saved[side], test), rates[side], atol=TOLERANCE, rtol=0)
    result = {'lightgbm_p_home': raw[:, 2] if f5 else raw[:, 1],
              'poisson_p_home': skellam.sf(0, rates['home'], rates['away']) if f5 else home_probability(rates['home'], rates['away'])[0],
              'lambda_home': rates['home'], 'lambda_away': rates['away'],
              'projected_total': rates['home']+rates['away']}
    if f5:
        tie = skellam.pmf(0, rates['home'], rates['away'])
        result.update(lightgbm_p_tie=raw[:, 1], lightgbm_p_away=raw[:, 0],
                      poisson_p_tie=tie, poisson_p_away=1-result['poisson_p_home']-tie)
    write_json(output/'feature_list.json', {'lightgbm': lgb_features, 'poisson': poisson_features})
    write_json(output/'metadata.json', {'target': 'f5_three_class' if f5 else 'home_win_and_full_game_runs',
        'lightgbm_parameters': parameters, 'train_ids_sha256': digest(train.game_id.tolist()),
        'test_ids_sha256': digest(test.game_id.tolist()), 'reload_verified': True, 'deployed': False,
        'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.name != 'metadata.json'}})
    return result


def with_additions(base, additions):
    return list(base) + [name for name in additions if name not in base]


def verify_cohort(part, expected):
    if part.game_id.tolist() != expected.game_id.tolist():
        raise ValueError('game IDs or their order changed')
    pd.testing.assert_frame_equal(part[['game_id', 'date', 'home_win', 'home_score', 'away_score']].reset_index(drop=True),
                                  expected[['game_id', 'date', 'home_win', 'home_score', 'away_score']].reset_index(drop=True))


def f5_head(train, test, lgb_features, poisson_features, params, output):
    ftrain = train.dropna(subset=GROUPS['F'][1]); ftest = test.dropna(subset=GROUPS['F'][1])
    status = {'train_labels': len(ftrain), 'test_labels': len(ftest), 'baseline_test_games': len(test),
              'full_game_target_unchanged': True, 'full_game_features_added': [],
              'tie_policy': 'Separate home/tie/away classes; unconditional home-win probability.'}
    # No full-game substitution, market-label inference or proportional scaling.
    classes = np.sign(ftrain.f5_home_runs-ftrain.f5_away_runs).unique()
    if len(ftrain) < 100 or len(ftest) < 50 or len(classes) != 3:
        return {**status, 'status': 'blocked_insufficient_real_archived_f5_labels'}
    p = fit_frozen_pair(ftrain, ftest, lgb_features, poisson_features, params, output, f5=True)
    labels = np.where(ftest.f5_home_runs > ftest.f5_away_runs, 2,
                      np.where(ftest.f5_home_runs == ftest.f5_away_runs, 1, 0))
    scores = {}
    for family in ('lightgbm', 'poisson'):
        probs = np.column_stack([p[family+'_p_away'], p[family+'_p_tie'], p[family+'_p_home']])
        scores[family] = {'n_test': len(ftest), 'logloss_three_class': float(log_loss(labels, probs, labels=[0, 1, 2])),
                          'home_win_brier': float(brier_score_loss(labels == 2, p[family+'_p_home'])),
                          'accuracy_three_class': float(accuracy_score(labels, probs.argmax(axis=1)))}
    rows = ftest[['game_id', 'date', 'f5_home_runs', 'f5_away_runs']].reset_index(drop=True).assign(**p)
    rows.to_parquet(output/'predictions.parquet', index=False)
    return {**status, 'status': 'trained_separate_head', 'metrics': scores}


def run(phase2, archive_dir, output):
    if output.exists() and any(output.iterdir()):
        raise ValueError('use an empty output directory to prevent stale experiment files')
    output.mkdir(parents=True, exist_ok=True)
    proof = verify_reference(phase2)
    accepted_report = json.loads((phase2/'metrics.json').read_bytes())
    features = json.loads((phase2/'feature_list.json').read_bytes())
    params = accepted_report['parameters']
    original = pd.read_parquet(phase2/'input_table.parquet')
    original_train, original_test = split(original)  # H flags 2020; never filters it.
    refs = json.loads((Path(__file__).parent/'model_refs.json').read_bytes())
    poisson_bytes = (Path(__file__).parent/'poisson_model.json').read_bytes()
    for raw, expected in [((phase2/'model.txt').read_bytes(), refs['lightgbm']['sha256']),
                          (poisson_bytes, refs['poisson']['sha256'])]:
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('champion model hash mismatch')
    poisson = json.loads(poisson_bytes)
    poisson_features = {s: poisson[s]['features'] for s in ('home', 'away')}
    booster = lgb.Booster(model_file=str(phase2/'model.txt'))
    if booster.feature_name() != features or accepted_report['features'] != features:
        raise ValueError('champion classifier feature list mismatch')
    reference = align_reference(original_test, pd.read_parquet(phase2/'test_predictions.parquet'))
    for name, part in [('train', original_train), ('test', original_test)]:
        if accepted_report[name] != {'start': part.date.min(), 'end': part.date.max(), 'games': len(part)}:
            raise ValueError('accepted split dates or sizes changed')
    base_lgb = booster.predict(original_test[features].astype(float))
    np.testing.assert_allclose(base_lgb, reference.p_home, atol=TOLERANCE, rtol=0)
    lh, la = [predict_exported(poisson[s], original_test) for s in ('home', 'away')]
    champion = {'lightgbm_p_home': base_lgb, 'poisson_p_home': home_probability(lh, la)[0],
                'lambda_home': lh, 'lambda_away': la, 'projected_total': lh+la}
    baseline_dir = output/'champion'
    baseline_dir.mkdir()
    shutil.copyfile(phase2/'model.txt', baseline_dir/'lightgbm.txt')
    (baseline_dir/'poisson.json').write_bytes(poisson_bytes)
    write_json(baseline_dir/'feature_list.json', {'lightgbm': features, 'poisson': poisson_features})
    write_json(baseline_dir/'references.json', refs)
    bundle, archive_receipt = load_archive(archive_dir)
    frame, archive_coverage = build(original, bundle)
    frame = add_audit_flags(frame)
    train, test = split(frame)
    verify_cohort(train, original_train); verify_cohort(test, original_test)
    pd.testing.assert_frame_equal(train[features], original_train[features])
    pd.testing.assert_frame_equal(test[features], original_test[features])
    y = test.home_win.astype(int).to_numpy()
    before = metric_pair(y, champion)
    reports, predictions, decisions, fitted_additions = {}, [('champion', champion)], {}, {}
    for group, (name, columns) in GROUPS.items():
        cov = coverage(train, test, columns)
        additions = isolated_features(group, train)
        group_train, group_test = train.copy(), test.copy()
        if group == 'G':
            group_train = weather_guard(group_train, train)
            group_test = weather_guard(group_test, test)
        verify_cohort(group_train, original_train); verify_cohort(group_test, original_test)
        if group == 'F':
            fp = f5_head(train, test, features, poisson_features, params, output/'diagnostics'/'F_f5')
            pred = champion  # A separate head has no full-game effect.
        else:
            pred = fit_frozen_pair(group_train, group_test, with_additions(features, additions),
                    {s: with_additions(v, additions) for s, v in poisson_features.items()},
                    params, output/'diagnostics'/group)
        if not additions and group != 'F':
            for key in champion:
                np.testing.assert_allclose(pred[key], champion[key], atol=REFIT_TOLERANCE, rtol=0)
        measured = metric_pair(y, pred)
        keep, reason = decision(group, cov['gate_missing_pct'], before, measured)
        decisions[group] = {'keep': keep, 'reason': reason}
        fitted_additions[group] = additions
        reports[group] = {'name': name, 'metrics': measured, 'coverage': cov,
                           'features_added': additions, 'same_train_and_test_ids': True,
                           'full_game_retrained': group != 'F', **decisions[group]}
        if group == 'F': reports[group]['separate_head'] = fp
        predictions.append((group, pred))
        print(json.dumps({'group': group, **reports[group]}), flush=True)
    keepers = [g for g in GROUPS if decisions[g]['keep']]
    additions = sorted({c for g in keepers for c in fitted_additions[g]})
    vtrain = weather_guard(train, train) if 'G' in keepers else train
    vtest = weather_guard(test, test) if 'G' in keepers else test
    v2_features = with_additions(features, additions)
    v2_poisson_features = {s: with_additions(v, additions) for s, v in poisson_features.items()}
    v2 = fit_frozen_pair(vtrain, vtest, v2_features, v2_poisson_features, params, output/'addon-v2')
    v2_metrics = metric_pair(y, v2)
    if not additions:
        for key in champion:
            np.testing.assert_allclose(v2[key], champion[key], atol=REFIT_TOLERANCE, rtol=0)
    policy = {'kept_groups': keepers, 'new_feature_columns': additions, 'as_of_weather_only': 'G' in keepers,
              'champion_features_preserved': features, 'serving_refs_changed': False,
              'inference': 'The fixed input schema excludes all unkept groups. Use weather_guard with verified build() evidence if any weather column is selected.'}
    write_json(output/'addon-v2'/'policy.json', policy)
    # Only the keeper schema leaves the experiment as a feature table. Rejected
    # toggle columns remain ephemeral; diagnostic models/metrics are labeled.
    keys = ['game_id', 'date', 'season', 'home_team', 'away_team', 'home_win', 'home_score', 'away_score', 'as_of_timestamp']
    clean = weather_guard(frame, frame) if 'G' in keepers else frame
    clean[keys+v2_features].to_parquet(output/'addon-v2'/'feature_table.parquet', index=False)
    predictions.append(('addon-v2', v2))
    pd.concat([test[keys].reset_index(drop=True).assign(group=name, **p) for name,p in predictions],
              ignore_index=True).to_parquet(output/'test_predictions.parquet', index=False)
    table = []
    for group, pred in predictions:
        measured = metric_pair(y, pred)
        for family in ('lightgbm', 'poisson'):
            table.append({'group': group, 'model': family, **measured[family],
                'agreement_accuracy': measured['agreement_p60']['accuracy'], 'agreement_games': measured['agreement_p60']['games'],
                'missing_train_pct': reports[group]['coverage']['train']['missing_pct'] if group in reports else None,
                'missing_test_pct': reports[group]['coverage']['test']['missing_pct'] if group in reports else None,
                'keep': decisions[group]['keep'] if group in decisions else None})
    pd.DataFrame(table).to_csv(output/'comparison.csv', index=False)
    report = {'experiment': 'KS1 isolated add-ons v2', 'champion': {'references': refs, 'metrics': before,
              'lightgbm_features': features, 'poisson_features': poisson_features},
              'train': accepted_report['train'], 'test': accepted_report['test'],
              'train_ids_sha256': digest(train.game_id.tolist()), 'test_ids_sha256': digest(test.game_id.tolist()),
              'isolated_groups': reports, 'kept_groups': keepers, 'addon_v2': {'metrics': v2_metrics, **policy},
              'v2_minus_champion_brier': {m: paired_delta(y, champion[m+'_p_home'], v2[m+'_p_home'], test.date)
                                         for m in ('lightgbm', 'poisson')},
              'selection_rule': 'Primary LightGBM Brier must strictly improve (>1e-12 numerical tolerance); drop worse Brier+logloss; first drop >40% raw-value missingness in either split. G retained by instruction. F is a separate head only.',
              'agreement_definition': 'Same side from both models, each with chosen-side probability >=0.60; away=1-P(home). Qualifying cohorts may differ.',
              'holdout_reused_for_requested_group_selection': True, 'prospective_validation': False,
              'hyperparameter_tuning': False, 'rows_dropped_for_H': 0, 'season_2020_rows': int((frame.season == 2020).sum()),
              'cross_platform_refit_tolerance': REFIT_TOLERANCE, 'native_reload_tolerance': TOLERANCE,
              'realized_weather_features_in_champion': sorted(set(features) & set(WEATHER)),
              'archive_coverage': archive_coverage, 'aws_writes': 0, 'provider_calls': 0, 'deployment': False}
    write_json(output/'comparison.json', report)
    write_json(output/'input_receipt.json', {'phase2': proof, 'archive': archive_receipt})
    write_json(output/'cohort.json', {'train_game_ids': train.game_id.tolist(), 'test_game_ids': test.game_id.tolist(),
                                     'dropped_game_ids': []})
    print(json.dumps({'kept_groups': keepers, 'champion': before, 'addon_v2': v2_metrics}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase2-dir', type=Path, required=True)
    parser.add_argument('--archive-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.phase2_dir, args.archive_dir, args.output)
