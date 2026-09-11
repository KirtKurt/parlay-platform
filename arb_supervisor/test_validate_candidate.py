import subprocess
from pathlib import Path

import pytest

import validate_candidate as policy


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "inqsi-arb" / "src").mkdir(parents=True)
    (repo / "inqsi-arb" / "tests").mkdir(parents=True)
    (repo / "inqsi-arb" / "src" / "safe.py").write_text("VALUE = 1\n")
    (repo / "inqsi-arb" / "tests" / "test_safe.py").write_text("def test_safe(): assert True\n")
    for args in (("init", "-q"), ("config", "user.name", "test"), ("config", "user.email", "test@example.invalid"),
                 ("add", "."), ("commit", "-qm", "base")):
        subprocess.run(["git", *args], cwd=repo, check=True)
    return repo, git(repo, "rev-parse", "HEAD")


def commit(repo: Path, message="candidate") -> str:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
    return git(repo, "rev-parse", "HEAD")


def test_safe_source_and_test_increment_is_eligible(tmp_path):
    repo, base = init_repo(tmp_path)
    (repo / "inqsi-arb" / "src" / "safe.py").write_text("VALUE = 2\n")
    (repo / "inqsi-arb" / "tests" / "test_safe.py").write_text("def test_safe(): assert 2 == 2\n")
    head = commit(repo)
    paths = policy.validate_candidate(repo, "agent/inqsi-arb-aec-123", head, base)
    assert paths == ["inqsi-arb/src/safe.py", "inqsi-arb/tests/test_safe.py"]


@pytest.mark.parametrize("path", [
    "inqsi-arb/ops/codex_cli_runner.sh",
    "inqsi-arb/template.yaml",
    ".github/workflows/inqsi-arb-deploy.yml",
    "arb_supervisor/promote.py",
    "README.md",
])
def test_high_risk_or_unrelated_path_is_never_auto_promoted(tmp_path, path):
    repo, base = init_repo(tmp_path)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("change\n")
    head = commit(repo)
    with pytest.raises(ValueError):
        policy.validate_candidate(repo, "agent/inqsi-arb-aec-123", head, base)


@pytest.mark.parametrize("source", [
    "place_bet()\n",
    "submit_wager(payload)\n",
    "subprocess.run(['curl'])\n",
    "os.system('echo no')\n",
    "# sam deploy\n",
])
def test_authority_expanding_source_primitive_fails(tmp_path, source):
    repo, base = init_repo(tmp_path)
    (repo / "inqsi-arb" / "src" / "safe.py").write_text(source)
    head = commit(repo)
    with pytest.raises(ValueError, match="DANGEROUS_ADDED_SOURCE"):
        policy.validate_candidate(repo, "agent/inqsi-arb-aec-123", head, base)


def test_candidate_must_be_direct_child_of_base(tmp_path):
    repo, base = init_repo(tmp_path)
    (repo / "inqsi-arb" / "src" / "safe.py").write_text("VALUE = 2\n")
    commit(repo, "first")
    (repo / "inqsi-arb" / "src" / "safe.py").write_text("VALUE = 3\n")
    head = commit(repo, "second")
    with pytest.raises(ValueError, match="CANDIDATE_PARENT_MISMATCH"):
        policy.validate_candidate(repo, "agent/inqsi-arb-aec-123", head, base)


def test_branch_and_sha_are_strict():
    with pytest.raises(ValueError):
        policy.validate_identity("feature/arb", "a" * 40, "b" * 40)
    with pytest.raises(ValueError):
        policy.validate_identity("agent/inqsi-arb-aec-1", "not-a-sha", "b" * 40)
