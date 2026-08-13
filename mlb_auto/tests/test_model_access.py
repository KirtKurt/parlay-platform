from pathlib import Path


def test_mlb_auto_model_chain_uses_configured_fallbacks_only():
    root = Path(__file__).resolve().parents[1]
    template = (root / 'template.yaml').read_text()
    handler = (root / 'src' / 'mlb_auto' / 'autonomous_handler.py').read_text()
    workflow = (root.parent / '.github' / 'workflows' / 'deploy-mlb-auto-v1.yml').read_text()

    for unavailable in ('claude-opus-4-8', 'claude-opus-4-7'):
        assert unavailable not in template
        assert unavailable not in handler
        assert unavailable not in workflow

    assert '_direct_opus_ids' not in handler
    assert 'us.anthropic.claude-opus-4-6-v1' in template
    assert 'us.anthropic.claude-sonnet-4-6' in template
    assert 'amazon.nova-pro-v1:0' in template
    assert 'amazon.nova-micro-v1:0' in template
