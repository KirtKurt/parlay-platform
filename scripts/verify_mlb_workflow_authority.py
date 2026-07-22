#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RETIRED_WORKFLOWS = (
    ".github/workflows/diagnose-mlb-live-pull-500.yml",
    ".github/workflows/enable-sportsdataio.yml",
    ".github/workflows/install-dedicated-mlb-v3-read.yml",
    ".github/workflows/install-pull-history-contract.yml",
    ".github/workflows/manual-mlb-force-run.yml",
    ".github/workflows/mlb-hot-pull-recovery.yml",
    ".github/workflows/mlb-v1-direct-lambda-hotfix.yml",
    ".github/workflows/mlb-v1-emergency-deploy.yml",
    ".github/workflows/proof-run-0400-et.yml",
    ".github/workflows/proof-run-1215-et.yml",
    ".github/workflows/proof-run-1250-et.yml",
    ".github/workflows/proof-run-1800-et-mlb-all-signals.yml",
)
FORBIDDEN_ABSENT_PATHS = (
    "scripts/patch_template_sportsdataio.py",
    "scripts/verify_mlb_sportsdataio_sam_wiring.py",
    "tests/unit/test_mlb_sportsdataio_sam_wiring.py",
)
READ_ONLY_STORAGE_POLICY = "READ_ONLY_CANONICAL_PREDICTION_AUTHORITY_ONLY"
PRODUCTION_ACCEPTANCE_WORKFLOW = ".github/workflows/mlb-production-acceptance.yml"
RELEASE_ACTIVATION_PREDEPLOY_SCRIPT = (
    "scripts/verify_mlb_release_activation_predeploy.py"
)


def _verify_read_only_hot_sides(root: Path) -> List[str]:
    errors: List[str] = []
    template_path = root / "template.yaml"
    template = template_path.read_text(encoding="utf-8") if template_path.is_file() else ""
    match = re.search(
        r"(?ms)^  MLBSignalApiFunction:\n(?P<body>.*?)(?=^  [A-Za-z0-9]+:\n|\Z)",
        template,
    )
    if match is None:
        errors.append("mlb_signal_api_sam_resource_missing")
    else:
        block = match.group("body")
        if "DynamoDBCrudPolicy" in block:
            errors.append("mlb_signal_api_retains_dynamodb_write_policy")
        for table in (
            "SnapshotsTable",
            "SignalLedgerTable",
            "PredictionsTable",
            "OutcomesTable",
        ):
            policy = (
                "- DynamoDBReadPolicy:\n"
                f"            TableName: !Ref {table}"
            )
            if policy not in block:
                errors.append(f"mlb_signal_api_read_policy_missing:{table}")

    source_contracts = {
        "hello_world/mlb_date_signal_api.py": {
            "required": (READ_ONLY_STORAGE_POLICY, '"storage_status": "READ_ONLY"'),
            "forbidden": (
                "PREDICTIONS_TABLE",
                "predictions_tbl.put_item",
                "_prediction_item",
                "_to_ddb",
                "ml_training_row",
                "store: bool",
                'params.get("store"',
            ),
        },
        "hello_world/mlb_signal_api.py": {
            "required": (READ_ONLY_STORAGE_POLICY, '"storage_status": "READ_ONLY"'),
            "forbidden": (
                "predictions_tbl.put_item",
                "_prediction_item",
                "ml_training_row",
                "store: bool",
                'params.get("store"',
            ),
        },
        "hello_world/mlb_manual_pull.py": {
            "required": ("_build_read_only_hot_sides",),
            "forbidden": ("_build_and_store_hot_sides",),
        },
        "hello_world/mlb_result_signals.py": {
            "required": ("prediction_by_game = _latest_prediction_by_game(game_date)",),
            "forbidden": ("hot_sides(", "import hot_sides"),
        },
        "hello_world/api.py": {
            "required": (
                'store = bool(store and sport != "mlb")',
                'store = params.get("store", "false").lower() == "true" and sport != "mlb"',
                'if sport == "mlb":',
                "MLB legacy prediction results are read-only",
                'hot_sides(min(int(params.get("limit") or 40), 200))',
            ),
            "forbidden": (
                'return _resp(200, hot_sides(min(int(params.get("limit") or 40), 200), params.get("store"',
            ),
        },
    }
    for relative, contract in source_contracts.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"mlb_hot_sides_source_missing:{relative}")
            continue
        text = path.read_text(encoding="utf-8")
        for token in contract["required"]:
            if token not in text:
                errors.append(f"mlb_hot_sides_read_only_marker_missing:{relative}:{token}")
        for token in contract["forbidden"]:
            if token in text:
                errors.append(f"mlb_hot_sides_writer_present:{relative}:{token}")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            errors.append(f"mlb_hot_sides_source_syntax_invalid:{relative}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if function_name == "hot_sides" and any(
                keyword.arg == "store" for keyword in node.keywords
            ):
                errors.append(
                    f"mlb_hot_sides_writer_present:{relative}:hot_sides(store=...)"
                )
    return errors


def verify_repository(root: Path = ROOT) -> List[str]:
    errors: List[str] = _verify_read_only_hot_sides(root)
    for relative in FORBIDDEN_RETIRED_WORKFLOWS:
        if (root / relative).exists():
            errors.append(f"forbidden_retired_workflow_present:{relative}")

    for relative in FORBIDDEN_ABSENT_PATHS:
        if (root / relative).exists():
            errors.append(f"forbidden_retired_path_present:{relative}")

    acceptance_path = root / PRODUCTION_ACCEPTANCE_WORKFLOW
    acceptance = (
        acceptance_path.read_text(encoding="utf-8")
        if acceptance_path.is_file()
        else ""
    )
    if not acceptance:
        errors.append("production_acceptance_workflow_missing")
    else:
        trigger_match = re.search(
            r"(?ms)^on:\n(?P<body>.*?)(?=^permissions:\n)",
            acceptance,
        )
        trigger_block = trigger_match.group("body") if trigger_match else ""
        if "  workflow_dispatch:\n" not in trigger_block:
            errors.append("production_acceptance_manual_trigger_missing")
        if re.search(r"(?m)^  (?:push|schedule):", trigger_block):
            errors.append(
                "production_acceptance_heavy_verifier_must_not_run_automatically"
            )
        if "--logical-resource-id MLBProductionVerifierFunction" not in acceptance:
            errors.append("production_acceptance_manual_verifier_diagnostic_missing")

    contract_path = root / ".github/workflows/mlb-production-source-contract.yml"
    contract = (
        contract_path.read_text(encoding="utf-8")
        if contract_path.is_file()
        else ""
    )
    if not contract:
        errors.append("production_source_contract_missing")
    else:
        for relative in (
            *FORBIDDEN_RETIRED_WORKFLOWS,
            *FORBIDDEN_ABSENT_PATHS,
        ):
            quoted = f"- '{relative}'"
            if contract.count(quoted) != 2:
                errors.append(
                    f"retired_or_forbidden_path_not_watched_on_push_and_pull_request:{relative}"
                )
        if "python scripts/verify_mlb_workflow_authority.py" not in contract:
            errors.append("production_source_contract_does_not_run_workflow_authority_verifier")
        if "python scripts/verify_mlb_bbs_sam_wiring.py" not in contract:
            errors.append("production_source_contract_does_not_verify_bbs_wiring")
        if "uses: aws-actions/setup-sam@v2" not in contract:
            errors.append("production_source_contract_does_not_install_sam_cli")
        if "sam validate --template-file template.yaml" not in contract:
            errors.append("production_source_contract_does_not_validate_sam_template")
        if (
            "sam build --no-cached --template-file template.yaml" not in contract
            or "python scripts/create_mlb_lambda_build_manifest.py" not in contract
        ):
            errors.append(
                "production_source_contract_does_not_build_and_fingerprint_sam_artifacts"
            )
        if "tests/unit/test_mlb_lambda_artifact_identity.py" not in contract:
            errors.append(
                "production_source_contract_does_not_test_lambda_artifact_identity"
            )
        if (
            "tests/unit/test_mlb_release_activation_predeploy.py"
            not in contract
        ):
            errors.append(
                "production_source_contract_does_not_test_release_activation_predeploy"
            )
        if (
            "python -m py_compile " + RELEASE_ACTIVATION_PREDEPLOY_SCRIPT
            not in contract
        ):
            errors.append(
                "production_source_contract_does_not_compile_release_activation_predeploy"
            )
        if "tests/unit/test_mlb_trainer_invoke_retry.py" not in contract:
            errors.append(
                "production_source_contract_does_not_test_trainer_invoke_retry"
            )
        if (
            "python -m py_compile scripts/invoke_mlb_trainer_with_retry.py"
            not in contract
        ):
            errors.append(
                "production_source_contract_does_not_compile_trainer_invoke_retry"
            )

    if not (root / RELEASE_ACTIVATION_PREDEPLOY_SCRIPT).is_file():
        errors.append("release_activation_predeploy_script_missing")

    helper_path = root / "scripts/invoke_mlb_trainer_with_retry.py"
    if not helper_path.is_file():
        errors.append("canonical_trainer_invoke_retry_helper_missing")

    deploy_path = root / ".github/workflows/deploy.yml"
    deploy = deploy_path.read_text(encoding="utf-8") if deploy_path.is_file() else ""
    if not deploy:
        errors.append("canonical_deploy_workflow_missing")
    else:
        status_training_token = (
            "--status-training-result /tmp/mlb-ml-v2-training.json"
        )
        status_selection_token = (
            "--status-selection-capture-result "
            "/tmp/mlb-ml-v2-selection-capture.json"
        )
        training_token = (
            "--payload '{\"sport\":\"mlb\",\"mode\":\"scheduled\","
            "\"run\":\"aws_native_fixed_prospective_shadow_training\"}'"
        )
        selection_token = (
            "--payload '{\"sport\":\"mlb\",\"mode\":\"selection_capture\","
            "\"run\":\"aws_native_prospective_selection_capture\"}'"
        )
        training = deploy.find(training_token)
        selection = deploy.find(selection_token)
        post_status = deploy.find(status_training_token)
        verifier = deploy.find("python scripts/verify_mlb_trainer_deploy_response.py")
        invoke_helper = "python scripts/invoke_mlb_trainer_with_retry.py"
        helper_positions = [
            match.start() for match in re.finditer(re.escape(invoke_helper), deploy)
        ]
        if len(helper_positions) != 3:
            errors.append("canonical_deploy_must_use_exactly_three_bounded_trainer_invokes")
        if "invoke_mlb_trainer_deploy_probe.py" in deploy:
            errors.append("canonical_deploy_retains_duplicate_trainer_invoke_helper")
        if "aws lambda invoke" in deploy:
            errors.append("canonical_deploy_retains_unbounded_lambda_invoke")
        if (
            len(helper_positions) == 3
            and verifier >= 0
            and helper_positions[-1] < verifier
        ):
            call_ends = [helper_positions[1], helper_positions[2], verifier]
            calls = [
                deploy[start:end]
                for start, end in zip(helper_positions, call_ends)
            ]
            call_contracts = (
                (
                    calls[0],
                    (
                        training_token,
                        "--response /tmp/mlb-ml-v2-training.json",
                        "--invocation /tmp/mlb-ml-v2-training-invoke.json",
                    ),
                    (selection_token, status_training_token, status_selection_token),
                ),
                (
                    calls[1],
                    (
                        selection_token,
                        "--response /tmp/mlb-ml-v2-selection-capture.json",
                        "--invocation /tmp/mlb-ml-v2-selection-capture-invoke.json",
                    ),
                    (training_token, status_training_token, status_selection_token),
                ),
                (
                    calls[2],
                    (
                        status_training_token,
                        status_selection_token,
                        "--response /tmp/mlb-ml-v2-status-after.json",
                        "--invocation /tmp/mlb-ml-v2-status-after-invoke.json",
                    ),
                    (training_token, selection_token, "--payload"),
                ),
            )
            for call, required, forbidden in call_contracts:
                if any(call.count(token) != 1 for token in required) or any(
                    token in call for token in forbidden
                ):
                    errors.append(
                        "canonical_deploy_trainer_invoke_evidence_pairing_is_invalid"
                    )
            mutating_calls = calls[:2]
            status_call = calls[2]
            if any(
                call.count("--retry-execution-lease") != 1
                for call in mutating_calls
            ) or "--retry-execution-lease" in status_call:
                errors.append("canonical_deploy_lease_retry_scope_is_invalid")
            if any(
                call.count("--deadline-seconds 1200") != 1
                for call in mutating_calls
            ) or "--deadline-seconds" in status_call:
                errors.append("canonical_deploy_lease_retry_deadline_is_invalid")
            if any(
                call.count("--retry-delay-seconds 20") != 1
                for call in mutating_calls
            ) or "--retry-delay-seconds" in status_call:
                errors.append("canonical_deploy_lease_retry_delay_is_invalid")
        if (
            deploy.count(status_training_token) != 1
            or deploy.count(status_selection_token) != 1
        ):
            errors.append("canonical_deploy_must_query_post_run_status_exactly_once")
        if deploy.count(training_token) != 1:
            errors.append("canonical_deploy_must_invoke_training_exactly_once")
        if deploy.count(selection_token) != 1:
            errors.append("canonical_deploy_must_invoke_selection_capture_exactly_once")
        if deploy.count("--retry-execution-lease") != 2:
            errors.append("canonical_deploy_lease_retry_scope_is_invalid")
        if deploy.count("--deadline-seconds 1200") != 2:
            errors.append("canonical_deploy_lease_retry_deadline_is_invalid")
        if deploy.count("--retry-delay-seconds 20") != 2:
            errors.append("canonical_deploy_lease_retry_delay_is_invalid")
        if not (0 <= training < selection < post_status < verifier):
            errors.append("canonical_deploy_split_run_status_order_is_invalid")
        if verifier < 0 or verifier < post_status:
            errors.append("canonical_deploy_does_not_verify_post_run_trainer_status")
        gate_call = (
            "python " + RELEASE_ACTIVATION_PREDEPLOY_SCRIPT
        )
        if deploy.count(gate_call) != 1:
            errors.append(
                "canonical_deploy_must_run_release_activation_predeploy_once"
            )
        step_matches = list(
            re.finditer(
                r"(?ms)^      - name: (?P<name>[^\n]+)\n"
                r"(?P<body>.*?)(?=^      - name: |\Z)",
                deploy,
            )
        )
        step_names = [match.group("name") for match in step_matches]
        gate_name = "Enforce durable MLB r3 release activation before SAM deploy"
        gate_body = ""
        try:
            wait_index = step_names.index("Wait for CloudFormation updateability")
            gate_index = step_names.index(gate_name)
            sam_deploy_index = step_names.index("Deploy exact canonical source")
        except ValueError:
            errors.append("canonical_deploy_release_activation_step_missing")
        else:
            if not (
                gate_index == wait_index + 1
                and sam_deploy_index == gate_index + 1
            ):
                errors.append(
                    "canonical_deploy_release_activation_step_order_invalid"
                )
            if step_names.count(gate_name) != 1:
                errors.append(
                    "canonical_deploy_release_activation_step_count_invalid"
                )
            gate_body = step_matches[gate_index].group("body")
            if gate_call not in gate_body:
                errors.append(
                    "canonical_deploy_release_activation_call_outside_gate_step"
                )
        for token in (
            "--stack-name parlay-platform-dev",
            '--region "${{ secrets.AWS_REGION }}"',
            "--output runtime_reports/mlb_release_activation_predeploy_latest.json",
        ):
            if token not in gate_body:
                errors.append(
                    "canonical_deploy_release_activation_argument_missing:" + token
                )
        if (
            "python -m py_compile " + RELEASE_ACTIVATION_PREDEPLOY_SCRIPT
            not in deploy
        ):
            errors.append(
                "canonical_deploy_does_not_compile_release_activation_predeploy"
            )
        if "tests/unit/test_mlb_release_activation_predeploy.py" not in deploy:
            errors.append(
                "canonical_deploy_does_not_test_release_activation_predeploy"
            )
        for required_capacity_token in (
            "Prove shared Lambda capacity recovered before trainer initialization",
            "capacity_deadline=$((SECONDS + 360))",
            "AWS_MAX_ATTEMPTS: \"1\"",
            "python scripts/invoke_mlb_trainer_with_retry.py",
            "scripts/mlb_deploy_http_probe.py",
            "from scripts.mlb_deploy_http_probe import fetch_json_object",
            "deadline=deadline",
        ):
            if required_capacity_token not in deploy:
                errors.append(
                    "canonical_deploy_capacity_backpressure_missing:"
                    + required_capacity_token
                )
        if deploy.count("python scripts/invoke_mlb_trainer_with_retry.py") != 3:
            errors.append(
                "canonical_deploy_must_use_bounded_invoke_retry_exactly_three_times"
            )
        if "invoke_with_capacity_retry" in deploy or "aws lambda invoke" in deploy:
            errors.append("canonical_deploy_retains_unsafe_inline_trainer_invoke")
        if "python scripts/verify_mlb_workflow_authority.py" not in deploy:
            errors.append("canonical_deploy_does_not_verify_retired_workflows")
        if "python scripts/verify_mlb_bbs_sam_wiring.py" not in deploy:
            errors.append("canonical_deploy_does_not_verify_bbs_wiring")
        if (
            "--expected-deploy-run-id" not in deploy
            or "steps.deploy.outputs.run_id" not in deploy
            or '"DeployRunId=${DEPLOY_RUN_ID}"' not in deploy
        ):
            errors.append("canonical_deploy_does_not_bind_lambda_to_unique_deploy_run")
        cold_start = deploy.find("Verify built MLB Lambda cold start")
        build_manifest = deploy.find(
            "Bind the verified clean SAM build to the deployment identity"
        )
        exact_deploy = deploy.find("Deploy exact canonical source")
        if (
            "python scripts/create_mlb_lambda_build_manifest.py" not in deploy
            or "--expected-code-manifest" not in deploy
            or "--template-file .aws-sam/build/template.yaml" not in deploy
            or 'PYTHONDONTWRITEBYTECODE: "1"' not in deploy
            or not (0 <= cold_start < build_manifest < exact_deploy)
        ):
            errors.append("canonical_deploy_does_not_bind_live_code_to_verified_sam_build")
        if (
            "Preflight Lambda artifact attestation access" not in deploy
            or "aws lambda get-function" not in deploy
            or "for attempt in range(1, 4):" not in deploy
        ):
            errors.append(
                "canonical_deploy_does_not_preflight_lambda_artifact_read_access"
            )
        if (
            "CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE|IMPORT_COMPLETE|IMPORT_ROLLBACK_COMPLETE|STACK_MISSING)"
            not in deploy
            or "UPDATE_ROLLBACK_COMPLETE|ROLLBACK_COMPLETE" in deploy
        ):
            errors.append("canonical_deploy_treats_terminal_rollback_as_updateable")
        if '${{ secrets.BBS_API_KEY }}' not in deploy:
            errors.append("canonical_deploy_does_not_consume_exact_bbs_secret")
        if '"BbsApiKey=${BBS_API_KEY_VALUE}"' not in deploy:
            errors.append("canonical_deploy_does_not_pass_bbs_noecho_parameter")
        for token in (
            "SPORTSDATAIO_API_KEY",
            "SportsDataIoApiKey",
            "patch_template_sportsdataio.py",
            "verify_mlb_sportsdataio_sam_wiring.py",
        ):
            if token in deploy:
                errors.append(f"canonical_deploy_retired_provider_token_present:{token}")
        shadow_runtime_tokens = {
            "disabled_manual_review_creates_shadow_pointer_only": (
                "canonical_deploy_does_not_require_shadow_only_manual_review"
            ),
            "payload.get('awsNativeTrainingInstalled') is not True": (
                "canonical_deploy_does_not_require_aws_training_installation"
            ),
            "payload.get('awsNativeTrainingAuthority') is not False": (
                "canonical_deploy_allows_training_to_claim_live_authority"
            ),
            "separate_mode_specific_status_contract": (
                "canonical_deploy_does_not_require_split_training_health"
            ),
            "payload.get('manualReviewCreatesShadowApprovalOnly') is not True": (
                "canonical_deploy_does_not_require_shadow_only_approval"
            ),
            "payload.get('v2InferenceConsumerInstalled') is not False": (
                "canonical_deploy_allows_unreviewed_v2_inference"
            ),
            "payload.get('runtimeAuthorityActivationAvailable') is not False": (
                "canonical_deploy_allows_unreviewed_runtime_activation"
            ),
        }
        for token, error in shadow_runtime_tokens.items():
            if token not in deploy:
                errors.append(error)

        for token, error in (
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
            (
                "tests/unit/test_mlb_trainer_invoke_retry.py",
                "production_source_contract_does_not_test_trainer_invoke_retry",
            ),
            (
                "tests/unit/test_mlb_lambda_artifact_identity.py",
                "production_source_contract_does_not_test_lambda_artifact_identity",
            ),
            (
                "python -m py_compile scripts/invoke_mlb_trainer_with_retry.py",
                "production_source_contract_does_not_compile_trainer_invoke_retry",
            ),
        ):
            if token not in contract:
                errors.append(error)

    return sorted(set(errors))


def main() -> int:
    errors = verify_repository()
    if errors:
        for error in errors:
            print(error)
        return 1
    print("MLB workflow deployment authority verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
