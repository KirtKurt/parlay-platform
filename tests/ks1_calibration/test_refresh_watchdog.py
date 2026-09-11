from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from ks1 import refresh_watchdog as watch

NOW = datetime(2026, 9, 11, 5, 30, tzinfo=timezone.utc)


def run(**changes):
    row = {'head_branch': 'main', 'event': 'schedule', 'status': 'completed',
           'conclusion': 'success', 'path': '.github/workflows/'+watch.TARGET,
           'head_repository': {'full_name': watch.REPOSITORY},
           'created_at': '2026-09-11T04:00:00Z', 'run_started_at': '2026-09-11T04:00:00Z'}
    return dict(row, **changes)


@pytest.mark.parametrize('age,due', [(3599, False), (3600, True), (14400, True)])
def test_exact_hourly_threshold(age, due):
    row = run(run_started_at=(NOW-timedelta(seconds=age)).isoformat())
    assert watch.decide([row], NOW)['dispatch'] is due


@pytest.mark.parametrize('status', ['queued', 'in_progress', 'waiting', 'pending', 'requested'])
def test_active_production_suppresses_dispatch(status):
    assert watch.decide([run(status=status)], NOW)['reason'] == 'production_run_active'


@pytest.mark.parametrize('changes', [
    {'event': 'pull_request'}, {'head_branch': 'codex/test'},
    {'path': '.github/workflows/tennis-hourly-production-report.yml'},
    {'head_repository': {'full_name': 'untrusted/fork'}},
])
def test_pr_fork_and_other_engines_do_not_suppress_recovery(changes):
    assert watch.decide([run(status='in_progress', **changes)], NOW)['dispatch'] is True


def test_failed_recent_run_gets_cooldown_and_rerun_uses_attempt_start():
    row = run(conclusion='failure', run_started_at='2026-09-11T05:29:00Z')
    assert watch.decide([row], NOW)['dispatch'] is False
    row['run_started_at'] = '2026-09-11T04:29:00Z'
    assert watch.decide([row], NOW)['dispatch'] is True


def test_no_runs_and_future_dates():
    assert watch.decide([], NOW)['dispatch'] is True
    with pytest.raises(ValueError, match='future'):
        watch.decide([run(run_started_at='2026-09-12T05:00:00Z')], NOW)
    with pytest.raises(ValueError, match='timezone'):
        watch.timestamp('2026-09-11T05:00:00')


@pytest.fixture
def owner(monkeypatch):
    env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': watch.REPOSITORY,
           'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
           'GITHUB_WORKFLOW_REF': watch.REPOSITORY+'/.github/workflows/'+watch.OWNER+'@refs/heads/main'}
    for k, v in env.items():
        monkeypatch.setenv(k, v)


class Clock:
    elapsed = 0
    def monotonic(self):
        return self.elapsed
    def now(self):
        return NOW+timedelta(seconds=self.elapsed)
    def sleep(self, seconds):
        self.elapsed += seconds


class API:
    def __init__(self, enabled=True, fail=False):
        self.calls = []
        self.enabled = enabled
        self.fail = fail
    def active(self):
        return self.enabled
    def runs(self):
        return []  # simulate post-dispatch indexing delay
    def dispatch(self, target):
        self.calls.append(target)
        if self.fail and target == watch.TARGET:
            raise RuntimeError('ambiguous POST timeout')


def exercise(api):
    clock = Clock()
    watch.run_owner(api, clock=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)
    return clock


def test_bounded_owner_dispatches_once_then_hands_off(owner):
    api = API()
    clock = exercise(api)
    assert clock.elapsed == watch.OWNER_SECONDS == 3300
    assert api.calls == [watch.TARGET, watch.OWNER]


def test_disabled_workflow_stops_without_reenable_or_handoff(owner):
    api = API(enabled=False)
    exercise(api)
    assert api.calls == []


def test_ambiguous_post_does_not_duplicate_and_keeps_failure_visible(owner):
    api = API(fail=True)
    with pytest.raises(RuntimeError, match='unconfirmed'):
        exercise(api)
    assert api.calls == [watch.TARGET, watch.OWNER]


@pytest.mark.parametrize('key,value', [
    ('GITHUB_REF', 'refs/pull/999/merge'), ('GITHUB_REPOSITORY', 'fork/repo'),
    ('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_WORKFLOW_REF', 'another-workflow'),
    ('GITHUB_ACTIONS', 'false'),
])
def test_dispatch_guard_fails_closed(owner, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    api = API()
    with pytest.raises(ValueError, match='main KS1 watchdog'):
        exercise(api)
    assert api.calls == []


def test_client_only_dispatches_fixed_main_targets(monkeypatch):
    calls = []
    class Result:
        returncode = 0
        stdout = ''
    def command(args, **kw):
        calls.append((args, kw))
        return Result()
    monkeypatch.setattr(watch.subprocess, 'run', command)
    client = watch.GitHub()
    client.dispatch(watch.TARGET)
    assert calls[0][0][-2:] == ['--input', '-']
    assert calls[0][1]['input'] == '{"ref": "main"}'
    assert '/repos/'+watch.REPOSITORY+'/actions/workflows/'+watch.TARGET+'/dispatches' in calls[0][0]
    with pytest.raises(ValueError, match='outside KS1'):
        client.dispatch('deploy.yml')
    assert len(calls) == 1


def test_client_reads_the_exact_workflow_and_redacts_api_errors(monkeypatch):
    api = watch.GitHub()
    calls = []
    def request(path):
        calls.append(path)
        return {'workflow_runs': [run()]}
    monkeypatch.setattr(api, 'request', request)
    assert api.runs() == [run()]
    assert calls == ['workflows/'+watch.TARGET+'/runs?branch=main&per_page=100']
    class Error:
        returncode = 1
        stdout = ''
        stderr = 'test-credential-do-not-echo'
    monkeypatch.setattr(watch.subprocess, 'run', lambda *a, **kw: Error())
    with pytest.raises(RuntimeError) as failure:
        watch.GitHub().dispatch(watch.TARGET)
    assert Error.stderr not in str(failure.value)


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'invalid_json'])
def test_get_retries_are_bounded_and_recover(monkeypatch, failure):
    calls, sleeps = [], []
    def command(*args, **kwargs):
        calls.append(kwargs['timeout'])
        if len(calls) < 3:
            if failure == 'timeout':
                raise watch.subprocess.TimeoutExpired('gh', 10, output='private')
            return type('Result', (), {'returncode': int(failure == 'exit'), 'stdout': 'private'})()
        return type('Result', (), {'returncode': 0, 'stdout': '{"workflow_runs": []}'})()
    monkeypatch.setattr(watch.subprocess, 'run', command)
    monkeypatch.setattr(watch.time, 'sleep', sleeps.append)
    assert watch.GitHub().runs() == []
    assert calls == [watch.REQUEST_TIMEOUT]*3
    assert sleeps == list(watch.READ_BACKOFF)


def test_get_exhaustion_and_post_timeout_are_redacted_and_bounded(monkeypatch):
    calls, sleeps = [], []
    def command(*args, **kwargs):
        calls.append(args)
        raise watch.subprocess.TimeoutExpired('gh', 10, output='private-token')
    monkeypatch.setattr(watch.subprocess, 'run', command)
    monkeypatch.setattr(watch.time, 'sleep', sleeps.append)
    for method, expected in [('GET', 3), ('POST', 1)]:
        calls.clear(); sleeps.clear()
        with pytest.raises(RuntimeError) as error:
            watch.GitHub().request('workflows/'+watch.TARGET, method)
        assert len(calls) == expected and len(sleeps) == expected-1
        assert 'private-token' not in str(error.value) and error.value.__suppress_context__


@pytest.mark.parametrize('where', ['active', 'runs'])
def test_owner_survives_read_failure_and_rechecks_before_dispatch(owner, where):
    api = API()
    original = getattr(api, where)
    observed = []
    def intermittent():
        observed.append(len(api.calls))
        if len(observed) <= 2:
            raise RuntimeError('read unavailable')
        return original()
    setattr(api, where, intermittent)
    clock = exercise(api)
    assert observed[:3] == [0, 0, 0]
    assert api.calls == [watch.TARGET, watch.OWNER]
    assert clock.elapsed == watch.OWNER_SECONDS


def test_persistent_run_read_failure_never_dispatches_ingestion(owner):
    api = API()
    def unavailable():
        raise RuntimeError('read unavailable')
    api.runs = unavailable
    with pytest.raises(RuntimeError, match='reads remained unavailable'):
        exercise(api)
    # Handoff is allowed only because the independent workflow-enabled read
    # succeeded. The successor must obtain its own fresh production run list.
    assert api.calls == [watch.OWNER]


def test_persistent_enabled_read_failure_never_dispatches_or_hands_off(owner):
    api = API()
    def unavailable():
        raise RuntimeError('read unavailable')
    api.active = unavailable
    with pytest.raises(RuntimeError, match='read unavailable'):
        exercise(api)
    assert api.calls == []


def test_watchdog_is_singleton_main_only_and_has_no_aws_credentials():
    root = Path(__file__).resolve().parents[2]
    path = root/'.github/workflows'/watch.OWNER
    body = path.read_text()
    workflow = yaml.load(body, Loader=yaml.BaseLoader)
    assert workflow['on']['push']['branches'] == ['main']
    assert 'pull_request' not in workflow['on']
    assert workflow['concurrency'] == {'group': 'ks1-refresh-watchdog-main', 'cancel-in-progress': 'false'}
    job = workflow['jobs']['watch']
    assert 'refs/heads/main' in job['if']
    assert job['timeout-minutes'] == '60'
    assert workflow['permissions'] == {'contents': 'read'}
    assert job['permissions'] == {'contents': 'read', 'actions': 'write'}
    assert 'secrets.' not in body and 'aws-actions' not in body
    assert 'persist-credentials: false' in body
