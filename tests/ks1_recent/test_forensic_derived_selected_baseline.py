import pandas as pd
import pytest

import ks1.forensic_derived_selected_baseline as subject


def test_selected_baseline_features_uses_exact_selected_recipe():
    report = {
        'trials': {
            'starter': {'features': ['base_a', 'base_b']},
            'starter_plus_batters_and_bullpen': {'features': ['base_a', 'bad_raw']},
        }
    }
    assert subject.selected_baseline_features(report, 'starter') == ['base_a', 'base_b']
    with pytest.raises(ValueError, match='evidence missing'):
        subject.selected_baseline_features(report, 'missing')


def test_nested_screen_selects_one_used_feature_per_group_without_outer_or_holdout_access(monkeypatch):
    fit = pd.DataFrame({'home_win': [0, 1, 0, 1]})
    validation = pd.DataFrame({'home_win': [0, 1]})
    monkeypatch.setattr(subject, 'split_development', lambda frame: (fit, validation))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame.assign(
        base=.1, market_a=.2, market_b=.3, lineup_a=.4, lineup_b=.5))
    monkeypatch.setattr(subject, 'TRIALS', {'shallow': {}})
    monkeypatch.setattr(subject, 'PARAMS', {})

    scores = {
        'market_a': (.24, .69, True),
        'market_b': (.23, .68, True),
        'lineup_a': (.22, .67, True),
        'lineup_b': (.21, .66, False),
    }

    def fake_trial(fit_frame, validation_frame, columns, params):
        feature = columns[-1]
        brier, logloss, used = scores[feature]
        return {
            'brier': brier,
            'logloss': logloss,
            'features_used_in_splits': ['base'] + ([feature] if used else []),
        }

    monkeypatch.setattr(subject, '_trial', fake_trial)
    selected, evidence = subject.screen_derived_features(
        pd.DataFrame({'unused': [1]}), ['base'],
        ['market_a', 'market_b', 'lineup_a', 'lineup_b'],
        {'market': ['market_a', 'market_b'], 'lineup': ['lineup_a', 'lineup_b']})

    assert selected == ['market_b', 'lineup_a']
    assert evidence['all_groups_screened'] is True
    assert evidence['outer_development_used_for_screening'] is False
    assert evidence['final_holdout_used_for_screening'] is False
    assert evidence['groups']['lineup']['candidates']['lineup_b']['feature_used_in_splits'] is False


def test_development_challenger_augments_selected_recipe_not_all_admitted_raw(monkeypatch):
    fit = pd.DataFrame({'home_win': [0, 1]})
    validation = pd.DataFrame({'home_win': [0, 1]})
    monkeypatch.setattr(subject, 'split_development', lambda train: (fit, validation))
    monkeypatch.setattr(subject, 'choose_features',
                        lambda frame: (['base_a', 'bad_raw_parent', 'parent_x'], [], {'home': 2, 'away': 2}))
    monkeypatch.setattr(subject, 'admit_derived',
                        lambda frame, raw, floor: (['forensic_x'], {}, None))
    monkeypatch.setattr(subject, 'derived_groups', lambda derived: {'market': ['forensic_x']})
    monkeypatch.setattr(subject, 'SPECS', {'forensic_x': {'parents': ('parent_x',)}})
    monkeypatch.setattr(subject, 'select', lambda train: (None, {
        'selected': 'starter',
        'metrics': {'starter': {'brier': .25, 'logloss': .70}},
        'trials': {'starter': {'features': ['base_a', 'parent_x']}},
    }))
    monkeypatch.setattr(subject, '_augment',
                        lambda frame, derived: frame.assign(base_a=.1, bad_raw_parent=.2,
                                                            parent_x=.3, forensic_x=.4))
    monkeypatch.setattr(subject, 'screen_derived_features', lambda *args: (
        ['forensic_x'], {
            'all_groups_screened': True,
            'outer_development_used_for_screening': False,
            'final_holdout_used_for_screening': False,
        }))
    seen = []

    def fake_trial(fit_frame, validation_frame, columns, params):
        seen.append(list(columns))
        return {'brier': .24, 'logloss': .69,
                'features_used_in_splits': ['base_a', 'forensic_x']}

    monkeypatch.setattr(subject, '_trial', fake_trial)
    monkeypatch.setattr(subject, 'TRIALS', {'only': {}})
    monkeypatch.setattr(subject, 'PARAMS', {})

    selected, report = subject.development_select(pd.DataFrame({'unused': [1]}))
    assert selected is not None
    assert seen == [['base_a', 'parent_x', 'forensic_x']]
    assert report['baseline_raw_features'] == ['base_a', 'parent_x']
    assert 'bad_raw_parent' not in report['features']
    assert report['accepted_for_final_holdout'] is True
    assert report['final_holdout_used_for_selection'] is False


def test_nested_screen_recovers_families_used_only_outside_shallow_trial(monkeypatch):
    outer_fit = pd.DataFrame({'home_win': [0, 1, 0, 1]}, index=[10, 11, 12, 13])
    inner_fit = outer_fit.iloc[:2].copy()
    inner_validation = outer_fit.iloc[2:].copy()
    families = ('market', 'starter_regime', 'starter_workload', 'lineup', 'bullpen')
    features = ['forensic_' + name for name in families]
    groups = dict(zip(families, ([name] for name in features)))
    trial_names = ('baseline', 'shallow', 'regularized', 'shallow_long', 'shallow_shrink')
    frozen_trials = {name: {'trial_id': name} for name in trial_names}
    monkeypatch.setattr(subject, 'TRIALS', frozen_trials)
    monkeypatch.setattr(subject, 'PARAMS', {'random_state': 1729})

    def split(frame):
        assert frame is outer_fit
        return inner_fit, inner_validation

    augmented = []

    def augment(frame, derived):
        assert frame is inner_fit or frame is inner_validation
        assert derived == features
        augmented.append(frame)
        return frame

    calls = []

    def trial(fit, validation, columns, params):
        assert fit is inner_fit and validation is inner_validation
        assert columns[:-1] == ['base_a', 'base_b']
        assert params == {'random_state': 1729, 'trial_id': params['trial_id']}
        calls.append((columns[-1], params['trial_id']))
        used = params['trial_id'] == 'baseline'
        return {'brier': .24 if used else .20, 'logloss': .68 if used else .60,
                'features_used_in_splits': columns if used else columns[:-1]}

    monkeypatch.setattr(subject, 'split_development', split)
    monkeypatch.setattr(subject, '_augment', augment)
    monkeypatch.setattr(subject, '_trial', trial)
    selected, evidence = subject.screen_derived_features(
        outer_fit, ['base_a', 'base_b'], features, groups)

    assert selected == features
    assert len(calls) == len(features) * len(frozen_trials)
    assert set(calls) == {(f, t) for f in features for t in trial_names}
    assert len(augmented) == 2
    assert frozen_trials == {name: {'trial_id': name} for name in trial_names}
    assert set(evidence['trials']) == set(frozen_trials)
    assert evidence['all_groups_screened'] is True
    assert evidence['outer_development_used_for_screening'] is False
    assert evidence['final_holdout_used_for_screening'] is False
    for family, feature in zip(families, features):
        candidate = evidence['groups'][family]['candidates'][feature]
        assert candidate['selected_trial'] == 'baseline'
        assert candidate['trials']['shallow']['feature_used_in_splits'] is False
        assert candidate['brier'] == .24


@pytest.mark.parametrize('reverse', [False, True])
def test_nested_screen_ties_are_deterministic_across_trial_and_feature_order(monkeypatch, reverse):
    names = ['baseline', 'shallow']
    features = ['forensic_a', 'forensic_b']
    if reverse:
        names.reverse()
        features.reverse()
    monkeypatch.setattr(subject, 'TRIALS', {name: {} for name in names})
    monkeypatch.setattr(subject, 'PARAMS', {})
    monkeypatch.setattr(subject, 'split_development', lambda frame: (frame, frame))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame)
    monkeypatch.setattr(subject, '_trial', lambda fit, validation, columns, params: {
        'brier': .24, 'logloss': .68, 'features_used_in_splits': columns})
    selected, evidence = subject.screen_derived_features(
        pd.DataFrame(), ['base'], features, {'lineup': features})
    assert selected == ['forensic_a']
    assert evidence['groups']['lineup']['candidates']['forensic_a']['selected_trial'] == 'baseline'


def test_nested_screen_never_forces_a_family_without_learned_usage(monkeypatch):
    monkeypatch.setattr(subject, 'TRIALS', {'baseline': {}, 'shallow': {}})
    monkeypatch.setattr(subject, 'PARAMS', {})
    monkeypatch.setattr(subject, 'split_development', lambda frame: (frame, frame))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame)
    monkeypatch.setattr(subject, '_trial', lambda fit, validation, columns, params: {
        'brier': .01, 'logloss': .01, 'features_used_in_splits': ['base']})
    selected, evidence = subject.screen_derived_features(
        pd.DataFrame(), ['base'], ['forensic_bullpen'], {'bullpen': ['forensic_bullpen']})
    assert selected == []
    assert evidence['all_groups_screened'] is False
    candidate = evidence['groups']['bullpen']['candidates']['forensic_bullpen']
    assert candidate['feature_used_in_splits'] is False
    assert candidate['selected_trial'] is None
    assert len(candidate['trials']) == 2


def test_nested_screen_rejects_empty_trial_search(monkeypatch):
    monkeypatch.setattr(subject, 'TRIALS', {})
    with pytest.raises(ValueError, match='nested screen trials unavailable'):
        subject.screen_derived_features(pd.DataFrame(), [], [], {})


@pytest.mark.parametrize('metric', ['brier', 'logloss'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nested_screen_rejects_nonfinite_scores(monkeypatch, metric, value):
    monkeypatch.setattr(subject, 'TRIALS', {'shallow': {}})
    monkeypatch.setattr(subject, 'PARAMS', {})
    monkeypatch.setattr(subject, 'split_development', lambda frame: (frame, frame))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame)
    result = {'brier': .24, 'logloss': .68, 'features_used_in_splits': ['base', 'forensic_a']}
    result[metric] = value
    monkeypatch.setattr(subject, '_trial', lambda *args: result)
    with pytest.raises(ValueError, match='metrics must be finite'):
        subject.screen_derived_features(
            pd.DataFrame(), ['base'], ['forensic_a'], {'lineup': ['forensic_a']})


@pytest.mark.parametrize('brier,logloss,use_bullpen,accepted', [
    (.24, .69, True, True),
    (.24, .70, True, True),
    (.25, .69, True, False),
    (.26, .69, True, False),
    (.24, .71, True, False),
    (.24, .69, False, False),
])
def test_screened_candidate_still_requires_unchanged_outer_gates(
        monkeypatch, brier, logloss, use_bullpen, accepted):
    fit = pd.DataFrame({'home_win': [0, 1]})
    validation = pd.DataFrame({'home_win': [0, 1]})
    features = ['forensic_lineup', 'forensic_lineup_extra', 'forensic_bullpen']
    selected_features = ['forensic_lineup', 'forensic_bullpen']
    monkeypatch.setattr(subject, 'split_development', lambda frame: (fit, validation))
    monkeypatch.setattr(subject, 'choose_features', lambda frame: (['base'], [], {}))
    monkeypatch.setattr(subject, 'admit_derived', lambda *args: (features, {}, None))
    monkeypatch.setattr(subject, 'derived_groups', lambda derived: {
        'lineup': features[:2], 'bullpen': features[2:]})
    monkeypatch.setattr(subject, 'SPECS', {name: {'parents': ('base',)} for name in features})
    monkeypatch.setattr(subject, 'select', lambda frame: (None, {
        'selected': 'starter', 'metrics': {'starter': {'brier': .25, 'logloss': .70}},
        'trials': {'starter': {'features': ['base']}},
    }))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame)
    monkeypatch.setattr(subject, 'TRIALS', {'shallow': {}})
    monkeypatch.setattr(subject, 'PARAMS', {})
    monkeypatch.setattr(subject, 'screen_derived_features', lambda *args: (
        selected_features, {'all_groups_screened': True}))
    calls = []

    def trial(train, development, columns, params):
        assert train is fit and development is validation
        calls.append(list(columns))
        if 'forensic_lineup_extra' in columns:
            return {'brier': .26, 'logloss': .71, 'features_used_in_splits': columns}
        used = columns if use_bullpen else ['base', 'forensic_lineup']
        return {'brier': brier, 'logloss': logloss, 'features_used_in_splits': used}

    monkeypatch.setattr(subject, '_trial', trial)
    candidate, report = subject.development_select(pd.DataFrame({'unused': [1]}))
    assert calls == [['base'] + features, ['base'] + selected_features]
    assert (candidate is not None) is accepted
    assert report['accepted_for_final_holdout'] is accepted
    assert report['final_holdout_used_for_selection'] is False
    if accepted:
        assert report['features'] == ['base'] + selected_features
        assert report['selected_trial'] == 'screened_shallow'
    else:
        assert report['selected_trial'] is None
        assert report['reason'] == 'derived_forensic_selected_baseline_candidate_not_superior_on_development'
