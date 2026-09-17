import pandas as pd

import ks1.forensic_derived_stable_selection as subject


def _candidate_trials(delta_a, delta_b):
    return {
        'baseline': {
            'brier': .25 + delta_a,
            'logloss': .70 + delta_a,
            'brier_delta_vs_same_trial_baseline': delta_a,
            'logloss_delta_vs_same_trial_baseline': delta_a,
            'feature_used_in_splits': True,
        },
        'shallow': {
            'brier': .26 + delta_b,
            'logloss': .71 + delta_b,
            'brier_delta_vs_same_trial_baseline': delta_b,
            'logloss_delta_vs_same_trial_baseline': delta_b,
            'feature_used_in_splits': True,
        },
    }


def _screen_evidence():
    groups = {
        'market': {'candidates': {}},
        'starter_regime': {'candidates': {
            'regime_fast': {'trials': _candidate_trials(-.03, -.02)},
            'regime_stable': {'trials': _candidate_trials(-.02, -.01)},
        }},
        'starter_workload': {'candidates': {
            'workload': {'trials': _candidate_trials(-.01, -.01)},
        }},
        'lineup': {'candidates': {
            'lineup': {'trials': _candidate_trials(-.01, -.01)},
        }},
        'bullpen': {'candidates': {
            'bullpen_fast': {'trials': _candidate_trials(-.03, -.02)},
            'bullpen_stable': {'trials': _candidate_trials(-.02, -.01)},
        }},
    }
    return {
        'trials': ['baseline', 'shallow'],
        'baseline_by_trial': {
            'baseline': {'brier': .25, 'logloss': .70},
            'shallow': {'brier': .26, 'logloss': .71},
        },
        'equivalent_existing_signal_groups': {'market': 'market_raw'},
        'groups': groups,
    }


def test_two_window_joint_screen_rejects_recent_only_winner(monkeypatch):
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
    monkeypatch.setattr(subject, 'TRIALS', {
        'baseline': {'trial': 'baseline'},
        'shallow': {'trial': 'shallow'},
    })
    monkeypatch.setattr(subject, 'PARAMS', {})

    groups = {
        'market': ['forensic_market'],
        'starter_regime': ['regime_fast', 'regime_stable'],
        'starter_workload': ['workload'],
        'lineup': ['lineup'],
        'bullpen': ['bullpen_fast', 'bullpen_stable'],
    }

    def fake_trial(fit, validation, columns, params):
        chosen = set(columns)
        fold = validation.iloc[0]['fold']
        used = ['base', 'market_raw', 'workload', 'lineup']
        for feature in ('regime_fast', 'regime_stable', 'bullpen_fast', 'bullpen_stable'):
            if feature in chosen:
                used.append(feature)

        if len(columns) == 2:  # baseline-only fold metric
            return {
                'brier': .25 if params['trial'] == 'baseline' else .26,
                'logloss': .70 if params['trial'] == 'baseline' else .71,
                'features_used_in_splits': used,
            }

        fast_pair = 'regime_fast' in chosen and 'bullpen_fast' in chosen
        stable_pair = 'regime_stable' in chosen and 'bullpen_stable' in chosen
        baseline_brier = .25 if params['trial'] == 'baseline' else .26
        baseline_log = .70 if params['trial'] == 'baseline' else .71
        if fast_pair:
            delta = -.03 if fold == 'recent_validation' else .02
        elif stable_pair:
            delta = -.012 if fold == 'recent_validation' else -.008
        else:
            delta = .01
        return {
            'brier': baseline_brier + delta,
            'logloss': baseline_log + delta,
            'features_used_in_splits': used,
        }

    monkeypatch.setattr(subject, '_trial', fake_trial)
    selected, report = subject.joint_screen_derived_features(
        pd.DataFrame({'root': [1]}), ['base', 'market_raw'],
        ['forensic_market', 'regime_fast', 'regime_stable', 'workload',
         'lineup', 'bullpen_fast', 'bullpen_stable'], groups, _screen_evidence())

    assert selected == ['regime_stable', 'workload', 'lineup', 'bullpen_stable']
    assert report['selected_trial'] == 'baseline'
    assert report['all_groups_screened'] is True
    assert report['outer_development_used_for_screening'] is False
    assert report['final_holdout_used_for_screening'] is False
    assert report['folds']['recent']['validation_games'] == 1
    assert report['folds']['earlier']['validation_games'] == 1
    assert report['selected_worst_brier_delta_vs_same_trial_baseline'] < 0


def test_two_window_joint_screen_requires_family_use_on_both_folds(monkeypatch):
    recent_fit = pd.DataFrame({'fold': ['recent_fit']})
    recent_validation = pd.DataFrame({'fold': ['recent_validation']})
    earlier_fit = pd.DataFrame({'fold': ['earlier_fit']})
    earlier_validation = pd.DataFrame({'fold': ['earlier_validation']})
    monkeypatch.setattr(
        subject, 'split_development',
        lambda frame: (recent_fit, recent_validation) if 'root' in frame.columns
        else (earlier_fit, earlier_validation))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame)
    monkeypatch.setattr(subject, 'TRIALS', {'baseline': {'trial': 'baseline'}})
    monkeypatch.setattr(subject, 'PARAMS', {})

    evidence = {
        'trials': ['baseline'],
        'baseline_by_trial': {'baseline': {'brier': .25, 'logloss': .70}},
        'equivalent_existing_signal_groups': {'market': 'market_raw'},
        'groups': {
            'market': {'candidates': {}},
            'lineup': {'candidates': {'lineup': {'trials': {
                'baseline': {
                    'brier': .24, 'logloss': .69,
                    'brier_delta_vs_same_trial_baseline': -.01,
                    'logloss_delta_vs_same_trial_baseline': -.01,
                    'feature_used_in_splits': True,
                }}}}},
        },
    }
    groups = {'market': ['forensic_market'], 'lineup': ['lineup']}

    def fake_trial(fit, validation, columns, params):
        baseline_only = len(columns) == 2
        used = ['base', 'market_raw']
        if not baseline_only and validation.iloc[0]['fold'] == 'recent_validation':
            used.append('lineup')
        return {'brier': .25, 'logloss': .70, 'features_used_in_splits': used}

    monkeypatch.setattr(subject, '_trial', fake_trial)
    selected, report = subject.joint_screen_derived_features(
        pd.DataFrame({'root': [1]}), ['base', 'market_raw'],
        ['forensic_market', 'lineup'], groups, evidence)

    assert selected == []
    assert report['all_groups_screened'] is False
    assert report['reason'] == 'no_joint_model_used_every_signal_family_on_both_inner_folds'
