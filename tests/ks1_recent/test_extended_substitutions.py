from copy import deepcopy

import pytest

from ks1.official_outcomes import (ACCOUNTING_METHOD, GAME_ADVISORY_METHOD,
                                  MOUND_VISIT_METHOD, PITCH_METHOD,
                                  TWO_STRIKE_METHOD, digest, endpoint, reconcile,
                                  reconciled_rows, source_name)
from ks1.statcast_history import load_training_statcast
from ks1.statcast_recovery import recover
from tests.ks1_recent.test_official_outcomes import evidence, raw_receipt
from tests.ks1_recent.test_pitch_attribution import pitch_fixture, reseal
from tests.ks1_recent.test_recovery import MemoryS3, authorize


def extended_fixture(kind):
    bundle, raw, key, source = pitch_fixture('substitution' if kind == 'two_strikes' else 'automatic')
    play = source['data']['liveData']['plays']['allPlays'][0]
    events = play['playEvents']
    if kind == 'two_strikes':
        events[0]['count'] = {'balls': 2, 'strikes': 2}
        events[1]['count']['strikes'] = 2
    else:
        events.insert(0, {'index': 0, 'isPitch': False, 'isSubstitution': True,
            'type': 'action', 'startTime': '2026-09-01T18:09:50Z',
            'endTime': '2026-09-01T18:10:00Z',
            'details': {'eventType': 'pitching_substitution'},
            'position': {'abbreviation': 'P'}, 'player': {'id': int(raw['rows'][0]['pitcher'])},
            'count': {'balls': 0, 'strikes': 0}})
        for i, event in enumerate(events): event['index'] = i
    reseal(source, raw)
    return bundle, raw, key, source


def mound_visit_fixture():
    bundle, raw, key, source = extended_fixture('prefix_pitcher')
    events = source['data']['liveData']['plays']['allPlays'][0]['playEvents']
    events.insert(0, {'index': 0, 'isPitch': False, 'type': 'action',
        'startTime': '2026-09-01T18:09:40Z', 'endTime': '2026-09-01T18:09:49Z',
        'details': {'eventType': 'mound_visit'},
        'count': {'balls': 0, 'strikes': 0}})
    for i, event in enumerate(events): event['index'] = i
    reseal(source, raw)
    return bundle, raw, key, source


def game_advisory_fixture():
    bundle, raw, key, source = pitch_fixture('automatic')
    # The general Statcast fixture deliberately uses player IDs as at-bat IDs.
    # Reindex it to a real game-wide PA sequence so the candidate is play zero.
    at_bats = {value: str(index + 1) for index, value in enumerate(dict.fromkeys(
        row['at_bat_number'] for row in raw['rows']))}
    for row in raw['rows']:
        row['at_bat_number'] = at_bats[row['at_bat_number']]
    pitch_events = deepcopy(source['data']['liveData']['plays']['allPlays'][0]['playEvents'])
    source = evidence(raw)
    play = source['data']['liveData']['plays']['allPlays'][0]
    events = play['playEvents'] = pitch_events
    advisories = [
        ('2026-09-01T17:00:00Z', '2026-09-01T17:30:00Z'),
        ('2026-09-01T17:30:00Z', '2026-09-01T17:50:00Z'),
        ('2026-09-01T17:50:00Z', events[0]['startTime']),
    ]
    events[:0] = [{'index': index, 'isPitch': False, 'type': 'action',
        'startTime': start, 'endTime': end,
        'details': {'eventType': 'game_advisory', 'description': description},
        'count': {'balls': 0, 'strikes': 0, 'outs': 0}}
        for index, ((start, end), description) in enumerate(zip(advisories, (
            'Status Change - Pre-Game', 'Status Change - Warmup',
            'Status Change - In Progress')))]
    play['about']['atBatIndex'] = play['atBatIndex'] = 0
    for index, event in enumerate(events):
        event['index'] = index
    reseal(source, raw)
    return bundle, raw, key, source


@pytest.mark.parametrize('kind', ['two_strikes', 'prefix_pitcher'])
def test_proven_substitution_recovers_both_gates_without_rewriting_players(monkeypatch, kind):
    authorize(monkeypatch)
    bundle, raw, key, source = extended_fixture(kind)
    before = deepcopy(raw); s3 = MemoryS3(); s3.seed(key, raw)
    report = recover(bundle, s3, 'b', load_training_statcast(bundle, s3, 'b'),
        reconcile_official=True, fetch=lambda _: pytest.fail('retained raw available'),
        fetch_official=lambda _: (source['data'], source['receipt']))
    assert report['recovered_dates'] == [raw['date']]
    assert raw == before
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []
    assert [r['batter'] for r in bundle['statcast']] == [r['batter'] for r in raw['rows']]
    for method in (PITCH_METHOD, ACCOUNTING_METHOD):
        with pytest.raises(ValueError):
            reconciled_rows(raw, {'1': source}, method=method)


@pytest.mark.parametrize('defect', ['strikeout', 'missing_prior_count', 'wrong_prior_balls',
                                  'wrong_prior_strikes', 'invalid_balls'])
def test_two_strike_credit_requires_non_strikeout_and_matching_inherited_count(defect):
    _, raw, _, source = extended_fixture('two_strikes')
    play = source['data']['liveData']['plays']['allPlays'][0]; events = play['playEvents']
    if defect == 'strikeout':
        play['result']['eventType'] = raw['rows'][1]['events'] = 'strikeout'
    elif defect == 'missing_prior_count': events[0].pop('count')
    elif defect == 'wrong_prior_balls': events[0]['count']['balls'] = 1
    elif defect == 'wrong_prior_strikes': events[0]['count']['strikes'] = 1
    elif defect == 'invalid_balls': events[1]['count']['balls'] = 4
    reseal(source, raw)
    with pytest.raises(ValueError): reconcile(raw, lambda *a: source, raw_receipt(raw))


@pytest.mark.parametrize('defect', ['mid_count', 'balls', 'strikes', 'player', 'position',
                                  'type', 'is_pitch', 'extra_substitution', 'chronology'])
def test_prefix_pitching_change_cannot_hide_other_substitutions(defect):
    _, raw, _, source = extended_fixture('prefix_pitcher')
    events = source['data']['liveData']['plays']['allPlays'][0]['playEvents']; sub = events[0]
    if defect == 'mid_count':
        events[0], events[1] = events[1], events[0]
        for i, event in enumerate(events): event['index'] = i
    elif defect in ('balls', 'strikes'): sub['count'][defect] = 1
    elif defect == 'player': sub['player']['id'] = 999
    elif defect == 'position': sub['position']['abbreviation'] = 'PH'
    elif defect == 'type': sub['details']['eventType'] = 'offensive_substitution'
    elif defect == 'is_pitch': sub['isPitch'] = True
    elif defect == 'extra_substitution': events[1]['isSubstitution'] = True
    elif defect == 'chronology': sub['endTime'] = '2026-09-01T18:10:01Z'
    reseal(source, raw)
    with pytest.raises(ValueError): reconcile(raw, lambda *a: source, raw_receipt(raw))


def test_zero_count_mound_visit_may_precede_prefix_pitching_change():
    _, raw, _, source = mound_visit_fixture()
    payload = reconcile(raw, lambda *a: source, raw_receipt(raw))
    assert payload['rows'][1]['release_speed'] == ''
    source['receipt'].update(endpoint=endpoint('1', pitch_evidence=True,
                                               game_advisories=False),
                             sha256=digest(source['data']))
    source['retained_receipt'] = {
        'name': source_name('1', raw['rows'], pitch_evidence=True,
                            game_advisories=False),
        'versionId': 'official-v8',
        'sha256': digest({key: source[key] for key in ('data', 'receipt')})}
    with pytest.raises(ValueError, match='unexpected substitution'):
        reconciled_rows(raw, {'1': source}, method=TWO_STRIKE_METHOD)


def test_contiguous_pregame_advisories_may_precede_first_count_event():
    _, raw, _, source = game_advisory_fixture()
    payload = reconcile(raw, lambda *a: source, raw_receipt(raw))
    assert payload['rows'][1]['release_speed'] == ''
    source['receipt'].update(endpoint=endpoint('1', pitch_evidence=True,
                                               game_advisories=False),
                             sha256=digest(source['data']))
    source['retained_receipt'] = {
        'name': source_name('1', raw['rows'], pitch_evidence=True,
                            game_advisories=False),
        'versionId': 'official-v9',
        'sha256': digest({key: source[key] for key in ('data', 'receipt')})}
    with pytest.raises(ValueError, match='chronology'):
        reconciled_rows(raw, {'1': source}, method=MOUND_VISIT_METHOD)


def test_retained_v9_endpoint_and_namespace_remain_reproducible():
    _, raw, _, source = mound_visit_fixture()
    source['receipt'].update(endpoint=endpoint('1', pitch_evidence=True,
                                               game_advisories=False),
                             sha256=digest(source['data']))
    source['retained_receipt'] = {
        'name': source_name('1', raw['rows'], pitch_evidence=True,
                            game_advisories=False),
        'versionId': 'official-v1',
        'sha256': digest({key: source[key] for key in ('data', 'receipt')})}
    rows, changes = reconciled_rows(raw, {'1': source}, method=MOUND_VISIT_METHOD)
    assert rows[1]['release_speed'] == ''
    assert len(changes) == 1


def test_v11_requests_and_retains_complete_advisory_count_evidence():
    assert 'description' in endpoint('1', pitch_evidence=True)
    assert 'outs' in endpoint('1', pitch_evidence=True)
    assert 'description' in endpoint('1', pitch_evidence=True, advisory_outs=False)
    assert 'outs' not in endpoint('1', pitch_evidence=True, advisory_outs=False)
    assert 'description' not in endpoint('1', pitch_evidence=True,
                                         game_advisories=False)
    assert source_name('1', [], pitch_evidence=True) != source_name(
        '1', [], pitch_evidence=True, advisory_outs=False)
    assert source_name('1', [], pitch_evidence=True) != source_name(
        '1', [], pitch_evidence=True, game_advisories=False)
    assert endpoint('1', inning_evidence=True).count('outs') == 1
    assert source_name('1', [], inning_evidence=True) == source_name(
        '1', [], inning_evidence=True, advisory_outs=False)


def test_retained_v10_endpoint_and_namespace_remain_reproducible():
    _, raw, _, source = pitch_fixture('automatic')
    source['receipt'].update(endpoint=endpoint('1', pitch_evidence=True,
                                               advisory_outs=False),
                             sha256=digest(source['data']))
    source['retained_receipt'] = {
        'name': source_name('1', raw['rows'], pitch_evidence=True,
                            advisory_outs=False),
        'versionId': 'official-v10',
        'sha256': digest({key: source[key] for key in ('data', 'receipt')})}
    rows, changes = reconciled_rows(raw, {'1': source}, method=GAME_ADVISORY_METHOD)
    assert rows[1]['release_speed'] == ''
    assert len(changes) == 1


@pytest.mark.parametrize('method', [MOUND_VISIT_METHOD, None])
def test_rich_source_endpoint_is_bound_to_reconciliation_method(method):
    _, raw, _, source = pitch_fixture('automatic')
    if method is None:
        source['receipt'].update(endpoint=endpoint('1', pitch_evidence=True,
                                                   advisory_outs=False),
                                 sha256=digest(source['data']))
        source['retained_receipt'] = {
            'name': source_name('1', raw['rows'], pitch_evidence=True,
                                advisory_outs=False),
            'versionId': 'official-v10',
            'sha256': digest({key: source[key] for key in ('data', 'receipt')})}
        call = lambda: reconciled_rows(raw, {'1': source})
    else:
        call = lambda: reconciled_rows(raw, {'1': source}, method=method)
    with pytest.raises(ValueError, match='does not match reconciliation method'):
        call()


def test_v11_validates_counts_on_every_retained_game_advisory():
    _, raw, _, source = pitch_fixture('automatic')
    source['data']['liveData']['plays']['allPlays'][1]['playEvents'] = [{
        'details': {'eventType': 'game_advisory'}, 'count': {'balls': 0, 'strikes': 0}}]
    reseal(source, raw)
    with pytest.raises(ValueError, match='game advisory count evidence incomplete'):
        reconcile(raw, lambda *args: source, raw_receipt(raw))


def test_v10_inning_artifact_does_not_inherit_v11_global_count_validation():
    from tests.ks1_recent.test_inning_ending import fixture as inning_fixture
    _, raw, _, source = inning_fixture()
    source['data']['liveData']['plays']['allPlays'][-1]['playEvents'] = [{
        'details': {'eventType': 'game_advisory'},
        'count': {'balls': 0, 'strikes': 0}}]
    source['receipt'].update(endpoint=endpoint('1', inning_evidence=True,
                                               advisory_outs=False),
                             sha256=digest(source['data']))
    source['retained_receipt'] = {
        'name': source_name('1', raw['rows'], inning_evidence=True,
                            advisory_outs=False),
        'versionId': 'official-v10',
        'sha256': digest({key: source[key] for key in ('data', 'receipt')})}
    rows, changes = reconciled_rows(raw, {'1': source}, method=GAME_ADVISORY_METHOD)
    assert rows[-1]['woba_denom'] == 0
    assert changes


@pytest.mark.parametrize('defect', ['not_first_pa', 'count', 'pitch', 'substitution',
                                  'pitch_data', 'pitch_number', 'event_type',
                                  'description', 'wrong_length', 'gap', 'late_end',
                                  'does_not_bracket_start'])
def test_pregame_advisory_prelude_is_narrow_and_fail_closed(defect):
    _, raw, _, source = game_advisory_fixture()
    play = source['data']['liveData']['plays']['allPlays'][0]
    events, advisory = play['playEvents'], play['playEvents'][0]
    if defect == 'not_first_pa': play['about']['atBatIndex'] = play['atBatIndex'] = 1
    elif defect == 'count': advisory['count']['outs'] = 1
    elif defect == 'pitch': advisory['isPitch'] = True
    elif defect == 'substitution': advisory['isSubstitution'] = True
    elif defect == 'pitch_data': advisory['pitchData'] = {'startSpeed': 94.3}
    elif defect == 'pitch_number': advisory['pitchNumber'] = 1
    elif defect == 'event_type': advisory['details']['eventType'] = 'mound_visit'
    elif defect == 'description': advisory['details']['description'] = 'Status Change - Delayed'
    elif defect == 'wrong_length': events.pop(0)
    elif defect == 'gap': events[1]['startTime'] = '2026-09-01T17:31:00Z'
    elif defect == 'late_end': events[2]['endTime'] = '2026-09-01T18:10:01Z'
    elif defect == 'does_not_bracket_start': advisory['startTime'] = '2026-09-01T18:01:00Z'
    for index, event in enumerate(events):
        event['index'] = index
    reseal(source, raw)
    with pytest.raises(ValueError):
        reconcile(raw, lambda *a: source, raw_receipt(raw))


@pytest.mark.parametrize('defect', ['event_type', 'count', 'pitch', 'substitution',
                                  'pitch_data', 'pitch_number', 'start_after_change',
                                  'backward_time', 'ends_after_first_count'])
def test_mound_visit_prelude_is_narrow_and_fail_closed(defect):
    _, raw, _, source = mound_visit_fixture()
    visit = source['data']['liveData']['plays']['allPlays'][0]['playEvents'][0]
    if defect == 'event_type': visit['details']['eventType'] = 'game_advisory'
    elif defect == 'count': visit['count']['balls'] = 1
    elif defect == 'pitch': visit['isPitch'] = True
    elif defect == 'substitution': visit['isSubstitution'] = True
    elif defect == 'pitch_data': visit['pitchData'] = {'startSpeed': 94.3}
    elif defect == 'pitch_number': visit['pitchNumber'] = 1
    elif defect == 'start_after_change': visit['startTime'] = '2026-09-01T18:09:51Z'
    elif defect == 'backward_time': visit['endTime'] = '2026-09-01T18:09:39Z'
    elif defect == 'ends_after_first_count': visit['endTime'] = '2026-09-01T18:10:06Z'
    reseal(source, raw)
    with pytest.raises(ValueError):
        reconcile(raw, lambda *a: source, raw_receipt(raw))


@pytest.mark.parametrize('kind', ['accounting', 'automatic', 'substitution'])
def test_retained_v5_objects_reproduce_under_their_original_policy(kind):
    from ks1.official_outcomes import verify_reconciliation
    from tests.ks1_recent.test_pa_accounting import fixture
    bundle, raw, _, source = fixture() if kind == 'accounting' else pitch_fixture(kind)
    accounting = kind == 'accounting'
    source['receipt'].update(endpoint=endpoint('1', accounting_evidence=accounting,
                                               pitch_evidence=not accounting,
                                               game_advisories=False),
                             sha256=digest(source['data']))
    source['retained_receipt'] = {
        'name': source_name('1', raw['rows'], accounting_evidence=accounting,
                            pitch_evidence=not accounting, game_advisories=False),
        'versionId': 'official-v5',
        'sha256': digest({key: source[key] for key in ('data', 'receipt')})}
    rows, changes = reconciled_rows(raw, {'1': source}, method=ACCOUNTING_METHOD)
    payload = {'date': raw['date'], 'raw_statcast': raw, 'rows': rows,
        'outcome_reconciliation': {'method': ACCOUNTING_METHOD, 'official_sources': {'1': source},
            'raw_receipt': raw_receipt(raw), 'derivations': changes}}
    verify_reconciliation(payload, {'1': bundle['full'][0]['completedAtUtc']})
