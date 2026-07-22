from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts import verify_mlb_workflow_authority as authority


ROOT = Path(__file__).resolve().parents[2]


def _copy_contract(tmp_path: Path) -> Path:
    for relative in (
        ".github/workflows/mlb-production-source-contract.yml",
        ".github/workflows/mlb-production-acceptance.yml",
        ".github/workflows/deploy.yml",
        "template.yaml",
        "hello_world/api.py",
        "hello_world/mlb_date_signal_api.py",
        "hello_world/mlb_signal_api.py",
        "hello_world/mlb_manual_pull.py",
        "hello_world/mlb_result_signals.py",
    ):
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return tmp_path


def test_repository_workflow_authority_is_hardened() -> None:
    assert authority.verify_repository(ROOT) == []


def test_pull_request_contract_installs_and_validates_sam() -> None:
    contract = (ROOT / ".github/workflows/mlb-production-source-contract.yml").read_text(
        encoding="utf-8"
    )
    assert "uses: aws-actions/setup-sam@v2" in contract
    assert "sam validate --template-file template.yaml" in contract


def test_production_acceptance_heavy_verifier_is_manual_only() -> None:
    workflow = (ROOT / authority.PRODUCTION_ACCEPTANCE_WORKFLOW).read_text(
        encoding="utf-8"
    )
    trigger_block = workflow.split("on:\n", 1)[1].split("permissions:\n", 1)[0]
    assert "  workflow_dispatch:\n" in trigger_block
    assert "  push:" not in trigger_block
    assert "  schedule:" not in trigger_block


def test_rejects_removal_of_capacity_safe_postdeploy_http_probe(
    tmp_path: Path,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace(
            "from scripts.mlb_deploy_http_probe import fetch_json_object",
            "# capacity-safe helper removed",
            1,
        ),
        encoding="utf-8",
    )

    assert any(
        error.startswith(
            "canonical_deploy_capacity_backpressure_missing:"
            "from scripts.mlb_deploy_http_probe import fetch_json_object"
        )
        for error in authority.verify_repository(root)
    )


@pytest.mark.parametrize("trigger", ("push", "schedule"))
def test_rejects_automated_heavy_production_acceptance(
    tmp_path: Path,
    trigger: str,
) -> None:
    root = _copy_contract(tmp_path)
    workflow = root / authority.PRODUCTION_ACCEPTANCE_WORKFLOW
    text = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        text.replace("  workflow_dispatch:\n", f"  {trigger}:\n  workflow_dispatch:\n", 1),
        encoding="utf-8",
    )

    assert (
        "production_acceptance_heavy_verifier_must_not_run_automatically"
        in authority.verify_repository(root)
    )


def test_rejects_reenabled_retired_workflow(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    retired = root / ".github/workflows/mlb-v1-emergency-deploy.yml"
    retired.parent.mkdir(parents=True, exist_ok=True)
    retired.write_text(
        "name: forbidden retired workflow\non:\n  workflow_dispatch:\njobs: {}\n",
        encoding="utf-8",
    )

    assert (
        "forbidden_retired_workflow_present:"
        ".github/workflows/mlb-v1-emergency-deploy.yml"
    ) in authority.verify_repository(root)


@pytest.mark.parametrize(
    "relative",
    authority.FORBIDDEN_RETIRED_WORKFLOWS,
)
def test_rejects_reenabled_out_of_band_writer_workflow(
    tmp_path: Path, relative: str
) -> None:
    root = _copy_contract(tmp_path)
    workflow = root / relative
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "name: forbidden retired workflow\non:\n  push:\njobs: {}\n",
        encoding="utf-8",
    )

    assert (
        f"forbidden_retired_workflow_present:{relative}"
        in authority.verify_repository(root)
    )


def test_rejects_mlb_signal_api_dynamodb_write_policy(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    template = root / "template.yaml"
    text = template.read_text(encoding="utf-8")
    resource = text.index("  MLBSignalApiFunction:\n")
    policy = text.index("        - DynamoDBReadPolicy:\n", resource)
    text = text[:policy] + text[policy:].replace(
        "        - DynamoDBReadPolicy:\n",
        "        - DynamoDBCrudPolicy:\n",
        1,
    )
    template.write_text(text, encoding="utf-8")

    assert (
        "mlb_signal_api_retains_dynamodb_write_policy"
        in authority.verify_repository(root)
    )


@pytest.mark.parametrize(
    ("relative", "writer", "error_token"),
    (
        (
            "hello_world/mlb_date_signal_api.py",
            "\npredictions_tbl.put_item(Item={})\n",
            "predictions_tbl.put_item",
        ),
        (
            "hello_world/mlb_signal_api.py",
            '\nml_training_row = True\n',
            "ml_training_row",
        ),
        (
            "hello_world/mlb_manual_pull.py",
            "\nhot_sides(store=True)\n",
            "hot_sides(store=...)",
        ),
        (
            "hello_world/mlb_result_signals.py",
            "\nhot_sides(game_date)\n",
            "hot_sides(",
        ),
    ),
)
def test_rejects_reintroduced_mlb_hot_sides_writer(
    tmp_path: Path, relative: str, writer: str, error_token: str
) -> None:
    root = _copy_contract(tmp_path)
    source = root / relative
    source.write_text(source.read_text(encoding="utf-8") + writer, encoding="utf-8")

    assert (
        f"mlb_hot_sides_writer_present:{relative}:{error_token}"
        in authority.verify_repository(root)
    )


def test_rejects_removed_generic_mlb_storage_guard(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    source = root / "hello_world/api.py"
    text = source.read_text(encoding="utf-8").replace(
        'store = bool(store and sport != "mlb")',
        "store = bool(store)",
        1,
    )
    source.write_text(text, encoding="utf-8")

    assert any(
        error.startswith(
            "mlb_hot_sides_read_only_marker_missing:hello_world/api.py:"
        )
        for error in authority.verify_repository(root)
    )


def test_requires_both_source_contract_path_filters(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    contract = root / ".github/workflows/mlb-production-source-contract.yml"
    text = contract.read_text(encoding="utf-8")
    watched = "      - '.github/workflows/enable-sportsdataio.yml'\n"
    contract.write_text(text.replace(watched, "", 1), encoding="utf-8")

    assert (
        "retired_or_forbidden_path_not_watched_on_push_and_pull_request:"
        ".github/workflows/enable-sportsdataio.yml"
    ) in authority.verify_repository(root)


def test_rejects_reenabled_alternate_prediction_writer(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    relative = ".github/workflows/mlb-hot-pull-recovery.yml"
    workflow = root / relative
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "name: forbidden\non:\n  workflow_dispatch:\njobs: {}\n",
        encoding="utf-8",
    )

    assert (
        f"forbidden_retired_workflow_present:{relative}"
        in authority.verify_repository(root)
    )


def test_requires_alternate_writer_source_contract_filters(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    contract = root / ".github/workflows/mlb-production-source-contract.yml"
    text = contract.read_text(encoding="utf-8")
    relative = ".github/workflows/proof-run-1800-et-mlb-all-signals.yml"
    watched = f"      - '{relative}'\n"
    contract.write_text(text.replace(watched, "", 1), encoding="utf-8")

    assert (
        f"retired_or_forbidden_path_not_watched_on_push_and_pull_request:{relative}"
        in authority.verify_repository(root)
    )


def test_requires_selection_capture_before_status_check(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    token = (
        "'{\"sport\":\"mlb\",\"mode\":\"selection_capture\","
        "\"run\":\"aws_native_prospective_selection_capture\"}'"
    )
    deploy.write_text(text.replace(token, "--payload '{}'", 1), encoding="utf-8")

    errors = authority.verify_repository(root)
    assert "canonical_deploy_must_invoke_selection_capture_exactly_once" in errors
    assert "canonical_deploy_split_run_status_order_is_invalid" in errors


def test_requires_bounded_retry_for_all_three_deploy_invocations(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace("python scripts/invoke_mlb_trainer_with_retry.py", "python", 1),
        encoding="utf-8",
    )

    errors = authority.verify_repository(root)
    assert (
        "canonical_deploy_must_use_bounded_invoke_retry_exactly_three_times"
        in errors
    )


def test_requires_capacity_probe_horizon_to_outlast_old_lock_backlog(
    tmp_path: Path,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace(
            "capacity_deadline=$((SECONDS + 360))",
            "capacity_deadline=$((SECONDS + 120))",
            1,
        ),
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_capacity_backpressure_missing:"
        "capacity_deadline=$((SECONDS + 360))"
        in authority.verify_repository(root)
    )


@pytest.mark.parametrize(
    ("token", "expected_error"),
    (
        (
            "tests/unit/test_mlb_daily_pick_lock_runtime.py",
            "production_source_contract_does_not_test_lock_runtime",
        ),
        (
            "tests/unit/test_mlb_daily_per_game_lock.py",
            "production_source_contract_does_not_test_per_game_lock",
        ),
    ),
)
def test_requires_lock_contracts_in_premerge_source_gate(
    tmp_path: Path, token: str, expected_error: str
) -> None:
    root = _copy_contract(tmp_path)
    contract = root / ".github/workflows/mlb-production-source-contract.yml"
    text = contract.read_text(encoding="utf-8")
    contract.write_text(text.replace(token, "removed-lock-contract.py"), encoding="utf-8")

    assert expected_error in authority.verify_repository(root)


def test_rejects_reintroduced_retired_provider_patcher(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    relative = "scripts/patch_template_sportsdataio.py"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("raise SystemExit('retired')\n", encoding="utf-8")

    assert (
        f"forbidden_retired_path_present:{relative}"
        in authority.verify_repository(root)
    )


def test_rejects_live_authority_claim_in_deploy_smoke(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace(
            "payload.get('awsNativeTrainingAuthority') is not False",
            "payload.get('awsNativeTrainingAuthority') is not True",
            1,
        ),
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_allows_training_to_claim_live_authority"
        in authority.verify_repository(root)
    )


def test_requires_unique_deploy_run_binding(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace('"DeployRunId=${DEPLOY_RUN_ID}"', '"DeployRunId=unknown"', 1),
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_does_not_bind_lambda_to_unique_deploy_run"
        in authority.verify_repository(root)
    )


@pytest.mark.parametrize(
    ("old", "new"),
    (
        (
            "--template-file .aws-sam/build/template.yaml",
            "--template-file template.yaml",
        ),
        (
            'PYTHONDONTWRITEBYTECODE: "1"',
            'PYTHONDONTWRITEBYTECODE: "0"',
        ),
    ),
)
def test_requires_exact_verified_sam_build_for_deploy(
    tmp_path: Path,
    old: str,
    new: str,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(text.replace(old, new, 1), encoding="utf-8")

    assert (
        "canonical_deploy_does_not_bind_live_code_to_verified_sam_build"
        in authority.verify_repository(root)
    )


def test_requires_lambda_artifact_access_preflight(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace("aws lambda get-function", "# artifact read removed", 1),
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_does_not_preflight_lambda_artifact_read_access"
        in authority.verify_repository(root)
    )


def test_rejects_terminal_rollback_as_updateable(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace(
            "UPDATE_ROLLBACK_COMPLETE|IMPORT_COMPLETE",
            "UPDATE_ROLLBACK_COMPLETE|ROLLBACK_COMPLETE|IMPORT_COMPLETE",
            1,
        ),
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_treats_terminal_rollback_as_updateable"
        in authority.verify_repository(root)
    )
