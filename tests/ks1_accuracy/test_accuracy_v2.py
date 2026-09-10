import json

import numpy as np
import pandas as pd
import pytest

from ks1.accuracy_v2 import (GROUPS, add_audit_flags, coverage, decision, f5_head,
                            fit_frozen_pair, isolated_features, verify_cohort,
                            weather_guard)
from ks1.train import PARAMS


def test_missingness_counts_unavailable_group_members_not_indicators():
    frame = pd.DataFrame({'home_starter_expected_ip': [5., 6.], 'away_starter_expected_ip': [4., 5.],
                          'home_opener': [None, None], 'away_opener': [None, None],
                          'home_opener_missing': [1., 1.]})
    rates = coverage(frame, frame, GROUPS['A'][1])
    assert rates['gate_missing_pct'] == 50
    assert rates['test']['any_required_value_missing_pct'] == 100


def test_keep_rules_enforce_missingness_and_improvement_with_g_exception():
    before = {'lightgbm': {'brier': .25, 'logloss': .70}}
    improved = {'lightgbm': {'brier': .24, 'logloss': .69}}
    worse = {'lightgbm': {'brier': .26, 'logloss': .71}}
    assert decision('A', 40, before, improved)[0]
    assert not decision('A', 40.01, before, improved)[0]
    assert not decision('A', 0, before, before)[0]
    assert not decision('A', 0, before, worse)[0]
    assert decision('G', 100, before, worse)[0]
    assert not decision('F', 0, before, improved)[0]


def test_eight_toggles_never_cross_groups_or_pass_f5_labels():
    names = {c for _, values in GROUPS.values() for c in values}
    frame = pd.DataFrame({c: [0., 1., 2.] for c in names})
    for c in names:
        frame[c+'_missing'] = [0., 1., 0.]
    frame['home_starter_projection_missing'] = [0., 1., 0.]
    frame['away_starter_projection_missing'] = [0., 1., 0.]
    for group, (_, values) in GROUPS.items():
        added = isolated_features(group, frame)
        allowed = set(values + [c+'_missing' for c in values])
        if group == 'A': allowed |= {'home_starter_projection_missing', 'away_starter_projection_missing'}
        assert set(added) <= allowed
        assert not set(added) & set(GROUPS['F'][1])
        if group in ('F', 'G'): assert added == []


def test_h_flags_2020_and_unknown_roles_without_dropping_rows():
    frame = pd.DataFrame({'game_id': ['a', 'b', 'c'], 'season': [2020, 2025, 2026],
                          'doubleheader_status': ['flagged', 'official_single', 'unknown']})
    result = add_audit_flags(frame)
    assert result.game_id.tolist() == frame.game_id.tolist()
    assert result.season_2020_flag.tolist() == [1., 0., 0.]
    assert result.doubleheader_flag.iloc[:2].tolist() == [1., 0.]
    assert result.doubleheader_flag.isna().iloc[2]
    assert result.scheduled_bullpen_flag.isna().all()


def test_g_replaces_untrusted_weather_and_checks_evidence_alignment():
    raw = pd.DataFrame({'game_id': ['a', 'b'], 'temp': [105., 77.], 'wind_speed': [44., 88.]})
    safe = pd.DataFrame({'game_id': ['a', 'b'], 'outdoor_forecast_temp_f': [72., np.nan]})
    result = weather_guard(raw, safe)
    assert result.temp.iloc[0] == 72 and np.isnan(result.temp.iloc[1])
    assert result.wind_speed.isna().all()
    changed = raw.assign(temp=[-999., 999.], wind_speed=[999., 0.])
    pd.testing.assert_frame_equal(result, weather_guard(changed, safe))
    with pytest.raises(ValueError, match='game IDs'):
        weather_guard(raw, safe.iloc[::-1])


def toy_frames():
    rng = np.random.default_rng(1729)
    def part(n, prefix):
        return pd.DataFrame({'game_id': [prefix+str(i) for i in range(n)],
            'date': '2025-01-01' if prefix == 'tr' else '2026-01-01',
            'x': rng.normal(size=n), 'z': rng.normal(size=n), 'home_win': rng.integers(0, 2, n),
            'home_score': rng.poisson(4.5, n), 'away_score': rng.poisson(4., n),
            'f5_home_runs': rng.poisson(2.5, n), 'f5_away_runs': rng.poisson(2., n)})
    return part(150, 'tr'), part(55, 'te')


def test_frozen_lists_reload_and_full_game_fit_ignores_f5_and_other_groups(tmp_path):
    train, test = toy_frames()
    first = fit_frozen_pair(train, test, ['z', 'x'], {'home': ['x'], 'away': ['z']}, PARAMS, tmp_path/'first')
    changed = train.assign(f5_home_runs=999, f5_away_runs=999, defense_oaa_diff=999)
    second = fit_frozen_pair(changed, test, ['z', 'x'], {'home': ['x'], 'away': ['z']}, PARAMS, tmp_path/'second')
    for name in first: np.testing.assert_allclose(first[name], second[name], atol=1e-12, rtol=0)
    spec = json.loads((tmp_path/'first'/'feature_list.json').read_text())
    assert spec == {'lightgbm': ['z', 'x'], 'poisson': {'home': ['x'], 'away': ['z']}}


def test_f5_head_requires_real_labels_and_ignores_full_game_targets(tmp_path):
    train, test = toy_frames()
    before = f5_head(train, test, ['x'], {'home': ['x'], 'away': ['x']}, PARAMS, tmp_path/'first')
    changed = train.assign(home_win=0, home_score=99, away_score=99)
    after = f5_head(changed, test, ['x'], {'home': ['x'], 'away': ['x']}, PARAMS, tmp_path/'second')
    assert before == after
    assert before['status'] == 'trained_separate_head'
    blocked = f5_head(train.assign(f5_home_runs=np.nan), test, ['x'], {'home': ['x'], 'away': ['x']}, PARAMS, tmp_path/'blocked')
    assert blocked['status'] == 'blocked_insufficient_real_archived_f5_labels'
    assert not (tmp_path/'blocked').exists()


def test_cohort_cannot_reorder_drop_or_change_full_game_labels():
    frame = pd.DataFrame({'game_id': ['a', 'b'], 'date': ['2020-01-01', '2026-01-01'],
                          'home_win': [1, 0], 'home_score': [4, 2], 'away_score': [2, 4]})
    verify_cohort(frame, frame.copy())
    for changed in (frame.iloc[::-1], frame.iloc[:1]):
        with pytest.raises(ValueError, match='game IDs'): verify_cohort(changed, frame)
    with pytest.raises(AssertionError): verify_cohort(frame.assign(home_score=99), frame)
