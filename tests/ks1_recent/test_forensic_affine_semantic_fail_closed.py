import pandas as pd
import pytest

import ks1.forensic_derived_affine_semantic_selection as subject


def test_affine_existing_parent_rejects_nonfinite_baseline_metric(monkeypatch):
    evidence = {
        'trials': ['baseline'],
        'baseline_by_trial': {'baseline': {'brier': float('nan'), 'logloss': .70}},
        'baseline_feature_usage_by_trial': {'baseline': ['market_raw']},
        'groups': {
            'market': {
                'candidates': {},
                'selected': 'forensic_market',
                'selected_trial': 'baseline',
                'existing_equivalent_parent': None,
                'existing_equivalent_parent_trial': None,
                'existing_equivalent_parent_is_new_feature': False,
            },
        },
        'selected_features': ['forensic_market'],
        'equivalent_existing_signal_groups': {},
        'all_groups_screened': True,
    }
    monkeypatch.setattr(
        subject, '_ORIGINAL_SCREEN', lambda *args: (['forensic_market'], evidence))
    monkeypatch.setattr(
        subject.base,
        '_single_affine_existing_parent',
        lambda columns, baseline: 'market_raw',
    )

    with pytest.raises(ValueError, match='metric nonfinite'):
        subject.screen_derived_features(
            pd.DataFrame({'home_win': [0, 1]}),
            ['market_raw'],
            ['forensic_market'],
            {'market': ['forensic_market']},
        )
