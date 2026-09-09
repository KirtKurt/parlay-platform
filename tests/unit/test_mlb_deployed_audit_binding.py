import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.run_deployed_mlb_audit import bound_environment

ROOT = Path(__file__).resolve().parents[2]


def configuration():
    return {"State": "Active", "LastUpdateStatus": "Successful", "Environment": {"Variables": {
        "MLB_ML_EXPERIMENT_ID": "mlb-v2-2026-08-31-historical-live-r8",
        "MLB_ML_RELEASE_CONTRACT_ID": "mlb-v2-2026-08-31-historical-live-r8",
        "MLB_ML_RELEASE_CUTOFF_UTC": "2026-08-31T04:00:00+00:00",
        "MLB_ML_FEATURE_VECTOR_VERSION": "vector-v2", "MLB_ML_EXECUTION_LEASE_SECONDS": "960",
        "SNAPSHOTS_TABLE": "deployed-snapshots", "INQSI_DEPLOY_GIT_SHA": "a" * 40,
        "INQSI_DEPLOY_TEMPLATE_SHA256": "b" * 64, "PRIVATE_DEPLOYED_SECRET": "not-for-auditor",
    }}}


def test_fresh_process_uses_deployed_r8_instead_of_retired_environment():
    environment = bound_environment(configuration(), {**os.environ,
        "PYTHONPATH": str(ROOT / "hello_world"), "MLB_ML_EXPERIMENT_ID": "retired-r7",
        "INQSI_MLB_ML_AUTO_PROMOTE": "true", "INQSI_MLB_ML_AUDIT_STORE": "true"})
    assert "PRIVATE_DEPLOYED_SECRET" not in environment
    assert environment["INQSI_MLB_ML_AUTO_PROMOTE"] == environment["INQSI_MLB_ML_AUDIT_STORE"] == "false"
    result = subprocess.run([sys.executable, "-c", "import mlb_ml_experiment_v2 as e; print(e.PRODUCTION_EXPERIMENT_ID); print(e.PRODUCTION_RELEASE_CUTOFF_UTC)"],
                            env=environment, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines()[-2:] == ["mlb-v2-2026-08-31-historical-live-r8", "2026-08-31T04:00:00+00:00"]


@pytest.mark.parametrize("field", ["MLB_ML_EXPERIMENT_ID", "MLB_ML_RELEASE_CONTRACT_ID",
    "MLB_ML_RELEASE_CUTOFF_UTC", "MLB_ML_FEATURE_VECTOR_VERSION", "MLB_ML_EXECUTION_LEASE_SECONDS",
    "SNAPSHOTS_TABLE", "INQSI_DEPLOY_GIT_SHA", "INQSI_DEPLOY_TEMPLATE_SHA256"])
def test_missing_deployed_contract_never_falls_back_to_r7(field):
    config = configuration()
    config["Environment"]["Variables"].pop(field)
    with pytest.raises(ValueError):
        bound_environment(config, {})


@pytest.mark.parametrize("changes", [{"State": "Pending"}, {"LastUpdateStatus": "InProgress"}])
def test_in_progress_deployment_cannot_bind_an_audit(changes):
    config = configuration()
    config.update(changes)
    with pytest.raises(ValueError, match="not stable"):
        bound_environment(config, {})


def test_all_aws_audit_entrypoints_bind_to_live_configuration():
    for name in ("mlb-fresh-audit-publisher", "mlb-rolling-24h-audit", "mlb-production-acceptance"):
        source = (ROOT / ".github/workflows" / (name + ".yml")).read_text()
        assert "python scripts/run_deployed_mlb_audit.py" in source
        assert "run: python scripts/run_mlb_ml_v3_audit_report.py" not in source


def test_audit_rejects_deployment_change_after_binding(monkeypatch):
    from scripts import run_mlb_ml_v3_audit_report as audit
    class CloudFormation:
        def describe_stack_resource(self, **kwargs):
            return {"StackResourceDetail": {"PhysicalResourceId": "trainer"}}
    class Lambda:
        def get_function_configuration(self, **kwargs):
            return configuration()
    clients = {"cloudformation": CloudFormation(), "lambda": Lambda()}
    monkeypatch.setattr("boto3.client", lambda service, **kwargs: clients[service])
    monkeypatch.setenv("MLB_AUDIT_EXPECTED_GIT_SHA", "c" * 40)
    with pytest.raises(RuntimeError, match="changed during audit"):
        audit._read_deployed_trainer_identity()
