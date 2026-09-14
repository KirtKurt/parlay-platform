from copy import deepcopy
import json

import pytest

from ks1.official_outcomes import digest, endpoint, reconcile, verify_reconciliation
from ks1.statcast_history import load_training_statcast, official_pitch_counts, validation_reason
from ks1.statcast_recovery import recover, read_recovery
from tests.ks1.test_statcast_history import fixture
from tests.ks1_recent.test_recovery import MemoryS3, authorize


def evidence(raw):
    plays = []
    for row in raw['rows']:
        if not row['events']:
            continue
        index = int(row['at_bat_number']) - 1
        plays.append({'atBatIndex': index, 'result': {'eventType': row['events']},
                      'about': {'atBatIndex': index, 'isComplete': True,
                                'endTime': '2026-09-01T20:00:00Z'},
                      'matchup': {'batter': {'id': int(row['batter'])},
                                  'pitcher': {'id': int(row['pitcher'])}}})
    data = {'gameData': {'game': {'pk': 1}, 'datetime': {'dateTime': '2026-09-01T18:00:00Z'},
                         'status': {'abstractGameState': 'Final'}},
            'liveData': {'plays': {'allPlays': plays}}}
    source = {'data': data, 'receipt': {'endpoint': endpoint('1'), 'sha256': digest(data),
                                      'retrievedAtUtc': '2026-09-14T20:00:00Z'}}
    seal(source, raw)
    return source


def seal(source, raw):
    source['receipt']['sha256'] = digest(source['data'])
    source['retained_receipt'] = {
        'name': 'sources/official-pa-accounting-v1/1/' + digest(raw['rows']) + '.json',
        'versionId': 'official-v1',
        'sha256': digest({key: source[key] for key in ('data', 'receipt')})}


def raw_receipt(raw):
    return {'name': f"sources/statcast-recovery-v1/{raw['date']}/raw/{digest(raw)}.json",
            'versionId': 'raw-v1', 'sha256': digest(raw)}


def raw_fixture():
    bundle, raw, key = fixture()
    terminal = raw['rows'][1]
    terminal.update(events='field_out', woba_denom='', woba_value='')
    return bundle, raw, key


def test_independent_official_outcomes_restore_accounting_without_editing_raw(monkeypatch):
    authorize(monkeypatch)
    bundle, raw, key = raw_fixture()
    original = deepcopy(raw)
    official = evidence(raw)
    s3 = MemoryS3()
    s3.seed(key, raw)
    original_object = s3.objects[key]
    initial = load_training_statcast(bundle, s3, 'b')
    result = recover(bundle, s3, 'b', initial, reconcile_official=True,
                     fetch=lambda value: pytest.fail('do not repeat the raw download'),
                     fetch_official=lambda url: (official['data'], official['receipt']))
    assert result['recovered_dates'] == ['2026-09-01']
    assert result['provider_requests'] == result['official_provider_requests'] == 1
    assert result['statcast_provider_requests'] == 0
    assert raw == original and s3.objects[key] == original_object
    payload, _ = read_recovery(s3, 'b', raw['date'])
    assert payload['raw_statcast'] == original
    assert payload['rows'][1]['woba_denom'] == 1
    assert payload['rows'][1]['woba_value'] == 0
    assert payload['rows'][1]['estimated_woba_using_speedangle'] == '.5'
    assert len(payload['outcome_reconciliation']['derivations']) == 1
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []
    assert recover(bundle, s3, 'b', initial, reconcile_official=True)['provider_requests'] == 0


@pytest.mark.parametrize('defect', ['date', 'game', 'finality', 'batter', 'pitcher', 'event',
                                   'missing_pa', 'duplicate_pa', 'incomplete_pa', 'hash', 'endpoint'])
def test_mismatched_official_evidence_cannot_derive_any_observation(defect):
    _, raw, _ = raw_fixture()
    source = evidence(raw)
    data = source['data']
    play = data['liveData']['plays']['allPlays'][0]
    if defect == 'date': data['gameData']['datetime']['dateTime'] = '2026-08-31T18:00:00Z'
    elif defect == 'game': data['gameData']['game']['pk'] = 2
    elif defect == 'finality': data['gameData']['status']['abstractGameState'] = 'Live'
    elif defect == 'batter': play['matchup']['batter']['id'] = 999
    elif defect == 'pitcher': play['matchup']['pitcher']['id'] = 999
    elif defect == 'event': play['result']['eventType'] = 'single'
    elif defect == 'missing_pa': data['liveData']['plays']['allPlays'].pop()
    elif defect == 'duplicate_pa': data['liveData']['plays']['allPlays'].append(deepcopy(play))
    elif defect == 'incomplete_pa': play['about']['isComplete'] = False
    seal(source, raw)
    if defect == 'hash': source['receipt']['sha256'] = '0' * 64
    if defect == 'endpoint': source['receipt']['endpoint'] = endpoint('2')
    original = deepcopy(raw)
    with pytest.raises(ValueError):
        reconcile(raw, lambda pk, rows: source, raw_receipt(raw))
    assert raw == original


@pytest.mark.parametrize('defect', ['contact_estimate', 'derived_value', 'derivation',
                                   'missing_proof', 'completion_boundary', 'before_final',
                                   'raw_receipt', 'official_version'])
def test_reconciliation_is_reproduced_and_time_bound_on_every_read(defect):
    bundle, raw, _ = raw_fixture()
    source = evidence(raw)
    payload = reconcile(raw, lambda pk, rows: source, raw_receipt(raw))
    expected, invalid = official_pitch_counts(bundle['full'])
    completed = {'1': bundle['full'][0]['completedAtUtc']}
    assert validation_reason(payload, raw['date'], {1}, expected, invalid, completed) is None
    if defect == 'contact_estimate': payload['rows'][1]['estimated_woba_using_speedangle'] = '0'
    elif defect == 'derived_value': payload['rows'][1]['woba_denom'] = 0
    elif defect == 'derivation': payload['outcome_reconciliation']['derivations'].clear()
    elif defect == 'missing_proof': payload.pop('outcome_reconciliation')
    elif defect == 'completion_boundary': completed['1'] = '2026-09-01T19:00:00Z'
    elif defect == 'before_final':
        changed = payload['outcome_reconciliation']['official_sources']['1']
        changed['receipt']['retrievedAtUtc'] = '2026-09-01T19:00:00Z'
        seal(changed, raw)
    elif defect == 'raw_receipt': payload['outcome_reconciliation']['raw_receipt']['sha256'] = '0' * 64
    elif defect == 'official_version': payload['outcome_reconciliation']['official_sources']['1']['retained_receipt']['versionId'] = 'null'
    assert validation_reason(payload, raw['date'], {1}, expected, invalid, completed) == 'official_outcome_reconciliation_unverified'


def test_positive_event_weights_and_contact_estimates_remain_unknown():
    bundle, raw, _ = raw_fixture()
    raw['rows'][3]['woba_value'] = ''  # A single requires its observed seasonal weight.
    raw['rows'][1]['estimated_woba_using_speedangle'] = ''
    source = evidence(raw)
    payload = reconcile(raw, lambda pk, rows: source, raw_receipt(raw))
    assert payload['rows'][3]['woba_value'] == ''
    assert payload['rows'][1]['estimated_woba_using_speedangle'] == ''
    expected, invalid = official_pitch_counts(bundle['full'])
    assert validation_reason(payload, raw['date'], {1}, expected, invalid,
                             {'1': bundle['full'][0]['completedAtUtc']}) == 'incomplete_pa_outcome_fields'


def test_supplied_contradictory_denominator_is_never_overwritten():
    bundle, raw, _ = raw_fixture()
    raw['rows'][1]['woba_denom'] = 0
    payload = reconcile(raw, lambda pk, rows: evidence(raw), raw_receipt(raw))
    assert payload['rows'][1]['woba_denom'] == 0
    expected, invalid = official_pitch_counts(bundle['full'])
    assert validation_reason(payload, raw['date'], {1}, expected, invalid,
                             {'1': bundle['full'][0]['completedAtUtc']}) == 'incomplete_pa_outcome_fields'


def test_new_accounting_method_can_revisit_a_raw_only_failure(monkeypatch):
    authorize(monkeypatch)
    bundle, raw, key = raw_fixture()
    s3 = MemoryS3()
    s3.seed(key, raw)
    initial = load_training_statcast(bundle, s3, 'b')
    assert not recover(bundle, s3, 'b', initial, fetch=lambda day: raw)['recovered_dates']
    source = evidence(raw)
    result = recover(bundle, s3, 'b', initial, reconcile_official=True,
                     fetch_official=lambda url: (source['data'], source['receipt']))
    assert result['recovered_dates'] == ['2026-09-01']


def test_reconciliation_cannot_compensate_for_a_missing_physical_pitch():
    bundle, raw, _ = raw_fixture()
    raw['rows'].pop(0)  # No terminal PA is lost, but a thrown pitch is missing.
    payload = reconcile(raw, lambda pk, rows: evidence(raw), raw_receipt(raw))
    expected, invalid = official_pitch_counts(bundle['full'])
    assert validation_reason(payload, raw['date'], {1}, expected, invalid,
                             {'1': bundle['full'][0]['completedAtUtc']}) == 'pitch_or_pa_identity_count_mismatch'


def test_budget_interruption_remains_resumable_without_false_failure_state(monkeypatch):
    authorize(monkeypatch)
    bundle, raw, key = raw_fixture()
    s3 = MemoryS3()
    s3.seed(key, raw)
    initial = load_training_statcast(bundle, s3, 'b')
    times = iter((0, .5, 1.1))
    with monkeypatch.context() as clock:
        clock.setattr('ks1.statcast_recovery.time.monotonic', lambda: next(times))
        result = recover(bundle, s3, 'b', initial, reconcile_official=True, seconds=1,
                         fetch_official=lambda url: pytest.fail('budget expired'))
    assert not result['attempts'] and not result['recovered_dates']
    assert result['deferred_dates'] == [{'date': raw['date'], 'reason': 'recovery_budget'}]
    source = evidence(raw)
    resumed = recover(bundle, s3, 'b', initial, reconcile_official=True,
                      fetch_official=lambda url: (source['data'], source['receipt']))
    assert resumed['recovered_dates'] == [raw['date']]
