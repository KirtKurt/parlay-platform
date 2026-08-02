from pathlib import Path

import verify_mlb_no_bbd_runtime as verifier


def test_active_no_bbd_contract_accepts_provider_neutral_files(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    files = {
        "template.yaml": "Parameters:\n  OddsApiKey:\n    Type: String\n",
        ".github/workflows/deploy.yml": "on:\n  workflow_dispatch:\n",
        ".github/workflows/mlb-v8-historical-context-backfill.yml": (
            "on:\n  schedule:\n    - cron: '47 * * * *'\n"
        ),
        "scripts/run_mlb_v8_historical_context_backfill_entrypoint.py": (
            "class OfficialContextClient: pass\n"
            "PROVIDER = \"official_mlb\"\n"
            "REPORT = {\"bbsApiUsed\": False, \"bbsCredentialRead\": False, "
            "\"productionAuthorityChanged\": False}\n"
        ),
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert verifier.verify_files() == []


def test_active_no_bbd_contract_rejects_secret_or_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    files = {
        "template.yaml": "Parameters:\n  BbsApiKey:\n    Type: String\n",
        ".github/workflows/deploy.yml": "on:\n  workflow_dispatch:\n",
        ".github/workflows/mlb-v8-historical-context-backfill.yml": (
            "on:\n  schedule:\n    - cron: '47 * * * *'\n"
        ),
        "scripts/run_mlb_v8_historical_context_backfill_entrypoint.py": (
            "class OfficialContextClient: pass\n"
            "PROVIDER = \"official_mlb\"\n"
            "REPORT = {\"bbsApiUsed\": False, \"bbsCredentialRead\": False, "
            "\"productionAuthorityChanged\": False}\n"
        ),
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    errors = verifier.verify_files()
    assert any("active_bbd_reference:template.yaml" in error for error in errors)
