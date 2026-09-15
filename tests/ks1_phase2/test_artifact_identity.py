import io
import json

from ks1 import train


class ArtifactStore:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, Metadata):
        self.objects[Key] = Body
        return {'VersionId': 'v1'}

    def get_object(self, Bucket, Key, VersionId):
        return {'Body': io.BytesIO(self.objects[Key])}


def test_progress_clock_cannot_change_experiment_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(train, 'artifact_write_authorized', lambda: True)
    store = ArtifactStore()
    receipts = []
    for run, clock in ((1, '2026-09-14T18:00:00Z'), (2, '2026-09-14T19:00:00Z')):
        output = tmp_path/str(run)
        output.mkdir()
        (output/'model.txt').write_text('identical fitted model bytes')
        (output/'metrics.json').write_text('{"accepted":false}')
        (output/'progress.json').write_text(json.dumps({'at_utc': clock}))
        train.save_artifact(store, 'bucket', output)
        receipts.append(json.loads((output/'artifact.json').read_text()))
        assert (output/'progress.json').exists()
    assert receipts[0] == receipts[1]
    assert len(store.objects) == 2
    assert not any(key.endswith('/progress.json') for key in store.objects)


def test_deferred_run_reports_recovery_and_never_claims_a_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(train, 'artifact_write_authorized', lambda: True)
    source = {'name': 'sources/statcast-recovery-v1/2026-05-23/objects/proof.json',
              'versionId': 'retained-v1', 'sha256': 'proof-hash'}
    documents = {
        'metrics.json': {'accepted': False, 'qualification_run': False,
                         'reason': 'full_recipe_not_selected_on_development', 'prediction_writes': 0},
        'statcast_recovery_report.json': {'recovery_method': 'v8', 'recovered_dates': ['2026-05-23'],
            'prediction_writes': 0, 'attempts': [{'date': '2026-05-23', 'reason': None,
                                                'verified_artifact': source}]},
        'development_selection.json': {'selected': 'starter', 'final_holdout_used_for_selection': False,
            'trials': {'full': {'selected_trial': 'baseline', 'trials': {'baseline': {
                'features_used_in_splits': ['home_lineup_platoon_xwoba_7d_missing',
                                            'away_lineup_platoon_xwoba_7d']}}}}},
    }
    for name, document in documents.items(): (tmp_path/name).write_text(json.dumps(document))
    store = ArtifactStore()
    train.save_artifact(store, 'bucket', tmp_path)
    printed = json.loads(capsys.readouterr().out)
    assert printed['artifact'] is None and printed['model_receipts'] == []
    assert printed['readback_verified'] and not printed['registered_for_serving']
    assert printed['run_summary']['decision']['qualification_run'] is False
    assert printed['run_summary']['recovery']['attempts'][0]['verified_artifact'] == source
    assert printed['run_summary']['development']['matchup_values_used']['full'] == ['away_lineup_platoon_xwoba_7d']
    assert not any(key.endswith('/model.txt') for key in store.objects)
