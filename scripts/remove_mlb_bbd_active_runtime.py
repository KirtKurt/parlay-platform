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


def _line(text: str, pattern: str, replacement: str = "") -> tuple[str, int]:
    return re.subn(pattern, replacement, text, flags=re.MULTILINE)


def _block(text: str, start: str, end: str) -> tuple[str, int]:
    pattern = rf"^{re.escape(start)}\n[\s\S]*?(?=^{re.escape(end)})"
    return re.subn(pattern, "", text, flags=re.MULTILINE)


def _exact(text: str, value: str) -> tuple[str, int]:
    count = text.count(value)
    return text.replace(value, ""), count


def patch_template(text: str) -> str:
    text, _ = _block(text, "  BbsApiKey:", "  InqsiAdminApiToken:")
    text, _ = _block(text, "  BbsApiSecret:", "  InqsiMembersTable:")
    for pattern in (
        r"^          BBS_API_SECRET_ARN:[^\n]*\n",
        r"^          BBS_SHADOW_CAPTURE_ENABLED:[^\n]*\n",
        r"^          BBS_SHADOW_S3_BUCKET:[^\n]*\n",
        r"^          BBS_SHADOW_SCHEMA_VERSION:[^\n]*\n",
    ):
        text, _ = _line(text, pattern)
    text, _ = _exact(
        text,
        "            - Effect: Allow\n"
        "              Action:\n"
        "                - secretsmanager:GetSecretValue\n"
        "              Resource: !Ref BbsApiSecret\n",
    )
    text, _ = _exact(
        text,
        "            - Effect: Allow\n"
        "              Action:\n"
        "                - s3:GetObject\n"
        "                - s3:PutObject\n"
        "              Resource: !Sub '${MLBMLArtifactsBucket.Arn}/mlb/providers/bbs/*'\n",
    )
    return text


def _insert_no_bbd_test(text: str) -> str:
    command = "          python -m pytest -q tests/unit/test_verify_mlb_no_bbd_runtime.py\n"
    list_entry = "            tests/unit/test_verify_mlb_no_bbd_runtime.py\n"
    if command in text or list_entry in text:
        return text

    command_marker = "          python -m pytest -q tests/unit/test_mlb_production_acceptance.py\n"
    if command_marker in text:
        return text.replace(command_marker, command_marker + command, 1)

    list_marker = "            tests/unit/test_mlb_production_acceptance.py\n"
    if list_marker in text:
        return text.replace(list_marker, list_marker + list_entry, 1)

    raise RuntimeError("deploy_test_insertion_marker_missing")


def patch_deploy(text: str) -> str:
    text, _ = _line(text, r"^          BBS_API_KEY_VALUE:[^\n]*\n")
    text, _ = _line(
        text, r"^          test -n \"\$\{BBS_API_KEY_VALUE:-\}\"[^\n]*\n"
    )
    text, _ = _block(
        text,
        "      - name: Verify Big Balls MLB shadow provider authentication and live schema",
        "      - name: Prove committed MLB source is canonical",
    )
    text, _ = _line(
        text, r"^          python scripts/verify_mlb_bbs_sam_wiring\.py\n"
    )
    text, _ = _line(text, r"^            tests/unit/test_mlb_bbs_status\.py\n")
    text, _ = _line(
        text, r'^            "BbsApiKey=\$\{BBS_API_KEY_VALUE\}"\n'
    )

    marker = "          python scripts/verify_mlb_daily_pull_start_gate.py\n"
    verifier = "          python scripts/verify_mlb_no_bbd_runtime.py\n"
    if verifier not in text:
        if marker not in text:
            raise RuntimeError("deploy_validation_insertion_marker_missing")
        text = text.replace(marker, verifier + marker, 1)

    return _insert_no_bbd_test(text)


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
