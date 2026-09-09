"""Run the read-only audit with the deployed trainer's explicit cohort contract.

Start a fresh interpreter so import-time experiment constants cannot retain R7.
Only non-secret contract fields are copied from Lambda configuration.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CONTRACT_FIELDS = (
    "MLB_ML_EXPERIMENT_ID", "MLB_ML_RELEASE_CONTRACT_ID",
    "MLB_ML_RELEASE_CUTOFF_UTC", "MLB_ML_FEATURE_VECTOR_VERSION",
    "MLB_ML_EXECUTION_LEASE_SECONDS", "SNAPSHOTS_TABLE",
)


def bound_environment(config: dict, environ: dict) -> dict:
    if config.get("State") != "Active" or config.get("LastUpdateStatus") != "Successful":
        raise ValueError("deployed trainer is not stable")
    variables = (config.get("Environment") or {}).get("Variables") or {}
    contract = {}
    for name in CONTRACT_FIELDS:
        value = variables.get(name)
        if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("missing or invalid deployed contract field: " + name)
        contract[name] = value
    cutoff = datetime.fromisoformat(contract["MLB_ML_RELEASE_CUTOFF_UTC"].replace("Z", "+00:00"))
    if cutoff.tzinfo is None or int(contract["MLB_ML_EXECUTION_LEASE_SECONDS"]) <= 0:
        raise ValueError("invalid deployed cutoff or lease")
    for source, target, length in (
        ("INQSI_DEPLOY_GIT_SHA", "MLB_AUDIT_EXPECTED_GIT_SHA", 40),
        ("INQSI_DEPLOY_TEMPLATE_SHA256", "MLB_AUDIT_EXPECTED_TEMPLATE_SHA256", 64),
    ):
        value = variables.get(source)
        if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{%d}" % length, value):
            raise ValueError("invalid deployed audit identity: " + source)
        contract[target] = value
    return {**environ, **contract,
            "INQSI_MLB_ML_AUTO_PROMOTE": "false", "INQSI_MLB_ML_AUDIT_STORE": "false",
            "INQSI_MLB_ALLOW_LOCAL_FILE_CHAMPION": "false"}


def main() -> int:
    import boto3
    from botocore.config import Config

    options = {"config": Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2})}
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if region:
        options["region_name"] = region
    detail = boto3.client("cloudformation", **options).describe_stack_resource(
        StackName=os.environ.get("MLB_PRODUCTION_STACK_NAME", "parlay-platform-dev"),
        LogicalResourceId="MLBMLTrainingFunction")["StackResourceDetail"]
    config = boto3.client("lambda", **options).get_function_configuration(
        FunctionName=detail["PhysicalResourceId"])
    environment = bound_environment(config, dict(os.environ))
    command = [sys.executable, str(Path(__file__).with_name("run_mlb_ml_v3_audit_report.py"))]
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
