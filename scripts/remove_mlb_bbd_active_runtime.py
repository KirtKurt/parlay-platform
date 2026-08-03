#!/usr/bin/env python3
"""Remove all BBD/BBS dependencies from active MLB deploy/runtime files.

The migration is intentionally narrow and idempotent. Legacy provider modules may
remain for historical artifact decoding, but no SAM resource, active GitHub
workflow, or production authority verifier may provision, require, validate, or
call the retired provider.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template.yaml"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
WORKFLOW_AUTHORITY = ROOT / "scripts" / "verify_mlb_workflow_authority.py"
ACTIVE_WORKFLOW_PATHS = (
    Path(".github/workflows/deploy.yml"),
    Path(".github/workflows/deploy-mlb-ranked-v15-10.yml"),
    Path(".github/workflows/mlb-backend-full-recovery.yml"),
    Path(".github/workflows/mlb-historical-optimizer.yml"),
    Path(".github/workflows/mlb-odds-pattern-v7-deploy.yml"),
    Path(".github/workflows/mlb-v8-fundamentals-deploy.yml"),
)
RETIRED_FUNDAMENTALS_WORKFLOW = Path(
    ".github/workflows/mlb-v8-fundamentals-deploy.yml"
)
RETIRED_WORKFLOW_TOKENS = (
    "BBS" + "_API_KEY",
    "BBS" + "_API_SECRET_ARN",
    "Bbs" + "ApiKey",
    "Bbs" + "ApiSecret",
    "verify_mlb_" + "bbs_sam_wiring.py",
    "test_mlb_" + "bbs_status.py",
)


def _line(text: str, pattern: str, replacement: str = "") -> tuple[str, int]:
    return re.subn(pattern, replacement, text, flags=re.MULTILINE)


def _block(text: str, start: str, end: str) -> tuple[str, int]:
    pattern = rf"^{re.escape(start)}\n[\s\S]*?(?=^{re.escape(end)})"
    return re.subn(pattern, "", text, flags=re.MULTILINE)


def _exact(text: str, value: str) -> tuple[str, int]:
    count = text.count(value)
    return text.replace(value, ""), count


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"authority_migration_marker_missing:{label}")


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
    # Retiring the only statements in the audited-pull inline policy must also
    # remove the now-empty SAM policy document. Leaving ``- Statement:`` with a
    # null value passes YAML parsing but fails cfn-lint E3510.
    text = text.replace(
        "        - Statement:\n      Events:\n",
        "      Events:\n",
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
    text = patch_generic_workflow(text)
    text, _ = _block(
        text,
        "      - name: Verify Big Balls MLB shadow provider authentication and live schema",
        "      - name: Prove committed MLB source is canonical",
    )

    marker = "          python scripts/verify_mlb_daily_pull_start_gate.py\n"
    verifier = "          python scripts/verify_mlb_no_bbd_runtime.py\n"
    if verifier not in text:
        if marker not in text:
            raise RuntimeError("deploy_validation_insertion_marker_missing")
        text = text.replace(marker, verifier + marker, 1)

    return _insert_no_bbd_test(text)


def patch_generic_workflow(text: str) -> str:
    """Remove credential reads, assertions, tests and parameter overrides."""
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        if any(token in line for token in RETIRED_WORKFLOW_TOKENS):
            continue
        kept.append(line)
    return "".join(kept)


def patch_workflow_authority(text: str) -> str:
    """Replace stale retired-provider requirements with no-BBD authority checks."""
    old_contract = (
        '        if "python scripts/verify_mlb_bbs_sam_wiring.py" not in contract:\n'
        '            errors.append("production_source_contract_does_not_verify_bbs_wiring")\n'
    )
    new_contract = (
        '        if "python scripts/verify_mlb_no_bbd_runtime.py" not in contract:\n'
        '            errors.append("production_source_contract_does_not_verify_no_bbd_runtime")\n'
        '        if "tests/unit/test_verify_mlb_no_bbd_runtime.py" not in contract:\n'
        '            errors.append("production_source_contract_does_not_test_no_bbd_runtime")\n'
    )
    text = _replace_once(text, old_contract, new_contract, "production_source_contract")

    old_deploy_verifier = (
        '        if "python scripts/verify_mlb_bbs_sam_wiring.py" not in deploy:\n'
        '            errors.append("canonical_deploy_does_not_verify_bbs_wiring")\n'
    )
    new_deploy_verifier = (
        '        if "python scripts/verify_mlb_no_bbd_runtime.py" not in deploy:\n'
        '            errors.append("canonical_deploy_does_not_verify_no_bbd_runtime")\n'
        '        if "tests/unit/test_verify_mlb_no_bbd_runtime.py" not in deploy:\n'
        '            errors.append("canonical_deploy_does_not_test_no_bbd_runtime")\n'
    )
    text = _replace_once(
        text,
        old_deploy_verifier,
        new_deploy_verifier,
        "canonical_deploy_verifier",
    )

    old_secret_requirements = (
        "        if '${{ secrets.BBS_API_KEY }}' not in deploy:\n"
        '            errors.append("canonical_deploy_does_not_consume_exact_bbs_secret")\n'
        "        if '\"BbsApiKey=${BBS_API_KEY_VALUE}\"' not in deploy:\n"
        '            errors.append("canonical_deploy_does_not_pass_bbs_noecho_parameter")\n'
    )
    new_secret_requirements = (
        '        retired_secret = "${{ secrets." + "BBS" + "_API_KEY }}"\n'
        '        retired_override = "\\\"Bbs" + "ApiKey=${" + "BBS" + "_API_KEY_VALUE}\\\""\n'
        '        if retired_secret in deploy:\n'
        '            errors.append("canonical_deploy_retains_retired_provider_secret")\n'
        '        if retired_override in deploy:\n'
        '            errors.append("canonical_deploy_retains_retired_provider_parameter")\n'
    )
    return _replace_once(
        text,
        old_secret_requirements,
        new_secret_requirements,
        "canonical_deploy_secret_contract",
    )


def retired_fundamentals_dispatcher() -> str:
    return """name: MLB V8 Fundamentals Compatibility Dispatcher

\"on\":
  workflow_dispatch:
    inputs:
      limit:
        description: Official/internal historical context games attempted
        required: false
        default: '25'

permissions:
  actions: write
  contents: read

concurrency:
  group: mlb-v8-fundamentals-compatibility-dispatcher
  cancel-in-progress: false

jobs:
  dispatch-official-context:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Dispatch active provider-neutral V8 context workflow
        env:
          GH_TOKEN: ${{ github.token }}
          LIMIT: ${{ inputs.limit || '25' }}
        run: |
          set -euo pipefail
          gh workflow run mlb-v8-historical-context-backfill.yml \\
            --repo \"$GITHUB_REPOSITORY\" \\
            --ref main \\
            --field limit=\"$LIMIT\"
          echo \"Dispatched provider-neutral V8 context workflow with limit=$LIMIT\"
"""


def patch_workflow(path: Path, text: str) -> str:
    if path == Path(".github/workflows/deploy.yml"):
        return patch_deploy(text)
    if path == RETIRED_FUNDAMENTALS_WORKFLOW:
        return retired_fundamentals_dispatcher()
    return patch_generic_workflow(text)


def verify(
    template: str,
    workflows: dict[Path, str],
    workflow_authority: str,
) -> list[str]:
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
    for name, body in (("template.yaml", template), *workflows.items()):
        for token in tokens:
            if token in body:
                errors.append(f"retired_provider_reference:{name}:{token}")
    if "        - Statement:\n      Events:\n" in template:
        errors.append("orphaned_empty_inline_policy")
    deploy = workflows[Path(".github/workflows/deploy.yml")]
    if "python scripts/verify_mlb_no_bbd_runtime.py" not in deploy:
        errors.append("no_bbd_verifier_missing_from_deploy")
    if "tests/unit/test_verify_mlb_no_bbd_runtime.py" not in deploy:
        errors.append("no_bbd_regression_test_missing_from_deploy")
    for marker in (
        "production_source_contract_does_not_verify_no_bbd_runtime",
        "production_source_contract_does_not_test_no_bbd_runtime",
        "canonical_deploy_does_not_verify_no_bbd_runtime",
        "canonical_deploy_does_not_test_no_bbd_runtime",
        "canonical_deploy_retains_retired_provider_secret",
        "canonical_deploy_retains_retired_provider_parameter",
    ):
        if marker not in workflow_authority:
            errors.append(f"provider_neutral_authority_marker_missing:{marker}")
    for obsolete in (
        "production_source_contract_does_not_verify_bbs_wiring",
        "canonical_deploy_does_not_verify_bbs_wiring",
        "canonical_deploy_does_not_consume_exact_bbs_secret",
        "canonical_deploy_does_not_pass_bbs_noecho_parameter",
    ):
        if obsolete in workflow_authority:
            errors.append(f"obsolete_provider_authority_requirement_present:{obsolete}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    template_before = TEMPLATE.read_text(encoding="utf-8")
    template_after = patch_template(template_before)
    workflow_before = {
        path: (ROOT / path).read_text(encoding="utf-8") for path in ACTIVE_WORKFLOW_PATHS
    }
    workflow_after = {
        path: patch_workflow(path, text) for path, text in workflow_before.items()
    }
    authority_before = WORKFLOW_AUTHORITY.read_text(encoding="utf-8")
    authority_after = patch_workflow_authority(authority_before)
    errors = verify(template_after, workflow_after, authority_after)
    if errors:
        for error in errors:
            print(error)
        return 1

    changed_paths = []
    if template_after != template_before:
        changed_paths.append(Path("template.yaml"))
    changed_paths.extend(
        path for path in ACTIVE_WORKFLOW_PATHS if workflow_after[path] != workflow_before[path]
    )
    if authority_after != authority_before:
        changed_paths.append(Path("scripts/verify_mlb_workflow_authority.py"))
    if args.check:
        if changed_paths:
            print("active MLB runtime still requires the no-BBD migration")
            for path in changed_paths:
                print(f"pending_migration:{path}")
            return 1
        print("active MLB runtime is already BBD-free")
        return 0

    TEMPLATE.write_text(template_after, encoding="utf-8")
    for path, text in workflow_after.items():
        (ROOT / path).write_text(text, encoding="utf-8")
    WORKFLOW_AUTHORITY.write_text(authority_after, encoding="utf-8")
    print(
        "removed BBD from active MLB runtime; changed="
        + str(bool(changed_paths)).lower()
    )
    for path in changed_paths:
        print(f"migrated:{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
