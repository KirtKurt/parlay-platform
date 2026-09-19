from copy import deepcopy

import pytest

from ks1.official_outcomes import (PITCH_METHOD, digest, endpoint, reconcile, reconciled_rows,
                                  source_name, verify_reconciliation)
from ks1.statcast_history import load_training_statcast
from ks1.statcast_recovery import recover, read_recovery
from tests.ks1_recent.test_official_outcomes import raw_fixture, evidence, raw_receipt
from tests.ks1_recent.test_recovery import MemoryS3, authorize


def fixture():
    bundle, raw, key = raw_fixture()
    row = raw['rows'][1]
    row.update(events='fielders_choice_out', woba_denom='1', woba_value='0',
               pitch_type='FF', release_speed='91.6', bb_type='ground_ball')
    raw['rows'][3]['woba_denom'] = ''
    source = evidence(raw)
    play = source['data']['liveData']['plays']['allPlays'][0]
    play['result'].update(eventType='force_out', isOut=True)
    play['playEvents'] = [{'isPitch': True, 'pitchNumber': 2, 'endTime': play['about']['endTime'],
                           'details': {'isInPlay': True, 'type': {'code': 'FF'}},
                           'pitchData': {'startSpeed': 91.6}, 'hitData': {'trajectory': 'ground_ball'}}]
    play['runners'] = [
        {'movement': {'isOut': True, 'outBase': '2B'},
         'details': {'runner': {'id': 202}, 'movementReason': 'r_force_out'}},
        {'movement': {'isOut': False, 'end': '1B'}, 'details': {'runner': {'id': 201}}}]
    seal(source, raw)
    return bundle, raw, key, source


def seal(source, raw, accounting=True):
    source['receipt'].update(endpoint=endpoint('1', accounting_evidence=accounting),
                             sha256=digest(source['data']))
    source['retained_receipt'] = {'name': source_name('1', raw['rows'], accounting_evidence=accounting),
                                'versionId': 'official-v1',
                                'sha256': digest({k: source[k] for k in ('data', 'receipt')})}


def test_force_out_taxonomy_requires_retained_runner_and_pitch_proof(monkeypatch):
    authorize(monkeypatch)
    bundle, raw, key, source = fixture()
    s3 = MemoryS3(); s3.seed(key, raw); before = deepcopy(raw); urls = []
    def fetch(url):
        urls.append(url); body = deepcopy(source['data'])
        if url == endpoint('1'):
            for p in body['liveData']['plays']['allPlays']:
                p.pop('runners', None); p.pop('playEvents', None)
        return body, {**source['receipt'], 'endpoint': url, 'sha256': digest(body)}
    report = recover(bundle, s3, 'b', load_training_statcast(bundle, s3, 'b'),
                     reconcile_official=True, fetch_official=fetch,
                     fetch=lambda _: pytest.fail('reuse retained raw'))
    assert report['recovered_dates'] == [raw['date']]
    assert urls == [endpoint('1'), endpoint('1', accounting_evidence=True)]
    payload, receipts = read_recovery(s3, 'b', raw['date'])
    assert raw == before == payload['raw_statcast']
    assert payload['rows'][1] == before['rows'][1]
    assert payload['rows'][3]['woba_denom'] == 1
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []
    assert any('official-game-advisory-count-taxonomy-v1/' in r['key'] for r in receipts)
    for key, version in list(s3.versions):
        if 'official-game-advisory-count-taxonomy-v1/' in key:
            del s3.versions[key, version]
    # The original physically complete rows can remain physical-only; the
    # missing outcome denominator cannot regain outcome coverage without proof.
    load_training_statcast(bundle, s3, 'b')
    assert raw['date'] not in bundle['statcast_retained_dates']
    assert raw['date'] in bundle['statcast_physical_dates']


@pytest.mark.parametrize('defect', ['not_forced', 'wrong_base', 'two_outs', 'batter_out',
    'batter_not_first', 'missing_runners', 'not_grounder', 'wrong_pitch', 'wrong_speed',
    'missing_tracking', 'later_pitch', 'nonzero_weight', 'missing_denom', 'ordinary_field_out',
    'wrong_batter', 'wrong_pitcher', 'wrong_source_schema'])
def test_accounting_comparison_does_not_hide_real_outcome_or_provenance_differences(defect):
    _, raw, _, source = fixture(); play = source['data']['liveData']['plays']['allPlays'][0]
    if defect == 'not_forced': play['runners'][0]['details']['movementReason'] = 'r_tag_out'
    elif defect == 'wrong_base': play['runners'][0]['movement']['outBase'] = '1B'
    elif defect == 'two_outs': play['runners'].append(deepcopy(play['runners'][0]))
    elif defect == 'batter_out': play['runners'][1]['movement']['isOut'] = True
    elif defect == 'batter_not_first': play['runners'][1]['movement']['end'] = '2B'
    elif defect == 'missing_runners': play['runners'] = []
    elif defect == 'not_grounder': play['playEvents'][-1]['hitData']['trajectory'] = 'line_drive'
    elif defect == 'wrong_pitch': play['playEvents'][-1]['pitchNumber'] = 3
    elif defect == 'wrong_speed': play['playEvents'][-1]['pitchData']['startSpeed'] = 80
    elif defect == 'missing_tracking': play['playEvents'][-1].pop('pitchData')
    elif defect == 'later_pitch': play['playEvents'][-1]['endTime'] = '2026-09-01T21:00:00Z'
    elif defect == 'nonzero_weight': raw['rows'][1]['woba_value'] = '0.9'
    elif defect == 'missing_denom': raw['rows'][1]['woba_denom'] = ''
    elif defect == 'ordinary_field_out': raw['rows'][1]['events'] = 'field_out'
    elif defect == 'wrong_batter': play['matchup']['batter']['id'] = 202
    elif defect == 'wrong_pitcher': play['matchup']['pitcher']['id'] = 251
    seal(source, raw, accounting=defect != 'wrong_source_schema')
    with pytest.raises((ValueError, KeyError)):
        reconcile(raw, lambda *a: source, raw_receipt(raw))


def test_v4_keeps_its_original_strict_event_comparison():
    _, raw, _, source = fixture(); seal(source, raw, accounting=False)
    with pytest.raises(ValueError, match='identities/outcomes disagree'):
        reconciled_rows(raw, {'1': source}, method=PITCH_METHOD)


def test_retry_reuses_retained_taxonomy_source_with_provider_offline(monkeypatch):
    from ks1.inventory import RESEARCH
    authorize(monkeypatch)
    bundle, raw, key, source = fixture()
    s3 = MemoryS3(); s3.seed(key, raw)
    # State after rich-source retention but before a verified daily artifact.
    name = source_name('1', raw['rows'], accounting_evidence=True)
    s3.seed(RESEARCH + name, {k: source[k] for k in ('data', 'receipt')})
    def offline(*args):
        pytest.fail('retained taxonomy evidence must not require the provider')
    report = recover(bundle, s3, 'b', load_training_statcast(bundle, s3, 'b'),
                     reconcile_official=True, fetch_official=offline, fetch=offline)
    assert report['recovered_dates'] == [raw['date']]
    assert report['provider_requests'] == 0
    payload, receipts = read_recovery(s3, 'b', raw['date'])
    assert payload['rows'][1] == raw['rows'][1]
    assert any(r['key'] == RESEARCH + name and r['versionId'] for r in receipts)
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []


@pytest.mark.parametrize('kind', ['automatic', 'substitution', 'walkoff'])
def test_retained_v4_attribution_objects_remain_reproducible(kind):
    from tests.ks1_recent.test_pitch_attribution import pitch_fixture, walkoff_fixture
    bundle, raw, _, source = walkoff_fixture() if kind == 'walkoff' else pitch_fixture(kind)
    source['receipt'].update(endpoint=endpoint('1', pitch_evidence=True,
                                               game_advisories=False),
                             sha256=digest(source['data']))
    source['retained_receipt'] = {
        'name': source_name('1', raw['rows'], pitch_evidence=True,
                            game_advisories=False),
        'versionId': 'official-v4',
        'sha256': digest({key: source[key] for key in ('data', 'receipt')})}
    rows, changes = reconciled_rows(raw, {'1': source}, method=PITCH_METHOD)
    payload = {'date': raw['date'], 'raw_statcast': raw, 'rows': rows,
               'outcome_reconciliation': {'method': PITCH_METHOD, 'official_sources': {'1': source},
                                           'raw_receipt': raw_receipt(raw), 'derivations': changes}}
    verify_reconciliation(payload, {'1': bundle['full'][0]['completedAtUtc']})
