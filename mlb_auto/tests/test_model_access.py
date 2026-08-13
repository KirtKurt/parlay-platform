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


class FakeStore:
    state = {}

    def __init__(self):
        type(self).state = {}

    def get_state(self, key):
        return dict(type(self).state.get(key) or {})

    def put_state(self, key, changes):
        current = dict(type(self).state.get(key) or {})
        current.update(dict(changes or {}))
        type(self).state[key] = current

    def query_training_examples(self, limit=5000):
        del limit
        return [
            {
                'SK': f'GAME#{index:03d}',
                'commence_time': f'2026-01-{(index % 28) + 1:02d}T00:00:00Z',
                'features': {
                    'market_home_probability': 0.50 + (index % 5) / 100,
                    'market_disagreement': (index % 7) / 100,
                },
            }
            for index in range(max(llm_rd.MIN_EXAMPLES, 80))
        ]

    def archive_json(self, key, payload):
        del key, payload


def test_provider_quota_state_is_truthful_and_non_blocking(monkeypatch):
    errors = [
        'us.amazon.nova-premier-v1:0:ThrottlingException:Too many tokens per day',
        'us.amazon.nova-pro-v1:0:ServiceUnavailableException:temporarily unavailable',
    ]

    def unavailable(*_args, **_kwargs):
        raise llm_rd.ModelProviderUnavailableError(errors)

    monkeypatch.setattr(llm_rd, '_invoke', unavailable)
    result = llm_rd.run_research(Store=FakeStore, force=True)
    state = FakeStore.state[llm_rd.STATE_KEY]
    status = llm_rd.status_payload(Store=FakeStore)

    assert result['ok'] is True
    assert result['generated'] is False
    assert result['reason'] == 'MODEL_PROVIDER_UNAVAILABLE'
    assert result['degraded'] is True
    assert result['provider_available'] is False
    assert result['provider_invocation_ok'] is False
    assert result['retryable'] is True
    assert state['last_run_ok'] is True
    assert state['last_invocation_ok'] is False
    assert state['last_result'] == 'MODEL_PROVIDER_UNAVAILABLE'
    assert state['provider_available'] is False
    assert state['degraded'] is True
    assert state['retryable'] is True
    assert status['last_run_ok'] is True
    assert status['last_invocation_ok'] is False
    assert status['last_result'] == 'MODEL_PROVIDER_UNAVAILABLE'
    assert status['provider_available'] is False
    assert status['degraded'] is True
    assert status['retryable'] is True


def test_configuration_defect_still_fails_closed(monkeypatch):
    def invalid_response(*_args, **_kwargs):
        raise ValueError('INVALID_RD_FEATURE_SPEC')

    monkeypatch.setattr(llm_rd, '_invoke', invalid_response)
    result = llm_rd.run_research(Store=FakeStore, force=True)
    state = FakeStore.state[llm_rd.STATE_KEY]

    assert result['ok'] is False
    assert result['reason'] == 'LLM_RD_FAILED'
    assert result['degraded'] is False
    assert state['last_run_ok'] is False
    assert state['last_invocation_ok'] is False
    assert state['last_result'] == 'LLM_RD_FAILED'
    assert state['provider_available'] is None
    assert state['degraded'] is False
    assert state['retryable'] is False
