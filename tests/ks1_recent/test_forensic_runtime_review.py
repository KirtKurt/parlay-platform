"""Review regressions for deterministic runtime diagnostics."""
import io
import json

import pandas as pd

from ks1 import forensic_runtime as runtime
from ks1 import train
from ks1.development import TRIALS
from ks1.train import PARAMS


class ArtifactStore:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, Metadata):
        self.objects[Key] = Body
        return {'VersionId': 'v1'}

    def get_object(self, Bucket, Key, VersionId):
        return {'Body': io.BytesIO(self.objects[Key])}


def _progress(output):
    return json.loads((output / runtime.PROGRESS_FILE).read_text())


def test_runtime_progress_reuses_existing_artifact_exclusion(tmp_path, monkeypatch):
    assert runtime.PROGRESS_FILE == 'progress.json'
    monkeypatch.setattr(train, 'artifact_write_authorized', lambda: True)
    output = tmp_path / 'experiment'
    output.mkdir()
    (output / 'metrics.json').write_text('{"accepted":false}')
    (output / runtime.PROGRESS_FILE).write_text('{"at_utc":"wall-clock-only"}')
    store = ArtifactStore()
    train.save_artifact(store, 'bucket', output)
    assert (output / runtime.PROGRESS_FILE).exists()
    assert not any(key.endswith('/progress.json') for key in store.objects)


def test_forensic_trial_checkpoint_identifies_candidate_fold_trial_and_source(tmp_path):
    fit = pd.DataFrame({'game_id': ['fit-1', 'fit-2'], 'x': [1.0, 2.0]})
    validation = pd.DataFrame({'game_id': ['val-1'], 'x': [3.0]})
    observed = {}

    @runtime.trace_trial
    def trial(a, b, columns, params):
        observed.update(_progress(tmp_path))
        return {'ok': True}

    @runtime.trace_development_run
    def invoke(input_path, proof_path, output):
        return trial(fit, validation, ['x'], dict(PARAMS))

    assert invoke(None, None, tmp_path) == {'ok': True}
    assert observed['phase'] == 'forensic'
    assert observed['trial'] == 'baseline'
    assert observed['candidate'].startswith('features:')
    assert observed['fold'].startswith('fold:')
    assert observed['source'].endswith(':invoke:0') is False
    assert ':invoke:' in observed['source']
    assert observed['trials_started'] == 1 and observed['trials_completed'] == 0
    assert observed['qualification_evidence'] is False


def test_forensic_candidate_and_fold_ids_distinguish_repeated_shape_trials(tmp_path):
    fit = pd.DataFrame({'game_id': ['fit-1', 'fit-2'], 'x': [1.0, 2.0], 'y': [2.0, 3.0]})
    validation_a = pd.DataFrame({'game_id': ['val-a'], 'x': [3.0], 'y': [4.0]})
    validation_b = pd.DataFrame({'game_id': ['val-b'], 'x': [3.0], 'y': [4.0]})
    events = []

    @runtime.trace_trial
    def trial(a, b, columns, params):
        events.append(_progress(tmp_path))
        return {'ok': True}

    @runtime.trace_development_run
    def invoke(input_path, proof_path, output):
        shallow = {**PARAMS, **TRIALS['shallow']}
        trial(fit, validation_a, ['x'], shallow)
        trial(fit, validation_a, ['y'], shallow)
        trial(fit, validation_b, ['y'], shallow)
        return {'done': True}

    assert invoke(None, None, tmp_path) == {'done': True}
    assert [event['trial'] for event in events] == ['shallow', 'shallow', 'shallow']
    assert events[0]['candidate'] != events[1]['candidate']
    assert events[1]['candidate'] == events[2]['candidate']
    assert events[0]['fold'] == events[1]['fold']
    assert events[1]['fold'] != events[2]['fold']
    assert len({event['source'] for event in events}) == 3
