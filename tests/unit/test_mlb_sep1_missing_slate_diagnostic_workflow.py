from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "diagnose-mlb-sep1-missing-slate-read-only.yml"
)
TARGET_DATE = "2026-09-01"
SAFE_ENVIRONMENT = {
    "SNAPSHOTS_TABLE",
    "OUTCOMES_TABLE",
    "MLB_ML_ARTIFACTS_BUCKET",
    "MLB_ML_EXPERIMENT_ID",
    "MLB_ML_RELEASE_CONTRACT_ID",
    "MLB_ML_RELEASE_CUTOFF_UTC",
    "MLB_ML_FEATURE_VECTOR_VERSION",
    "INQSI_DEPLOY_GIT_SHA",
    "INQSI_DEPLOY_TEMPLATE_SHA256",
    "INQSI_MLB_ML_AUTO_PROMOTE",
    "INQSI_SLATE_TIMEZONE",
    "MLB_R7_HISTORICAL_TRAINING_ENABLED",
    "MLB_R7_HISTORICAL_MAX_ROWS",
}


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _document() -> dict:
    return yaml.load(_source(), Loader=yaml.BaseLoader)


def _step(name: str) -> dict:
    return next(
        value
        for value in _document()["jobs"]["diagnose"]["steps"]
        if value.get("name") == name
    )


def _program() -> str:
    run = _step("Read, compare, and sanitize missing slate evidence")["run"]
    heredoc = run.split("python - <<'PY'", 1)[1].split("\n", 1)[1]
    return heredoc.rsplit("\nPY", 1)[0]


def test_workflow_is_manual_main_only_and_checks_out_exact_deployed_sha() -> None:
    document = _document()
    job = document["jobs"]["diagnose"]

    assert set(document["on"]) == {"workflow_dispatch"}
    assert document["permissions"] == {"contents": "read"}
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert int(job["timeout-minutes"]) <= 20
    credentials = next(
        step
        for step in job["steps"]
        if step.get("uses") == "aws-actions/configure-aws-credentials@v4"
    )
    assert credentials["with"]["mask-aws-account-id"] == "true"
    checkout = next(
        step for step in job["steps"] if step.get("uses") == "actions/checkout@v4"
    )
    assert checkout["with"] == {
        "ref": "${{ steps.production.outputs.deploy_sha }}",
        "fetch-depth": "1",
        "persist-credentials": "false",
    }
    assert _step("Verify exact deployed checkout")["run"] == (
        'test "$(git rev-parse HEAD)" = "$INQSI_DEPLOY_GIT_SHA"'
    )
    source = _source()
    for trigger in ("push:", "schedule:", "pull_request:", "workflow_call:"):
        assert f"\n  {trigger}" not in source


def test_lambda_environment_is_an_explicit_non_secret_projection() -> None:
    run = _step("Resolve projected production trainer contract")["run"]
    match = re.search(
        r"get-function-configuration.*?--query '([^']+)'",
        run,
        flags=re.DOTALL,
    )
    assert match is not None
    projection = match.group(1)
    projected_names = {
        item.split(":", 1)[0]
        for item in projection.strip("{}").split(",")
    }
    assert projected_names == {"Handler", *SAFE_ENVIRONMENT}
    for name in SAFE_ENVIRONMENT:
        assert f"{name}:Environment.Variables.{name}" in projection
    assert "Environment:Environment" not in projection
    assert "Variables:Environment.Variables" not in projection
    for forbidden in ("SECRET", "PASSWORD", "TOKEN", "API_KEY", "ARN"):
        assert forbidden not in projection
    assert "rm -f \"$projection_path\"" in run
    assert "cat \"$projection_path\"" not in run
    for exact_value in (
        "mlb-v2-2026-08-31-historical-live-r8",
        "2026-08-31T04:00:00+00:00",
        "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v2-lock-safe-temporal-missingness",
        '"MLB_R7_HISTORICAL_MAX_ROWS": "500"',
    ):
        assert exact_value in run


def test_program_has_only_the_authorized_read_and_filter_path() -> None:
    program = _program()
    compile(program, str(WORKFLOW), "exec")

    assert "import mlb_ml_aws_training_v1_compat as compat" in program
    assert "canonical = compat.canonical" in program
    assert "canonical.TrainingConfig.from_env()" in program
    assert "canonical.AwsTrainingStore(" in program
    assert "canonical.TrainingService(store, config)" in program
    assert "service._validate_manifest_contract(result)" in program
    assert program.count("service.row_loader(config)") == 1
    assert program.count("canonical.experiment.filter_records(") == 1
    assert program.count("load_existing_manifest()") == 3
    assert program.count("optimizer_state_snapshot()") == 3
    assert "ConsistentRead=True" in program
    assert "ProjectionExpression=\"#record_type,#revision,#data\"" in program

    ordered = (
        "manifest_before = load_existing_manifest()",
        "optimizer_before = optimizer_state_snapshot()",
        "loaded_result = service.row_loader(config)",
        "filtered = canonical.experiment.filter_records(",
        "optimizer_after = optimizer_state_snapshot()",
        "manifest_after = load_existing_manifest()",
    )
    positions = [program.index(value) for value in ordered]
    assert positions == sorted(positions)
    for forbidden in (
        "_load_or_create_manifest",
        "lambda_handler(",
        "service.run(",
        "service.run_scheduled(",
        "service.capture_selections(",
        "service.status(",
        "advance_manifest(",
        "save_manifest(",
        "save_status(",
        "_save_run_status(",
        "_save_state(",
        ".put_item(",
        ".update_item(",
        ".delete_item(",
        ".transact_write_items(",
        ".put_object(",
        ".invoke(",
    ):
        assert forbidden not in program

    executable = _source().lower()
    for forbidden in (
        "aws lambda invoke",
        "aws dynamodb put-item",
        "aws dynamodb update-item",
        "aws dynamodb delete-item",
        "aws dynamodb transact-write-items",
        "aws s3 cp",
        "aws s3api put-object",
        "aws cloudformation deploy",
        "sam deploy",
        "git commit",
        "git push",
        "gh workflow run",
    ):
        assert forbidden not in executable


def test_only_the_sanitized_report_is_uploaded() -> None:
    uploads = [
        step
        for step in _document()["jobs"]["diagnose"]["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
    ]
    assert len(uploads) == 1
    assert uploads[0]["with"]["path"] == (
        "${{ runner.temp }}/mlb-sep1-missing-slate-read-only.json"
    )
    assert uploads[0]["with"]["if-no-files-found"] == "error"
    assert "projection_path" not in uploads[0]["with"]["path"]


@pytest.mark.parametrize("optimizer_drift", (False, True))
def test_runtime_sanitizes_canaries_and_fails_on_state_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    optimizer_drift: bool,
) -> None:
    secret = "raw-secret-canary"
    game_id = "official-game-id-canary"
    row_identity = "row-identity-canary"
    events: list[str] = []
    manifest = {
        "revision": 12,
        "manifestDigest": "b" * 64,
        "assignedSlateDates": {
            TARGET_DATE: {
                "rowCount": 1,
                "slateFingerprint": "d" * 64,
                "rowIdentities": [row_identity],
            }
        },
        "historicalDiagnosticSlateDates": {},
        "rawConfig": secret,
    }

    class Rows(list):
        continuity = {
            "version": (
                "MLB-ML-CANONICAL-SLATE-CONTINUITY-"
                "v2-exact-official-game-set"
            ),
            "ok": True,
            "skippedUnresolvedSlateDates": [TARGET_DATE],
            "unresolvedSlateErrors": {
                TARGET_DATE: (
                    "OFFICIAL_SLATE_UNRESOLVED:TrainingContractError:"
                    + game_id
                )
            },
            "blockedSlateDate": None,
            "blocker": (
                "OFFICIAL_SCHEDULE_UNPROVEN:RuntimeError:" + secret
            ),
            "finalizedSlateAuthorities": {TARGET_DATE: {"officialGamePks": [game_id]}},
        }

    class Config:
        artifacts_bucket = "safe-bucket"
        experiment_id = "safe-experiment"
        deployment_git_sha = "a" * 40

    class TrainingConfig:
        @classmethod
        def from_env(cls) -> Config:
            return Config()

    class Store:
        def load_manifest(self, experiment_id: str) -> dict:
            assert experiment_id == "safe-experiment"
            events.append("manifest")
            return copy.deepcopy(manifest)

    class AwsTrainingStore:
        def __new__(cls, **kwargs: object) -> Store:
            assert set(kwargs) == {
                "table_name",
                "artifacts_bucket",
                "dynamodb_resource",
                "s3_client",
            }
            return Store()

    class TrainingService:
        def __init__(self, store: Store, config: Config):
            self.store = store
            self.config = config

        def row_loader(self, config: Config) -> Rows:
            assert config is self.config
            events.append("row_loader")
            return Rows(
                [{
                    "featureSnapshot": {"slateDateEt": TARGET_DATE},
                    "gameId": game_id,
                    "recordIdentity": row_identity,
                    "secret": secret,
                }]
            )

        def _validate_manifest_contract(self, value: dict) -> None:
            assert value["rawConfig"] == secret
            events.append("validate_manifest")

    class Experiment:
        @staticmethod
        def manifest_digest(value: dict) -> str:
            return str(value["manifestDigest"])

        @staticmethod
        def filter_records(rows: list, value: dict) -> dict:
            events.append("filter_records")
            assert rows and value["rawConfig"] == secret
            return {
                "acceptedRows": [],
                "rejectedRows": [{
                    "gameId": game_id,
                    "slateDateEt": TARGET_DATE,
                    "reasons": [
                        "missing_prediction_lock",
                        "invalid reason:" + secret,
                    ],
                }],
            }

        @staticmethod
        def slate_fingerprint(rows: list) -> str:
            assert rows
            return "c" * 64

    canonical = SimpleNamespace(
        TrainingConfig=TrainingConfig,
        AwsTrainingStore=AwsTrainingStore,
        TrainingService=TrainingService,
        experiment=Experiment,
    )
    compat = types.ModuleType("mlb_ml_aws_training_v1_compat")
    compat.canonical = canonical
    bridge = types.ModuleType("mlb_r7_historical_walkforward_bridge")
    bridge.STATE_PK = "MLB_HISTORICAL_OPTIMIZER#V1"
    bridge.STATE_SK = "STATE"

    class Table:
        reads = 0

        def get_item(self, **kwargs: object) -> dict:
            assert kwargs["ConsistentRead"] is True
            assert kwargs["Key"] == {
                "PK": bridge.STATE_PK,
                "SK": bridge.STATE_SK,
            }
            assert kwargs["ProjectionExpression"] == "#record_type,#revision,#data"
            events.append("optimizer")
            self.reads += 1
            revision = 8 if optimizer_drift and self.reads == 2 else 7
            return {
                "Item": {
                    "record_type": "mlb_historical_optimizer_state_v1",
                    "revision": revision,
                    "data": {
                        "revision": revision,
                        "completedSlates": [{"raw": secret, "id": game_id}],
                    },
                }
            }

    table = Table()

    class Dynamo:
        def Table(self, name: str) -> Table:
            assert name == "safe-snapshots"
            return table

    boto3 = types.ModuleType("boto3")
    boto3.resource = lambda name: Dynamo()
    boto3.client = lambda name: object()
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "mlb_ml_aws_training_v1_compat", compat)
    monkeypatch.setitem(sys.modules, "mlb_r7_historical_walkforward_bridge", bridge)
    monkeypatch.setenv("SNAPSHOTS_TABLE", "safe-snapshots")
    monkeypatch.setenv("DEPLOYED_HANDLER_MATCHES", "true")
    monkeypatch.setenv("UNUSED_SECRET_CANARY", secret)
    report_path = tmp_path / "report.json"
    monkeypatch.setenv("REPORT_PATH", str(report_path))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="a" * 40 + "\n"),
    )

    if optimizer_drift:
        with pytest.raises(SystemExit, match="state bracket changed"):
            exec(compile(_program(), str(WORKFLOW), "exec"), {})
    else:
        exec(compile(_program(), str(WORKFLOW), "exec"), {})

    assert events[:8] == [
        "manifest",
        "validate_manifest",
        "optimizer",
        "row_loader",
        "filter_records",
        "optimizer",
        "manifest",
        "validate_manifest",
    ]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(report) == {
        "targetSlateDate",
        "deploymentIdentityMatches",
        "perDate",
        "continuity",
        "readOnlyBracket",
    }
    date_report = report["perDate"][0]
    assert date_report["loadedRowCount"] == 1
    assert date_report["acceptedRowCount"] == 0
    assert date_report["rejectedRowCount"] == 1
    assert date_report["rejectionReasonCounts"] == {
        "missing_prediction_lock": 1,
        "unknown_rejection_reason": 1,
    }
    assert report["continuity"]["unresolvedSlateErrorCodes"][TARGET_DATE] == {
        "code": "OFFICIAL_SLATE_UNRESOLVED",
        "exceptionType": "TrainingContractError",
    }
    assert report["continuity"]["blocker"] == {
        "code": "OFFICIAL_SCHEDULE_UNPROVEN",
        "exceptionType": "RuntimeError",
    }
    assert report["readOnlyBracket"]["optimizerState"]["digestEqual"] is (
        not optimizer_drift
    )
    assert report["readOnlyBracket"]["optimizerState"]["contentEqual"] is (
        not optimizer_drift
    )
    serialized = json.dumps(report, sort_keys=True)
    captured = capsys.readouterr()
    for canary in (secret, game_id, row_identity):
        assert canary not in serialized
        assert canary not in captured.out
        assert canary not in captured.err
