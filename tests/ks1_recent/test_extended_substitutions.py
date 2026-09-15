from copy import deepcopy

import pytest

from ks1.official_outcomes import (ACCOUNTING_METHOD, PITCH_METHOD, TWO_STRIKE_METHOD,
                                  reconcile, reconciled_rows)
from ks1.statcast_history import load_training_statcast
from ks1.statcast_recovery import recover
from tests.ks1_recent.test_official_outcomes import raw_receipt
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
    with pytest.raises(ValueError, match='unexpected substitution'):
        reconciled_rows(raw, {'1': source}, method=TWO_STRIKE_METHOD)


@pytest.mark.parametrize('defect', ['event_type', 'count', 'pitch', 'substitution',
                                  'start_after_change', 'backward_time'])
def test_mound_visit_prelude_is_narrow_and_fail_closed(defect):
    _, raw, _, source = mound_visit_fixture()
    visit = source['data']['liveData']['plays']['allPlays'][0]['playEvents'][0]
    if defect == 'event_type': visit['details']['eventType'] = 'game_advisory'
    elif defect == 'count': visit['count']['balls'] = 1
    elif defect == 'pitch': visit['isPitch'] = True
    elif defect == 'substitution': visit['isSubstitution'] = True
    elif defect == 'start_after_change': visit['startTime'] = '2026-09-01T18:09:51Z'
    elif defect == 'backward_time': visit['endTime'] = '2026-09-01T18:09:39Z'
    reseal(source, raw)
    with pytest.raises(ValueError, match='unexpected substitution'):
        reconcile(raw, lambda *a: source, raw_receipt(raw))


@pytest.mark.parametrize('kind', ['accounting', 'automatic', 'substitution'])
def test_retained_v5_objects_reproduce_under_their_original_policy(kind):
    from ks1.official_outcomes import verify_reconciliation
    from tests.ks1_recent.test_pa_accounting import fixture
    bundle, raw, _, source = fixture() if kind == 'accounting' else pitch_fixture(kind)
    rows, changes = reconciled_rows(raw, {'1': source}, method=ACCOUNTING_METHOD)
    payload = {'date': raw['date'], 'raw_statcast': raw, 'rows': rows,
        'outcome_reconciliation': {'method': ACCOUNTING_METHOD, 'official_sources': {'1': source},
            'raw_receipt': raw_receipt(raw), 'derivations': changes}}
    verify_reconciliation(payload, {'1': bundle['full'][0]['completedAtUtc']})
