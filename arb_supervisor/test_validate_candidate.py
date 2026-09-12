import subprocess
from pathlib import Path

import pytest

from arb_supervisor import validate_candidate as policy

BRANCH = "agent/inqsi-arb-aec-123"


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def init_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "inqsi-arb").mkdir(parents=True)
    (repo / "inqsi-arb/ARB_STATUS.md").write_text("Status\n")
    (repo / "README.md").write_text("Unrelated file\n")
    for args in (("init", "-q"), ("config", "user.name", "test"), ("config", "user.email", "test@example.invalid"),
                 ("add", "."), ("commit", "-qm", "base")):
        subprocess.run(["git", *args], cwd=repo, check=True)
    return repo, git(repo, "rev-parse", "HEAD")


def commit(repo):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "candidate")
    return git(repo, "rev-parse", "HEAD")


def test_only_regular_status_document_increment_is_eligible(tmp_path):
    repo, base = init_repo(tmp_path)
    (repo / "inqsi-arb/ARB_STATUS.md").write_text("Status updated\n")
    (repo / "inqsi-arb/ARB_BACKLOG.md").write_text("Live proof remains pending.\n")
    assert policy.validate_candidate(repo, BRANCH, commit(repo), base) == [
        "inqsi-arb/ARB_BACKLOG.md", "inqsi-arb/ARB_STATUS.md"]


@pytest.mark.parametrize("path", [
    "inqsi-arb/src/provider.py", "inqsi-arb/src/safe.py", "inqsi-arb/src/requirements.txt",
    "inqsi-arb/src/pyproject.toml", "inqsi-arb/src/setup.py", "inqsi-arb/tests/conftest.py",
    "inqsi-arb/tests/test_safe.py", "inqsi-arb/ops/codex_cli_runner.sh", "inqsi-arb/template.yaml",
    ".github/workflows/inqsi-arb-deploy.yml", "arb_supervisor/promote.py", "README.md",
])
def test_executable_or_unrelated_paths_require_review(tmp_path, path):
    repo, base = init_repo(tmp_path)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("change\n")
    with pytest.raises(ValueError, match="PATH_REQUIRES_REVIEW"):
        policy.validate_candidate(repo, BRANCH, commit(repo), base)


def test_rename_cannot_hide_forbidden_source_path(tmp_path):
    repo, base = init_repo(tmp_path)
    git(repo, "mv", "README.md", "inqsi-arb/ARB_BACKLOG.md")
    head = commit(repo)
    assert "README.md" in policy.changed_files(repo, base, head)
    with pytest.raises(ValueError, match="PATH_REQUIRES_REVIEW:README.md"):
        policy.validate_candidate(repo, BRANCH, head, base)


def test_unusual_filenames_are_preserved_for_policy(tmp_path):
    repo, base = init_repo(tmp_path)
    path = 'inqsi-arb/ARB_BACKLOG.md\nREADME.md'
    (repo / path).write_text('bad\n')
    head = commit(repo)
    assert policy.changed_files(repo, base, head) == [path]
    with pytest.raises(ValueError, match="PATH_REQUIRES_REVIEW"):
        policy.validate_candidate(repo, BRANCH, head, base)


@pytest.mark.parametrize("kind", ["symlink", "executable", "delete", "binary", "nonutf8", "oversize"])
def test_document_cannot_carry_executable_or_oversize_payload(tmp_path, kind):
    repo, base = init_repo(tmp_path)
    target = repo / "inqsi-arb/ARB_STATUS.md"
    if kind == "symlink":
        target.unlink()
        target.symlink_to("../README.md")
    elif kind == "executable":
        target.chmod(0o755)
    elif kind == "delete":
        target.unlink()
    elif kind == "binary":
        target.write_bytes(b"body\x00payload")
    elif kind == "nonutf8":
        target.write_bytes(b"\xff")
    else:
        target.write_bytes(b"x" * (policy.MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError):
        policy.validate_candidate(repo, BRANCH, commit(repo), base)


def test_total_byte_budget_is_independent_of_line_count(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    monkeypatch.setattr(policy, "MAX_TOTAL_BYTES", 10)
    (repo / "inqsi-arb/ARB_STATUS.md").write_text("x" * 8)
    (repo / "inqsi-arb/ARB_BACKLOG.md").write_text("y" * 8)
    with pytest.raises(ValueError, match="CANDIDATE_BYTES_TOO_LARGE"):
        policy.validate_candidate(repo, BRANCH, commit(repo), base)


def test_candidate_must_have_exactly_one_parent_equal_to_base(tmp_path):
    repo, base = init_repo(tmp_path)
    (repo / "inqsi-arb/ARB_STATUS.md").write_text("first\n")
    first = commit(repo)
    (repo / "inqsi-arb/ARB_STATUS.md").write_text("second\n")
    second = commit(repo)
    with pytest.raises(ValueError, match="CANDIDATE_PARENT_MISMATCH"):
        policy.validate_candidate(repo, BRANCH, second, base)
    tree = git(repo, "rev-parse", second + '^{tree}')
    merged = git(repo, "commit-tree", tree, "-p", first, "-p", base, "-m", "merge")
    with pytest.raises(ValueError, match="CANDIDATE_PARENT_MISMATCH"):
        policy.validate_candidate(repo, BRANCH, merged, first)


def test_branch_and_sha_are_strict():
    with pytest.raises(ValueError):
        policy.validate_identity("feature/arb", "a" * 40, "b" * 40)
    with pytest.raises(ValueError):
        policy.validate_identity(BRANCH, "not-a-sha", "b" * 40)
