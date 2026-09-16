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


def test_development_challenger_augments_selected_recipe_not_all_admitted_raw(monkeypatch):
    fit = pd.DataFrame({'home_win': [0, 1]})
    validation = pd.DataFrame({'home_win': [0, 1]})
    monkeypatch.setattr(subject, 'split_development', lambda train: (fit, validation))
    monkeypatch.setattr(subject, 'choose_features',
                        lambda frame: (['base_a', 'bad_raw_parent', 'parent_x'], [], {'home': 2, 'away': 2}))
    monkeypatch.setattr(subject, 'admit_derived',
                        lambda frame, raw, floor: (['forensic_x'], {}, None))
    monkeypatch.setattr(subject, 'derived_groups', lambda derived: {'market': ['forensic_x']})
    monkeypatch.setattr(subject, 'select', lambda train: (None, {
        'selected': 'starter',
        'metrics': {'starter': {'brier': .25, 'logloss': .70}},
        'trials': {'starter': {'features': ['base_a', 'parent_x']}},
    }))
    monkeypatch.setattr(subject, '_augment',
                        lambda frame, derived: frame.assign(base_a=.1, bad_raw_parent=.2,
                                                            parent_x=.3, forensic_x=.4))
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
