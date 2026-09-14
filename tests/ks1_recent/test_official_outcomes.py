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
    terminal.update(events='field_out', woba_denom='', woba_value='0')
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
    assert payload['rows'][1]['woba_value'] == '0'
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


@pytest.mark.parametrize('base_state', ['absent', 'stale'])
def test_repairable_game_set_revision_prevents_redundant_download(monkeypatch, base_state):
    from ks1.inventory import RESEARCH
    authorize(monkeypatch)
    bundle, raw, key = raw_fixture()
    source = evidence(raw)
    s3 = MemoryS3()
    if base_state == 'stale':
        stale = deepcopy(raw)
        stale['rows'].pop()
        stale['rows'][0]['game_pk'] = '999'
        s3.seed(key, stale)
    revision_key = RESEARCH + f"sources/statcast-v2-revisions/{raw['date']}/{digest([1])}.json"
    s3.seed(revision_key, raw)
    initial = load_training_statcast(bundle, s3, 'b')
    result = recover(bundle, s3, 'b', initial, reconcile_official=True,
                     fetch=lambda value: pytest.fail('retained revision is repairable'),
                     fetch_official=lambda url: (source['data'], source['receipt']))
    assert result['recovered_dates'] == [raw['date']]
    assert result['statcast_provider_requests'] == 0
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []


@pytest.mark.parametrize('feed_time', ['2026-09-02T18:00:00Z', '2026-09-02T19:00:00Z'])
def test_suspended_game_requires_exact_retained_resume_time(monkeypatch, feed_time):
    authorize(monkeypatch)
    bundle, raw, key = raw_fixture()
    bundle['schedule'][0]['resumeDate'] = '2026-09-02T18:00:00Z'
    bundle['full'][0]['completedAtUtc'] = '2026-09-02T21:00:00Z'
    source = evidence(raw)
    source['data']['gameData']['datetime']['dateTime'] = feed_time
    # Most PAs occurred before suspension, one after the independently recorded resume.
    source['data']['liveData']['plays']['allPlays'][-1]['about']['endTime'] = '2026-09-02T20:00:00Z'
    seal(source, raw)
    s3 = MemoryS3()
    s3.seed(key, raw)
    initial = load_training_statcast(bundle, s3, 'b')
    result = recover(bundle, s3, 'b', initial, reconcile_official=True,
                     fetch_official=lambda url: (source['data'], source['receipt']))
    if feed_time != bundle['schedule'][0]['resumeDate']:
        assert result['recovered_dates'] == []
        assert 'game/date/finality mismatch' in result['attempts'][0]['reason']
        return
    assert result['recovered_dates'] == [raw['date']]
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []
    # A payload cannot authorize its own resume time on a later read.
    bundle['schedule'][0].pop('resumeDate')
    assert load_training_statcast(bundle, s3, 'b')['errors']


@pytest.mark.parametrize('missing_source', ['raw', 'official'])
def test_deleted_exact_evidence_version_invalidates_composite(monkeypatch, missing_source):
    from ks1.inventory import RESEARCH
    authorize(monkeypatch)
    bundle, raw, key = raw_fixture()
    source = evidence(raw)
    s3 = MemoryS3()
    s3.seed(key, raw)
    initial = load_training_statcast(bundle, s3, 'b')
    recovered = recover(bundle, s3, 'b', initial, reconcile_official=True,
                        fetch_official=lambda url: (source['data'], source['receipt']))
    assert recovered['recovered_dates'] == [raw['date']]
    payload, receipts = read_recovery(s3, 'b', raw['date'])
    proof = payload['outcome_reconciliation']
    pointers = [proof['raw_receipt'], proof['official_sources']['1']['retained_receipt']]
    assert len(receipts) == 4  # State, composite, raw source, official source.
    for pointer in pointers:
        assert any(r['key'] == RESEARCH + pointer['name']
                   and r['versionId'] == pointer['versionId']
                   and r['sha256'] == pointer['sha256'] for r in receipts)
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []
    assert all(any(r['key'] == receipt['key'] and r['versionId'] == receipt['versionId']
                   for r in bundle['source_receipts']) for receipt in receipts)
    pointer = pointers[missing_source == 'official']
    # Leave the latest object and embedded copy intact; the exact version is gone.
    del s3.versions[RESEARCH + pointer['name'], pointer['versionId']]
    from botocore.exceptions import ClientError
    with pytest.raises(ClientError):
        read_recovery(s3, 'b', raw['date'])
    assert load_training_statcast(bundle, s3, 'b')['errors']
    assert raw['date'] not in bundle['statcast_retained_dates']


def test_denominator_only_method_preserves_supplied_nonzero_event_weights():
    bundle, raw, _ = raw_fixture()
    raw['rows'][3].update(events='field_error', woba_value='0.9')
    payload = reconcile(raw, lambda pk, rows: evidence(raw), raw_receipt(raw))
    assert payload['rows'][3] == raw['rows'][3]
    assert all(set(change['fields']) == {'woba_denom'}
               for change in payload['outcome_reconciliation']['derivations'])
    verify_reconciliation(payload, {'1': bundle['full'][0]['completedAtUtc']})


@pytest.mark.parametrize('event', ['catcher_interf', 'intent_walk', 'sac_bunt', 'sac_bunt_double_play'])
def test_official_exclusion_keeps_raw_denominator_and_records_canonical_difference(event):
    bundle, raw, _ = raw_fixture()
    raw['rows'][1].update(events=event, woba_denom='1', woba_value='0.7')
    original = deepcopy(raw)
    source = evidence(raw)
    payload = reconcile(raw, lambda pk, rows: source, raw_receipt(raw))
    assert raw == original == payload['raw_statcast']
    assert payload['rows'][1]['woba_denom'] == 0
    assert payload['rows'][1]['woba_value'] == '0.7'
    change = payload['outcome_reconciliation']['derivations'][0]
    assert change['original_fields'] == {'woba_denom': '1'}
    assert change['fields'] == {'woba_denom': 0}
    verify_reconciliation(payload, {'1': bundle['full'][0]['completedAtUtc']})
    expected, invalid = official_pitch_counts(bundle['full'])
    assert validation_reason(payload, raw['date'], {1}, expected, invalid,
                             {'1': bundle['full'][0]['completedAtUtc']}) is None
    change['original_fields']['woba_denom'] = ''
    with pytest.raises(ValueError, match='cannot be reproduced'):
        verify_reconciliation(payload, {'1': bundle['full'][0]['completedAtUtc']})


def test_new_method_never_infers_a_missing_zero_weight():
    bundle, raw, _ = raw_fixture()
    raw['rows'][1]['woba_value'] = ''
    payload = reconcile(raw, lambda pk, rows: evidence(raw), raw_receipt(raw))
    assert payload['rows'][1]['woba_value'] == ''
    expected, invalid = official_pitch_counts(bundle['full'])
    assert validation_reason(payload, raw['date'], {1}, expected, invalid,
                             {'1': bundle['full'][0]['completedAtUtc']}) == 'incomplete_pa_outcome_fields'


def test_previously_retained_v1_derivations_remain_reproducible():
    from ks1.official_outcomes import LEGACY_METHOD, reconciled_rows
    bundle, raw, _ = raw_fixture()
    raw['rows'][1]['woba_value'] = ''
    sources = {'1': evidence(raw)}
    rows, changes = reconciled_rows(raw, sources, method=LEGACY_METHOD)
    payload = {'date': raw['date'], 'rows': rows, 'raw_statcast': raw,
               'outcome_reconciliation': {'method': LEGACY_METHOD,
                                         'official_sources': sources,
                                         'raw_receipt': raw_receipt(raw),
                                         'derivations': changes}}
    assert rows[1]['woba_value'] == 0
    verify_reconciliation(payload, {'1': bundle['full'][0]['completedAtUtc']})
