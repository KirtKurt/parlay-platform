from copy import deepcopy

import pytest

from ks1.inventory import RESEARCH
from ks1.recent_statcast import restore_recent_history
from ks1.statcast_history import load_training_statcast
from ks1.statcast_recovery import PREFIX, recovery_pointer_key
from ks1.official_outcomes import digest, reconcile, schedule_times
from mlb_research.mlb_research_store_v1 import Store
from tests.ks1_recent.test_official_outcomes import unfinished_fixture
from tests.ks1_recent.test_recovery import MemoryS3


def retained_fixture(monkeypatch):
    bundle, raw, key, source = unfinished_fixture('')
    s3 = MemoryS3(); s3.seed(key, raw)
    # Build retained source fixtures without importing the training runtime;
    # Phase 1 intentionally does not install LightGBM.
    store = Store('b', s3)
    raw_pointer = store.put(PREFIX + raw['date'] + '/raw/' + digest(raw) + '.json', raw)
    source['retained_receipt'] = store.put(source['retained_receipt']['name'],
                                          {key: source[key] for key in ('data', 'receipt')})
    payload = reconcile(raw, lambda pk, rows: source, raw_pointer, schedule_times(bundle['schedule']))
    pointer = store.put(PREFIX + raw['date'] + '/objects/' + digest(payload) + '.json', payload)
    s3.seed(recovery_pointer_key(raw['date']), {'verified_artifact': pointer})
    prior = {'games': bundle['full'], 'schedule': bundle['schedule'], 'priorYear': 2025,
             'priorYearCoverageComplete': True, 'currentYearCoverageComplete': True}
    history = {'games': bundle['full'], 'schedule': bundle['schedule'],
               'statcast': deepcopy(raw['rows']), 'source_receipts': [],
               'statcast_retained_dates': [], 'statcast_verified_games': [],
               'statcast_physical_dates': [], 'statcast_physical_games': [],
               'statcast_coverage_complete': False,
               'statcast_observed_at': '2026-09-01T21:00:00Z'}
    receipt = {'versionId': 'prior-v1', 'sha256': 'prior-sha',
               'stored_at': '2026-09-01T21:00:00Z'}
    monkeypatch.setenv('GITHUB_REF', 'refs/pull/904/merge')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    return history, prior, receipt, s3, raw


def test_live_capture_uses_same_reconciliation_without_source_writes(monkeypatch):
    history, prior, receipt, s3, raw = retained_fixture(monkeypatch)
    writes = list(s3.writes); reads = []; original = s3.get_object
    s3.get_object = lambda **kw: (reads.append(kw) or original(**kw))
    report = restore_recent_history(history, prior, receipt, s3, 'b', '2026-09-02')
    assert len(report['requested_dates']) == report['expected_dates'] == 30
    assert report['provider_requests'] == report['source_writes'] == 0
    assert s3.writes == writes
    assert history['statcast'][-1]['events'] == 'caught_stealing_2b'
    assert history['statcast'][-1]['woba_denom'] == 0
    assert '2026-09-01' in history['statcast_physical_dates']
    assert '2026-09-01' in history['statcast_retained_dates']
    assert history['statcast_verified_games'] == ['1']
    assert history['statcast_coverage_complete'] is False
    assert any('/raw/' in r['key'] for r in history['source_receipts'])
    assert any('official-pa-accounting-v1/' in r['key'] for r in history['source_receipts'])
    assert all(r.get('versionId') for r in history['source_receipts'])
    assert not any('2026-09-02' in r['Key'] for r in reads)


def test_deleted_exact_raw_version_cannot_qualify_live_pitch_windows(monkeypatch):
    history, prior, receipt, s3, raw = retained_fixture(monkeypatch)
    for key, version in list(s3.versions):
        if '/raw/' in key:
            del s3.versions[key, version]
    report = restore_recent_history(history, prior, receipt, s3, 'b', '2026-09-02')
    assert report['errors']
    assert '2026-09-01' not in history['statcast_physical_dates']
    assert '2026-09-01' not in history['statcast_retained_dates']
    assert history['statcast_physical_games'] == []


def test_live_restore_never_reads_outside_its_calendar_window(monkeypatch):
    history, prior, receipt, s3, raw = retained_fixture(monkeypatch)
    old = deepcopy(prior['games'][0])
    old.update(officialGamePk=2, startAtUtc='2026-07-01T18:00:00Z',
               completedAtUtc='2026-07-01T21:00:00Z')
    prior['games'].append(old)
    prior['schedule'].append({**prior['schedule'][0], 'gamePk': 2, 'gameDate': old['startAtUtc']})
    reads = []; original = s3.get_object
    s3.get_object = lambda **kw: (reads.append(kw) or original(**kw))
    restore_recent_history(history, prior, receipt, s3, 'b', '2026-09-02')
    assert not any('2026-07-01' in r['Key'] for r in reads)


@pytest.mark.parametrize('defect', ['years', 'version'])
def test_missing_official_proof_preserves_existing_capture_without_new_claims(monkeypatch, defect):
    history, prior, receipt, s3, raw = retained_fixture(monkeypatch)
    if defect == 'years':
        prior['priorYearCoverageComplete'] = prior['currentYearCoverageComplete'] = False
    else:
        receipt['versionId'] = 'null'
    before = deepcopy(history); writes = list(s3.writes)
    report = restore_recent_history(history, prior, receipt, s3, 'b', '2026-09-02')
    assert report['status'] == 'complete_versioned_official_history_unavailable'
    assert history == before and s3.writes == writes


def test_existing_proof_outside_live_window_is_preserved(monkeypatch):
    history, prior, receipt, s3, raw = retained_fixture(monkeypatch)
    old = {**raw['rows'][0], 'game_pk': '2', 'game_date': '2026-07-01'}
    history['statcast'].append(old)
    history['statcast_physical_dates'] = ['2026-07-01']
    history['statcast_retained_dates'] = ['2026-07-01']
    history['statcast_physical_games'] = ['2']
    history['statcast_verified_games'] = ['2']
    source_receipts = history['source_receipts']
    restore_recent_history(history, prior, receipt, s3, 'b', '2026-09-02')
    assert old in history['statcast']
    assert '2026-07-01' in history['statcast_physical_dates']
    assert '2026-07-01' in history['statcast_retained_dates']
    assert history['statcast_verified_games'] == ['1', '2']
    assert history['source_receipts'] is source_receipts
    assert source_receipts


def test_requested_date_scope_cannot_expand_beyond_live_bound():
    from datetime import date, timedelta
    from tests.ks1.test_statcast_history import fixture
    bundle, raw, key = fixture()
    s3 = MemoryS3()
    dates = [(date(2026, 9, 1) - timedelta(days=i)).isoformat() for i in range(31)]
    with pytest.raises(ValueError, match='at most 30 dates'):
        load_training_statcast(bundle, s3, 'b', requested_dates=dates)
    assert not s3.writes


def test_restored_evidence_is_not_backdated_to_old_compact_observation(monkeypatch):
    from ks1.features import utc
    history, prior, receipt, s3, raw = retained_fixture(monkeypatch)
    before = utc(history['statcast_observed_at'])
    restore_recent_history(history, prior, receipt, s3, 'b', '2026-09-02')
    assert utc(history['statcast_observed_at']) > before
    assert utc(history['statcast_observed_at']) >= max(
        utc(r['stored_at']) for r in history['source_receipts'])


@pytest.mark.parametrize('timestamp', [None, 'invalid'])
def test_missing_retained_timestamp_cannot_claim_pregame_availability(monkeypatch, timestamp):
    history, prior, receipt, s3, raw = retained_fixture(monkeypatch)
    def load_with_timestamp(*args, **kwargs):
        report = load_training_statcast(*args, **kwargs)
        args[0]['source_receipts'][-1]['stored_at'] = timestamp
        return report
    monkeypatch.setattr('ks1.recent_statcast.load_training_statcast', load_with_timestamp)
    before = deepcopy(history); receipts = history['source_receipts']
    report = restore_recent_history(history, prior, receipt, s3, 'b', '2026-09-02')
    assert report['status'] == 'restored_observation_time_unavailable'
    assert report['restored_evidence_applied'] is False
    assert history == before
    assert history['source_receipts'] is receipts


@pytest.mark.parametrize('timestamp', [None, 'invalid'])
def test_unknown_official_time_rejects_even_receiptless_empty_date_coverage(
        monkeypatch, timestamp):
    history, prior, receipt, s3, raw = retained_fixture(monkeypatch)
    receipt['stored_at'] = timestamp
    # The target window contains empty dates that can change coverage without
    # adding a daily pitch receipt; their official source time must still bind.
    before = deepcopy(history); receipts = history['source_receipts']
    report = restore_recent_history(history, prior, receipt, s3, 'b', '2026-09-02')
    assert report['status'] == 'restored_observation_time_unavailable'
    assert report['restored_evidence_applied'] is False
    assert history == before
    assert history['source_receipts'] is receipts
