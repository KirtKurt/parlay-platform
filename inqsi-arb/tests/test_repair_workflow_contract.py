import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github' / 'workflows' / 'inqsi-arb-repair.yml'
# Closed contract for the security-sensitive preamble: extra YAML keys, comments,
# and reordered steps cannot turn this into a substring-only test. This avoids
# adding YAML dependencies to the existing pytest/boto3-only ARB test runtime.
PREAMBLE = '''name: Inqsi ARB Bounded Repair
on:
  workflow_dispatch:
permissions:
  contents: read
concurrency:
  group: inqsi-arb-bounded-repair
  cancel-in-progress: false
jobs:
  repair:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.sha }}
      - name: Verify immutable dispatch revision
        shell: bash
        run: |
'''
NEXT_STEP = '      - uses: actions/setup-python@v5\n'


def guard_script(text):
    prefix, separator, remainder = text.partition(NEXT_STEP)
    assert separator, 'Python setup must follow checkout and mandatory verification'
    match = re.fullmatch(re.escape(PREAMBLE) + r'(?P<script>(?:          [^\n]*\n)+)', prefix)
    assert match, 'Checkout and unconditional guard must be the first two steps'
    assert len(re.findall(r'^      - uses: actions/checkout@', text, re.M)) == 1
    # Only evidence upload may run after a failed guard. Tests/build/deploy keep
    # the implicit success() condition and cannot ignore their own failures.
    for step in re.split(r'(?=^      - )', NEXT_STEP + remainder, flags=re.M):
        if not step or step.startswith('      - uses: actions/upload-artifact@'):
            continue
        assert not re.search(r'^        (?:if|continue-on-error):', step, re.M)
    return textwrap.dedent(match['script'])


def test_bounded_repair_pins_dispatch_revision_before_privileged_steps():
    guard_script(WORKFLOW.read_text())


@pytest.mark.parametrize('mutation', [
    'moving_main', 'commented_pin', 'guard_disabled', 'continue_on_error',
    'guard_after_deploy', 'second_checkout', 'deploy_always',
])
def test_workflow_contract_rejects_provenance_bypasses(mutation):
    text = WORKFLOW.read_text()
    start = text.index('      - name: Verify immutable dispatch revision\n')
    block = text[start:text.index(NEXT_STEP)]
    if mutation == 'moving_main':
        text = text.replace('ref: ${{ github.sha }}', 'ref: main')
    elif mutation == 'commented_pin':
        text = text.replace('ref: ${{ github.sha }}', '# ref: ${{ github.sha }}')
    elif mutation == 'guard_disabled':
        text = text.replace('        shell: bash\n', '        if: ${{ false }}\n        shell: bash\n', 1)
    elif mutation == 'continue_on_error':
        text = text.replace('        shell: bash\n', '        continue-on-error: true\n        shell: bash\n', 1)
    elif mutation == 'guard_after_deploy':
        text = text.replace(block, '').replace('      - name: Verify repair\n', block + '      - name: Verify repair\n')
    elif mutation == 'second_checkout':
        text += '      - uses: actions/checkout@v4\n'
    else:
        text = text.replace('      - name: Redeploy dispatched revision once\n',
                            '      - name: Redeploy dispatched revision once\n        if: always()\n')
    with pytest.raises(AssertionError):
        guard_script(text)


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo, text=True, stderr=subprocess.PIPE).strip()


@pytest.fixture
def revisions(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    for message in ('dispatched', 'main advanced'):
        git(repo, '-c', 'user.name=Repair Test', '-c', 'user.email=repair@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', message)
    dispatched = git(repo, 'rev-parse', 'HEAD^')
    advanced = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '--detach', dispatched)
    return repo, dispatched, advanced


def run_guard(repo, sha, ref='refs/heads/main'):
    env = dict(os.environ)
    for key, value in (('GITHUB_SHA', sha), ('GITHUB_REF', ref)):
        env.pop(key, None)
        if value is not None:
            env[key] = value
    script = guard_script(WORKFLOW.read_text())
    # Match Actions' explicit bash shell; the sentinel proves execution stopped.
    return subprocess.run(['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c',
                           script + "\nprintf 'AFTER_GUARD'\n"],
                          cwd=repo, env=env, text=True, capture_output=True)


def assert_stopped(result):
    assert result.returncode != 0
    assert 'AFTER_GUARD' not in result.stdout


def test_dispatched_sha_remains_valid_after_main_advances(revisions):
    repo, dispatched, advanced = revisions
    assert git(repo, 'rev-parse', 'main') == advanced
    assert git(repo, 'rev-parse', 'HEAD') == dispatched
    result = run_guard(repo, dispatched)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'AFTER_GUARD'


def test_moving_main_checkout_stops_before_downstream_work(revisions):
    repo, dispatched, advanced = revisions
    git(repo, 'checkout', '--detach', advanced)
    assert_stopped(run_guard(repo, dispatched))


@pytest.mark.parametrize('ref', ['refs/heads/feature', 'refs/tags/main', 'main', '', None])
def test_non_main_dispatch_stops_even_with_matching_sha(revisions, ref):
    repo, dispatched, _ = revisions
    assert_stopped(run_guard(repo, dispatched, ref))


@pytest.mark.parametrize('sha', ['0' * 40, 'bad-sha', '', None])
def test_wrong_or_missing_sha_stops(revisions, sha):
    repo, _, _ = revisions
    assert_stopped(run_guard(repo, sha))


def test_git_read_failure_stops(tmp_path):
    assert_stopped(run_guard(tmp_path, 'a' * 40))


def test_failed_git_read_cannot_pass_using_its_stdout(tmp_path, monkeypatch):
    # A failing command substitution must fail even if it emits the expected SHA.
    fake_git = tmp_path / 'git'
    fake_git.write_text('#!/bin/sh\nprintf "%s\\n" "$GITHUB_SHA"\nexit 1\n')
    fake_git.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    assert_stopped(run_guard(tmp_path, 'a' * 40))
