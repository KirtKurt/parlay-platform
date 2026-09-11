"""Offline regressions: new research implementation must not reuse old evidence."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'mlb_research'))
import mlb_research_provenance_v1 as provenance


@pytest.fixture
def source_tree(tmp_path):
    for name in provenance.SOURCE_FILES:
        (tmp_path / name).write_bytes(('test fixture for ' + name + '\n').encode())
    return tmp_path


def registry_key(rows, protocol):
    # Exactly the current worker's identity input, without S3 or real labels.
    encoded = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    digest = lambda value: hashlib.sha256(encoded(value)).hexdigest()
    return digest({'rowsHash': digest(rows), 'protocol': protocol})


def test_manifest_repeats_without_timestamps_paths_or_environment_secrets(source_tree, monkeypatch):
    first = provenance.implementation_manifest(source_tree)
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'TEST_NOT_A_REAL_SECRET')
    monkeypatch.setenv('GITHUB_SHA', 'unrelated-report-commit')
    monkeypatch.setenv('INQSI_DEPLOY_GIT_SHA', 'different-deployment')
    assert first == provenance.implementation_manifest(source_tree)
    text = json.dumps(first)
    assert 'TEST_NOT_A_REAL_SECRET' not in text
    assert str(source_tree) not in text
    assert set(first) == {'version', 'files', 'runtime', 'sha256'}
    assert len(first['sha256']) == 64
    assert set(first['files']) == set(provenance.SOURCE_FILES)


@pytest.mark.parametrize('name', provenance.SOURCE_FILES)
def test_each_research_component_change_invalidates_identical_data_and_settings(source_tree, name):
    rows = [{'officialGamePk': 'fixture-1', 'features': {'signal': 1}}]
    first = provenance.implementation_manifest(source_tree)
    (source_tree / name).write_bytes((source_tree / name).read_bytes() + b'# implementation changed\n')
    second = provenance.implementation_manifest(source_tree)
    assert first['sha256'] != second['sha256']
    assert first['files'][name] != second['files'][name]
    before = {'minimumGames': 600, 'implementation': first}
    after = {'minimumGames': 600, 'implementation': second}
    assert registry_key(rows, before) != registry_key(rows, after)


def test_unrelated_file_does_not_create_a_new_experiment(source_tree):
    first = provenance.implementation_manifest(source_tree)
    (source_tree / 'tennis_report.json').write_text('{"update": true}')
    assert first == provenance.implementation_manifest(source_tree)


@pytest.mark.parametrize('mode', ['missing', 'empty', 'symlink'])
def test_incomplete_or_indirect_source_is_not_accepted(source_tree, mode):
    path = source_tree / provenance.SOURCE_FILES[0]
    path.unlink()
    if mode == 'empty':
        path.write_bytes(b'')
    elif mode == 'symlink':
        path.symlink_to(source_tree / provenance.SOURCE_FILES[1])
    with pytest.raises(ValueError, match='research provenance requires'):
        provenance.implementation_manifest(source_tree)


def test_manifest_changes_when_numeric_runtime_changes(source_tree, monkeypatch):
    import numpy
    before = provenance.implementation_manifest(source_tree)
    monkeypatch.setattr(numpy, '__version__', 'fixture-different-version')
    after = provenance.implementation_manifest(source_tree)
    assert before['files'] == after['files']
    assert before['sha256'] != after['sha256']


def test_manifest_digest_covers_every_provenance_field(source_tree):
    manifest = provenance.implementation_manifest(source_tree)
    body = {k: v for k, v in manifest.items() if k != 'sha256'}
    encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    assert hashlib.sha256(encoded).hexdigest() == manifest['sha256']


def test_historical_experiment_record_is_not_modified_when_identity_changes(source_tree):
    rows = [{'officialGamePk': 'fixture-1'}]
    old_protocol = {'minimumGames': 600}
    old_key = registry_key(rows, old_protocol)
    old_record = {'status': 'HISTORICAL_SCREEN_FAILED', 'protocol': old_protocol}
    registry = {old_key: copy.deepcopy(old_record)}
    new_key = registry_key(rows, {**old_protocol, 'implementation': provenance.implementation_manifest(source_tree)})
    assert new_key != old_key and new_key not in registry
    assert registry[old_key] == old_record


def test_real_protocol_binds_source_without_weakening_any_promotion_threshold():
    # Uses the real modules in repository CI, never a mock model implementation.
    import mlb_research_models_v1 as models
    assert models.PROTOCOL['implementation'] == provenance.implementation_manifest()
    assert models.PROTOCOL['minimumGames'] == 600
    assert models.PROTOCOL['minimumSlates'] == 40
    assert models.PROTOCOL['freshTestMinimum'] == 100
    assert models.PROTOCOL['newRowsAfterFailedTest'] == 100
    assert models.PROTOCOL['minimumFeatureCoverage'] == .8
    assert models.PROTOCOL['automaticPromotionEnabled'] is False
    assert models.PROTOCOL['wholeSlatePartitions'] is True
    assert models.PROTOCOL['configurations'] == [['linear', .1], ['linear', 1.], ['trees', 15], ['trees', 30], ['poisson', .1], ['adaptive_linear', 1.]]
    assert models.PROTOCOL['featureDiscovery']['minimumWalkForwardBrierGain'] == .001
    assert models.PROTOCOL['featureDiscovery']['maximumAdaptiveFits'] == 4
    assert models.PROTOCOL['featureDiscovery']['arbitraryCodeExecution'] is False


def test_runtime_keeps_active_prospective_test_ahead_of_experiment_lookup():
    # The repair must NOT reopen a sealed future test or restart an active one.
    tree = ast.parse((ROOT / 'mlb_research/mlb_research_runtime_v1.py').read_text())
    train = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'train')
    frozen_gate = next(node for node in train.body if isinstance(node, ast.If) and ast.unparse(node.test) == 'frozen')
    assert any(isinstance(node, ast.Return) for node in ast.walk(frozen_gate))
    experiment_gets = [node for node in ast.walk(train) if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Attribute) and node.func.attr == 'get'
                       and node.args and 'experiments/' in ast.unparse(node.args[0])]
    assert len(experiment_gets) == 1 and frozen_gate.end_lineno < experiment_gets[0].lineno
    assert "'protocol': models.PROTOCOL" in ast.unparse(train)


class RegistryStore:
    """Conditional-create registry substitute; no cloud credentials or writes."""
    def __init__(self, data, frozen=None):
        self.data = data
        self.frozen = frozen
        self.records = {'dataset.json': {'rowsHash': 'fixture-data-hash', 'artifact': {'kind': 'dataset'}}}
        if frozen is not None:
            self.records['active-frozen.json'] = {'artifact': {'kind': 'frozen'}}

    def get(self, name):
        return copy.deepcopy(self.records.get(name))

    def load(self, pointer):
        return copy.deepcopy(self.data if pointer['kind'] == 'dataset' else self.frozen)

    def once(self, name, value):
        return copy.deepcopy(self.records.setdefault(name, copy.deepcopy(value)))

    def latest(self, name, value):
        self.records[name] = copy.deepcopy(value)


def test_actual_worker_reuses_identical_code_but_researches_a_changed_implementation(source_tree, monkeypatch):
    import mlb_research_models_v1 as models
    import mlb_research_runtime_v1 as runtime

    data = {'rows': [{'officialGamePk': 'fixture-1'}], 'originalRows': 0}
    store = RegistryStore(data)
    calls = []
    def research(rows):
        calls.append(copy.deepcopy(rows))
        return {'status': 'HISTORICAL_SCREEN_FAILED', 'implementation': copy.deepcopy(models.PROTOCOL['implementation'])}
    monkeypatch.setattr(models, 'research', research)
    protocol = copy.deepcopy(models.PROTOCOL)
    protocol['implementation'] = provenance.implementation_manifest(source_tree)
    monkeypatch.setattr(models, 'PROTOCOL', protocol)
    first = runtime.train(store)
    assert len(calls) == 1
    before = copy.deepcopy(store.records['experiments/' + first['experimentId'] + '.json'])
    assert runtime.train(store)['experimentId'] == first['experimentId']
    assert len(calls) == 1
    changed = source_tree / 'mlb_research_models_v1.py'
    changed.write_bytes(changed.read_bytes() + b'# different research implementation\n')
    protocol['implementation'] = provenance.implementation_manifest(source_tree)
    second = runtime.train(store)
    assert len(calls) == 2 and first['experimentId'] != second['experimentId']
    assert store.records['experiments/' + first['experimentId'] + '.json'] == before
    assert store.get('active-frozen.json') is None
    assert first['productionAuthorityChanged'] is second['productionAuthorityChanged'] is False


@pytest.mark.parametrize('test', [
    {'sealed': False, 'passed': False, 'status': 'ACCUMULATING_FRESH_TEST'},
    {'sealed': True, 'passed': True, 'status': 'FRESH_TEST_PASSED_AWAITING_REVIEW'},
    {'sealed': True, 'passed': False, 'status': 'FRESH_TEST_SEALED_FAILED'},
])
def test_new_source_identity_does_not_reopen_or_override_frozen_test(test, monkeypatch):
    import mlb_research_models_v1 as models
    import mlb_research_runtime_v1 as runtime

    frozen = {'id': 'existing-frozen-model', 'developmentRows': 600}
    store = RegistryStore({'rows': [{'officialGamePk': 'fixture-1'}], 'originalRows': 0}, frozen=frozen)
    pointer = store.get('active-frozen.json')
    monkeypatch.setattr(runtime, 'evaluate_fresh', lambda _store, _frozen: copy.deepcopy(test))
    def forbidden(_rows):
        raise AssertionError('an implementation change cannot bypass the prospective test gate')
    monkeypatch.setattr(models, 'research', forbidden)
    result = runtime.train(store)
    assert result['productionAuthorityChanged'] is False
    assert store.get('active-frozen.json') == pointer
    assert not any(name.startswith('experiments/') for name in store.records)
    if test['sealed'] and not test['passed']:
        assert result['status'] == 'SEALED_RESEARCH_TEST_FAILED_WAITING_FOR_NEW_DATA'
    else:
        assert result['status'] == test['status']


def test_new_mlb_module_is_automatically_included_in_implementation_identity(source_tree):
    before = provenance.implementation_manifest(source_tree)
    (source_tree / 'mlb_research_new_signal.py').write_text('NEW_SIGNAL = 1\n')
    after = provenance.implementation_manifest(source_tree)
    assert before['sha256'] != after['sha256']
    assert 'mlb_research_new_signal.py' in after['files']
