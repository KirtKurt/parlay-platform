"""Admission failures retain input bindings, never a candidate or promotion."""
import hashlib
import io
import json
import sys

import pyarrow as pa
import pytest

from ks1 import retrain_recent


def test_main_retains_source_proof_before_failed_admission(tmp_path, monkeypatch):
    model = b'test incumbent'
    refs = {'lightgbm': {'key': 'model.txt', 'version_id': 'version',
                         'sha256': hashlib.sha256(model).hexdigest()}}
    (tmp_path/'model_refs.json').write_text(json.dumps(refs))
    monkeypatch.setattr(retrain_recent, '__file__', str(tmp_path/'retrain_recent.py'))
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    monkeypatch.setenv('GITHUB_REF', 'refs/pull/1/merge')
    receipt = {'bucket': 'bucket', 'key': 'retained', 'versionId': 'v1', 'sha256': 'a'*64}
    bundle = {'source_receipts': [receipt], 'official_history_source': {'complete_years': [2025, 2026]}}
    class ReadOnlyS3:
        def get_object(self, **kwargs):
            assert kwargs == {'Bucket': 'bucket', 'Key': 'model.txt', 'VersionId': 'version'}
            return {'Body': io.BytesIO(model)}
    monkeypatch.setattr(retrain_recent, 'aws_clients', lambda *args: (None, ReadOnlyS3(), 'bucket'))
    monkeypatch.setattr(retrain_recent, 'load_existing', lambda *args: bundle)
    monkeypatch.setattr(retrain_recent, 'load_training_statcast', lambda *args: {'verified_dates': []})
    report = {'source_receipts': [receipt], 'coverage': {}, 'optional_reads': []}
    monkeypatch.setattr(retrain_recent, 'build', lambda *args:
                        (pa.Table.from_pylist([{'game_id': '1'}]), report))
    monkeypatch.setattr(retrain_recent, 'PriorPitcherContext', lambda *args: None)
    def reject(*args, **kwargs):
        raise ValueError('insufficient training games with verified team context')
    monkeypatch.setattr(retrain_recent, 'evaluate', reject)
    monkeypatch.setattr(retrain_recent, 'save_artifact', lambda *args:
                        pytest.fail('admission failure must not publish candidate artifacts'))
    output = tmp_path/'output'
    monkeypatch.setattr(sys, 'argv', ['retrain_recent', '--output', str(output)])
    with pytest.raises(ValueError, match='insufficient training games'):
        retrain_recent.main()
    proof = json.loads((output/'input_proof.json').read_bytes())
    assert proof['source_receipts'] == [receipt]
    assert proof['incumbent_ref'] == refs['lightgbm']
    assert proof['input_table_sha256'] == hashlib.sha256((output/'input_table.parquet').read_bytes()).hexdigest()
    assert not (output/'model.txt').exists()
    assert not (output/'test_predictions.parquet').exists()
    assert not (output/'metrics.json').exists()
