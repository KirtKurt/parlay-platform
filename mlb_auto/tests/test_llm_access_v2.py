from pathlib import Path

from mlb_auto import llm_access_v2 as access


def test_template_keeps_mlb_auto_isolated_and_dynamodb_keys_valid():
    text = (Path(__file__).resolve().parents[1] / 'template.yaml').read_text()
    assert 'Handler: mlb_auto.autonomous_handler_v2.handler' in text
    assert 'prod-bk5rjg4eo2pke' in text
    assert 'prod-d2ik6zgct5hxi' in text
    assert 'AttributeType: RANGE' not in text
    assert 'parlay-platform-tennis' not in text
    assert 'parlay-platform-dev' not in text


def test_sol_is_first_and_opus_models_are_next():
    models = access.configured_models()
    assert models[:3] == (
        'openai.gpt-5.6-sol',
        'anthropic.claude-opus-4-8',
        'anthropic.claude-opus-4-7',
    )
