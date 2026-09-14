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
