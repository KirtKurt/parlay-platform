from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"migration marker missing: {label}")


def _patch_manual_pull() -> None:
    path = ROOT / "hello_world" / "mlb_manual_pull.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("DEFAULT_DAYS_AHEAD = 1", "DEFAULT_DAYS_AHEAD = 0")
    path.write_text(text, encoding="utf-8")


def _patch_invariants() -> None:
    path = ROOT / "scripts" / "verify_mlb_schedule_invariants.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("    'MLBProductionIngestVerifyDaily435Et': 'daily ingest verification schedule missing',\n", "")
    text = text.replace("    'MLBProductionLockVerifyDaily556Et': 'daily lock verification schedule missing',\n", "")

    anchor = """if '\"days_ahead\":0' not in text and '\"days_ahead\": 0' not in text:
    violations.append('same-day days_ahead=0 input missing')
"""
    checks = anchor + """if "MLB_PULL_START_AT_ET: '01:00'" not in text:
    violations.append('recurring daily 1 AM ET pull gate missing')
if "Schedule: rate(15 minutes)" not in text or "results_pull_15m" not in text:
    violations.append('MLB result settlement is not scheduled every 15 minutes')
for obsolete in ['MLBProductionIngestVerifyDaily435Et', 'MLBProductionLockVerifyDaily556Et']:
    if obsolete in text:
        violations.append(f'obsolete fixed-UTC verifier schedule exists: {obsolete}')
for required in [
    'DeployGitSha:',
    'DeployTemplateSha256:',
    'INQSI_DEPLOY_GIT_SHA: !Ref DeployGitSha',
    'INQSI_DEPLOY_TEMPLATE_SHA256: !Ref DeployTemplateSha256',
]:
    if required not in text:
        violations.append(f'deploy identity contract missing: {required}')
"""
    if "recurring daily 1 AM ET pull gate missing" not in text:
        text = _replace_once(text, anchor, checks, "invariant stabilization checks")
    path.write_text(text, encoding="utf-8")


def _patch_deploy_workflow() -> None:
    path = ROOT / ".github" / "workflows" / "deploy.yml"
    text = path.read_text(encoding="utf-8")
    text = text.replace('      - ".github/workflows/**"\n', "")

    patch_anchor = """      - name: Patch SAM template for MLB settlement routes
        run: python scripts/patch_template_mlb_results_routes.py
"""
    idempotence = patch_anchor + """
      - name: Verify deploy patchers are source-idempotent
        run: git diff --exit-code -- template.yaml hello_world/api.py
"""
    if "Verify deploy patchers are source-idempotent" not in text:
        text = _replace_once(text, patch_anchor, idempotence, "deploy source idempotence")

    build_anchor = """      - name: Verify built MLB v3 Lambda cold-start runtime
        env:
          INQSI_MLB_LAMBDA_TASK_ROOT: .aws-sam/build/MLBV3ReadFunction
        run: python scripts/verify_api_function_mlb_v3_import.py
"""
    identity_step = build_anchor + """
      - name: Calculate canonical MLB deploy identity
        id: deploy_identity
        run: |
          TEMPLATE_SHA256=$(sha256sum template.yaml | awk '{print $1}')
          test -n "$TEMPLATE_SHA256"
          echo "template_sha256=$TEMPLATE_SHA256" >> "$GITHUB_OUTPUT"
          echo "Git SHA: $GITHUB_SHA"
          echo "Template SHA-256: $TEMPLATE_SHA256"
"""
    if "Calculate canonical MLB deploy identity" not in text:
        text = _replace_once(text, build_anchor, identity_step, "deploy identity calculation")

    deploy_start = text.index("      - name: Deploy\n")
    next_step = text.index("\n      - name:", deploy_start + 10)
    deploy_block = text[deploy_start:next_step]
    if "DEPLOY_TEMPLATE_SHA256:" not in deploy_block:
        old_env = """        env:
          ODDS_API_KEY_VALUE: ${{ secrets.ODDS_API_KEY }}
          INQSI_ADMIN_API_TOKEN_VALUE: ${{ secrets.INQSI_ADMIN_API_TOKEN }}
        run: |
"""
        new_env = """        env:
          ODDS_API_KEY_VALUE: ${{ secrets.ODDS_API_KEY }}
          INQSI_ADMIN_API_TOKEN_VALUE: ${{ secrets.INQSI_ADMIN_API_TOKEN }}
          DEPLOY_TEMPLATE_SHA256: ${{ steps.deploy_identity.outputs.template_sha256 }}
        run: |
"""
        deploy_block = _replace_once(deploy_block, old_env, new_env, "deploy identity environment")
    if 'DeployGitSha="${GITHUB_SHA}"' not in deploy_block:
        old_parameters = """              OddsApiKey="${ODDS_API_KEY_VALUE}" \\
              InqsiAdminApiToken="${INQSI_ADMIN_API_TOKEN_VALUE:-}"
"""
        new_parameters = """              OddsApiKey="${ODDS_API_KEY_VALUE}" \\
              InqsiAdminApiToken="${INQSI_ADMIN_API_TOKEN_VALUE:-}" \\
              DeployGitSha="${GITHUB_SHA}" \\
              DeployTemplateSha256="${DEPLOY_TEMPLATE_SHA256}"
"""
        deploy_block = _replace_once(deploy_block, old_parameters, new_parameters, "deploy parameter overrides")
    text = text[:deploy_start] + deploy_block + text[next_step:]

    schedule_anchor = "      - name: Verify MLB lock EventBridge schedule\n"
    verify_block = """      - name: Verify exact MLB deployment identity and schedules
        env:
          EXPECTED_TEMPLATE_SHA256: ${{ steps.deploy_identity.outputs.template_sha256 }}
        run: |
          python scripts/verify_mlb_deploy_identity.py \\
            --stack-name parlay-platform-dev \\
            --region "${{ secrets.AWS_REGION }}" \\
            --expected-git-sha "$GITHUB_SHA" \\
            --expected-template-sha256 "$EXPECTED_TEMPLATE_SHA256"

      - name: Upload MLB deployment identity proof
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: mlb-deployment-identity-${{ github.run_id }}
          path: runtime_reports/mlb_deploy_identity_latest.json
          if-no-files-found: warn

"""
    if "Verify exact MLB deployment identity and schedules" not in text:
        text = _replace_once(text, schedule_anchor, verify_block + schedule_anchor, "postdeploy identity verification")

    path.write_text(text, encoding="utf-8")


def main() -> None:
    _patch_manual_pull()
    _patch_invariants()
    _patch_deploy_workflow()
    print("Applied deterministic MLB deployment source stabilization.")


if __name__ == "__main__":
    main()
