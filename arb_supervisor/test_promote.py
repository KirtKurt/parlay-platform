import pytest

from arb_supervisor import promote


def good_pr():
    return {
        "state": "open",
        "draft": True,
        "merged": False,
        "user": {"login": "github-actions[bot]"},
        "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
        "head": {"ref": "agent/inqsi-arb-aec-123", "sha": "a" * 40, "repo": {"full_name": "owner/repo"}},
    }


def test_verify_pr_accepts_exact_actions_draft():
    promote.verify_pr(good_pr(), branch="agent/inqsi-arb-aec-123", head_sha="a" * 40, require_draft=True, repository="owner/repo")


@pytest.mark.parametrize("mutation", ["author", "branch", "sha", "base", "closed", "merged", "not-draft", "head-repo", "base-repo"])
def test_verify_pr_fails_closed_on_identity_or_state_drift(mutation):
    pr = good_pr()
    if mutation == "author": pr["user"]["login"] = "human"
    elif mutation == "branch": pr["head"]["ref"] = "other"
    elif mutation == "sha": pr["head"]["sha"] = "b" * 40
    elif mutation == "base": pr["base"]["ref"] = "dev"
    elif mutation == "closed": pr["state"] = "closed"
    elif mutation == "merged": pr["merged"] = True
    elif mutation == "head-repo": pr["head"]["repo"]["full_name"] = "attacker/repo"
    elif mutation == "base-repo": pr["base"]["repo"]["full_name"] = "attacker/repo"
    else: pr["draft"] = False
    with pytest.raises(promote.PromotionError):
        promote.verify_pr(pr, branch="agent/inqsi-arb-aec-123", head_sha="a" * 40, require_draft=True, repository="owner/repo")


def test_ready_pr_can_be_rechecked_without_draft_requirement():
    pr = good_pr()
    pr["draft"] = False
    promote.verify_pr(pr, branch="agent/inqsi-arb-aec-123", head_sha="a" * 40, require_draft=False, repository="owner/repo")



BINDING = dict(workflow='arb-supervisor-validate.yml', title='ARB supervisor candidate nonce',
               trusted_sha='b' * 40, dispatched_at='2026-09-11T22:00:00Z')


def good_run():
    return dict(id=123, display_title=BINDING['title'], head_sha=BINDING['trusted_sha'],
                created_at=BINDING['dispatched_at'], head_branch='main', event='workflow_dispatch',
                path='.github/workflows/arb-supervisor-validate.yml', status='completed', conclusion='success')


@pytest.mark.parametrize('field,value', [
    ('display_title', 'old title'), ('head_sha', 'a' * 40), ('head_branch', 'attacker'),
    ('event', 'pull_request'), ('path', '.github/workflows/other.yml'),
    ('created_at', '2026-09-11T21:59:59Z')])
def test_run_binding_rejects_wrong_or_older_receipt(field, value):
    row = good_run()
    row[field] = value
    assert not promote.run_matches(row, **BINDING)


def test_run_binding_accepts_fresh_exact_dispatch():
    assert promote.run_matches(good_run(), **BINDING)


def test_wait_rejects_wrong_run_and_allows_healthy_job_over_three_minutes(monkeypatch):
    now = [0]
    monkeypatch.setattr(promote.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(promote.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    def api(repo, endpoint):
        run = good_run()
        if endpoint.startswith('actions/workflows'):
            wrong = {**run, 'id': 999, 'head_branch': 'attacker'}
            return {'workflow_runs': [wrong, run]}
        assert endpoint == 'actions/runs/123'
        if now[0] < 240:
            run.update(status='in_progress', conclusion=None)
        return run
    monkeypatch.setattr(promote, 'api', api)
    result = promote.wait_for_run(repo='owner/repo', workflow=BINDING['workflow'],
        expected_title=BINDING['title'], trusted_sha=BINDING['trusted_sha'],
        dispatched_at=BINDING['dispatched_at'], timeout=promote.VALIDATION_TIMEOUT)
    assert result['id'] == 123 and now[0] >= 240


def test_wait_fails_when_pinned_receipt_changes_identity(monkeypatch):
    def api(repo, endpoint):
        if endpoint.startswith('actions/workflows'):
            return {'workflow_runs': [good_run()]}
        return {**good_run(), 'head_sha': 'c' * 40}
    monkeypatch.setattr(promote, 'api', api)
    with pytest.raises(promote.PromotionError, match='DISPATCH_RECEIPT_IDENTITY_CHANGED'):
        promote.wait_for_run(repo='owner/repo', workflow=BINDING['workflow'],
            expected_title=BINDING['title'], trusted_sha=BINDING['trusted_sha'],
            dispatched_at=BINDING['dispatched_at'], timeout=30)


def test_dispatch_includes_unique_nonce_and_pins_trusted_main(monkeypatch):
    commands, waits = [], []
    monkeypatch.setattr(promote, 'command', lambda args, **kwargs: commands.append(args))
    monkeypatch.setattr(promote, 'api', lambda *a: {'commit': {'sha': 'b' * 40}})
    def wait(**kwargs):
        waits.append(kwargs)
        return {'id': 123}
    monkeypatch.setattr(promote, 'wait_for_run', wait)
    for _ in range(2):
        promote.dispatch_bound('owner/repo', 'arb-supervisor-validate.yml', 'ARB supervisor head', {}, 100)
    assert waits[0]['expected_title'] != waits[1]['expected_title']
    for cmd, receipt in zip(commands, waits):
        assert '--repo' in cmd and 'owner/repo' in cmd
        nonce = next(arg.split('=', 1)[1] for arg in cmd if arg.startswith('dispatch_nonce='))
        assert receipt['expected_title'].endswith(nonce)
        assert receipt['trusted_sha'] == 'b' * 40


def ci_run(conclusion='success'):
    return dict(id=234, workflow_id=345, head_sha='a' * 40, event='pull_request',
                pull_requests=[{'number': 831}], path='.github/workflows/inqsi-arb-deploy.yml',
                status='completed', conclusion=conclusion)


@pytest.mark.parametrize('conclusion', ['action_required', 'failure', 'cancelled', 'timed_out'])
def test_pr_ci_blocks_before_dispatch_for_unsatisfied_github_gate(monkeypatch, conclusion):
    monkeypatch.setattr(promote, 'api', lambda *a: {'workflow_runs': [ci_run(conclusion)], 'total_count': 1})
    with pytest.raises(promote.PromotionError, match='PR_ACTIONS_APPROVAL_REQUIRED' if conclusion == 'action_required' else 'PR_CI_NOT_GREEN'):
        promote.require_pr_ci('owner/repo', 831, 'a' * 40)


def test_pr_ci_does_not_accept_another_pr_or_missing_build(monkeypatch):
    row = ci_run()
    row['pull_requests'] = [{'number': 999}]
    monkeypatch.setattr(promote, 'api', lambda *a: {'workflow_runs': [row], 'total_count': 1})
    with pytest.raises(promote.PromotionError, match='PR_CI_EVIDENCE_MISSING'):
        promote.require_pr_ci('owner/repo', 831, 'a' * 40)


def test_pr_ci_requires_successful_actual_test_build_job(monkeypatch):
    def api(repo, endpoint):
        if endpoint.startswith('actions/runs?'):
            return {'workflow_runs': [ci_run()], 'total_count': 1}
        return {'jobs': [{'name': 'test-build', 'status': 'completed', 'conclusion': 'skipped'}], 'total_count': 1}
    monkeypatch.setattr(promote, 'api', api)
    with pytest.raises(promote.PromotionError, match='REQUIRED_JOB_NOT_GREEN'):
        promote.require_pr_ci('owner/repo', 831, 'a' * 40)


def test_merge_parent_drift_stops_deployment(monkeypatch):
    monkeypatch.setattr(promote, 'api', lambda *a: {'parents': [{'sha': 'c' * 40}]})
    with pytest.raises(promote.PromotionError, match='MERGED_BASE_CHANGED_DEPLOYMENT_BLOCKED'):
        promote.verify_merge_parent('owner/repo', 'd' * 40, 'b' * 40, 'a' * 40)


def test_merge_verification_checks_landed_tree(monkeypatch):
    def api(repo, endpoint):
        if endpoint.startswith('git/commits'):
            return {'parents': [{'sha': 'b' * 40}]}
        sha = 'e' * 40 if endpoint.endswith('a' * 40) else 'f' * 40
        return {'tree': [{'path': 'inqsi-arb', 'sha': sha}, {'path': 'arb_supervisor', 'sha': '1' * 40}]}
    monkeypatch.setattr(promote, 'api', api)
    with pytest.raises(promote.PromotionError, match='MERGED_TREE_MISMATCH'):
        promote.verify_merge_parent('owner/repo', 'd' * 40, 'b' * 40, 'a' * 40)


def prepare_main(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'offline-fixture')
    monkeypatch.setattr(promote.sys, 'argv', ['promote', '--repository', 'owner/repo',
        '--pr-number', '831', '--branch', 'agent/inqsi-arb-aec-123',
        '--head-sha', 'a' * 40, '--base-sha', 'b' * 40, '--auto-deploy'])
    monkeypatch.setattr(promote, 'validate_candidate', lambda *a: ['inqsi-arb/ARB_STATUS.md'])
    monkeypatch.setattr(promote, 'pr_info', lambda *a: good_pr())
    monkeypatch.setattr(promote, 'ensure_no_relevant_main_advance', lambda *a: 'b' * 40)


def test_main_never_dispatches_or_merges_while_pr_requires_approval(monkeypatch):
    prepare_main(monkeypatch)
    monkeypatch.setattr(promote, 'api', lambda *a: {'workflow_runs': [ci_run('action_required')], 'total_count': 1})
    calls = []
    for name in ['dispatch_validation', 'mark_ready', 'merge_exact', 'dispatch_deploy']:
        monkeypatch.setattr(promote, name, lambda *a, _name=name: calls.append(_name))
    with pytest.raises(promote.PromotionError, match='PR_ACTIONS_APPROVAL_REQUIRED'):
        promote.main()
    assert calls == []


def test_main_preserves_merge_race_failure_without_deploying(monkeypatch):
    prepare_main(monkeypatch)
    monkeypatch.setattr(promote, 'require_pr_ci', lambda *a: None)
    monkeypatch.setattr(promote, 'dispatch_validation', lambda *a: {'id': 123})
    ready = good_pr()
    ready['draft'] = False
    responses = iter([good_pr(), good_pr(), ready])
    monkeypatch.setattr(promote, 'pr_info', lambda *a: next(responses))
    monkeypatch.setattr(promote, 'mark_ready', lambda *a: None)
    monkeypatch.setattr(promote, 'merge_exact', lambda *a: 'd' * 40)
    monkeypatch.setattr(promote, 'api', lambda *a: {'parents': [{'sha': 'c' * 40}]})
    calls = []
    monkeypatch.setattr(promote, 'dispatch_deploy', lambda *a: calls.append(a))
    with pytest.raises(promote.PromotionError, match='MERGED_BASE_CHANGED_DEPLOYMENT_BLOCKED:' + 'd' * 40):
        promote.main()
    assert not calls


def test_normal_pr_ci_queue_waits_without_substituting_another_workflow(monkeypatch):
    now = [0]
    calls = []
    monkeypatch.setattr(promote.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(promote.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    def check(*args):
        calls.append(now[0])
        if len(calls) < 3:
            raise promote.PRCIWaiting('PR_CI_PENDING:123')
    monkeypatch.setattr(promote, 'require_pr_ci', check)
    promote.wait_for_pr_ci('owner/repo', 831, 'a' * 40)
    assert len(calls) == 3


def test_actions_approval_stops_without_polling_or_dispatch(monkeypatch):
    def check(*args):
        raise promote.PromotionError('PR_ACTIONS_APPROVAL_REQUIRED:123')
    monkeypatch.setattr(promote, 'require_pr_ci', check)
    monkeypatch.setattr(promote.time, 'sleep', lambda *_: pytest.fail('Approval is not a queue delay'))
    with pytest.raises(promote.PromotionError, match='PR_ACTIONS_APPROVAL_REQUIRED'):
        promote.wait_for_pr_ci('owner/repo', 831, 'a' * 40)
