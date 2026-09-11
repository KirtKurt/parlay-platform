import hashlib
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import release_identity as identity
spec = importlib.util.spec_from_file_location("arb_release_proof", ROOT / "ops" / "release_proof.py")
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "inqsi-arb" / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("VALUE = 1\n")
    return src


def write_manifest(source, **changes):
    digest, count = identity.source_fingerprint(source)
    data = {"schema_version": 1, "revision": "a" * 40, "python_source_sha256": digest,
            "python_source_files": count, "workflow_run_id": "123", "workflow_run_attempt": "1",
            "built_at": "2026-09-11T18:00:00+00:00"}
    data.update(changes)
    (source / identity.MANIFEST_NAME).write_text(json.dumps(data))
    return identity.read_identity(source)


def test_valid_source_identity(source):
    result = write_manifest(source, api_key="must-not-be-returned")
    assert result["status"] == "verified"
    assert set(result) == {"status", *identity.FIELDS}
    # Independent byte-layout oracle for one source file.
    path, data = b"app.py", b"VALUE = 1\n"
    expected = hashlib.sha256(len(path).to_bytes(8, "big") + path + len(data).to_bytes(8, "big") + data).hexdigest()
    assert result["python_source_sha256"] == expected


@pytest.mark.parametrize("change", [{"revision": "main"}, {"revision": "A" * 40}, {"python_source_sha256": "oops"},
                                   {"python_source_files": True}, {"python_source_files": 0}, {"schema_version": True},
                                   {"workflow_run_id": "0"}, {"workflow_run_attempt": "-1"},
                                   {"built_at": "2026-09-11"}, {"built_at": None}])
def test_invalid_manifest_never_verified(source, change):
    assert write_manifest(source, **change)["status"] == "unverified"


@pytest.mark.parametrize("contents", ["{", "[]", "null", '"string"', "x" * 16385])
def test_malformed_manifest(source, contents):
    (source / identity.MANIFEST_NAME).write_text(contents)
    assert identity.read_identity(source)["reason"] == "RELEASE_MANIFEST_INVALID"


def test_missing_manifest(source):
    assert identity.read_identity(source) == {"status": "unverified", "reason": "RELEASE_MANIFEST_MISSING"}


@pytest.mark.parametrize("mutation", ["edit", "add", "rename", "delete"])
def test_source_change_invalidates_identity(source, mutation):
    write_manifest(source)
    path = source / "app.py"
    if mutation == "edit": path.write_text("VALUE = 2\n")
    elif mutation == "add": (source / "other.py").write_text("VALUE = 1\n")
    elif mutation == "rename": path.rename(source / "other.py")
    else: path.unlink()
    assert identity.read_identity(source)["status"] == "unverified"


def test_cache_files_excluded(source):
    write_manifest(source)
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "ignored.py").write_text("ignored")
    (source / "app.pyc").write_bytes(b"cache")
    assert identity.read_identity(source)["status"] == "verified"


@pytest.mark.parametrize("kind", ["source", "directory", "manifest"])
def test_symlinks_are_not_verified(source, tmp_path, kind):
    write_manifest(source)
    if kind == "source": (source / "link.py").symlink_to(source / "app.py")
    elif kind == "directory": (source / "linked").symlink_to(tmp_path, target_is_directory=True)
    else:
        manifest = source / identity.MANIFEST_NAME
        contents = manifest.read_text()
        manifest.unlink()
        other = tmp_path / "manifest.json"
        other.write_text(contents)
        manifest.symlink_to(other)
    assert identity.read_identity(source)["status"] == "unverified"


@pytest.mark.parametrize("key", list(identity.FIELDS) + ["status"])
def test_live_mismatches_fail_closed(source, key):
    expected = write_manifest(source)
    live = {"ok": True, "places_bets": False, "release": {**expected, key: "wrong"}}
    with pytest.raises(ValueError): proof.assert_live_identity(live, expected)


@pytest.mark.parametrize("live", [None, {}, {"ok": True, "places_bets": False}, {"ok": False}, {"ok": True, "places_bets": True}])
def test_missing_live_proof_fails(source, live):
    with pytest.raises(ValueError): proof.assert_live_identity(live, write_manifest(source))


def test_matching_live_proof(source):
    expected = write_manifest(source)
    result = proof.assert_live_identity({"ok": True, "places_bets": False, "release": expected}, expected)
    assert result["ok"] is True


def test_local_unverified_manifest_cannot_verify_live():
    with pytest.raises(ValueError): proof.assert_live_identity({"ok": True}, {"status": "unverified"})


def test_health_wrapper_only_adds_release(monkeypatch, source):
    original = {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": '{"ok":true,"places_bets":false}'}
    fake_app = types.SimpleNamespace(lambda_handler=lambda event, context: original,
                                    _method=lambda event: event.get("httpMethod"), _path=lambda event: event.get("path"))
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setattr(identity, "runtime_identity", lambda: write_manifest(source))
    response = identity.lambda_handler({"httpMethod": "GET", "path": "/v1/arb/health"}, None)
    assert response["headers"] == original["headers"]
    assert json.loads(response["body"])["release"]["status"] == "verified"
    assert "release" not in json.loads(original["body"])
    assert identity.lambda_handler({"httpMethod": "GET", "path": "/v1/arb/scan"}, None) is original


def init_git(source):
    root = source.parents[1]
    for args in (("init", "-q"), ("config", "user.name", "test"), ("config", "user.email", "test@example.invalid"),
                 ("add", "."), ("commit", "-qm", "fixture")):
        subprocess.run(["git", *args], cwd=root, check=True)
    return root


def test_stamp_binds_actual_head_and_clean_sources(source):
    root = init_git(source)
    result = proof.stamp(source, "123", "1")
    assert result["revision"] == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    assert result["status"] == "verified"
    assert proof.stamp(source, "123", "2")["workflow_run_attempt"] == "2"


@pytest.mark.parametrize("mutation", ["dirty", "staged", "untracked", "ignored"])
def test_stamp_rejects_unbound_source(source, mutation):
    root = init_git(source)
    if mutation in ("dirty", "staged"):
        (source / "app.py").write_text("CHANGED=2")
        if mutation == "staged": subprocess.run(["git", "add", "."], cwd=root, check=True)
    else:
        if mutation == "ignored": (root / ".gitignore").write_text("ignored.py\n")
        (source / "ignored.py").write_text("UNTRACKED=1")
    with pytest.raises((ValueError, subprocess.CalledProcessError)): proof.stamp(source, "123", "1")


def test_stamp_rejects_tracked_manifest(source):
    write_manifest(source)
    init_git(source)
    with pytest.raises(ValueError): proof.stamp(source, "123", "1")


class FakeResponse:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size): return self.data[:size]


def test_http_retry_only_returns_matching_release(monkeypatch, source):
    expected = write_manifest(source)
    responses = iter([b'{"ok":true,"places_bets":false}', json.dumps(
        {"ok": True, "places_bets": False, "release": expected}).encode()])
    monkeypatch.setattr(proof.urllib.request, "urlopen", lambda *a, **k: FakeResponse(next(responses)))
    monkeypatch.setattr(proof.time, "sleep", lambda seconds: None)
    result = proof.verify_live("https://example.invalid/Prod", expected)
    assert result["ok"] is True and result["attempt"] == 2


@pytest.mark.parametrize("raw", [b"x" * 65537, b"not-json", b"{}"])
def test_invalid_http_responses_never_pass(monkeypatch, source, raw):
    expected = write_manifest(source)
    monkeypatch.setattr(proof.urllib.request, "urlopen", lambda *a, **k: FakeResponse(raw))
    with pytest.raises(ValueError): proof.verify_live("https://example.invalid", expected, attempts=1)


def test_failure_receipt_is_preserved_without_raw_response(monkeypatch, tmp_path):
    output = tmp_path / "proof.json"
    monkeypatch.setattr(sys, "argv", ["release_proof", "verify", "--api-url", "https://example.invalid", "--output", str(output)])
    monkeypatch.setattr(proof, "read_identity", lambda source: {"status": "unverified"})
    assert proof.main() == 1
    result = json.loads(output.read_text())
    assert result["ok"] is False and result["reason"] == "LOCAL_RELEASE_IDENTITY_UNVERIFIED"


def test_deploy_and_repair_stamp_before_build_and_verify_after_deploy():
    root = ROOT.parent
    for filename, expected_stamps in (("inqsi-arb-deploy.yml", 2), ("inqsi-arb-repair.yml", 1)):
        text = (root / ".github" / "workflows" / filename).read_text()
        assert text.count("python inqsi-arb/ops/release_proof.py stamp") == expected_stamps
        assert text.index("release_proof.py stamp") < text.index("sam build")
        assert text.index("sam deploy") < text.index("release_proof.py verify")
        assert "arb-release-proof.json" in text
        assert "contents: read" in text
        assert "pull-requests: write" not in text
    template = (ROOT / "template.yaml").read_text()
    assert "Handler: release_identity.lambda_handler" in template
