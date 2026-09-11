"""Bounded KS1-only GitHub dispatch owner; no AWS, model or provider access.

The cron is a recovery seed, not a delivery guarantee. A singleton owner checks
for an overdue main production run and hands off to a new bounded owner before
its runner timeout. GitHub dispatch/runners can still be delayed or unavailable.
"""
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import time

REPOSITORY = 'KirtKurt/parlay-platform'
TARGET = 'mlb-research-ingestion.yml'
OWNER = 'ks1-refresh-watchdog.yml'
EVENTS = {'push', 'schedule', 'workflow_dispatch'}
INTERVAL = timedelta(hours=1)
OWNER_SECONDS = 55 * 60
POLL_SECONDS = 60


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('GitHub run timestamp must have a timezone')
    return parsed.astimezone(timezone.utc)


def decide(runs, now):
    production = [r for r in runs if r.get('head_branch') == 'main'
                  and r.get('event') in EVENTS
                  and r.get('path') == '.github/workflows/'+TARGET
                  and r.get('head_repository', {}).get('full_name') == REPOSITORY]
    if any(r.get('status') != 'completed' for r in production):
        return {'dispatch': False, 'reason': 'production_run_active'}
    if not production:
        return {'dispatch': True, 'reason': 'no_recent_production_run'}
    # run_started_at accounts for re-runs; created_at alone can be days old.
    started = max(timestamp(r.get('run_started_at') or r['created_at']) for r in production)
    age = now-started
    if age.total_seconds() < 0:
        raise ValueError('future production run timestamp')
    return {'dispatch': age >= INTERVAL,
            'reason': 'hourly_refresh_due' if age >= INTERVAL else 'recent_production_run',
            'age_seconds': int(age.total_seconds())}


class GitHub:
    def request(self, path, method='GET', payload=None):
        command = ['gh', 'api', '--method', method,
                   '/repos/'+REPOSITORY+'/actions/'+path]
        if payload is not None:
            command += ['--input', '-']
        result = subprocess.run(command, input=json.dumps(payload) if payload else None,
                                capture_output=True, text=True, timeout=40)
        if result.returncode:
            # Do not echo stderr/response bodies or auth details into artifacts.
            raise RuntimeError('GitHub KS1 '+method+' request failed')
        return json.loads(result.stdout) if result.stdout.strip() else None

    def active(self):
        return all(self.request('workflows/'+name)['state'] == 'active'
                   for name in (TARGET, OWNER))

    def runs(self):
        # Filter to the exact workflow, not the repository-wide activity stream
        # where other engines can push KS1 beyond the first page in one hour.
        result = self.request('workflows/'+TARGET+'/runs?branch=main&per_page=100')
        return result['workflow_runs']

    def dispatch(self, name):
        if name not in (TARGET, OWNER):
            raise ValueError('dispatch target is outside KS1')
        self.request('workflows/'+name+'/dispatches', 'POST', {'ref': 'main'})


def require_owner():
    expected = REPOSITORY+'/.github/workflows/'+OWNER+'@refs/heads/main'
    if not (os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
            and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_EVENT_NAME') in EVENTS
            and os.environ.get('GITHUB_WORKFLOW_REF') == expected):
        raise ValueError('dispatch requires the main KS1 watchdog workflow')


def run_owner(api, *, clock=lambda: datetime.now(timezone.utc),
              monotonic=time.monotonic, sleep=time.sleep):
    require_owner()
    deadline = monotonic()+OWNER_SECONDS
    last_dispatch = None
    failures = 0
    while monotonic() < deadline:
        if not api.active():
            print(json.dumps({'status': 'disabled_workflow_no_handoff'}), flush=True)
            return
        now = clock()
        decision = decide(api.runs(), now)
        # Cover GitHub's short post-dispatch indexing delay within this owner.
        if last_dispatch and now-last_dispatch < INTERVAL:
            decision = {'dispatch': False, 'reason': 'local_dispatch_cooldown'}
        print(json.dumps(decision), flush=True)
        if decision['dispatch']:
            try:
                api.dispatch(TARGET)
            except RuntimeError:
                # A POST timeout is ambiguous. Do not retry immediately: it may
                # already have queued a run. The next read/owner rechecks it.
                failures += 1
                print(json.dumps({'status': 'dispatch_unconfirmed'}), flush=True)
            last_dispatch = now
        sleep(min(POLL_SECONDS, max(0, deadline-monotonic())))
    if api.active():
        api.dispatch(OWNER)
        print(json.dumps({'status': 'next_bounded_owner_requested'}), flush=True)
    if failures:
        raise RuntimeError('KS1 dispatch was unconfirmed; inspect the production runs')


if __name__ == '__main__':
    run_owner(GitHub())
