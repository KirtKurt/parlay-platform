from copy import deepcopy

import pytest

from ks1.inventory import RESEARCH
from ks1.official_outcomes import (SUBSTITUTION_METHOD, digest, endpoint, reconcile,
                                  reconciled_rows, source_name)
from ks1.statcast_history import (load_training_statcast, official_physical_pitch_counts,
                                  physical_validation_reason)
from ks1.statcast_recovery import recover, read_recovery
from tests.ks1_recent.test_official_outcomes import unfinished_fixture, raw_receipt
from tests.ks1_recent.test_recovery import MemoryS3, authorize


def fixture():
    bundle, raw, key, source = unfinished_fixture('')
    raw['rows'][-1].update(type='B', release_speed='90.0', pitch_type='FF')
    plays = source['data']['liveData']['plays']['allPlays']; play = plays[-1]
    plays[-2]['about'].update(inning=5, isTopInning=True, endTime='2026-09-01T19:59:00Z')
    plays[-2]['count'] = {'outs': 2}
    play['result'].update(eventType='wild_pitch', isOut=True)
    play['about'].update(inning=5, isTopInning=True)
    play['count'] = {'balls': 2, 'strikes': 2, 'outs': 3}
    play['playEvents'] = [{'index': 0, 'isPitch': True, 'pitchNumber': 1,
        'count': {'balls': 2, 'strikes': 2, 'outs': 2},
        'details': {'isInPlay': False, 'type': {'code': 'FF'}},
        'pitchData': {'startSpeed': 90.0}, 'endTime': play['about']['endTime']}]
    play['runners'] = [{'movement': {'isOut': True, 'outNumber': 3, 'outBase': '3B'},
        'details': {'eventType': 'other_out', 'movementReason': 'r_runner_out',
                    'runner': {'id': 202}, 'playIndex': 0}}]
    plays.append({'atBatIndex': 999, 'about': {'atBatIndex': 999, 'inning': 5, 'isTopInning': False, 'isComplete': True,
                            'endTime': '2026-09-01T20:01:00Z'},
                  'result': {'eventType': 'caught_stealing_2b'}})
    seal(source, raw)
    return bundle, raw, key, source


def seal(source, raw):
    source['receipt'].update(endpoint=endpoint('1', inning_evidence=True), sha256=digest(source['data']))
    source['retained_receipt'] = {'name': source_name('1', raw['rows'], inning_evidence=True),
        'versionId': 'official-v1', 'sha256': digest({k: source[k] for k in ('data', 'receipt')})}


@pytest.mark.parametrize('cached', [False, True])
def test_third_runner_out_restores_both_gates_with_exact_retained_proof(monkeypatch, cached):
    authorize(monkeypatch)
    bundle, raw, key, source = fixture(); before = deepcopy(raw)
    s3 = MemoryS3(); s3.seed(key, raw); urls = []
    if cached:
        s3.seed(RESEARCH + source_name('1', raw['rows'], inning_evidence=True),
                {k: source[k] for k in ('data', 'receipt')})
    def fetch(url):
        assert not cached, 'retained rich source must work with provider offline'
        urls.append(url)
        return source['data'], {**source['receipt'], 'endpoint': url}
    report = recover(bundle, s3, 'b', load_training_statcast(bundle, s3, 'b'),
        reconcile_official=True, fetch_official=fetch, fetch=lambda _: pytest.fail('reuse raw'))
    assert report['recovered_dates'] == [raw['date']]
    assert urls == ([] if cached else [endpoint('1'), endpoint('1', inning_evidence=True)])
    payload, receipts = read_recovery(s3, 'b', raw['date'])
    assert payload['raw_statcast'] == raw == before
    assert payload['rows'][-1]['events'] == '' and payload['rows'][-1]['woba_denom'] == 0
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []
    expected, batters, invalid = official_physical_pitch_counts(bundle['full'])
    detached = {'date': raw['date'], 'rows': deepcopy(payload['rows'])}
    assert physical_validation_reason(detached, raw['date'], {1}, expected, batters, invalid)
    detached['rows'][-1]['events'] = 'official_non_pa_inning_ending'
    assert physical_validation_reason(detached, raw['date'], {1}, expected, batters, invalid)
    for k, v in list(s3.versions):
        if 'official-game-advisory-inning-v1/' in k: del s3.versions[k, v]
    assert load_training_statcast(bundle, s3, 'b')['errors']


@pytest.mark.parametrize('defect', ['not_third', 'not_two_before', 'same_half', 'wrong_inning',
    'batter_out', 'two_outs', 'wrong_runner_event', 'wrong_play_index', 'not_third_runner',
    'four_balls', 'third_strike', 'count_disagrees', 'in_play', 'wrong_speed', 'wrong_pitch',
    'late_event', 'missing_runners', 'missing_next', 'wrong_schema'])
def test_inning_ending_cannot_suppress_an_unproven_pa(defect):
    _, raw, _, source = fixture(); plays = source['data']['liveData']['plays']['allPlays']
    play = plays[-2]; terminal = play['playEvents'][-1]; out = play['runners'][0]
    if defect == 'not_third': play['count']['outs'] = 2
    elif defect == 'not_two_before': plays[-3]['count']['outs'] = 1
    elif defect == 'same_half': plays[-1]['about']['isTopInning'] = True
    elif defect == 'wrong_inning': plays[-1]['about']['inning'] = 7
    elif defect == 'batter_out': out['details']['runner']['id'] = int(raw['rows'][-1]['batter'])
    elif defect == 'two_outs': play['runners'].append(deepcopy(out))
    elif defect == 'wrong_runner_event': out['details']['eventType'] = 'field_out'
    elif defect == 'wrong_play_index': out['details']['playIndex'] = 1
    elif defect == 'not_third_runner': out['movement']['outNumber'] = 2
    elif defect == 'four_balls': play['count']['balls'] = terminal['count']['balls'] = 4
    elif defect == 'third_strike': play['count']['strikes'] = terminal['count']['strikes'] = 3
    elif defect == 'count_disagrees': terminal['count']['balls'] = 1
    elif defect == 'in_play': terminal['details']['isInPlay'] = True
    elif defect == 'wrong_speed': terminal['pitchData']['startSpeed'] = 80
    elif defect == 'wrong_pitch': terminal['pitchNumber'] = 2
    elif defect == 'late_event': terminal['endTime'] = '2026-09-01T20:00:01Z'
    elif defect == 'missing_runners': play['runners'] = []
    elif defect == 'missing_next': plays.pop()
    seal(source, raw)
    if defect == 'wrong_schema': source['receipt']['endpoint'] = endpoint('1', pitch_evidence=True)
    with pytest.raises(ValueError): reconcile(raw, lambda *a: source, raw_receipt(raw))


def test_v6_does_not_gain_new_inning_ending_policy():
    _, raw, _, source = fixture()
    with pytest.raises(ValueError): reconciled_rows(raw, {'1': source}, method=SUBSTITUTION_METHOD)


@pytest.mark.parametrize('kind', ['two_strikes', 'prefix_pitcher'])
def test_v6_substitution_artifacts_still_reproduce(kind):
    from tests.ks1_recent.test_extended_substitutions import extended_fixture
    from ks1.official_outcomes import verify_reconciliation
    bundle, raw, _, source = extended_fixture(kind)
    rows, changes = reconciled_rows(raw, {'1': source}, method=SUBSTITUTION_METHOD)
    payload = {'date': raw['date'], 'raw_statcast': raw, 'rows': rows,
        'outcome_reconciliation': {'method': SUBSTITUTION_METHOD, 'official_sources': {'1': source},
            'raw_receipt': raw_receipt(raw), 'derivations': changes}}
    verify_reconciliation(payload, {'1': bundle['full'][0]['completedAtUtc']})
