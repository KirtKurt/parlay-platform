from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "inqsi-arb" / "ops" / "codex_cli_runner.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "inqsi-arb-engineering-controller.yml"
SUPERVISOR = ROOT / "arb_supervisor" / "promote.py"
SUPERVISOR_WORKFLOW = ROOT / ".github" / "workflows" / "arb-supervisor-validate.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "inqsi-arb-deploy.yml"


def test_runner_is_arb_scoped_and_codex_never_directly_merges_or_deploys():
    text = RUNNER.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "inqsi-arb/*|.github/workflows/inqsi-arb-*" in text
    assert "gh pr create" in text
    assert "--draft" in text
    assert "gh pr merge" not in text
    assert "sam deploy" not in text
    assert "do not place wagers" in lowered
    assert "do not merge or deploy" in lowered
    assert "python -m pytest -q inqsi-arb/tests" in text


def test_runner_denies_codex_repo_and_aws_credentials_until_after_validation():
    text = RUNNER.read_text(encoding="utf-8")
    codex_pos = text.index("codex --ask-for-approval")
    pytest_pos = text.index("python -m pytest -q inqsi-arb/tests")
    sam_pos = text.index("sam build --no-cached")
    auth_restore_pos = text.index('PUBLISH_BASIC="$(printf')
    push_pos = text.index('git push --set-upstream origin "$BRANCH"')
    cleanup_after_push_pos = text.index("cleanup_publish_auth\ntrap - EXIT", push_pos)
    pr_pos = text.index("gh pr create")
    promote_pos = text.index("python -m arb_supervisor.promote")

    assert "git config --local --unset-all http.https://github.com/.extraheader" in text
    assert "-u GITHUB_TOKEN -u GH_TOKEN" in text
    assert "-u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN" in text
    assert "Do not push, open a pull request, alter remotes, or publish repository changes" in text
    assert "gh auth setup-git" not in text
    assert 'git config --local http.https://github.com/.extraheader "AUTHORIZATION: basic ${PUBLISH_BASIC}"' in text
    assert "x-access-token:%s" in text
    assert codex_pos < pytest_pos < sam_pos < auth_restore_pos < push_pos < cleanup_after_push_pos < pr_pos < promote_pos


def test_runner_cleans_temporary_publication_auth_on_success_or_failure():
    text = RUNNER.read_text(encoding="utf-8")
    assert "cleanup_publish_auth()" in text
    assert "trap cleanup_publish_auth EXIT" in text
    assert "unset PUBLISH_BASIC" in text
    assert "cleanup_publish_auth\ntrap - EXIT" in text
    assert "https://x-access-token" not in text
    assert "token@github.com" not in text


def test_runner_restores_host_path_normalizes_commits_and_defers_high_risk_auto_merge():
    text = RUNNER.read_text(encoding="utf-8")
    assert 'HOST_PATH="$PATH"' in text
    assert 'PATH="$HOST_PATH"' in text
    assert 'export PATH="$HOST_PATH"' in text
    assert 'git diff --no-renames --name-only "$BASE_SHA"...HEAD' in text
    assert 'git reset --mixed "$BASE_SHA"' in text
    assert "git ls-files --others --exclude-standard" in text
    assert "python -m arb_supervisor.validate_candidate" in text
    assert "candidate is not eligible for unattended promotion; leaving draft PR open" in text


def test_controller_uses_existing_secret_and_enables_trusted_autonomy():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.Inqsi_ARB_Autonomous_Coding_Agent" in text
    assert "npm install --global @openai/codex" in text
    assert "bash inqsi-arb/ops/codex_cli_runner.sh" in text
    assert "fetch-depth: 0" in text
    assert "ARB_AEC_AUTO_PROMOTE" in text and "'true'" in text
    assert "ARB_AEC_AUTO_DEPLOY" in text
    assert "python -m pytest -q arb_supervisor/test_validate_candidate.py arb_supervisor/test_promote.py" in text


def test_supervisor_validation_is_outside_codex_scope_and_has_no_deploy_credentials():
    runner = RUNNER.read_text(encoding="utf-8")
    validation = SUPERVISOR_WORKFLOW.read_text(encoding="utf-8")
    assert "arb_supervisor/" not in runner.split("STRICT WRITE SCOPE:", 1)[1].split("Do not modify", 1)[0]
    assert "contents: read" in validation
    assert "candidate_sha" in validation and "candidate_branch" in validation and "base_sha" in validation
    assert "python -m arb_supervisor.validate_candidate" in validation
    assert "python -m pytest -q inqsi-arb/tests" in validation
    assert "sam build --no-cached" in validation
    assert "AWS_ACCESS_KEY_ID" in validation and "test -z" in validation
    assert "configure-aws-credentials" not in validation


def test_supervisor_promotes_only_after_trusted_validation_and_dispatches_deployment():
    text = SUPERVISOR.read_text(encoding="utf-8")
    validation_pos = text.index("dispatch_validation(")
    ready_pos = text.index("mark_ready(", validation_pos)
    merge_pos = text.index("merge_exact(", ready_pos)
    deploy_pos = text.index("dispatch_deploy(", merge_pos)
    assert validation_pos < ready_pos < merge_pos < deploy_pos
    assert "github-actions[bot]" in text
    assert "RELEVANT_MAIN_ADVANCED" in text
    assert "sha={head_sha}" in text
    assert "merge_method=squash" in text


def test_deploy_workflow_allows_only_main_dispatch_and_binds_merge_ancestry():
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    assert "arb_merge_sha" in text
    assert "git merge-base --is-ancestor" in text
    assert "test \"$GITHUB_REF\" = 'refs/heads/main'" in text
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "sam deploy" in text
    assert "release_proof.py verify" in text
