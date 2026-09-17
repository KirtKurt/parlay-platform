import pandas as pd

import ks1.forensic_derived_affine_semantic_selection as subject


def _evidence(alias_used=True, parent_used=True):
    return {
        'method': 'old',
        'trials': ['baseline'],
        'baseline_by_trial': {'baseline': {'brier': .25, 'logloss': .70}},
        'baseline_feature_usage_by_trial': {
            'baseline': ['base'] + (['market_raw'] if parent_used else []),
        },
        'groups': {
            'market': {
                'candidates': {
                    'forensic_market': {
                        'trials': {
                            'baseline': {
                                'brier': .24,
                                'logloss': .69,
                                'brier_delta_vs_same_trial_baseline': -.01,
                                'logloss_delta_vs_same_trial_baseline': -.01,
                                'feature_used_in_splits': alias_used,
                            },
                        },
                    },
                },
                'selected': 'forensic_market' if alias_used else None,
                'selected_trial': 'baseline' if alias_used else None,
                'existing_equivalent_parent': None,
                'existing_equivalent_parent_trial': None,
                'existing_equivalent_parent_is_new_feature': False,
            },
            'lineup': {
                'candidates': {},
                'selected': 'lineup_a',
                'selected_trial': 'baseline',
                'existing_equivalent_parent': None,
                'existing_equivalent_parent_trial': None,
                'existing_equivalent_parent_is_new_feature': False,
            },
        },
        'selected_features': (
            ['forensic_market', 'lineup_a'] if alias_used else ['lineup_a']
        ),
        'equivalent_existing_signal_groups': {},
        'all_groups_screened': True,
    }


def test_affine_existing_family_uses_raw_parent_even_when_alias_gets_split(monkeypatch):
    evidence = _evidence(alias_used=True, parent_used=True)
    monkeypatch.setattr(subject, '_ORIGINAL_SCREEN', lambda *args: (
        ['forensic_market', 'lineup_a'], evidence))
    monkeypatch.setattr(
        subject.base,
        '_single_affine_existing_parent',
        lambda columns, baseline: 'market_raw' if columns == ['forensic_market'] else None,
    )

    selected, result = subject.screen_derived_features(
        pd.DataFrame({'home_win': [0, 1]}),
        ['base', 'market_raw'],
        ['forensic_market', 'lineup_a'],
        {'market': ['forensic_market'], 'lineup': ['lineup_a']},
    )

    assert selected == ['lineup_a']
    assert result['equivalent_existing_signal_groups'] == {'market': 'market_raw'}
    assert result['groups']['market']['selected'] is None
    assert result['groups']['market']['existing_equivalent_parent'] == 'market_raw'
    assert result['groups']['market']['existing_equivalent_parent_trial'] == 'baseline'
    assert result['groups']['market']['candidates']['forensic_market']['trials']['baseline'][
        'feature_used_in_splits'] is True
    assert result['all_groups_screened'] is True
    assert result['affine_existing_signal_policy'].startswith('raw_parent_must')


def test_affine_alias_cannot_substitute_when_existing_raw_parent_is_not_used(monkeypatch):
    evidence = _evidence(alias_used=True, parent_used=False)
    monkeypatch.setattr(subject, '_ORIGINAL_SCREEN', lambda *args: (
        ['forensic_market', 'lineup_a'], evidence))
    monkeypatch.setattr(
        subject.base,
        '_single_affine_existing_parent',
        lambda columns, baseline: 'market_raw' if columns == ['forensic_market'] else None,
    )

    selected, result = subject.screen_derived_features(
        pd.DataFrame({'home_win': [0, 1]}),
        ['base', 'market_raw'],
        ['forensic_market', 'lineup_a'],
        {'market': ['forensic_market'], 'lineup': ['lineup_a']},
    )

    assert selected == ['lineup_a']
    assert result['equivalent_existing_signal_groups'] == {}
    assert result['groups']['market']['selected'] is None
    assert result['groups']['market']['existing_equivalent_parent'] is None
    assert result['all_groups_screened'] is False


def test_development_wrapper_restores_base_screen_and_preserves_gate_metadata(monkeypatch):
    original = subject.base.screen_derived_features

    def fake_outer(train):
        assert subject.base.screen_derived_features is subject.screen_derived_features
        return None, {'contract': 'old', 'reason': 'development_rejected'}

    monkeypatch.setattr(subject.outer, 'development_select', fake_outer)
    selected, report = subject.development_select(pd.DataFrame({'home_win': [0, 1]}))

    assert selected is None
    assert subject.base.screen_derived_features is original
    assert report['contract'] == subject.CONTRACT
    assert report['affine_existing_signal_semantics'] == {
        'policy': 'raw_parent_only_for_whole_single_affine_existing_family',
        'final_holdout_used': False,
        'metric_gates_changed': False,
        'trial_set_changed': False,
    }
