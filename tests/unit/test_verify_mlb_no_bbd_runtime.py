from pathlib import Path

import remove_mlb_bbd_active_runtime as migration
import verify_mlb_no_bbd_runtime as verifier


ROOT = Path(__file__).resolve().parents[2]


def _write_required_contract_files(tmp_path: Path) -> None:
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
        "mlb_auto_llm/handler.py": (
            'VERSION = "MLB-AUTO-LLM-v1-three-source-autonomous"\n'
            'MLB = "https://statsapi.mlb.com/api/v1/schedule"\n'
            'ODDS = "https://api.the-odds-api.com/v4/sports/baseball_mlb"\n'
            'BBD = "https://api.bigballsdata.com"\n'
            'BEDROCK = boto3.client("bedrock-runtime")\n'
            "FIRST_GAME_SAFETY_MINUTES = 10\n"
            'inserted = put(condition="attribute_not_exists(PK)")\n'
            'CARD = {"authority": "MLB_AUTO_LLM_PRIMARY"}\n'
        ),
        "mlb_auto_llm/orchestrator.py": (
            "THREE_SOURCE_GAME_COVERAGE_INCOMPLETE = True\n"
            "BEDROCK_DECISION_REQUIRED = True\n"
            "threeSourceCoverageComplete = True\n"
            "teamRecentForm = True\n"
            "playerRollingStats = True\n"
            "bbsLeagueContext = True\n"
        ),
        "mlb-auto-llm-template.yaml": (
            "Parameters:\n"
            "  OddsApiKey:\n"
            "  BbsApiKey:\n"
            "Environment:\n"
            "  BBS_API_SECRET_ARN: secret\n"
            "  MLB_AUTO_FIRST_GAME_SAFETY_MINUTES: '10'\n"
            "Policies:\n"
            "  - bedrock:InvokeModel\n"
            "Events:\n"
            "  Schedule: cron(2/5 * * * ? *)\n"
            "DeletionPolicy: Retain\n"
        ),
        ".github/workflows/deploy-mlb-auto-prospective.yml": (
            "on:\n  push:\n    branches: [main]\n  workflow_dispatch:\n"
            "env:\n"
            "  ODDS: ${{ secrets.ODDS_API_KEY }}\n"
            "  BBS: ${{ secrets.BBS_API_KEY }}\n"
            "steps:\n"
            "  - run: sam deploy BbsApiKey=\"${BBS_API_KEY_VALUE}\" "
            "TargetDailyAccuracy='0.80'\n"
            '  - run: grep \'AUTHORITY = "AWS_ML_PROSPECTIVE_R7"\' source.py\n'
            "  - name: Prove live Bedrock inference without ML fallback\n"
            "  - run: deployment_provider_smoke\n"
        ),
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_provider_boundary_accepts_neutral_legacy_and_three_source_auto(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_contract_files(tmp_path)

    assert verifier.verify_files() == []


def test_provider_boundary_rejects_bbd_secret_in_legacy_root(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_contract_files(tmp_path)
    (tmp_path / "template.yaml").write_text(
        "Parameters:\n  BbsApiKey:\n    Type: String\n", encoding="utf-8"
    )

    errors = verifier.verify_files()
    assert any("active_bbd_reference:template.yaml" in error for error in errors)


def test_provider_boundary_rejects_stale_legacy_deploy_stabilizer(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_contract_files(tmp_path)
    forbidden = verifier.FORBIDDEN[0]
    (tmp_path / "scripts/stabilize_mlb_deploy_source.py").write_text(
        f"REQUIRED = [\"{forbidden}\"]\n", encoding="utf-8"
    )

    errors = verifier.verify_files()
    assert any(
        "active_bbd_reference:scripts/stabilize_mlb_deploy_source.py" in error
        for error in errors
    )


def test_isolated_auto_requires_bedrock(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_contract_files(tmp_path)
    handler = tmp_path / "mlb_auto_llm/handler.py"
    handler.write_text(
        handler.read_text(encoding="utf-8").replace(
            'boto3.client("bedrock-runtime")', "bedrock_missing"
        ),
        encoding="utf-8",
    )

    errors = verifier.verify_files()
    assert any(
        "isolated_three_source_marker_missing:mlb_auto_llm/handler.py" in error
        and "bedrock-runtime" in error
        for error in errors
    )


def test_isolated_auto_requires_complete_per_game_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_contract_files(tmp_path)
    orchestrator = tmp_path / "mlb_auto_llm/orchestrator.py"
    orchestrator.write_text(
        orchestrator.read_text(encoding="utf-8").replace(
            "THREE_SOURCE_GAME_COVERAGE_INCOMPLETE", "coverage_missing"
        ),
        encoding="utf-8",
    )

    errors = verifier.verify_files()
    assert any(
        "isolated_three_source_marker_missing:mlb_auto_llm/orchestrator.py" in error
        and "THREE_SOURCE_GAME_COVERAGE_INCOMPLETE" in error
        for error in errors
    )


def test_isolated_auto_requires_immutable_t10_card(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_contract_files(tmp_path)
    handler = tmp_path / "mlb_auto_llm/handler.py"
    handler.write_text(
        handler.read_text(encoding="utf-8")
        .replace("FIRST_GAME_SAFETY_MINUTES", "first_game_guard_missing")
        .replace('condition="attribute_not_exists(PK)"', "condition=None"),
        encoding="utf-8",
    )

    errors = verifier.verify_files()
    assert any("FIRST_GAME_SAFETY_MINUTES" in error for error in errors)
    assert any("attribute_not_exists(PK)" in error for error in errors)


def test_isolated_bbd_discovery_workflow_is_not_a_false_global_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    _write_required_contract_files(tmp_path)
    discovery = tmp_path / ".github/workflows/discover-bbd-pro-mlb-endpoints-v1.yml"
    discovery.write_text(
        "on:\n  workflow_dispatch:\n"
        "env:\n  KEY: ${{ secrets.BBS_API_KEY }}\n"
        "steps:\n  - run: curl https://api.bigballsdata.com/openapi.json\n",
        encoding="utf-8",
    )

    assert verifier.verify_files() == []


def test_canonical_root_deploy_still_enforces_provider_boundary():
    source = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "python scripts/verify_mlb_no_bbd_runtime.py" in source
    assert "tests/unit/test_verify_mlb_no_bbd_runtime.py" in source


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
