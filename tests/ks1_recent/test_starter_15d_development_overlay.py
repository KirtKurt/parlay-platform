from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import ks1.forensic_features as forensic_features
import ks1.forensic_starter_15d_selection as overlay
from ks1.historical_starter_15d_enrichment import _starter_values
from ks1.table import contract


def _stats(*, era_runs, outs, home_runs=1, walks=1, strikeouts=5, bf=20):
    return {
        'gamesStarted': 1,
        'battersFaced': bf,
        'strikeOuts': strikeouts,
        'baseOnBalls': walks,
        'outs': outs,
        'earnedRuns': era_runs,
        'runs': era_runs,
        'hits': 4,
        'homeRuns': home_runs,
        'hitBatsmen': 0,
        'wins': 1,
        'losses': 0,
    }


def _history_row(day, completed, player_id, stats):
    return {
        'day': day,
        'completed': completed,
        'players': [{'id': player_id, 'stats': stats}],
        'starters': stats,
    }


def test_starter_15d_uses_only_strict_prior_middle_window():
    recent = _stats(era_runs=2, outs=15)
    old = _stats(era_runs=8, outs=15, home_runs=3, walks=4, strikeouts=2)
    history = SimpleNamespace(rows=[
        _history_row(
            pd.Timestamp('2026-06-10').date(),
            datetime(2026, 6, 10, 22, tzinfo=timezone.utc),
            '10', recent,
        ),
        _history_row(
            pd.Timestamp('2026-06-01').date(),
            datetime(2026, 6, 1, 22, tzinfo=timezone.utc),
            '10', old,
        ),
    ])
    context = {
        'as_of': '2026-06-20T19:50:00+00:00',
        'sides': {'home': {'probable_pitcher_id': '10'}},
    }
    row = {'date': '2026-06-20', 'home_starter_id': '10'}

    values = _starter_values(history, context, row, 'home')

    assert values['era'] == pytest.approx(3.6)
    assert values['fip'] == pytest.approx(4.3)


def test_starter_15d_rejects_pregame_identity_disagreement():
    history = SimpleNamespace(rows=[])
    context = {
        'as_of': '2026-06-20T19:50:00+00:00',
        'sides': {'home': {'probable_pitcher_id': '10'}},
    }
    row = {'date': '2026-06-20', 'home_starter_id': '11'}

    with pytest.raises(ValueError, match='starter15_pregame_identity_mismatch'):
        _starter_values(history, context, row, 'home')


def test_development_parent_namespace_cannot_enter_raw_recipe():
    _, dictionary = contract({
        'dev_starter15_home_fip': 3.4,
        'dev_starter15_home_era': 3.8,
    })
    roles = {entry['column']: entry['role'] for entry in dictionary}
    assert roles == {
        'dev_starter15_home_era': 'audit',
        'dev_starter15_home_fip': 'audit',
    }


def test_overlay_is_scoped_and_preserves_existing_feature_contract(monkeypatch):
    observed = {}

    def fake_select(train):
        observed['spec_present'] = 'forensic_starter_fip_15d_regime_advantage' in forensic_features.SPECS
        observed['parent_allowed'] = forensic_features._development_parent_allowed(
            'dev_starter15_home_fip')
        values = forensic_features.derive_mapping({
            'dev_starter15_away_fip': 4.7,
            'away_starter_fip_30d': 4.2,
            'dev_starter15_home_fip': 3.4,
            'home_starter_fip_30d': 4.0,
        })
        observed['advantage'] = values['forensic_starter_fip_15d_regime_advantage']
        return ['candidate'], {'contract': 'inner'}

    monkeypatch.setattr(overlay, '_development_select', fake_select)
    selected, report = overlay.development_select(pd.DataFrame({'home_win': [0, 1]}))

    assert selected == ['candidate']
    assert observed['spec_present'] is True
    assert observed['parent_allowed'] is True
    assert observed['advantage'] == pytest.approx(1.1)
    assert report['contract'] == overlay.CONTRACT
    assert report['starter_15d_development_overlay']['final_holdout_used'] is False
    assert 'forensic_starter_fip_15d_regime_advantage' not in forensic_features.SPECS
    assert forensic_features._development_parent_allowed('dev_starter15_home_fip') is False
