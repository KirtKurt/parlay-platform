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
        "scripts/verify_mlb_release_activation_predeploy.py",
        "scripts/invoke_mlb_trainer_with_retry.py",
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


def test_release_activation_gate_is_tested_compiled_and_immediately_predeploy() -> None:
    contract = (ROOT / ".github/workflows/mlb-production-source-contract.yml").read_text(
        encoding="utf-8"
    )
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    step_names = [
        line.removeprefix("      - name: ")
        for line in deploy.splitlines()
        if line.startswith("      - name: ")
    ]
    wait = step_names.index("Wait for CloudFormation updateability")
    gate = step_names.index(
        "Enforce durable MLB r3 release activation before SAM deploy"
    )
    sam_deploy = step_names.index("Deploy exact canonical source")

    assert "tests/unit/test_mlb_release_activation_predeploy.py" in contract
    assert (
        "python -m py_compile scripts/verify_mlb_release_activation_predeploy.py"
        in contract
    )
    assert gate == wait + 1
    assert sam_deploy == gate + 1


def test_rejects_missing_release_activation_predeploy_call(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace(
            "python scripts/verify_mlb_release_activation_predeploy.py",
            "python scripts/missing_release_activation_gate.py",
            1,
        ),
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_must_run_release_activation_predeploy_once"
        in authority.verify_repository(root)
    )


def test_rejects_step_inserted_between_updateability_and_activation_gate(
    tmp_path: Path,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace(
            "      - name: Enforce durable MLB r3 release activation before SAM deploy\n",
            "      - name: Unsafe intervening step\n"
            "        run: echo unsafe\n\n"
            "      - name: Enforce durable MLB r3 release activation before SAM deploy\n",
            1,
        ),
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_release_activation_step_order_invalid"
        in authority.verify_repository(root)
    )


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


def test_rejects_heavy_probe_wrapper_that_drops_attempt_limit(
    tmp_path: Path,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace(
            "                  max_attempts=max_attempts,\n",
            "",
            1,
        ),
        encoding="utf-8",
    )

    assert any(
        error.endswith("max_attempts=max_attempts,")
        for error in authority.verify_repository(root)
    )


@pytest.mark.parametrize(
    "occurrence",
    (1, 2),
)
def test_rejects_status_or_prediction_probe_delivery_retries(
    tmp_path: Path,
    occurrence: int,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    marker = "                  max_attempts=1,"
    assert text.count(marker) == 2
    offset = 0
    position = -1
    for _ in range(occurrence):
        position = text.index(marker, offset)
        offset = position + len(marker)
    mutated = (
        text[:position]
        + marker.replace("=1", "=2")
        + text[position + len(marker):]
    )
    deploy.write_text(
        mutated,
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_heavy_read_probe_must_attempt_each_delivery_once"
        in authority.verify_repository(root)
    )


@pytest.mark.parametrize(
    ("deadline_line", "replacement"),
    (
        ("                  deadline=deadline,", "                  deadline=None,"),
        (
            "                  deadline=prediction_deadline,",
            "                  deadline=None,",
        ),
    ),
)
def test_rejects_status_or_prediction_probe_without_active_deadline(
    tmp_path: Path,
    deadline_line: str,
    replacement: str,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    assert deadline_line in text
    deploy.write_text(
        text.replace(
            deadline_line,
            replacement,
            1,
        )
        + "\n# Detached deadline=deadline, deadline=prediction_deadline markers.\n",
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_heavy_read_probe_deadlines_are_invalid"
        in authority.verify_repository(root)
    )


@pytest.mark.parametrize(
    ("required_test", "expected_error"),
    (
        (
            "tests/unit/test_mlb_lock_status_request_cache.py",
            "canonical_deploy_does_not_require_lock_status_scale_regression",
        ),
        (
            "tests/unit/test_mlb_public_per_game_authority.py",
            "canonical_deploy_does_not_require_public_read_scale_regression",
        ),
    ),
)
def test_rejects_scale_regression_moved_to_optional_tests(
    tmp_path: Path,
    required_test: str,
    expected_error: str,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    required_line = f"            {required_test}\n"
    assert required_line in text
    deploy.write_text(
        text.replace(required_line, "", 1).replace(
            "          optional_tests=(\n",
            "          optional_tests=(\n" + required_line,
            1,
        ),
        encoding="utf-8",
    )

    assert expected_error in authority.verify_repository(root)


def test_rejects_missing_trainer_invoke_retry_helper(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    (root / "scripts/invoke_mlb_trainer_with_retry.py").unlink()

    assert (
        "canonical_trainer_invoke_retry_helper_missing"
        in authority.verify_repository(root)
    )


def test_rejects_deploy_that_bypasses_one_trainer_retry_helper_call(
    tmp_path: Path,
) -> None:
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


def test_rejects_reintroduced_inline_aws_trainer_invoke(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    deploy.write_text(
        deploy.read_text(encoding="utf-8")
        + "\n# invoke_with_capacity_retry\n# aws lambda invoke\n",
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_retains_unsafe_inline_trainer_invoke"
        in authority.verify_repository(root)
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    (
        (
            "            tests/unit/test_mlb_trainer_invoke_retry.py \\\n",
            "production_source_contract_does_not_test_trainer_invoke_retry",
        ),
        (
            "          python -m py_compile scripts/invoke_mlb_trainer_with_retry.py\n",
            "production_source_contract_does_not_compile_trainer_invoke_retry",
        ),
    ),
)
def test_source_contract_must_execute_trainer_retry_checks(
    tmp_path: Path, token: str, expected: str
) -> None:
    root = _copy_contract(tmp_path)
    contract = root / ".github/workflows/mlb-production-source-contract.yml"
    text = contract.read_text(encoding="utf-8")
    assert token in text
    contract.write_text(text.replace(token, "", 1), encoding="utf-8")

    assert expected in authority.verify_repository(root)


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


def test_requires_dynamic_status_to_bind_both_exact_run_artifacts(
    tmp_path: Path,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace(
            "--status-selection-capture-result "
            "/tmp/mlb-ml-v2-selection-capture.json",
            "--status-selection-capture-result "
            "/tmp/mlb-ml-v2-training.json",
            1,
        ),
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_must_query_post_run_status_exactly_once"
        in authority.verify_repository(root)
    )


def test_requires_lease_retry_on_both_mutating_deploy_probes(
    tmp_path: Path,
) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    deploy.write_text(
        text.replace("--retry-execution-lease", "", 1),
        encoding="utf-8",
    )

    assert (
        "canonical_deploy_lease_retry_scope_is_invalid"
        in authority.verify_repository(root)
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
        (
            "tests/unit/test_mlb_lock_status_request_cache.py",
            "production_source_contract_does_not_test_lock_status_request_cache",
        ),
        (
            "tests/unit/test_mlb_public_per_game_authority.py",
            "production_source_contract_does_not_test_public_per_game_authority",
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
