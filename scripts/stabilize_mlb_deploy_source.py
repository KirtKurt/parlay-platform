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
    text = text.replace(
        'PULL_POLICY = "rolling_open_today_plus_tomorrow_every_15_min_date_isolated_hot_only"',
        'PULL_POLICY = "rolling_today_every_15_min_date_isolated_hot_only"',
    )
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
if "Schedule: cron(6/15 * * * ? *)" not in text or "results_pull_15m" not in text:
    violations.append('MLB result settlement is not scheduled every 15 minutes')
for obsolete in ['MLBProductionIngestVerifyDaily435Et', 'MLBProductionLockVerifyDaily556Et']:
    if obsolete in text:
        violations.append(f'obsolete fixed-UTC verifier schedule exists: {obsolete}')
for required in [
    'DeployGitSha:',
    'DeployTemplateSha256:',
    'DeployRunId:',
    'INQSI_DEPLOY_GIT_SHA: !Ref DeployGitSha',
    'INQSI_DEPLOY_TEMPLATE_SHA256: !Ref DeployTemplateSha256',
    'INQSI_DEPLOY_RUN_ID: !Ref DeployRunId',
]:
    if required not in text:
        violations.append(f'deploy identity contract missing: {required}')
"""
    if "recurring daily 1 AM ET pull gate missing" not in text:
        text = _replace_once(text, anchor, checks, "invariant stabilization checks")
    path.write_text(text, encoding="utf-8")


def _validate_deploy_workflow() -> None:
    path = ROOT / ".github" / "workflows" / "deploy.yml"
    text = path.read_text(encoding="utf-8")

    required = [
        "Prove committed MLB source is canonical",
        "Calculate canonical deployment identity",
        "Deploy exact canonical source",
        "BBS_API_KEY_VALUE: ${{ secrets.BBS_API_KEY }}",
        "Missing BBS_API_KEY",
        'parameter_overrides=(',
        '"OddsApiKey=${ODDS_API_KEY_VALUE}"',
        '"BbsApiKey=${BBS_API_KEY_VALUE}"',
        '"InqsiAdminApiToken=${INQSI_ADMIN_API_TOKEN_VALUE}"',
        '"DeployGitSha=${GITHUB_SHA}"',
        '"DeployTemplateSha256=${DEPLOY_TEMPLATE_SHA256}"',
        '"DeployRunId=${DEPLOY_RUN_ID}"',
        '--parameter-overrides "${parameter_overrides[@]}"',
        "--template-file .aws-sam/build/template.yaml",
        "Bind the verified clean SAM build to the deployment identity",
        "create_mlb_lambda_build_manifest.py",
        "runtime_reports/mlb_lambda_build_manifest_deploy.json",
        "PYTHONDONTWRITEBYTECODE",
        "Preflight Lambda artifact attestation access",
        "aws lambda get-function",
        "for attempt in range(1, 4):",
        "CREATE_COMPLETE|UPDATE_COMPLETE|UPDATE_ROLLBACK_COMPLETE|IMPORT_COMPLETE|IMPORT_ROLLBACK_COMPLETE|STACK_MISSING)",
        "Prove exact deployed Lambda identity and schedules",
        "verify_mlb_deploy_identity.py",
        "--expected-deploy-run-id",
        "steps.deploy.outputs.run_id",
        "--expected-code-manifest",
        "Run AWS-native MLB trainer and verify fresh split health",
        "invoke_mlb_trainer_with_retry.py",
        "test_mlb_trainer_invoke_retry.py",
        "verify_mlb_trainer_deploy_response.py",
        "--retry-execution-lease",
        "--deadline-seconds 1200",
        "--status-training-result /tmp/mlb-ml-v2-training.json",
        "--status-selection-capture-result /tmp/mlb-ml-v2-selection-capture.json",
        "aws_native_fixed_prospective_shadow_training",
        "aws_native_prospective_selection_capture",
        "trainingHealth",
        "selectionCaptureHealth",
        "deploymentIdentityMatches",
        "Verify The Odds API without writing an unscheduled pull",
        "writePerformed",
        "mlb-deployment-identity-${{ github.run_id }}",
        "Enforce MLB published prediction and immutable lock regressions",
        '"pytest>=8,<9"',
        "test_mlb_published_prediction_authority.py",
        "test_mlb_daily_per_game_lock.py",
        "test_mlb_storage_authority.py",
        "test_mlb_rolling_canonical_authority.py",
        "test_mlb_ml_runtime_install_explicit.py",
        "test_mlb_lambda_artifact_identity.py",
    ]
    missing = [token for token in required if token not in text]
    cold_start = text.find("Verify built MLB Lambda cold start")
    build_manifest = text.find(
        "Bind the verified clean SAM build to the deployment identity"
    )
    deploy = text.find("Deploy exact canonical source")
    if not (0 <= cold_start < build_manifest < deploy):
        missing.append("verified build manifest must be created after cold start and before deploy")
    verifier = (
        ROOT / "scripts" / "verify_mlb_trainer_deploy_response.py"
    ).read_text(encoding="utf-8")
    for token in [
        "mlb-v2-2026-07-22-future-prospective-r3",
        "2026-07-22T04:00:00+00:00",
        "MLB-ML-AWS-TRAINING-v1-persisted-cutover-selection-ledger-shadow",
    ]:
        if token not in verifier:
            missing.append(f"trainer verifier identity: {token}")
    forbidden = [
        token
        for token in [
            '      - ".github/workflows/**"',
            "/v1/pull/mlb",
            "force=true",
            "deploy_live_odds_smoke",
            "Smoke test live MLB Odds API pull and storage",
            "SPORTSDATAIO_API_KEY_VALUE",
            "SportsDataIoApiKey",
            "secrets.BS_API_KEY",
            "UPDATE_ROLLBACK_COMPLETE|ROLLBACK_COMPLETE",
        ]
        if token in text
    ]
    if missing or forbidden:
        details = []
        if missing:
            details.append("missing deploy contract: " + ", ".join(missing))
        if forbidden:
            details.append("forbidden polluting deploy behavior: " + ", ".join(forbidden))
        raise RuntimeError("Unsafe MLB deploy workflow; " + "; ".join(details))


def main() -> None:
    _patch_manual_pull()
    _patch_invariants()
    _validate_deploy_workflow()
    print("MLB deployment source is canonical, identity-bound, and non-polluting.")


if __name__ == "__main__":
    main()
