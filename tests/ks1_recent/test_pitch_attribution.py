from copy import deepcopy

import pytest

from ks1.official_outcomes import (CREDIT_METHOD, digest, endpoint, reconcile,
                                  reconciled_rows, source_name, verify_reconciliation)
from ks1.statcast_history import load_training_statcast
from ks1.statcast_recovery import recover, read_recovery
from tests.ks1.test_statcast_history import fixture
from tests.ks1_recent.test_official_outcomes import evidence, raw_receipt
from tests.ks1_recent.test_recovery import MemoryS3, authorize


def pitch_fixture(kind):
    bundle, raw, key = fixture()
    raw['rows'][0]['release_speed'] = '94.3'
    raw['rows'][1]['release_speed'] = '94.6'
    if kind == 'automatic':
        automatic = {**raw['rows'][0], 'pitch_number': '2', 'pitch_type': '',
                     'description': 'automatic_ball', 'release_speed': '79.8'}
        raw['rows'][1]['pitch_number'] = '3'
        raw['rows'].insert(1, automatic)
    else:
        raw['rows'][0]['batter'] = '202'
    source = evidence(raw)
    play = source['data']['liveData']['plays']['allPlays'][0]
    def event(index, physical_number, speed):
        return {'index': index, 'isPitch': True, 'type': 'pitch', 'pitchNumber': physical_number,
                'startTime': f'2026-09-01T18:10:{index * 10:02d}Z',
                'endTime': f'2026-09-01T18:10:{index * 10 + 5:02d}Z',
                'pitchData': {'startSpeed': speed}, 'details': {'type': {'code': 'FF'}}}
    middle = {'index': 1, 'isPitch': False, 'type': 'no_pitch',
              'startTime': '2026-09-01T18:10:10Z', 'endTime': '2026-09-01T18:10:15Z',
              'details': {'call': {'code': 'VP'}}}
    if kind == 'substitution':
        middle.update(type='action', isSubstitution=True, count={'balls': 2, 'strikes': 1},
                      player={'id': 201}, replacedPlayer={'id': 202}, position={'abbreviation': 'PH'},
                      details={'eventType': 'offensive_substitution'})
    play['playEvents'] = [event(0, 1, 94.3), middle, event(2, 2, 94.6)]
    reseal(source, raw)
    return bundle, raw, key, source


def reseal(source, raw):
    source['receipt'].update(endpoint=endpoint('1', pitch_evidence=True), sha256=digest(source['data']))
    source['retained_receipt'] = {'name': source_name('1', raw['rows'], pitch_evidence=True),
                                'versionId': 'official-v1',
                                'sha256': digest({k: source[k] for k in ('data', 'receipt')})}


@pytest.mark.parametrize('kind', ['automatic', 'substitution'])
def test_exact_official_pitch_evidence_recovers_both_coverage_gates(monkeypatch, kind):
    authorize(monkeypatch)
    bundle, raw, key, source = pitch_fixture(kind)
    before = deepcopy(raw); s3 = MemoryS3(); s3.seed(key, raw)
    initial = load_training_statcast(bundle, s3, 'b')
    assert initial['errors']
    report = recover(bundle, s3, 'b', initial, reconcile_official=True,
                     fetch=lambda _: pytest.fail('reuse retained raw'),
                     fetch_official=lambda _: (source['data'], source['receipt']))
    assert report['recovered_dates'] == [raw['date']]
    payload, receipts = read_recovery(s3, 'b', raw['date'])
    assert raw == before and payload['raw_statcast'] == before
    assert [r['batter'] for r in payload['rows']] == [r['batter'] for r in before['rows']]
    assert [r['woba_value'] for r in payload['rows']] == [r['woba_value'] for r in before['rows']]
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []
    assert bundle['statcast_physical_dates'] == bundle['statcast_retained_dates']
    for key, version in list(s3.versions):
        if 'official-game-advisory-count-pitch-v1/' in key:
            del s3.versions[key, version]
    assert load_training_statcast(bundle, s3, 'b')['errors']
    assert raw['date'] not in bundle['statcast_physical_dates']


@pytest.mark.parametrize('defect', ['real_pitch', 'tracking', 'description', 'sequence',
                                  'speed', 'player', 'chronology', 'event_end', 'source_endpoint'])
def test_contradictory_automatic_evidence_fails_closed(defect):
    bundle, raw, _, source = pitch_fixture('automatic')
    events = source['data']['liveData']['plays']['allPlays'][0]['playEvents']
    if defect == 'real_pitch': events[1]['isPitch'] = True
    elif defect == 'tracking': events[1]['pitchData'] = {'startSpeed': 79.8}
    elif defect == 'description': events[1]['details']['call']['code'] = 'AC'
    elif defect == 'sequence': events.pop()
    elif defect == 'speed': events[0]['pitchData']['startSpeed'] = 80
    elif defect == 'player': raw['rows'][0]['pitcher'] = '251'
    elif defect == 'chronology': events[1]['startTime'] = '2026-09-01T18:09:00Z'
    elif defect == 'event_end': events[-1]['endTime'] = '2026-09-02T18:10:25Z'
    reseal(source, raw)
    if defect == 'source_endpoint': source['receipt']['endpoint'] = endpoint('1')
    with pytest.raises(ValueError):
        reconcile(raw, lambda *a: source, raw_receipt(raw))


@pytest.mark.parametrize('defect', ['two_strikes', 'predecessor', 'successor', 'position',
                                  'extra_substitution', 'wrong_transition', 'missing_credit'])
def test_unproven_substitution_credit_fails_closed(defect):
    bundle, raw, _, source = pitch_fixture('substitution')
    events = source['data']['liveData']['plays']['allPlays'][0]['playEvents']
    if defect == 'two_strikes': events[1]['count']['strikes'] = 2
    elif defect == 'predecessor': events[1]['replacedPlayer']['id'] = 203
    elif defect == 'successor': events[1]['player']['id'] = 203
    elif defect == 'position': events[1]['position']['abbreviation'] = 'PR'
    elif defect == 'extra_substitution': events[0]['isSubstitution'] = True
    elif defect == 'wrong_transition': raw['rows'][0]['batter'], raw['rows'][1]['batter'] = '201', '202'
    elif defect == 'missing_credit': raw['rows'][1]['events'] = ''
    reseal(source, raw)
    with pytest.raises(ValueError):
        reconcile(raw, lambda *a: source, raw_receipt(raw))


def test_legacy_v3_derivations_are_still_reproduced():
    from tests.ks1_recent.test_official_outcomes import unfinished_fixture
    bundle, raw, _, source = unfinished_fixture('')
    rows, changes = reconciled_rows(raw, {'1': source}, method=CREDIT_METHOD)
    payload = {'date': raw['date'], 'raw_statcast': raw, 'rows': rows,
               'outcome_reconciliation': {'method': CREDIT_METHOD, 'official_sources': {'1': source},
                                           'raw_receipt': raw_receipt(raw), 'derivations': changes}}
    verify_reconciliation(payload, {'1': bundle['full'][0]['completedAtUtc']})


@pytest.mark.parametrize('code,description', [('V', 'automatic_ball'), ('VB', 'automatic_ball'),
    ('VC', 'automatic_ball'), ('VP', 'automatic_ball'), ('VS', 'automatic_ball'),
    ('A', 'automatic_strike'), ('AB', 'automatic_strike'), ('AC', 'automatic_strike')])
def test_automatic_codes_match_official_pitch_code_classification(code, description):
    _, raw, _, source = pitch_fixture('automatic')
    source['data']['liveData']['plays']['allPlays'][0]['playEvents'][1]['details']['call']['code'] = code
    raw['rows'][1]['description'] = description
    reseal(source, raw)
    payload = reconcile(raw, lambda *a: source, raw_receipt(raw))
    assert payload['rows'][1]['release_speed'] == ''
    assert payload['rows'][1]['description'] == description


def walkoff_fixture():
    from tests.ks1_recent.test_official_outcomes import unfinished_fixture
    bundle, raw, key, source = unfinished_fixture('')
    plays = source['data']['liveData']['plays']['allPlays']
    play = plays[-1]
    plays[-2]['result'].update(homeScore=2, awayScore=2)
    play['result'].update(eventType='stolen_base_3b', homeScore=3, awayScore=2)
    play['about'].update(isScoringPlay=True, isTopInning=False, inning=11,
                         endTime='2026-09-01T20:01:00Z')
    play['playEvents'] = [{'count': {'balls': 1, 'strikes': 0}, 'endTime': '2026-09-01T20:01:00Z'}]
    play['runners'] = [{'movement': {'end': 'score', 'isOut': False},
                        'details': {'isScoringEvent': True, 'runner': {'id': 202}}}]
    reseal(source, raw)
    return bundle, raw, key, source


def test_walkoff_baserunning_requires_rich_retained_game_ending_evidence(monkeypatch):
    authorize(monkeypatch)
    bundle, raw, key, source = walkoff_fixture()
    s3 = MemoryS3(); s3.seed(key, raw); urls = []
    def fetch(url):
        urls.append(url)
        return source['data'], {**source['receipt'], 'endpoint': url}
    report = recover(bundle, s3, 'b', load_training_statcast(bundle, s3, 'b'),
                     reconcile_official=True, fetch_official=fetch,
                     fetch=lambda _: pytest.fail('retained raw is sufficient'))
    assert urls == [endpoint('1'), endpoint('1', pitch_evidence=True)]
    assert report['recovered_dates'] == [raw['date']]
    payload, _ = read_recovery(s3, 'b', raw['date'])
    assert payload['rows'][-1]['events'] == raw['rows'][-1]['events']
    assert payload['rows'][-1]['woba_denom'] == 0
    assert payload['raw_statcast'] == raw
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []


@pytest.mark.parametrize('defect', ['not_last', 'top', 'early_inning', 'not_scoring',
                                  'not_winning', 'already_ahead', 'no_runner', 'batter_scored',
                                  'four_balls', 'three_strikes', 'late_event', 'not_rich'])
def test_stolen_base_without_proven_walkoff_cannot_remove_a_pa(defect):
    _, raw, _, source = walkoff_fixture()
    plays = source['data']['liveData']['plays']['allPlays']; play = plays[-1]
    if defect == 'not_last': plays.append(deepcopy(plays[0]))
    elif defect == 'top': play['about']['isTopInning'] = True
    elif defect == 'early_inning': play['about']['inning'] = 8
    elif defect == 'not_scoring': play['about']['isScoringPlay'] = False
    elif defect == 'not_winning': play['result']['homeScore'] = 2
    elif defect == 'already_ahead': plays[-2]['result']['homeScore'] = 3
    elif defect == 'no_runner': play['runners'] = []
    elif defect == 'batter_scored': play['runners'][0]['details']['runner']['id'] = 201
    elif defect == 'four_balls': play['playEvents'][-1]['count']['balls'] = 4
    elif defect == 'three_strikes': play['playEvents'][-1]['count']['strikes'] = 3
    elif defect == 'late_event': play['playEvents'][-1]['endTime'] = '2026-09-01T20:02:00Z'
    reseal(source, raw)
    if defect == 'not_rich':
        from tests.ks1_recent.test_official_outcomes import seal
        source['receipt']['endpoint'] = endpoint('1'); seal(source, raw)
    with pytest.raises(ValueError):
        reconcile(raw, lambda *a: source, raw_receipt(raw))


def test_substitution_credit_retains_independent_original_and_resume_times(monkeypatch):
    authorize(monkeypatch)
    bundle, raw, key, source = pitch_fixture('substitution')
    bundle['schedule'][0]['resumeDate'] = '2026-09-02T18:00:00Z'
    bundle['full'][0]['completedAtUtc'] = '2026-09-02T21:00:00Z'
    source['data']['gameData']['datetime']['dateTime'] = bundle['schedule'][0]['resumeDate']
    source['data']['liveData']['plays']['allPlays'][-1]['about']['endTime'] = '2026-09-02T20:00:00Z'
    reseal(source, raw)
    s3 = MemoryS3(); s3.seed(key, raw)
    report = recover(bundle, s3, 'b', load_training_statcast(bundle, s3, 'b'),
                     reconcile_official=True, fetch_official=lambda _: (source['data'], source['receipt']),
                     fetch=lambda _: pytest.fail('reuse retained original-day pitches'))
    assert report['recovered_dates'] == [raw['date']]
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []


@pytest.mark.parametrize('code,description', [('VP', 'automatic_ball'), ('AC', 'automatic_strike')])
def test_documented_top_level_details_code_is_sufficient(code, description):
    _, raw, _, source = pitch_fixture('automatic')
    source['data']['liveData']['plays']['allPlays'][0]['playEvents'][1]['details'] = {'code': code}
    raw['rows'][1]['description'] = description
    reseal(source, raw)
    payload = reconcile(raw, lambda *a: source, raw_receipt(raw))
    assert payload['rows'][1]['release_speed'] == ''


def test_conflicting_official_code_fields_cannot_derive_an_automatic_event():
    _, raw, _, source = pitch_fixture('automatic')
    source['data']['liveData']['plays']['allPlays'][0]['playEvents'][1]['details']['code'] = 'AC'
    reseal(source, raw)
    with pytest.raises(ValueError, match='code fields contradict'):
        reconcile(raw, lambda *a: source, raw_receipt(raw))


def test_walkoff_credit_cannot_be_detached_from_its_reproducible_proof():
    from ks1.statcast_history import official_physical_pitch_counts, physical_validation_reason
    bundle, raw, _, source = walkoff_fixture()
    payload = reconcile(raw, lambda *a: source, raw_receipt(raw))
    expected, batters, invalid = official_physical_pitch_counts(bundle['full'])
    assert physical_validation_reason(payload, raw['date'], {1}, expected, batters, invalid) is None
    detached = {'date': raw['date'], 'rows': deepcopy(payload['rows'])}
    assert physical_validation_reason(detached, raw['date'], {1}, expected, batters, invalid) is not None
    detached['rows'][-1]['events'] = 'official_non_pa_walkoff'
    assert physical_validation_reason(detached, raw['date'], {1}, expected, batters, invalid) is not None
