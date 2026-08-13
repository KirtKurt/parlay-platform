from pathlib import Path

from mlb_auto.mantle_client import invoke_anthropic


def test_opus_48_uses_native_mantle_messages():
    observed = {}

    def post(**kwargs):
        observed.update(kwargs)
        return {
            'content': [{'type': 'text', 'text': '{"mlb_auto_opus_access":true}'}],
            'usage': {'input_tokens': 5, 'output_tokens': 6},
        }

    payload, usage = invoke_anthropic(
        'us.anthropic.claude-opus-4-8',
        'probe',
        max_tokens=512,
        token_provider=lambda: 'test-token',
        post=post,
    )

    assert payload == {'mlb_auto_opus_access': True}
    assert usage['endpoint_family'] == 'bedrock-mantle-anthropic'
    assert usage['foundation_model_id'] == 'anthropic.claude-opus-4-8'
    assert observed['url'].endswith('/anthropic/v1/messages')
    assert observed['json']['model'] == 'anthropic.claude-opus-4-8'


def test_opus_47_uses_adaptive_thinking_without_sampling_parameters():
    observed = {}

    def post(**kwargs):
        observed.update(kwargs)
        return {
            'content': [{'type': 'text', 'text': '```json\n{"health":"ok"}\n```'}],
            'usage': {},
        }

    payload, usage = invoke_anthropic(
        'global.anthropic.claude-opus-4-7',
        'probe',
        max_tokens=512,
        token_provider=lambda: 'test-token',
        post=post,
    )

    assert payload == {'health': 'ok'}
    assert usage['foundation_model_id'] == 'anthropic.claude-opus-4-7'
    assert observed['json']['thinking'] == {'type': 'adaptive'}
    assert observed['json']['output_config'] == {'effort': 'low'}
    assert 'temperature' not in observed['json']
    assert 'top_p' not in observed['json']
    assert 'top_k' not in observed['json']


def test_mantle_configuration_is_mlb_auto_only():
    root = Path(__file__).resolve().parents[1]
    template = (root / 'template.yaml').read_text()
    requirements = (root / 'src' / 'requirements.txt').read_text()

    assert 'bedrock-mantle:CallWithBearerToken' in template
    assert 'bedrock:CallWithBearerToken' in template
    assert 'aws-bedrock-token-generator' in requirements
    assert 'parlay-platform-tennis' not in template
    assert 'parlay-platform-dev' not in template
