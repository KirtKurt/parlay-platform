"""Regression: unhealthy provider catalog still leaves a sanitized observation artifact."""
import json
import textwrap
import urllib.request
from pathlib import Path

import pytest


def test_observation_failure_writes_sanitized_artifact(monkeypatch, tmp_path):
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "inqsi-arb-observation-proof.yml"
    ).read_text(encoding="utf-8")
    embedded = workflow.split("          python - <<'PY'\n", 1)[1]
    script = textwrap.dedent(embedded.split("\n          PY", 1)[0])

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    requested = []

    def fake_urlopen(request, timeout=60):
        requested.append(request.full_url)
        if request.full_url.endswith("/v1/arb/health"):
            return Response({
                "ok": True,
                "places_bets": False,
                "audit_persistence": True,
                "automatic_market_discovery": True,
            })
        if request.full_url.endswith("/v1/arb/catalog"):
            return Response({
                "ok": False,
                "sports": [],
                "n_sports": 0,
                "provider": {
                    "ok": False,
                    "status": 401,
                    "error": "PROVIDER_HTTP_401",
                    "secret": "MUST_NOT_APPEAR_IN_ARTIFACT",
                },
            })
        raise AssertionError("Unhealthy catalog must not proceed to scans")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("API_URL", "https://example.invalid/Prod")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(AssertionError, match="PROVIDER_SPORTS_CATALOG_UNHEALTHY"):
        exec(compile(script, "observation-proof-embedded.py", "exec"), {"__name__": "__main__"})

    artifact = tmp_path / "arb-observation-proof.json"
    assert artifact.is_file()
    proof = json.loads(artifact.read_text(encoding="utf-8"))
    assert proof["stage"] == "catalog"
    assert proof["ok"] is False
    assert proof["n_sports"] == 0
    assert proof["provider_status"] == 401
    assert proof["provider_error_code"] == "PROVIDER_HTTP_401"
    assert "MUST_NOT_APPEAR_IN_ARTIFACT" not in artifact.read_text(encoding="utf-8")
    assert len(requested) == 2
