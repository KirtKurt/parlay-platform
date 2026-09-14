from copy import deepcopy

import pytest

from ks1.features import Features
from ks1.statcast_events import is_thrown_pitch
from ks1.statcast_history import load_training_statcast, official_pitch_counts, pitches_complete
from tests.ks1.test_statcast_history import RetainedS3, fixture


def automatic(row, description='automatic_strike'):
    return {**row, 'pitch_number': '3', 'description': description,
            'pitch_type': '', 'release_speed': '', 'type': 'S',
            'events': 'strikeout', 'woba_denom': '1', 'woba_value': '0',
            'estimated_woba_using_speedangle': ''}


@pytest.mark.parametrize('description', ['automatic_ball', 'automatic_strike'])
def test_automatic_events_reconcile_without_relaxing_pitch_counts(description):
    bundle, payload, _ = fixture()
    expected, invalid = official_pitch_counts(bundle['full'])
    rows = payload['rows']
    event = automatic(rows[0], description)
    assert pitches_complete(rows+[event], {1}, expected, invalid)
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
    rows = payload['rows']
    for row in rows:
        row['release_speed'] = '95'
        if row['pitch_number'] == '1':
            row.update(type='S', description='called_strike', woba_denom='0')
    events = [automatic(row) for row in rows if row['pitch_number'] == '2']
    payload['rows'] = rows+events
    report = load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'bucket')
    assert report['verified_pitch_objects'] == 1
    assert report['pitch_coverage_method'] == 'official_box_thrown_pitch_counts_v2'
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
    bundle, payload, _ = fixture()
    source = deepcopy(bundle['full'])
    source[0]['teams']['home']['players']['151']['stats']['pitching']['numberOfPitches'] = 0
    expected, invalid = official_pitch_counts(source)
    rows = [r for r in payload['rows'] if r['pitcher'] != '151']
    rows.append(automatic(payload['rows'][0]))
    assert pitches_complete(rows, {1}, expected, invalid)
