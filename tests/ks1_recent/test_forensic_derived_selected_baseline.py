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


def test_nested_screen_selects_incremental_used_feature_across_prespecified_trials(monkeypatch):
    fit = pd.DataFrame({'home_win': [0, 1, 0, 1]})
    validation = pd.DataFrame({'home_win': [0, 1]})
    monkeypatch.setattr(subject, 'split_development', lambda frame: (fit, validation))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame.assign(
        base=.1, market_a=.2, market_b=.3, lineup_a=.4, lineup_b=.5))
    monkeypatch.setattr(subject, 'TRIALS', {
        'shallow': {'trial': 'shallow'},
        'baseline': {'trial': 'baseline'},
    })
    monkeypatch.setattr(subject, 'PARAMS', {})

    baselines = {
        'shallow': (.25, .70),
        'baseline': (.20, .65),
    }
    scores = {
        ('market_a', 'shallow'): (.24, .69, True),
        ('market_a', 'baseline'): (.195, .645, True),
        ('market_b', 'shallow'): (.23, .68, True),
        ('market_b', 'baseline'): (.199, .649, True),
        ('lineup_a', 'shallow'): (.22, .67, True),
        ('lineup_a', 'baseline'): (.19, .64, True),
        # An unused feature cannot win even with much better apparent metrics.
        ('lineup_b', 'shallow'): (.18, .62, False),
        ('lineup_b', 'baseline'): (.17, .61, False),
    }

    def fake_trial(fit_frame, validation_frame, columns, params):
        trial = params['trial']
        if columns == ['base']:
            brier, logloss = baselines[trial]
            return {
                'brier': brier,
                'logloss': logloss,
                'features_used_in_splits': ['base'],
            }
        feature = columns[-1]
        brier, logloss, used = scores[(feature, trial)]
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

    # Selection is by same-trial incremental value, not absolute cross-trial score.
    assert selected == ['market_b', 'lineup_a']
    assert evidence['all_groups_screened'] is True
    assert evidence['outer_development_used_for_screening'] is False
    assert evidence['final_holdout_used_for_screening'] is False
    assert evidence['trials'] == ['shallow', 'baseline']
    assert evidence['baseline_by_trial']['shallow'] == {'brier': .25, 'logloss': .70}
    assert evidence['groups']['market']['selected_trial'] == 'shallow'
    assert evidence['groups']['lineup']['selected_trial'] == 'shallow'
    assert evidence['groups']['market']['candidates']['market_b']['trials']['shallow'][
        'brier_delta_vs_same_trial_baseline'] == pytest.approx(-.02)
    assert all(
        not trial['feature_used_in_splits']
        for trial in evidence['groups']['lineup']['candidates']['lineup_b']['trials'].values()
    )


def test_nested_screen_uses_existing_parent_only_for_single_affine_whole_group(monkeypatch):
    fit = pd.DataFrame({'home_win': [0, 1, 0, 1]})
    validation = pd.DataFrame({'home_win': [0, 1]})
    monkeypatch.setattr(subject, 'split_development', lambda frame: (fit, validation))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame.assign(
        base=.1, market_raw=.6, forensic_market=.1))
    monkeypatch.setattr(subject, 'TRIALS', {
        'shallow': {'trial': 'shallow'},
        'baseline': {'trial': 'baseline'},
    })
    monkeypatch.setattr(subject, 'PARAMS', {})
    monkeypatch.setattr(subject, 'SPECS', {
        'forensic_market': {
            'group': 'market', 'operation': 'offset', 'offset': -.5,
            'parents': ('market_raw',),
        },
    })

    def fake_trial(fit_frame, validation_frame, columns, params):
        # The derived alias never receives a split. The existing raw market
        # coordinate does, proving the family is present without pretending the
        # recentered alias adds information.
        used = ['base', 'market_raw']
        return {'brier': .24, 'logloss': .69, 'features_used_in_splits': used}

    monkeypatch.setattr(subject, '_trial', fake_trial)
    selected, evidence = subject.screen_derived_features(
        pd.DataFrame({'unused': [1]}), ['base', 'market_raw'],
        ['forensic_market'], {'market': ['forensic_market']})

    assert selected == []
    assert evidence['all_groups_screened'] is True
    assert evidence['equivalent_existing_signal_groups'] == {'market': 'market_raw'}
    market = evidence['groups']['market']
    assert market['selected'] is None
    assert market['existing_equivalent_parent'] == 'market_raw'
    assert market['existing_equivalent_parent_is_new_feature'] is False
    assert all(
        not trial['feature_used_in_splits']
        for trial in market['candidates']['forensic_market']['trials'].values()
    )


def test_nested_screen_does_not_count_unused_or_multifeature_raw_parents(monkeypatch):
    fit = pd.DataFrame({'home_win': [0, 1, 0, 1]})
    validation = pd.DataFrame({'home_win': [0, 1]})
    monkeypatch.setattr(subject, 'split_development', lambda frame: (fit, validation))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame.assign(
        base=.1, market_raw=.6, forensic_market=.1, workload_a=.2, workload_b=.3))
    monkeypatch.setattr(subject, 'TRIALS', {'only': {}})
    monkeypatch.setattr(subject, 'PARAMS', {})
    monkeypatch.setattr(subject, 'SPECS', {
        'forensic_market': {
            'group': 'market', 'operation': 'offset', 'offset': -.5,
            'parents': ('market_raw',),
        },
        'workload_a': {
            'group': 'starter_workload', 'operation': 'offset_minus', 'offset': 4.5,
            'parents': ('innings_raw',),
        },
        'workload_b': {
            'group': 'starter_workload', 'operation': 'difference',
            'parents': ('innings_raw', 'other_raw'),
        },
    })

    def fake_trial(fit_frame, validation_frame, columns, params):
        # No candidate and no raw equivalent is actually used.
        return {'brier': .24, 'logloss': .69, 'features_used_in_splits': ['base']}

    monkeypatch.setattr(subject, '_trial', fake_trial)
    selected, evidence = subject.screen_derived_features(
        pd.DataFrame({'unused': [1]}),
        ['base', 'market_raw', 'innings_raw', 'other_raw'],
        ['forensic_market', 'workload_a', 'workload_b'],
        {'market': ['forensic_market'],
         'starter_workload': ['workload_a', 'workload_b']})

    assert selected == []
    assert evidence['all_groups_screened'] is False
    assert evidence['equivalent_existing_signal_groups'] == {}
    assert evidence['groups']['market']['existing_equivalent_parent'] is None
    # A multi-feature family can never bypass actual derived-feature use via raw parents.
    assert evidence['groups']['starter_workload']['existing_equivalent_parent'] is None


def test_nested_screen_fails_closed_without_prespecified_trials(monkeypatch):
    monkeypatch.setattr(subject, 'TRIALS', {})
    with pytest.raises(ValueError, match='nested screen trials unavailable'):
        subject.screen_derived_features(
            pd.DataFrame({'home_win': [0, 1]}), ['base'], ['forensic_x'],
            {'market': ['forensic_x']})


@pytest.mark.parametrize('market_used, expected_selected', [(True, True), (False, False)])
def test_screened_outer_gate_requires_split_use_of_equivalent_existing_signal(
        monkeypatch, market_used, expected_selected):
    fit = pd.DataFrame({'home_win': [0, 1]})
    validation = pd.DataFrame({'home_win': [0, 1]})
    monkeypatch.setattr(subject, 'split_development', lambda train: (fit, validation))
    monkeypatch.setattr(
        subject, 'choose_features',
        lambda frame: (['base', 'market_raw', 'lineup_parent'], [], {'home': 2, 'away': 2}))
    monkeypatch.setattr(
        subject, 'admit_derived',
        lambda frame, raw, floor: (['forensic_market', 'forensic_lineup'], {}, None))
    monkeypatch.setattr(
        subject, 'derived_groups',
        lambda derived: {'market': ['forensic_market'], 'lineup': ['forensic_lineup']})
    monkeypatch.setattr(subject, 'SPECS', {
        'forensic_market': {
            'group': 'market', 'operation': 'offset', 'offset': -.5,
            'parents': ('market_raw',),
        },
        'forensic_lineup': {
            'group': 'lineup', 'operation': 'difference',
            'parents': ('lineup_parent', 'lineup_other'),
        },
    })
    monkeypatch.setattr(subject, 'select', lambda train: (None, {
        'selected': 'starter',
        'metrics': {'starter': {'brier': .25, 'logloss': .70}},
        'trials': {'starter': {'features': ['base', 'market_raw', 'lineup_parent']}},
    }))
    monkeypatch.setattr(
        subject, '_augment',
        lambda frame, derived: frame.assign(
            base=.1, market_raw=.6, lineup_parent=.7,
            forensic_market=.1, forensic_lineup=.05))
    monkeypatch.setattr(subject, 'screen_derived_features', lambda *args: (
        ['forensic_lineup'], {
            'all_groups_screened': True,
            'outer_development_used_for_screening': False,
            'final_holdout_used_for_screening': False,
            'equivalent_existing_signal_groups': {'market': 'market_raw'},
        }))
    monkeypatch.setattr(subject, 'TRIALS', {'only': {}})
    monkeypatch.setattr(subject, 'PARAMS', {})

    def fake_trial(fit_frame, validation_frame, columns, params):
        if 'forensic_market' in columns:
            # Preserve the all-derived path but make it lose development.
            return {
                'brier': .26, 'logloss': .71,
                'features_used_in_splits': [
                    'base', 'market_raw', 'forensic_market', 'forensic_lineup'],
            }
        used = ['base', 'forensic_lineup']
        if market_used:
            used.append('market_raw')
        return {'brier': .24, 'logloss': .69, 'features_used_in_splits': used}

    monkeypatch.setattr(subject, '_trial', fake_trial)
    selected, report = subject.development_select(pd.DataFrame({'unused': [1]}))

    if expected_selected:
        assert selected is not None
        assert report['selected_trial'] == 'screened_only'
        trial = report['trials']['screened_only']
        assert trial['all_derived_groups_used'] is False
        assert trial['all_signal_groups_used'] is True
        assert trial['existing_equivalent_group_usage'] == {'market': ['market_raw']}
        assert trial['signal_group_usage']['lineup'] == ['forensic_lineup']
    else:
        assert selected is None
        assert report['accepted_for_final_holdout'] is False
        assert report['trials']['screened_only']['all_signal_groups_used'] is False


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
