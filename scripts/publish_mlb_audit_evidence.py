"""Publish one immutable generated audit bundle atop current main without rebasing it.

Only the named report files are copied into disposable worktrees. A newer
published audit wins; unrelated concurrent repository changes are preserved.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile

FILES = (
    'runtime_reports/mlb_rolling_24h_audit_latest.json',
    'runtime_reports/mlb_ml_v3_audit_execution_latest.json',
    'runtime_reports/mlb_audit_freshness_latest.json',
    'runtime_reports/mlb_ml_v3_validation_latest.json',
    'runtime_reports/mlb_ml_installation_1_5_latest.json',
)
EXECUTION = FILES[1]


def created(content):
    value = datetime.fromisoformat(str(json.loads(content)['createdAtUtc']).replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('audit timestamp must be timezone-aware')
    return value.astimezone(timezone.utc)


def git(root, *args, check=True):
    return subprocess.run(['git', '-C', str(root), *args], text=True, capture_output=True, check=check)


def publish(root, *, attempts=3):
    root = Path(root).resolve()
    bundle = {path: (root/path).read_bytes() for path in FILES}
    observed = created(bundle[EXECUTION])
    freshness = json.loads(bundle[FILES[2]])
    if created(json.dumps({'createdAtUtc': freshness['auditCreatedAtUtc']})) != observed:
        raise ValueError('freshness proof belongs to another audit')
    for attempt in range(attempts):
        git(root, 'fetch', 'origin', 'main')
        with tempfile.TemporaryDirectory(prefix='mlb-audit-publish-') as directory:
            work = Path(directory)/'work'
            git(root, 'worktree', 'add', '--detach', str(work), 'origin/main')
            try:
                current = work/EXECUTION
                if current.exists() and created(current.read_bytes()) >= observed:
                    return {'published': False, 'reason': 'equal_or_newer_audit_already_published'}
                for path, content in bundle.items():
                    target = work/path;target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(content)
                git(work, 'add', '--', *FILES)
                git(work, '-c', 'user.name=github-actions[bot]', '-c',
                    'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                    'commit', '-m', 'Publish fresh MLB rolling audit proof')
                result = git(work, 'push', 'origin', 'HEAD:main', check=False)
                if result.returncode == 0:
                    return {'published': True, 'commit': git(work, 'rev-parse', 'HEAD').stdout.strip(), 'attempts': attempt+1}
                if not any(message in result.stderr for message in ('fetch first', 'non-fast-forward', 'stale info')):
                    raise RuntimeError('audit publication failed: ' + result.stderr[-1000:])
            finally:
                git(root, 'worktree', 'remove', '--force', str(work))
    raise RuntimeError('main advanced during every bounded audit publication attempt')


if __name__ == '__main__':
    print(json.dumps(publish(Path(__file__).resolve().parents[1])))
