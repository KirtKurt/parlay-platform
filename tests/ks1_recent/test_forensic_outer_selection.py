import pandas as pd

import ks1.forensic_derived_outer_selection as subject


def _stable_rejection_report():
    def item(feature):
        return {
            'features_by_group': {'lineup': feature},
            'trial': 'baseline',
            'folds': {
                'recent': {
                    'brier_delta_vs_same_trial_baseline': -.001,
                    'logloss_delta_vs_same_trial_baseline': -.002,
                },
                'earlier': {
                    'brier_delta_vs_same_trial_baseline': -.0005,
                    'logloss_delta_vs_same_trial_baseline': -.001,
                },
            },
            'all_signal_groups_used_on_both_folds': True,
            'passes_metric_gates_on_both_folds': True,
        }

    return {
        'contract': 'old',
        'accepted_for_final_holdout': False,
        'reason': 'derived_forensic_selected_baseline_candidate_not_superior_on_development',
        'derived_admitted_features': ['forensic_market', 'lineup_a', 'lineup_b'],
        'derived_groups': {
            'market': ['forensic_market'],
            'lineup': ['lineup_a', 'lineup_b'],
        },
        'baseline_raw_features': ['base', 'market_raw'],
        'baseline_metrics': {'brier': .25, 'logloss': .70},
        'nested_group_screen': {
            'selected_features': ['lineup_a'],
        },
        'nested_joint_group_screen': {
            'equivalent_existing_signal_groups': {'market': 'market_raw'},
            'selected_features': ['lineup_a'],
            'combinations': [item('lineup_a'), item('lineup_b')],
        },
        'trials': {},
        'selected_trial': None,
    }


def _patch_outer(monkeypatch, used_lineup=True):
    report = _stable_rejection_report()
    monkeypatch.setattr(subject.stable, 'development_select', lambda train: (None, report))
    fit = pd.DataFrame({'fold': ['fit']})
    validation = pd.DataFrame({'fold': ['validation']})
    monkeypatch.setattr(subject.stable, 'split_development', lambda train: (fit, validation))
    monkeypatch.setattr(subject.stable, '_augment', lambda frame, derived: frame)
    monkeypatch.setattr(subject.stable, 'TRIALS', {'baseline': {'trial': 'baseline'}})
    monkeypatch.setattr(subject.stable, 'PARAMS', {})

    def fake_trial(fit_frame, validation_frame, columns, params):
        assert columns == ['base', 'market_raw', 'lineup_b']
        used = ['market_raw'] + (['lineup_b'] if used_lineup else [])
        return {
            'brier': .24,
            'logloss': .69,
            'features_used_in_splits': used,
            'parameters': params,
        }

    monkeypatch.setattr(subject.stable, '_trial', fake_trial)
    return report


def test_outer_development_can_select_second_independently_stable_feature_set(monkeypatch):
    _patch_outer(monkeypatch, used_lineup=True)
    selected, report = subject.development_select(pd.DataFrame({'root': [1]}))

    assert selected is not None
    assert report['contract'] == subject.CONTRACT
    assert report['accepted_for_final_holdout'] is True
    assert report['selected_trial'] == 'joint_stable_alternate_01_baseline'
    assert report['features'] == ['base', 'market_raw', 'lineup_b']
    assert report['trials']['joint_stable_alternate_01_baseline']['beats_selected_baseline'] is True
    assert report['trials']['joint_stable_alternate_01_baseline']['all_signal_groups_used'] is True
    outer = report['outer_stable_candidate_selection']
    assert outer['eligible_alternative_feature_sets'] == 1
    assert outer['final_holdout_used_for_selection'] is False
    assert outer['outer_development_used_for_inner_screening'] is False
    assert outer['selected_trial'] == 'joint_stable_alternate_01_baseline'


def test_outer_development_still_requires_substantive_family_use(monkeypatch):
    _patch_outer(monkeypatch, used_lineup=False)
    selected, report = subject.development_select(pd.DataFrame({'root': [1]}))

    assert selected is None
    assert report['accepted_for_final_holdout'] is False
    assert report['reason'] == 'derived_forensic_selected_baseline_candidate_not_superior_on_development'
    trial = report['trials']['joint_stable_alternate_01_baseline']
    assert trial['beats_selected_baseline'] is True
    assert trial['all_signal_groups_used'] is False
    outer = report['outer_stable_candidate_selection']
    assert outer['reason'] == 'no_inner_stable_alternative_superior_on_outer_development'
    assert outer['final_holdout_used_for_selection'] is False
