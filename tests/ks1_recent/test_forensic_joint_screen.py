import pandas as pd
import pytest

import ks1.forensic_derived_selected_baseline as subject


def _trial_evidence(brier_delta, logloss_delta, used=True):
    return {
        'baseline': {
            'brier': .25 + brier_delta,
            'logloss': .70 + logloss_delta,
            'brier_delta_vs_same_trial_baseline': brier_delta,
            'logloss_delta_vs_same_trial_baseline': logloss_delta,
            'feature_used_in_splits': used,
        },
        'shallow': {
            'brier': .26 + brier_delta,
            'logloss': .71 + logloss_delta,
            'brier_delta_vs_same_trial_baseline': brier_delta,
            'logloss_delta_vs_same_trial_baseline': logloss_delta,
            'feature_used_in_splits': used,
        },
    }


def test_joint_screen_recovers_interaction_safe_second_ranked_representatives(monkeypatch):
    inner_fit = pd.DataFrame({'home_win': [0, 1, 0, 1]})
    inner_validation = pd.DataFrame({'home_win': [0, 1]})
    monkeypatch.setattr(subject, 'split_development', lambda frame: (inner_fit, inner_validation))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame.assign(
        base=.1, market_raw=.6, regime_a=.1, regime_b=.2, workload_a=.3,
        lineup_a=.4, bullpen_a=.5, bullpen_b=.6))
    monkeypatch.setattr(subject, 'TRIALS', {
        'baseline': {'trial': 'baseline'},
        'shallow': {'trial': 'shallow'},
    })
    monkeypatch.setattr(subject, 'PARAMS', {})

    groups = {
        'market': ['forensic_market'],
        'starter_regime': ['regime_a', 'regime_b'],
        'starter_workload': ['workload_a'],
        'lineup': ['lineup_a'],
        'bullpen': ['bullpen_a', 'bullpen_b'],
    }
    evidence = {
        'trials': ['baseline', 'shallow'],
        'baseline_by_trial': {
            'baseline': {'brier': .25, 'logloss': .70},
            'shallow': {'brier': .26, 'logloss': .71},
        },
        'equivalent_existing_signal_groups': {'market': 'market_raw'},
        'groups': {
            'market': {'candidates': {}},
            'starter_regime': {'candidates': {
                'regime_a': {'trials': _trial_evidence(-.02, -.02)},
                'regime_b': {'trials': _trial_evidence(-.01, -.01)},
            }},
            'starter_workload': {'candidates': {
                'workload_a': {'trials': _trial_evidence(-.01, -.01)},
            }},
            'lineup': {'candidates': {
                'lineup_a': {'trials': _trial_evidence(-.01, -.01)},
            }},
            'bullpen': {'candidates': {
                'bullpen_a': {'trials': _trial_evidence(-.02, -.02)},
                'bullpen_b': {'trials': _trial_evidence(-.01, -.01)},
            }},
        },
    }

    def fake_trial(fit, validation, columns, params):
        chosen = set(columns)
        used = ['base', 'market_raw', 'workload_a', 'lineup_a']
        # The independently strongest regime_a + bullpen_a pair is redundant
        # when fit together. The second-ranked pair is the only joint model that
        # actually learns every requested signal family.
        if 'regime_b' in chosen and 'bullpen_b' in chosen:
            used += ['regime_b', 'bullpen_b']
            brier, logloss = (.23, .68) if params['trial'] == 'baseline' else (.24, .69)
        else:
            used += [name for name in ('regime_a', 'regime_b') if name in chosen]
            brier, logloss = (.245, .695) if params['trial'] == 'baseline' else (.255, .705)
        return {'brier': brier, 'logloss': logloss, 'features_used_in_splits': used}

    monkeypatch.setattr(subject, '_trial', fake_trial)
    selected, joint = subject.joint_screen_derived_features(
        pd.DataFrame({'unused': [1]}), ['base', 'market_raw'],
        ['forensic_market', 'regime_a', 'regime_b', 'workload_a',
         'lineup_a', 'bullpen_a', 'bullpen_b'],
        groups, evidence)

    assert selected == ['regime_b', 'workload_a', 'lineup_a', 'bullpen_b']
    assert joint['all_groups_screened'] is True
    assert joint['selected_trial'] == 'baseline'
    assert joint['selected_brier_delta_vs_same_trial_baseline'] == pytest.approx(-.02)
    assert joint['selected_logloss_delta_vs_same_trial_baseline'] == pytest.approx(-.02)
    assert joint['outer_development_used_for_screening'] is False
    assert joint['final_holdout_used_for_screening'] is False
    assert joint['candidate_shortlists']['starter_regime'] == ['regime_a', 'regime_b']
    assert joint['candidate_shortlists']['bullpen'] == ['bullpen_a', 'bullpen_b']
    assert joint['combinations_evaluated'] == 8
    assert joint['eligible_joint_models'] == 2


def test_joint_screen_fails_closed_if_frozen_trial_identity_changes(monkeypatch):
    monkeypatch.setattr(subject, 'TRIALS', {'baseline': {}})
    with pytest.raises(ValueError, match='trial evidence mismatch'):
        subject.joint_screen_derived_features(
            pd.DataFrame({'home_win': [0, 1]}), ['base'], ['feature'],
            {'lineup': ['feature']},
            {'trials': ['different'], 'groups': {}, 'baseline_by_trial': {}})
