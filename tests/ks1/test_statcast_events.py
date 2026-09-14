from copy import deepcopy

import pytest

from ks1.features import Features
from ks1.statcast_events import is_thrown_pitch
from ks1.statcast_history import (load_training_statcast, official_pitch_counts,
                                  pitches_complete)
from tests.ks1.test_statcast_history import RetainedS3, fixture


def automatic(row, description='automatic_strike'):
    return {**row, 'at_bat_number': str(10000+int(row['at_bat_number'])),
            'pitch_number': '1', 'description': description,
            'pitch_type': '', 'release_speed': '', 'type': 'S',
            'events': 'walk' if description == 'automatic_ball' else 'strikeout',
            'woba_denom': '1', 'woba_value': '.7' if description == 'automatic_ball' else '0',
            'estimated_woba_using_speedangle': ''}


@pytest.mark.parametrize('description', ['automatic_ball', 'automatic_strike'])
def test_automatic_events_reconcile_without_relaxing_pitch_counts(description):
    bundle, payload, _ = fixture()
    bundle['full'][0]['teams']['home']['players']['151']['stats']['pitching']['battersFaced'] = 10
    expected, invalid = official_pitch_counts(bundle['full'])
    rows = payload['rows']
    event = automatic(rows[0], description)
    assert pitches_complete(rows+[event], {1}, expected, invalid)
    # Physical counts still match if only the automatic outcome is lost.
    assert not pitches_complete(rows, {1}, expected, invalid)
    assert not pitches_complete(rows[1:]+[event], {1}, expected, invalid)
    assert not pitches_complete(rows+[event, event], {1}, expected, invalid)
    assert not pitches_complete(rows+[dict(event, pitcher='999')], {1}, expected, invalid)
    assert not pitches_complete(rows+[dict(event, game_pk='999')], {1}, expected, invalid)
    # A genuine untracked pitch remains in the exact count.
    assert is_thrown_pitch(dict(event, description='ball'))
    assert not pitches_complete(rows+[dict(event, description='ball')], {1}, expected, invalid)
    for key, value in [('pitch_type', 'FF'), ('release_speed', '95')]:
        assert is_thrown_pitch({**event, key: value})
        assert not pitches_complete(rows+[{**event, key: value}], {1}, expected, invalid)


def test_automatic_outcomes_survive_but_do_not_dilute_physical_pitch_features():
    bundle, payload, key = fixture()
    for side, pid in [('home', '151'), ('away', '151')]:
        bundle['full'][0]['teams'][side]['players'][pid]['stats']['pitching']['battersFaced'] = 18
    rows = payload['rows']
    for row in rows:
        row['release_speed'] = '95'
        if row['pitch_number'] == '1':
            row.update(type='S', description='called_strike', woba_denom='0')
    events = [automatic(row) for row in rows if row['pitch_number'] == '2']
    payload['rows'] = rows+events
    report = load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'bucket')
    assert report['verified_pitch_objects'] == 1
    assert report['verified_physical_pitch_objects'] == 1
    assert report['pitch_coverage_method'] == 'official_box_physical_v1_plus_pa_outcomes_v3'
    engine = Features(bundle['full'], bundle['statcast'],
                      statcast_retained_dates=bundle['statcast_retained_dates'])
    profiles, values = engine.lineup_batters_at(
        '2026-09-02T17:50:00Z', list(range(101, 110)), '251', 'R')
    assert values['lineup_platoon_xwoba_7d'] == .25
    assert values['lineup_pitch_type_matchup_xwoba_30d'] == .5
    for profile in profiles:
        window = profile['windows']['7d']
        assert window['xwoba'] == .25
        assert window['csw_pct'] == 50
    starter = engine.statcast('251', {'1'}, 18)
    assert starter['complete'] == 1
    assert starter['pitches'] == 18 and starter['velocity'] == 95
    assert starter['xwoba'] == .25 and starter['xwoba_pa'] == 18
    assert starter['csw_pct'] == 50 and starter['ff_mix_pct'] == 100
    bullpen = engine.bullpen_roster_at('2026-09-02T17:50:00Z', '10', ['151'])
    assert bullpen['bullpen_context_xwoba_7d'] == .25
    assert bullpen['bullpen_context_csw_pct_7d'] == 50
    assert bullpen['bullpen_context_velocity_7d'] == 95


def test_zero_pitch_official_appearance_can_contain_automatic_outcome():
    bundle, payload, key = fixture()
    source = deepcopy(bundle['full'])
    source[0]['teams']['home']['players']['151']['stats']['pitching']['numberOfPitches'] = 0
    source[0]['teams']['home']['players']['151']['stats']['pitching']['battersFaced'] = 1
    expected, invalid = official_pitch_counts(source)
    rows = [r for r in payload['rows'] if r['pitcher'] != '151']
    rows.append(automatic(payload['rows'][0]))
    assert pitches_complete(rows, {1}, expected, invalid)
    bundle['full'], payload['rows'] = source, rows
    report = load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'bucket')
    assert report['verified_pitch_objects'] == 1
    assert report['verified_physical_pitch_objects'] == 1
    engine = Features(source, bundle['statcast'], statcast_retained_dates=bundle['statcast_retained_dates'])
    bullpen = engine.bullpen_roster_at('2026-09-02T17:50:00Z', '10', ['151'])
    assert bullpen['bullpen_context_xwoba_7d'] == 0
    for metric in ('csw_pct', 'swstr_pct', 'velocity', 'avg_ev_allowed', 'barrel_pct'):
        assert bullpen[f'bullpen_context_{metric}_7d'] is None


@pytest.mark.parametrize('defect', ['missing_pa_count', 'duplicate_pa', 'missing_terminal', 'unknown_outcome'])
def test_outcomes_need_independent_official_completeness(defect):
    bundle, payload, key = fixture()
    if defect == 'missing_pa_count':
        bundle['full'][0]['teams']['home']['players']['151']['stats']['pitching'].pop('battersFaced')
    elif defect == 'duplicate_pa':
        payload['rows'][0]['events'] = 'walk'
    else:
        payload['rows'][1]['events'] = '' if defect == 'missing_terminal' else 'unsupported_event'
    report = load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'bucket')
    assert report['verified_pitch_objects'] == 0
    assert report['verified_physical_pitch_objects'] == 1
    assert '2026-09-01' not in bundle['statcast_retained_dates']
    assert '2026-09-01' in bundle['statcast_physical_dates']
    assert report['errors'][0]['physical_pitch_coverage_retained'] is True


def test_sacrifice_pa_is_counted_without_woba_denominator():
    bundle, payload, _ = fixture()
    payload['rows'][1].update(events='sac_bunt', woba_denom='0')
    expected, invalid = official_pitch_counts(bundle['full'])
    assert pitches_complete(payload['rows'], {1}, expected, invalid)


@pytest.mark.parametrize('event', ['batter_interference', 'fan_interference',
                                  'strike_out', 'strikeout_triple_play'])
def test_additional_official_terminal_codes_are_not_lost(event):
    bundle, payload, _ = fixture()
    payload['rows'][1].update(events=event, woba_denom='1', woba_value='0')
    expected, invalid = official_pitch_counts(bundle['full'])
    assert pitches_complete(payload['rows'], {1}, expected, invalid)


@pytest.mark.parametrize('description', ['automatic_ball', 'automatic_strike'])
@pytest.mark.parametrize('field,value', [('woba_denom', ''), ('woba_denom', None),
                                      ('woba_denom', '0'), ('woba_denom', 'nan'),
                                      ('woba_value', ''), ('woba_value', None),
                                      ('woba_value', 'nan'), ('woba_value', 'inf'),
                                      ('woba_value', '-1')])
def test_incomplete_automatic_outcome_never_qualifies(description, field, value):
    bundle, payload, key = fixture()
    bundle['full'][0]['teams']['home']['players']['151']['stats']['pitching']['battersFaced'] = 10
    payload['rows'].append({**automatic(payload['rows'][0], description), field: value})
    report = load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'bucket')
    assert report['verified_pitch_objects'] == 0
    assert report['verified_physical_pitch_objects'] == 1
    assert '2026-09-01' not in bundle['statcast_retained_dates']
    assert '2026-09-01' in bundle['statcast_physical_dates']


def test_pending_official_scoring_is_not_a_complete_outcome():
    bundle, payload, _ = fixture()
    payload['rows'][1]['events'] = 'os_ruling_pending_primary'
    expected, invalid = official_pitch_counts(bundle['full'])
    assert not pitches_complete(payload['rows'], {1}, expected, invalid)


def test_physical_only_receipt_enables_whiff_matchup_but_not_xwoba():
    bundle, payload, key = fixture()
    payload['rows'][-1]['woba_denom'] = ''
    report = load_training_statcast(
        bundle, RetainedS3({key: (payload, 'v1', None)}), 'bucket')
    assert '2026-09-01' in bundle['statcast_physical_dates']
    assert '2026-09-01' not in bundle['statcast_retained_dates']
    assert report['verified_physical_dates'] == report['verified_outcome_dates']+1
    engine = Features(
        bundle['full'], bundle['statcast'],
        statcast_retained_dates=bundle['statcast_retained_dates'],
        statcast_physical_dates=bundle['statcast_physical_dates'])
    profiles, values = engine.lineup_batters_at(
        '2026-09-02T17:50:00Z', list(range(101, 110)), '251', 'R')
    assert values['lineup_pitch_type_matchup_xwoba_30d'] is None
    assert values['lineup_pitch_type_matchup_whiff_pct_30d'] == 0
    assert all(profile['windows']['7d']['xwoba'] is None for profile in profiles)
    assert all(profile['windows']['7d']['csw_pct'] == 50 for profile in profiles)


def test_starter_windows_reject_unverified_pa_dates_but_keep_official_results():
    bundle, payload, key = fixture()
    bundle['full'][0]['teams']['home']['players']['151']['stats']['pitching']['battersFaced'] = 10
    # Compact rows remain loaded, but are missing the automatic terminal PA.
    bundle['statcast'] = deepcopy(payload['rows'])
    load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'bucket')
    engine = Features(bundle['full'], bundle['statcast'],
                      statcast_retained_dates=bundle['statcast_retained_dates'],
                      statcast_physical_dates=bundle['statcast_physical_dates'])
    values = engine.at('2026-09-02T17:50:00Z', '10', '151')
    for window in ('7d', '30d'):
        assert values[f'starter_era_{window}'] == 0
        assert values[f'starter_complete_{window}'] == 1
        assert values[f'starter_xwoba_{window}'] is None
        assert values[f'starter_csw_pct_{window}'] == 50
    # Restoring genuine provider evidence re-enables the same windows.
    payload['rows'].append(automatic(payload['rows'][0]))
    load_training_statcast(bundle, RetainedS3({key: (payload, 'v2', None)}), 'bucket')
    repaired = Features(bundle['full'], bundle['statcast'],
                        statcast_retained_dates=bundle['statcast_retained_dates'],
                        statcast_physical_dates=bundle['statcast_physical_dates'])
    assert repaired.at('2026-09-02T17:50:00Z', '10', '151')['starter_xwoba_7d'] == .45


def test_starter_last_three_requires_every_contributing_date_verified():
    bundle, payload, _ = fixture()
    games, rows, dates = [], [], []
    for pk, date in ((1, '2026-08-20'), (2, '2026-08-25'), (3, '2026-09-01')):
        game = deepcopy(bundle['full'][0])
        game.update(officialGamePk=pk, startAtUtc=date+'T18:00:00Z', completedAtUtc=date+'T21:00:00Z')
        game['teams']['home']['players']['151']['stats']['pitching']['gamesStarted'] = 1
        games.append(game)
        rows.extend({**row, 'game_pk':str(pk), 'game_date':date} for row in payload['rows'])
        dates.append(date)
    partial = Features(games, rows, statcast_retained_dates=dates[1:]).at('2026-09-02T17:50:00Z', '10', '151')
    complete = Features(games, rows, statcast_retained_dates=dates).at('2026-09-02T17:50:00Z', '10', '151')
    assert partial['starter_era_last3'] == complete['starter_era_last3'] == 0
    assert partial['starter_xwoba_last3'] is None
    assert complete['starter_xwoba_last3'] == .5
    game_verified = Features(games, rows, statcast_retained_dates=dates[1:],
                             statcast_verified_games=['1'])
    assert game_verified.at('2026-09-02T17:50:00Z', '10', '151')['starter_xwoba_last3'] == .5
    # One retained game must not certify every other club's games that day.
    from datetime import date as calendar_date
    assert not game_verified.team_statcast_window_complete(calendar_date(2026, 9, 2), 30)
