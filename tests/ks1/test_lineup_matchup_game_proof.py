"""Lineup pitch-type matchup proof may use exact games without certifying a date."""
from copy import deepcopy
from datetime import date

import pytest

from ks1.features import Features
from mlb_research.ks1.features import Features as ResearchFeatures
from tests.ks1.test_statcast_history import fixture


def two_complete_games():
    bundle, payload, _ = fixture()
    recent = deepcopy(bundle['full'][0])
    older = deepcopy(recent)
    older.update(
        officialGamePk=2,
        startAtUtc='2026-08-30T18:00:00Z',
        completedAtUtc='2026-08-30T21:00:00Z',
    )
    rows = list(payload['rows']) + [
        {**row, 'game_pk': '2', 'game_date': '2026-08-30'}
        for row in payload['rows']
    ]
    return [older, recent], rows


def engine(*, outcome_games, physical_games):
    games, rows = two_complete_games()
    return Features(
        games,
        rows,
        statcast_complete=False,
        statcast_retained_dates=[],
        statcast_physical_dates=[],
        statcast_verified_games=outcome_games,
        statcast_physical_games=physical_games,
    )


def matchup_values(subject):
    _, values = subject.lineup_batters_at(
        '2026-09-02T17:50:00Z', range(101, 110), '251', 'R')
    return values


def test_exact_game_proof_recovers_matchup_without_certifying_rejected_dates():
    subject = engine(outcome_games=['1', '2'], physical_games=['1', '2'])
    assert not subject.team_statcast_window_complete(date(2026, 9, 2), 30)
    assert not subject.team_statcast_outcome_window_complete(date(2026, 9, 2), 30)
    values = matchup_values(subject)
    assert values['lineup_pitch_type_matchup_xwoba_30d'] == .5
    assert values['lineup_pitch_type_matchup_whiff_pct_30d'] == 0
    # This repair is deliberately narrower than generic batter-window coverage.
    assert values['lineup_xwoba_30d'] is None


def test_physical_game_proof_can_recover_whiff_without_outcome_xwoba():
    values = matchup_values(engine(outcome_games=[], physical_games=['1', '2']))
    assert values['lineup_pitch_type_matchup_xwoba_30d'] is None
    assert values['lineup_pitch_type_matchup_whiff_pct_30d'] == 0


def test_missing_one_exact_contributing_game_remains_fail_closed():
    values = matchup_values(engine(outcome_games=['1', '2'], physical_games=['1']))
    assert values['lineup_pitch_type_matchup_xwoba_30d'] is None
    assert values['lineup_pitch_type_matchup_whiff_pct_30d'] is None


def test_rowless_unverified_official_starter_appearance_remains_fail_closed():
    games, rows = two_complete_games()
    for player in games[0]['teams']['home']['players'].values():
        player['stats']['batting'] = {}
    subject = Features(
        games,
        [row for row in rows if row['game_pk'] == '1'],
        statcast_complete=False,
        statcast_retained_dates=[],
        statcast_physical_dates=[],
        statcast_verified_games=['1'],
        statcast_physical_games=['1'],
    )
    values = matchup_values(subject)
    assert values['lineup_pitch_type_matchup_xwoba_30d'] is None
    assert values['lineup_pitch_type_matchup_whiff_pct_30d'] is None


def test_unverified_row_bearing_relief_appearance_remains_fail_closed():
    games, rows = two_complete_games()
    games[0]['teams']['away']['players']['151']['stats']['pitching']['gamesStarted'] = 0
    for player in games[0]['teams']['home']['players'].values():
        player['stats']['batting'] = {}
    subject = Features(
        games,
        rows,
        statcast_complete=False,
        statcast_retained_dates=[],
        statcast_physical_dates=[],
        statcast_verified_games=['1'],
        statcast_physical_games=['1'],
    )
    values = matchup_values(subject)
    assert values['lineup_pitch_type_matchup_xwoba_30d'] is None
    assert values['lineup_pitch_type_matchup_whiff_pct_30d'] is None


def test_legacy_complete_archive_semantics_remain_available_without_receipt_sets():
    games, rows = two_complete_games()
    values = matchup_values(Features(games, rows, statcast_complete=True))
    assert values['lineup_pitch_type_matchup_xwoba_30d'] == .5
    assert values['lineup_pitch_type_matchup_whiff_pct_30d'] == 0


@pytest.mark.parametrize('pitching', [
    {'gamesStarted': 0, 'numberOfPitches': 18, 'battersFaced': 9},
    {'gamesStarted': 0, 'numberOfPitches': 2, 'battersFaced': 0},
    {'gamesStarted': 0},
])
@pytest.mark.parametrize('feature_class', [Features, ResearchFeatures])
def test_rowless_official_relief_appearance_requires_physical_proof(pitching, feature_class):
    games, rows = two_complete_games()
    games[0]['teams']['away']['players']['151']['stats']['pitching'] = pitching
    # Remove the lineup's boxes so only the opposing pitcher's appearance can
    # require this game; no observed Statcast rows remain to discover it.
    for player in games[0]['teams']['home']['players'].values():
        player['stats']['batting'] = {}
    subject = feature_class(
        games, [row for row in rows if row['game_pk'] == '1'],
        statcast_complete=False, statcast_retained_dates=[],
        statcast_physical_dates=[], statcast_verified_games=['1'],
        statcast_physical_games=['1'],
    )
    values = matchup_values(subject)
    for window in ('7d', '30d'):
        assert values['lineup_pitch_type_matchup_xwoba_' + window] is None
        assert values['lineup_pitch_type_matchup_whiff_pct_' + window] is None


@pytest.mark.parametrize('feature_class', [Features, ResearchFeatures])
@pytest.mark.parametrize('state', ['verified_relief', 'roster_only', 'after_cutoff'])
def test_official_appearance_proof_preserves_supported_and_prior_only_paths(feature_class, state):
    games, rows = two_complete_games()
    pitcher = games[0]['teams']['away']['players']['151']
    pitcher['stats']['pitching']['gamesStarted'] = 0
    proofs = ['1', '2']
    if state != 'verified_relief':
        rows = [row for row in rows if row['game_pk'] == '1']
        proofs = ['1']
        for player in games[0]['teams']['home']['players'].values():
            player['stats']['batting'] = {}
        if state == 'roster_only':
            pitcher['stats']['pitching'] = {}
        else:
            games[0]['completedAtUtc'] = '2026-09-02T18:00:00Z'
    values = matchup_values(feature_class(
        games, rows, statcast_complete=False, statcast_retained_dates=[],
        statcast_physical_dates=[], statcast_verified_games=proofs,
        statcast_physical_games=proofs,
    ))
    for window in ('7d', '30d'):
        assert values['lineup_pitch_type_matchup_xwoba_' + window] == .5
        assert values['lineup_pitch_type_matchup_whiff_pct_' + window] == 0
