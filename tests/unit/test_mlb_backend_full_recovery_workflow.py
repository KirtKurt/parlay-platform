from pathlib import Path


WORKFLOW = Path('.github/workflows/mlb-backend-full-recovery.yml')


def test_backend_recovery_binds_clean_build_to_deployment_identity():
    source = WORKFLOW.read_text(encoding='utf-8')

    create_manifest = (
        'python scripts/create_mlb_lambda_build_manifest.py'
    )
    manifest_path = (
        'runtime_reports/mlb_backend_recovery_code_manifest.json'
    )
    deploy = 'sam deploy \\\n'
    verify = 'python scripts/verify_mlb_deploy_identity.py'

    assert create_manifest in source
    assert '--build-root .aws-sam-recovery' in source
    assert '--expected-git-sha "$GITHUB_SHA"' in source
    assert '--expected-template-sha256 "$template_sha"' in source
    assert f'--output {manifest_path}' in source
    assert f'--expected-code-manifest {manifest_path}' in source
    assert source.index(create_manifest) < source.index(deploy)
    assert source.index(deploy) < source.index(verify)


def test_backend_recovery_preserves_manifest_in_durable_proof():
    source = WORKFLOW.read_text(encoding='utf-8')

    assert (
        "code_manifest=json.loads((root/'mlb_backend_recovery_code_manifest.json').read_text())"
        in source
    )
    assert "'expectedCodeManifest':code_manifest" in source
    assert (
        'runtime_reports/mlb_backend_recovery_code_manifest.json'
        in source.split('path: |', 2)[-1]
    )


def test_manifest_contract_changes_trigger_backend_recovery():
    source = WORKFLOW.read_text(encoding='utf-8')

    for path in (
        'scripts/create_mlb_lambda_build_manifest.py',
        'scripts/mlb_lambda_artifact_identity.py',
    ):
        assert source.count(f"- '{path}'") == 2
