import pandas as pd

import ks1.forensic_derived_stable_selection as subject


def test_two_window_joint_screen_requires_metric_win_on_each_fold(monkeypatch):
    recent_fit = pd.DataFrame({'fold': ['recent_fit']})
    recent_validation = pd.DataFrame({'fold': ['recent_validation']})
    earlier_fit = pd.DataFrame({'fold': ['earlier_fit']})
    earlier_validation = pd.DataFrame({'fold': ['earlier_validation']})

    def fake_split(frame):
        if 'root' in frame.columns:
            return recent_fit, recent_validation
        assert frame.iloc[0]['fold'] == 'recent_fit'
        return earlier_fit, earlier_validation

    monkeypatch.setattr(subject, 'split_development', fake_split)
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame)
    monkeypatch.setattr(subject, 'TRIALS', {'baseline': {'trial': 'baseline'}})
    monkeypatch.setattr(subject, 'PARAMS', {})

    evidence = {
        'trials': ['baseline'],
        'baseline_by_trial': {'baseline': {'brier': .25, 'logloss': .70}},
        'equivalent_existing_signal_groups': {'market': 'market_raw'},
        'groups': {
            'market': {'candidates': {}},
            'lineup': {'candidates': {
                'lineup': {'trials': {
                    'baseline': {
                        'brier': .24,
                        'logloss': .69,
                        'brier_delta_vs_same_trial_baseline': -.01,
                        'logloss_delta_vs_same_trial_baseline': -.01,
                        'feature_used_in_splits': True,
                    },
                }},
            }},
        },
    }
    groups = {'market': ['forensic_market'], 'lineup': ['lineup']}

    def fake_trial(fit, validation, columns, params):
        baseline_only = len(columns) == 2
        used = ['base', 'market_raw']
        if not baseline_only:
            used.append('lineup')
        if baseline_only:
            return {'brier': .25, 'logloss': .70, 'features_used_in_splits': used}
        delta = -.02 if validation.iloc[0]['fold'] == 'recent_validation' else .01
        return {
            'brier': .25 + delta,
            'logloss': .70 + delta,
            'features_used_in_splits': used,
        }

    monkeypatch.setattr(subject, '_trial', fake_trial)
    selected, report = subject.joint_screen_derived_features(
        pd.DataFrame({'root': [1]}),
        ['base', 'market_raw'],
        ['forensic_market', 'lineup'],
        groups,
        evidence,
    )

    assert selected == []
    assert report['all_groups_screened'] is False
    assert report['split_used_joint_models'] == 1
    assert report['eligible_joint_models'] == 0
    assert report['reason'] == (
        'no_joint_model_improved_brier_with_no_worse_logloss_on_both_inner_folds')
    assert report['combinations'][0]['all_signal_groups_used_on_both_folds'] is True
    assert report['combinations'][0]['passes_metric_gates_on_both_folds'] is False
    assert report['outer_development_used_for_screening'] is False
    assert report['final_holdout_used_for_screening'] is False
