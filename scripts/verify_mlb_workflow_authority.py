#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parents[1]
RETIRED_WORKFLOWS: Dict[str, str] = {
    ".github/workflows/mlb-v1-direct-lambda-hotfix.yml": (
        "name: Retired - MLB V1 Direct Lambda Hotfix\n\n"
        "# Retired permanently: production Lambda code may only be deployed by deploy.yml.\n"
        "on: []\n\npermissions: {}\n\njobs:\n  retired:\n"
        "    if: ${{ false }}\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: exit 1\n"
    ),
    ".github/workflows/mlb-v1-emergency-deploy.yml": (
        "name: Retired - MLB V1 Emergency Deploy\n\n"
        "# Retired permanently: production changes may only be deployed by deploy.yml.\n"
        "on: []\n\npermissions: {}\n\njobs:\n  retired:\n"
        "    if: ${{ false }}\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: exit 1\n"
    ),
    ".github/workflows/manual-mlb-force-run.yml": (
        "name: Retired - Manual MLB Force Run\n\n"
        "# Retired permanently: production MLB pulls run only through canonical SAM schedules.\n"
        "on: []\n\npermissions: {}\n\njobs:\n  retired:\n"
        "    if: ${{ false }}\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: exit 1\n"
    ),
    ".github/workflows/diagnose-mlb-live-pull-500.yml": (
        "name: Retired - Diagnose MLB Live Pull 500\n\n"
        "# Retired permanently: diagnostics may not invoke production MLB writers.\n"
        "on: []\n\npermissions: {}\n\njobs:\n  retired:\n"
        "    if: ${{ false }}\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: exit 1\n"
    ),
    ".github/workflows/proof-run-0400-et.yml": (
        "name: Retired - Proof Run 0400 ET\n\n"
        "# Retired permanently: proof workflows may not invoke production schedulers.\n"
        "on: []\n\npermissions: {}\n\njobs:\n  retired:\n"
        "    if: ${{ false }}\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: exit 1\n"
    ),
    ".github/workflows/proof-run-1215-et.yml": (
        "name: Retired - Proof Run 1215 ET\n\n"
        "# Retired permanently: proof workflows may not invoke production schedulers.\n"
        "on: []\n\npermissions: {}\n\njobs:\n  retired:\n"
        "    if: ${{ false }}\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: exit 1\n"
    ),
    ".github/workflows/proof-run-1250-et.yml": (
        "name: Retired - Proof Run 1250 ET\n\n"
        "# Retired permanently: proof workflows may not invoke production schedulers.\n"
        "on: []\n\npermissions: {}\n\njobs:\n  retired:\n"
        "    if: ${{ false }}\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: exit 1\n"
    ),
}
DISABLED_ALTERNATE_WORKFLOWS = (
    ".github/workflows/proof-run-1800-et-mlb-all-signals.yml",
)
FORBIDDEN_ABSENT_PATHS = (
    ".github/workflows/enable-sportsdataio.yml",
    ".github/workflows/mlb-hot-pull-recovery.yml",
    "scripts/patch_template_sportsdataio.py",
    "scripts/verify_mlb_sportsdataio_sam_wiring.py",
    "tests/unit/test_mlb_sportsdataio_sam_wiring.py",
)
READ_ONLY_STORAGE_POLICY = "READ_ONLY_CANONICAL_PREDICTION_AUTHORITY_ONLY"


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
    for relative, expected in RETIRED_WORKFLOWS.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"retired_workflow_missing:{relative}")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            errors.append(f"retired_workflow_not_canonical_disabled_stub:{relative}")

    for relative in DISABLED_ALTERNATE_WORKFLOWS:
        path = root / relative
        if not path.is_file():
            errors.append(f"disabled_alternate_workflow_missing:{relative}")
            continue
        actual = path.read_text(encoding="utf-8")
        if (
            "\non: []\n" not in actual
            or "permissions:\n  contents: read\n" not in actual
            or "contents: write" in actual
            or "if: ${{ false }}" not in actual
        ):
            errors.append(f"alternate_workflow_not_disabled:{relative}")

    for relative in FORBIDDEN_ABSENT_PATHS:
        if (root / relative).exists():
            errors.append(f"forbidden_retired_path_present:{relative}")

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
            *RETIRED_WORKFLOWS,
            *DISABLED_ALTERNATE_WORKFLOWS,
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

    deploy_path = root / ".github/workflows/deploy.yml"
    deploy = deploy_path.read_text(encoding="utf-8") if deploy_path.is_file() else ""
    if not deploy:
        errors.append("canonical_deploy_workflow_missing")
    else:
        status_token = "--payload '{\"mode\":\"status\"}'"
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
        post_status = deploy.find(status_token)
        verifier = deploy.find("python scripts/verify_mlb_trainer_deploy_response.py")
        if deploy.count(status_token) != 1:
            errors.append("canonical_deploy_must_query_post_run_status_exactly_once")
        if deploy.count(training_token) != 1:
            errors.append("canonical_deploy_must_invoke_training_exactly_once")
        if deploy.count(selection_token) != 1:
            errors.append("canonical_deploy_must_invoke_selection_capture_exactly_once")
        if not (0 <= training < selection < post_status < verifier):
            errors.append("canonical_deploy_split_run_status_order_is_invalid")
        if verifier < 0 or verifier < post_status:
            errors.append("canonical_deploy_does_not_verify_post_run_trainer_status")
        if "python scripts/verify_mlb_workflow_authority.py" not in deploy:
            errors.append("canonical_deploy_does_not_verify_retired_workflows")
        if "python scripts/verify_mlb_bbs_sam_wiring.py" not in deploy:
            errors.append("canonical_deploy_does_not_verify_bbs_wiring")
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
