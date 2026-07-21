from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts import verify_mlb_workflow_authority as authority


ROOT = Path(__file__).resolve().parents[2]


def _copy_contract(tmp_path: Path) -> Path:
    for relative in (
        *authority.RETIRED_WORKFLOWS,
        *authority.DISABLED_ALTERNATE_WORKFLOWS,
        ".github/workflows/mlb-production-source-contract.yml",
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


def test_rejects_reenabled_retired_workflow(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    retired = root / ".github/workflows/mlb-v1-emergency-deploy.yml"
    retired.write_text(
        retired.read_text(encoding="utf-8").replace("on: []", "on:\n  workflow_dispatch:"),
        encoding="utf-8",
    )

    assert (
        "retired_workflow_not_canonical_disabled_stub:"
        ".github/workflows/mlb-v1-emergency-deploy.yml"
    ) in authority.verify_repository(root)


@pytest.mark.parametrize(
    "relative",
    (
        ".github/workflows/manual-mlb-force-run.yml",
        ".github/workflows/diagnose-mlb-live-pull-500.yml",
        ".github/workflows/proof-run-0400-et.yml",
        ".github/workflows/proof-run-1215-et.yml",
        ".github/workflows/proof-run-1250-et.yml",
    ),
)
def test_rejects_reenabled_out_of_band_writer_workflow(
    tmp_path: Path, relative: str
) -> None:
    root = _copy_contract(tmp_path)
    workflow = root / relative
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "on: []", "on:\n  workflow_dispatch:", 1
        ),
        encoding="utf-8",
    )

    assert (
        f"retired_workflow_not_canonical_disabled_stub:{relative}"
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
        "retired_workflow_not_watched_on_push_and_pull_request:"
        ".github/workflows/enable-sportsdataio.yml"
    ) in authority.verify_repository(root)


def test_rejects_reenabled_alternate_prediction_writer(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    relative = ".github/workflows/mlb-hot-pull-recovery.yml"
    workflow = root / relative
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "on: []", "on:\n  workflow_dispatch:", 1
        ),
        encoding="utf-8",
    )

    assert (
        f"alternate_workflow_not_disabled:{relative}"
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
        f"retired_workflow_not_watched_on_push_and_pull_request:{relative}"
        in authority.verify_repository(root)
    )


def test_requires_selection_capture_before_status_check(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    deploy = root / ".github/workflows/deploy.yml"
    text = deploy.read_text(encoding="utf-8")
    token = (
        "--payload '{\"sport\":\"mlb\",\"mode\":\"selection_capture\","
        "\"run\":\"aws_native_prospective_selection_capture\"}'"
    )
    deploy.write_text(text.replace(token, "--payload '{}'", 1), encoding="utf-8")

    errors = authority.verify_repository(root)
    assert "canonical_deploy_must_invoke_selection_capture_exactly_once" in errors
    assert "canonical_deploy_split_run_status_order_is_invalid" in errors


def test_requires_sportsdataio_patcher_source_filters(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    contract = root / ".github/workflows/mlb-production-source-contract.yml"
    text = contract.read_text(encoding="utf-8")
    watched = f"      - '{authority.SPORTSDATAIO_PATCHER}'\n"
    contract.write_text(text.replace(watched, "", 1), encoding="utf-8")

    assert (
        "sportsdataio_patcher_not_watched_on_push_and_pull_request"
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
