from copy import deepcopy

import pytest

from ks1 import calibration_store
from ks1 import settled_loss_patterns as subject
from tests.ks1.test_settled_loss_patterns import evidence


def test_completed_nightly_recovers_exact_committed_ledger_when_capture_is_stale(tmp_path, monkeypatch):
    source, ledger = evidence()
    source['committed_ledger'] = dict(deepcopy(ledger), rows=[])
    report = {'status': 'completed', 'night_date': '2026-09-17', 'ledger_rows': 1}
    expected_key = calibration_store.PREFIX+'date=2026-09-17/graded_ledger.json'
    calls = []

    def read_json(s3, bucket, key, version=None):
        calls.append((bucket, key, version))
        assert key == expected_key and version is None
        return deepcopy(ledger), {'bucket': bucket, 'key': key, 'version_id': 'immutable-ledger-version',
                                  'sha256': 'd'*64, 'etag': 'etag'}

    monkeypatch.setattr(calibration_store, 'read_json', read_json)
    selected, identity = subject.select_ledger(report, source, tmp_path, s3=object(), bucket='test-bucket')
    assert selected == ledger
    assert identity['source'] == 'exact_committed_aws_ledger'
    assert identity['artifact']['key'] == expected_key
    assert calls == [('test-bucket', expected_key, None)]


def test_completed_nightly_stale_capture_fails_without_exact_aws_readback(tmp_path):
    source, _ = evidence()
    source['committed_ledger'] = dict(source['committed_ledger'], rows=[])
    report = {'status': 'completed', 'night_date': '2026-09-17', 'ledger_rows': 1}
    with pytest.raises(ValueError, match='nightly_ledger_count_mismatch'):
        subject.select_ledger(report, source, tmp_path)


def test_catchup_ledger_key_is_revision_scoped_and_immutable():
    key = subject.committed_ledger_key({'status': 'completed_catchup', 'night_date': '2026-09-17',
                                        'catchup_revision': 12})
    assert key == calibration_store.PREFIX+'date=2026-09-17/catchup=000012/graded_ledger.json'


def test_old_official_grade_remains_traceable_after_current_final_ages_out():
    source, ledger = evidence()
    del source['finals']['G1']
    result = subject.build(source, ledger, set())
    observation = result['observations'][0]
    assert observation['hit'] == 0
    assert observation['final_score'] == {'home': 1, 'away': 3}
    assert observation['current_final_crosscheck'] == 'persisted_official_grade_only_current_final_aged_out'
    assert observation['final_evidence'] == ledger['rows'][0]['final_evidence']


def test_aged_out_current_final_does_not_make_persisted_score_or_label_mutable():
    source, ledger = evidence()
    del source['finals']['G1']
    ledger['rows'][0]['home_score'] = ledger['rows'][0]['away_score']
    with pytest.raises(ValueError, match='invalid_persisted_final_tie'):
        subject.build(source, ledger, set())


def test_retained_current_final_is_still_crosschecked_against_durable_grade():
    source, ledger = evidence()
    result = subject.build(source, ledger, set())
    assert result['observations'][0]['current_final_crosscheck'] == 'verified_against_current_retained_final'
    source['finals']['G1']['home_score'] = 9
    with pytest.raises(ValueError, match='final_score_mismatch'):
        subject.build(source, ledger, set())
