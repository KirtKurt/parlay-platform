#!/usr/bin/env python3
"""Fail closed when BBS shadow credentials or authority escape their boundary."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template.yaml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"

AUDITED_PULL_RESOURCE = "MLBAuditedPullFunction"
BBS_SECRET_NAME = "BBS_API_KEY"
BBS_PARAMETER = "BbsApiKey"
BBS_SECRET_RESOURCE = "BbsApiSecret"

RETIRED_ENVIRONMENT_NAMES = (
    "SPORTSDATAIO_API_KEY",
    "SPORTSDATAIO_BASE_URL",
    "SPORTSDATAIO_MLB_GAMES_ENDPOINT",
    "SPORTSDATAIO_MLB_PBP_ENDPOINT",
)

RETIRED_ACTIVATION_PATHS = (
    ".github/workflows/enable-sportsdataio.yml",
    ".github/workflows/mlb-hot-pull-recovery.yml",
    "hello_world/sportsdataio_client.py",
    "hello_world/sportsdataio_api_patch.py",
    "hello_world/mlb_fundamentals_engine.py",
    "hello_world/mlb_fundamentals_optimizer_patch.py",
    "hello_world/mlb_hot_pull_recovery_lambda.py",
    "scripts/mlb_recover_hot_pull_and_predict.py",
    "scripts/patch_template_sportsdataio.py",
)


class ContractError(RuntimeError):
    """The checked-in deployment contract is not source-honest or least privilege."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _resource_block(template: str, logical_id: str) -> str:
    marker = f"  {logical_id}:\n"
    start = template.find(marker)
    _require(start >= 0, f"missing SAM resource: {logical_id}")
    match = re.search(r"\n  [A-Za-z0-9]+:\n", template[start + len(marker) :])
    end = len(template) if match is None else start + len(marker) + match.start()
    return template[start:end]


def _function_resource_ids(template: str) -> list[str]:
    return re.findall(
        r"^  ([A-Za-z0-9]+Function):\n    Type: AWS::Serverless::Function$",
        template,
        flags=re.MULTILINE,
    )


def _assert_absent(paths: Iterable[str]) -> None:
    present = [path for path in paths if (ROOT / path).exists()]
    _require(not present, "retired provider activation paths remain: " + ", ".join(present))


def verify() -> dict[str, object]:
    template = TEMPLATE.read_text(encoding="utf-8")
    deploy = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    _require(
        re.search(
            rf"^  {BBS_PARAMETER}:\n    Type: String\n    NoEcho: true$",
            template,
            flags=re.MULTILINE,
        )
        is not None,
        f"{BBS_PARAMETER} must be a NoEcho string parameter",
    )
    secret_block = _resource_block(template, BBS_SECRET_RESOURCE)
    _require("Type: AWS::SecretsManager::Secret" in secret_block, "BBS credential must use Secrets Manager")
    _require(f"SecretString: !Ref {BBS_PARAMETER}" in secret_block, "BBS secret must receive the exact NoEcho parameter")
    _require("DeletionPolicy: Retain" in secret_block, "BBS secret must be retained across stack changes")

    audited = _resource_block(template, AUDITED_PULL_RESOURCE)
    required_audited_tokens = (
        f"BBS_API_SECRET_ARN: !Ref {BBS_SECRET_RESOURCE}",
        "BBS_SHADOW_CAPTURE_ENABLED: 'true'",
        "BBS_SHADOW_S3_BUCKET: !Ref MLBMLArtifactsBucket",
        "secretsmanager:GetSecretValue",
        f"Resource: !Ref {BBS_SECRET_RESOURCE}",
        "s3:PutObject",
        "s3:GetObject",
        "/mlb/providers/bbs/*",
    )
    for token in required_audited_tokens:
        _require(token in audited, f"audited-pull BBS boundary missing: {token}")

    _require(BBS_SECRET_NAME not in template, "plaintext BBS_API_KEY must never be a SAM environment variable")
    _require("BBS_API_SECRET_ARN" not in template.replace(audited, "", 1), "BBS secret ARN escaped the audited-pull Lambda")
    _require("secretsmanager:GetSecretValue" not in template.replace(audited, "", 1), "another resource can read secrets")
    _require("/mlb/providers/bbs/*" not in template.replace(audited, "", 1), "another resource can write BBS shadow artifacts")

    functions = _function_resource_ids(template)
    _require(AUDITED_PULL_RESOURCE in functions, "audited-pull Lambda is not declared")
    for logical_id in functions:
        if logical_id == AUDITED_PULL_RESOURCE:
            continue
        block = _resource_block(template, logical_id)
        _require("BBS_" not in block and "BbsApi" not in block, f"BBS authority leaked into {logical_id}")

    for retired_name in RETIRED_ENVIRONMENT_NAMES:
        _require(retired_name not in template, f"retired provider environment variable remains: {retired_name}")
        _require(retired_name not in deploy, f"retired provider deploy input remains: {retired_name}")
    _assert_absent(RETIRED_ACTIVATION_PATHS)

    exact_secret_ref = "${{ secrets.BBS_API_KEY }}"
    _require(exact_secret_ref in deploy, "deploy workflow does not consume exact secrets.BBS_API_KEY")
    _require("Missing BBS_API_KEY" in deploy, "deploy workflow does not fail closed on an absent BBS key")
    _require('"BbsApiKey=${BBS_API_KEY_VALUE}"' in deploy, "SAM deployment does not pass the BBS NoEcho parameter")
    _require("verify_bbs_api_live_contract.py" in deploy, "authenticated BBS pre-deploy proof is missing")
    _require("verify_mlb_bbs_sam_wiring.py" in deploy, "BBS least-privilege verifier is not in the deploy gate")
    _require("secrets.BS_API_KEY" not in deploy, "retired BS_API_KEY alias must not be accepted")
    _require("secrets.SPORTSDATAIO" not in deploy.upper(), "retired provider secret remains in deploy")

    return {
        "ok": True,
        "secretName": BBS_SECRET_NAME,
        "credentialStorage": "AWS_SECRETS_MANAGER",
        "credentialConsumer": AUDITED_PULL_RESOURCE,
        "shadowOnly": True,
        "functionCountChecked": len(functions),
        "retiredActivationPathsAbsent": len(RETIRED_ACTIVATION_PATHS),
    }


def main() -> int:
    result = verify()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
