"""Lineup pitch-type matchup proof may use exact games without certifying a date."""
from copy import deepcopy
from datetime import date

from ks1.features import Features
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


def test_legacy_complete_archive_semantics_remain_available_without_receipt_sets():
    games, rows = two_complete_games()
    values = matchup_values(Features(games, rows, statcast_complete=True))
    assert values['lineup_pitch_type_matchup_xwoba_30d'] == .5
    assert values['lineup_pitch_type_matchup_whiff_pct_30d'] == 0
