from pathlib import Path

from mlb_auto.mantle_client import invoke_anthropic


class RuntimeSuccess:
    def __init__(self):
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {
            'output': {
                'message': {
                    'content': [{'text': '{"health":"ok"}'}],
                },
            },
            'usage': {'inputTokens': 5, 'outputTokens': 6},
        }


class RuntimeFailure:
    def __init__(self):
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError('runtime unavailable')


def test_configured_claude_profile_uses_bedrock_runtime_first():
    runtime = RuntimeSuccess()

    def unexpected_post(**_kwargs):
        raise AssertionError('Mantle must not run after Runtime succeeds')

    payload, usage = invoke_anthropic(
        'us.anthropic.claude-sonnet-4-6',
        'probe',
        max_tokens=512,
        runtime_client=runtime,
        token_provider=lambda: 'test-token',
        post=unexpected_post,
    )

    assert payload == {'health': 'ok'}
    assert usage['endpoint_family'] == 'bedrock-runtime-converse'
    assert usage['runtime_model_id'] == 'us.anthropic.claude-sonnet-4-6'
    assert runtime.calls[0]['modelId'] == 'us.anthropic.claude-sonnet-4-6'


def test_runtime_failure_uses_mantle_alias_for_sonnet_46():
    runtime = RuntimeFailure()
    observed = {}

    def post(**kwargs):
        observed.update(kwargs)
        return {
            'content': [{'type': 'text', 'text': '{"health":"fallback"}'}],
            'usage': {'input_tokens': 5, 'output_tokens': 6},
        }

    payload, usage = invoke_anthropic(
        'us.anthropic.claude-sonnet-4-6',
        'probe',
        max_tokens=512,
        runtime_client=runtime,
        token_provider=lambda: 'test-token',
        post=post,
    )

    assert payload == {'health': 'fallback'}
    assert usage['endpoint_family'] == 'bedrock-mantle-anthropic'
    assert usage['foundation_model_id'] == 'anthropic.claude-sonnet-4-6'
    assert usage['mantle_model_id'] == 'anthropic.claude-sonnet-4-6-v1'
    assert observed['url'].endswith('/anthropic/v1/messages')
    assert observed['json']['model'] == 'anthropic.claude-sonnet-4-6-v1'


def test_opus_46_mantle_model_id_is_preserved():
    observed = {}

    def post(**kwargs):
        observed.update(kwargs)
        return {
            'content': [{'type': 'text', 'text': '```json\n{"health":"ok"}\n```'}],
            'usage': {},
        }

    payload, usage = invoke_anthropic(
        'us.anthropic.claude-opus-4-6-v1',
        'probe',
        max_tokens=512,
        token_provider=lambda: 'test-token',
        post=post,
    )

    assert payload == {'health': 'ok'}
    assert usage['foundation_model_id'] == 'anthropic.claude-opus-4-6-v1'
    assert usage['mantle_model_id'] == 'anthropic.claude-opus-4-6-v1'
    assert observed['json']['model'] == 'anthropic.claude-opus-4-6-v1'


def test_mantle_configuration_is_mlb_auto_only():
    root = Path(__file__).resolve().parents[1]
    template = (root / 'template.yaml').read_text()
    requirements = (root / 'src' / 'requirements.txt').read_text()

    assert 'bedrock-mantle:CallWithBearerToken' in template
    assert 'bedrock:CallWithBearerToken' in template
    assert 'aws-bedrock-token-generator' in requirements
    assert 'parlay-platform-tennis' not in template
    assert 'parlay-platform-dev' not in template
