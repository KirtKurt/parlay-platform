from mlb_auto import provider_patch


class FakeRuntime:
    def __init__(self, successes):
        self.successes = set(successes)
        self.calls = []

    def converse(self, **kwargs):
        model_id = kwargs['modelId']
        self.calls.append(kwargs)
        if model_id not in self.successes:
            raise RuntimeError(f'unavailable:{model_id}')
        return {
            'output': {
                'message': {
                    'content': [{
                        'text': '{"mlb_auto_opus_access":true}',
                    }],
                },
            },
            'usage': {'inputTokens': 4, 'outputTokens': 4},
        }


def test_opus_identifier_order_tries_foundation_then_global_then_us():
    assert provider_patch._model_candidates(
        'us.anthropic.claude-opus-4-8'
    ) == (
        'anthropic.claude-opus-4-8',
        'global.anthropic.claude-opus-4-8',
        'us.anthropic.claude-opus-4-8',
    )


def test_access_probe_accepts_foundation_identifier_when_us_profile_is_blocked():
    runtime = FakeRuntime({'anthropic.claude-opus-4-8'})
    result = provider_patch._patched_access_probe(
        runtime,
        'us.anthropic.claude-opus-4-8',
    )

    assert result['ok'] is True
    assert result['response_confirmed'] is True
    assert result['resolved_model_id'] == 'anthropic.claude-opus-4-8'
    assert runtime.calls[0]['modelId'] == 'anthropic.claude-opus-4-8'


def test_opus_47_keeps_adaptive_thinking_on_resolved_identifier():
    runtime = FakeRuntime({'global.anthropic.claude-opus-4-7'})
    result = provider_patch._patched_access_probe(
        runtime,
        'us.anthropic.claude-opus-4-7',
    )

    assert result['ok'] is True
    assert result['resolved_model_id'] == 'global.anthropic.claude-opus-4-7'
    successful_call = runtime.calls[-1]
    assert successful_call['additionalModelRequestFields'] == {
        'thinking': {'type': 'adaptive'},
        'output_config': {'effort': 'low'},
    }


def test_non_anthropic_models_are_not_rewritten():
    assert provider_patch._model_candidates('amazon.nova-pro-v1:0') == (
        'amazon.nova-pro-v1:0',
    )
