#!/usr/bin/env python3
"""Remove all BBD/BBS dependencies from active MLB deploy/runtime files.

The migration is intentionally narrow and idempotent. Legacy provider modules may
remain for historical artifact decoding, but no SAM resource or active GitHub
workflow may provision, read, validate, or call the retired provider.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template.yaml"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"


def _sub(text: str, pattern: str, replacement: str = "") -> tuple[str, int]:
    return re.subn(pattern, replacement, text, flags=re.MULTILINE | re.DOTALL)


def patch_template(text: str) -> str:
    text, _ = _sub(
        text,
        r"^  BbsApiKey:\n(?:    .*\n)+?(?=  InqsiAdminApiToken:)",
    )
    text, _ = _sub(
        text,
        r"^  BbsApiSecret:\n.*?(?=^  InqsiMembersTable:)",
    )
    for pattern in (
        r"^          BBS_API_SECRET_ARN:.*\n",
        r"^          BBS_SHADOW_CAPTURE_ENABLED:.*\n",
        r"^          BBS_SHADOW_S3_BUCKET:.*\n",
        r"^          BBS_SHADOW_SCHEMA_VERSION:.*\n",
    ):
        text, _ = _sub(text, pattern)
    text, _ = _sub(
        text,
        r"^            - Effect: Allow\n"
        r"              Action:\n"
        r"                - secretsmanager:GetSecretValue\n"
        r"              Resource: !Ref BbsApiSecret\n",
    )
    text, _ = _sub(
        text,
        r"^            - Effect: Allow\n"
        r"              Action:\n"
        r"                - s3:GetObject\n"
        r"                - s3:PutObject\n"
        r"              Resource: !Sub '\$\{MLBMLArtifactsBucket\.Arn\}/mlb/providers/bbs/\*'\n",
    )
    return text


def patch_deploy(text: str) -> str:
    text, _ = _sub(text, r"^          BBS_API_KEY_VALUE:.*\n")
    text, _ = _sub(text, r"^          test -n \"\$\{BBS_API_KEY_VALUE:-\}\".*\n")
    text, _ = _sub(
        text,
        r"^      - name: Verify Big Balls MLB shadow provider authentication and live schema\n"
        r".*?(?=^      - name: Prove committed MLB source is canonical)",
    )
    text, _ = _sub(text, r"^          python scripts/verify_mlb_bbs_sam_wiring\.py\n")
    text, _ = _sub(text, r"^            tests/unit/test_mlb_bbs_status\.py\n")
    text, _ = _sub(text, r'^            "BbsApiKey=\$\{BBS_API_KEY_VALUE\}"\n')

    marker = "          python scripts/verify_mlb_daily_pull_start_gate.py\n"
    verifier = "          python scripts/verify_mlb_no_bbd_runtime.py\n"
    if verifier not in text:
        if marker not in text:
            raise RuntimeError("deploy_validation_insertion_marker_missing")
        text = text.replace(marker, verifier + marker, 1)

    test_marker = "            tests/unit/test_mlb_production_acceptance.py\n"
    test_line = "            tests/unit/test_verify_mlb_no_bbd_runtime.py\n"
    if test_line not in text:
        if test_marker not in text:
            raise RuntimeError("deploy_test_insertion_marker_missing")
        text = text.replace(test_marker, test_marker + test_line, 1)
    return text


def verify(template: str, deploy: str) -> list[str]:
    tokens = (
        "BbsApiKey",
        "BbsApiSecret",
        "BBS_API_KEY",
        "BBS_API_SECRET_ARN",
        "BBS_SHADOW_CAPTURE_ENABLED",
        "BBS_SHADOW_S3_BUCKET",
        "BBS_SHADOW_SCHEMA_VERSION",
        "api.bigballsdata.com",
        "verify_bbs_api_live_contract.py",
        "verify_mlb_bbs_sam_wiring.py",
        "test_mlb_bbs_status.py",
        "mlb/providers/bbs/",
    )
    errors = []
    for name, body in (("template.yaml", template), ("deploy.yml", deploy)):
        for token in tokens:
            if token in body:
                errors.append(f"retired_provider_reference:{name}:{token}")
    if "python scripts/verify_mlb_no_bbd_runtime.py" not in deploy:
        errors.append("no_bbd_verifier_missing_from_deploy")
    if "tests/unit/test_verify_mlb_no_bbd_runtime.py" not in deploy:
        errors.append("no_bbd_regression_test_missing_from_deploy")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    template_before = TEMPLATE.read_text(encoding="utf-8")
    deploy_before = DEPLOY.read_text(encoding="utf-8")
    template_after = patch_template(template_before)
    deploy_after = patch_deploy(deploy_before)
    errors = verify(template_after, deploy_after)
    if errors:
        for error in errors:
            print(error)
        return 1
    changed = template_after != template_before or deploy_after != deploy_before
    if args.check:
        if changed:
            print("active MLB runtime still requires the no-BBD migration")
            return 1
        print("active MLB runtime is already BBD-free")
        return 0
    TEMPLATE.write_text(template_after, encoding="utf-8")
    DEPLOY.write_text(deploy_after, encoding="utf-8")
    print(f"removed BBD from active MLB runtime; changed={str(changed).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
