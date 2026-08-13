from pathlib import Path

from mlb_auto import llm_rd


def test_account_safe_model_policy_excludes_unavailable_claude_and_uses_geo_profiles():
    models, excluded = llm_rd._account_safe_models((
        'us.anthropic.claude-opus-4-8-v1',
        'us.anthropic.claude-opus-4-7-v1',
        'us.anthropic.claude-opus-4-6-v1',
        'amazon.nova-premier-v1:0',
        'amazon.nova-pro-v1:0',
        'amazon.nova-lite-v1:0',
        'amazon.nova-micro-v1:0',
    ))

    assert models == (
        'us.amazon.nova-premier-v1:0',
        'us.amazon.nova-pro-v1:0',
        'us.amazon.nova-lite-v1:0',
        'us.amazon.nova-micro-v1:0',
    )
    assert excluded == (
        'us.anthropic.claude-opus-4-8-v1',
        'us.anthropic.claude-opus-4-7-v1',
        'us.anthropic.claude-opus-4-6-v1',
    )
    assert all('anthropic.claude-' not in model_id for model_id in llm_rd.MODEL_IDS)
    assert llm_rd.ACCOUNT_MODEL_POLICY == 'ACCOUNT_SAFE_AMAZON_GEO_FALLBACK_V1'


def test_runtime_does_not_receive_account_enrollment_permissions():
    root = Path(__file__).resolve().parents[1]
    template = (root / 'template.yaml').read_text()
    handler = (root / 'src' / 'mlb_auto' / 'autonomous_handler.py').read_text()
    workflow = (root.parent / '.github' / 'workflows' / 'deploy-mlb-auto-v1.yml').read_text()

    for unavailable in ('claude-opus-4-8', 'claude-opus-4-7'):
        assert unavailable not in handler
        assert unavailable not in workflow

    assert '_direct_opus_ids' not in handler
    assert 'bedrock:PutUseCaseForModelAccess' not in template
    assert 'bedrock:CreateFoundationModelAgreement' not in template
    assert 'aws-marketplace:Subscribe' not in template
    assert 'aws-marketplace:Unsubscribe' not in template
