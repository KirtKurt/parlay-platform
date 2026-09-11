from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "inqsi-arb" / "ops" / "codex_cli_runner.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "inqsi-arb-engineering-controller.yml"


def test_runner_is_arb_scoped_and_draft_only():
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

    assert "git config --local --unset-all http.https://github.com/.extraheader" in text
    assert "-u GITHUB_TOKEN -u GH_TOKEN" in text
    assert "-u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN" in text
    assert "Do not push, open a pull request, alter remotes, or publish repository changes" in text
    assert "gh auth setup-git" not in text
    assert 'git config --local http.https://github.com/.extraheader "AUTHORIZATION: basic ${PUBLISH_BASIC}"' in text
    assert "x-access-token:%s" in text
    assert codex_pos < pytest_pos < sam_pos < auth_restore_pos < push_pos < cleanup_after_push_pos < pr_pos


def test_runner_cleans_temporary_publication_auth_on_success_or_failure():
    text = RUNNER.read_text(encoding="utf-8")
    assert "cleanup_publish_auth()" in text
    assert "trap cleanup_publish_auth EXIT" in text
    assert "unset PUBLISH_BASIC" in text
    assert "cleanup_publish_auth\ntrap - EXIT" in text
    assert "https://x-access-token" not in text
    assert "token@github.com" not in text


def test_runner_restores_host_path_and_normalizes_codex_local_commits():
    text = RUNNER.read_text(encoding="utf-8")
    assert 'HOST_PATH="$PATH"' in text
    assert 'PATH="$HOST_PATH"' in text
    assert 'export PATH="$HOST_PATH"' in text
    assert 'git diff --name-only "$BASE_SHA"...HEAD' in text
    assert 'git reset --mixed "$BASE_SHA"' in text
    assert "git ls-files --others --exclude-standard" in text


def test_controller_uses_existing_secret_and_codex_cli_runner():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.Inqsi_ARB_Autonomous_Coding_Agent" in text
    assert "npm install --global @openai/codex" in text
    assert "bash inqsi-arb/ops/codex_cli_runner.sh" in text
    assert "fetch-depth: 0" in text
