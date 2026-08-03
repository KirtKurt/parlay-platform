from pathlib import Path

import remove_mlb_bbd_active_runtime as migration
import verify_mlb_no_bbd_runtime as verifier


ROOT = Path(__file__).resolve().parents[2]


def _write_required_provider_neutral_files(tmp_path: Path) -> None:
    files = {
        "template.yaml": "Parameters:\n  OddsApiKey:\n    Type: String\n",
        ".github/workflows/deploy.yml": "on:\n  workflow_dispatch:\n",
        ".github/workflows/mlb-v8-historical-context-backfill.yml": (
            "on:\n  schedule:\n    - cron: '47 * * * *'\n"
        ),
        "scripts/stabilize_mlb_deploy_source.py": "PROVIDER_NEUTRAL = True\n",
        "scripts/verify_mlb_deploy_identity.py": "PROVIDER_NEUTRAL = True\n",
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


def test_active_no_bbd_contract_accepts_provider_neutral_files(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_provider_neutral_files(tmp_path)

    assert verifier.verify_files() == []


def test_active_no_bbd_contract_rejects_secret_or_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_provider_neutral_files(tmp_path)
    (tmp_path / "template.yaml").write_text(
        "Parameters:\n  BbsApiKey:\n    Type: String\n", encoding="utf-8"
    )

    errors = verifier.verify_files()
    assert any("active_bbd_reference:template.yaml" in error for error in errors)


def test_active_no_bbd_contract_rejects_stale_deploy_stabilizer(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_provider_neutral_files(tmp_path)
    forbidden = verifier.FORBIDDEN[0]
    (tmp_path / "scripts/stabilize_mlb_deploy_source.py").write_text(
        f"REQUIRED = [\"{forbidden}\"]\n", encoding="utf-8"
    )

    errors = verifier.verify_files()
    assert any(
        "active_bbd_reference:scripts/stabilize_mlb_deploy_source.py" in error
        for error in errors
    )


def test_migration_workflow_name_is_accepted_but_contents_are_still_scanned(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_provider_neutral_files(tmp_path)
    migration_workflow = (
        tmp_path / ".github/workflows/mlb-remove-bbd-active-runtime-once.yml"
    )
    migration_workflow.write_text(
        'name: Retired provider migration\n"on":\n  workflow_dispatch:\n',
        encoding="utf-8",
    )

    assert verifier.verify_files() == []

    forbidden = verifier.FORBIDDEN[0]
    migration_workflow.write_text(
        'name: Retired provider migration\n"on":\n  workflow_dispatch:\n'
        f"env:\n  RETIRED_SECRET: ${{{{ secrets.{forbidden} }}}}\n",
        encoding="utf-8",
    )
    errors = verifier.verify_files()
    assert any(
        "active_bbd_workflow_reference:.github/workflows/mlb-remove-bbd-active-runtime-once.yml"
        in error
        for error in errors
    )


def test_other_scheduled_workflow_names_with_retired_provider_are_rejected(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_provider_neutral_files(tmp_path)
    workflow = tmp_path / ".github/workflows/mlb-bbs-live-provider.yml"
    workflow.write_text(
        'name: Invalid provider workflow\n"on":\n  schedule:\n    - cron: "0 * * * *"\n',
        encoding="utf-8",
    )

    errors = verifier.verify_files()
    assert any("active_bbd_workflow_name" in error for error in errors)


def test_workflow_authority_requires_no_bbd_and_forbids_retired_credentials():
    source = (ROOT / "scripts/verify_mlb_workflow_authority.py").read_text(
        encoding="utf-8"
    )
    for marker in (
        "production_source_contract_does_not_verify_no_bbd_runtime",
        "production_source_contract_does_not_test_no_bbd_runtime",
        "canonical_deploy_does_not_verify_no_bbd_runtime",
        "canonical_deploy_does_not_test_no_bbd_runtime",
        "canonical_deploy_retains_retired_provider_secret",
        "canonical_deploy_retains_retired_provider_parameter",
    ):
        assert marker in source
    for obsolete in (
        "production_source_contract_does_not_verify_bbs_wiring",
        "canonical_deploy_does_not_verify_bbs_wiring",
        "canonical_deploy_does_not_consume_exact_bbs_secret",
        "canonical_deploy_does_not_pass_bbs_noecho_parameter",
    ):
        assert obsolete not in source


def test_workflow_authority_migration_is_idempotent():
    source = (ROOT / "scripts/verify_mlb_workflow_authority.py").read_text(
        encoding="utf-8"
    )

    assert migration.patch_workflow_authority(source) == source


def test_template_migration_removes_orphaned_empty_inline_policy():
    orphan = "        - Statement:\n      Events:\n"
    cleaned = "      Events:\n"
    synthetic = "Policies:\n" + orphan

    migrated = migration.patch_template(synthetic)

    assert migrated == "Policies:\n" + cleaned
    assert migration.patch_template(migrated) == migrated
    assert orphan not in (ROOT / "template.yaml").read_text(encoding="utf-8")
