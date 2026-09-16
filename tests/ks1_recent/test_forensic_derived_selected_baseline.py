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


def test_representation_columns_replace_redundant_parents_without_losing_information(monkeypatch):
    monkeypatch.setattr(subject, 'SPECS', {
        'forensic_market': {'parents': ('market_raw',)},
        'forensic_delta': {'parents': ('left', 'right')},
    })

    one_parent, one_evidence = subject.representation_columns(
        ['keep', 'market_raw'], 'forensic_market')
    assert one_parent == ['keep', 'forensic_market']
    assert one_evidence == {
        'mode': 'replace_redundant_parents',
        'parents': ['market_raw'],
        'anchor_parent': None,
        'information_preserving': True,
    }

    two_parent, two_evidence = subject.representation_columns(
        ['keep', 'left', 'right'], 'forensic_delta')
    assert two_parent == ['keep', 'left', 'forensic_delta']
    assert two_evidence == {
        'mode': 'replace_redundant_parents',
        'parents': ['left', 'right'],
        'anchor_parent': 'left',
        'information_preserving': True,
    }


def test_nested_screen_uses_representation_when_parents_already_in_baseline(monkeypatch):
    fit = pd.DataFrame({'home_win': [0, 1, 0, 1]})
    validation = pd.DataFrame({'home_win': [0, 1]})
    monkeypatch.setattr(subject, 'split_development', lambda frame: (fit, validation))
    monkeypatch.setattr(subject, '_augment', lambda frame, derived: frame.assign(
        base=.1, market_raw=.6, forensic_market=.1))
    monkeypatch.setattr(subject, 'TRIALS', {'shallow': {}})
    monkeypatch.setattr(subject, 'PARAMS', {})
    monkeypatch.setattr(subject, 'SPECS', {
        'forensic_market': {'parents': ('market_raw',)},
    })
    seen = []

    def fake_trial(fit_frame, validation_frame, columns, params):
        seen.append(list(columns))
        return {
            'brier': .24,
            'logloss': .69,
            'features_used_in_splits': ['base', 'forensic_market'],
        }

    monkeypatch.setattr(subject, '_trial', fake_trial)
    selected, evidence = subject.screen_derived_features(
        pd.DataFrame({'unused': [1]}), ['base', 'market_raw'],
        ['forensic_market'], {'market': ['forensic_market']})

    assert seen == [['base', 'forensic_market']]
    assert selected == ['forensic_market']
    assert evidence['all_groups_screened'] is True
    assert evidence['groups']['market']['candidates']['forensic_market']['representation']['mode'] == (
        'replace_redundant_parents')


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


def test_combined_screened_representation_preserves_one_coordinate_per_replaced_pair(monkeypatch):
    monkeypatch.setattr(subject, 'SPECS', {
        'forensic_market': {'parents': ('market_raw',)},
        'forensic_regime': {'parents': ('recent', 'long')},
        'forensic_lineup': {'parents': ('lineup_home', 'lineup_away')},
    })
    columns, evidence = subject.screened_representation_columns(
        ['base', 'market_raw', 'recent', 'long'],
        ['forensic_market', 'forensic_regime', 'forensic_lineup'])

    assert columns == [
        'base', 'forensic_market', 'recent', 'forensic_regime', 'forensic_lineup']
    assert evidence['forensic_market']['anchor_parent'] is None
    assert evidence['forensic_regime']['anchor_parent'] == 'recent'
    assert evidence['forensic_lineup']['mode'] == 'additive'


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
