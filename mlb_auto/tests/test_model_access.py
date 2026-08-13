from __future__ import annotations

import json

from mlb_auto.model_access import OPUS_MODELS, ensure_opus_access


class FakeAwsError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.response = {'Error': {'Code': code, 'Message': message}}


class FakeStore:
    def __init__(self):
        self.states = []

    def put_state(self, name, payload):
        self.states.append((name, payload))


class FakeBedrock:
    def __init__(self, *, use_case_present=True):
        self.use_case_present = use_case_present
        self.submitted_form = None
        self.agreements = set()
        self.offer_calls = []

    def get_use_case_for_model_access(self):
        if not self.use_case_present:
            raise FakeAwsError('ResourceNotFoundException', 'missing')
        return {'formData': b'present'}

    def put_use_case_for_model_access(self, *, formData):
        self.submitted_form = json.loads(formData)
        self.use_case_present = True
        return {}

    def get_foundation_model_availability(self, *, modelId):
        ready = modelId in self.agreements
        return {
            'modelId': modelId,
            'agreementAvailability': {'status': 'AVAILABLE' if ready else 'NOT_AVAILABLE'},
            'authorizationStatus': 'AUTHORIZED' if ready else 'NOT_AUTHORIZED',
            'entitlementAvailability': 'AVAILABLE' if ready else 'NOT_AVAILABLE',
            'regionAvailability': 'AVAILABLE',
            'ResponseMetadata': {'RequestId': 'ignored'},
        }

    def list_foundation_model_agreement_offers(self, *, modelId, offerType='PUBLIC'):
        self.offer_calls.append((modelId, offerType))
        return {'offers': [{'offerId': f'offer-{modelId}', 'offerToken': f'token-{modelId}'}]}

    def create_foundation_model_agreement(self, *, modelId, offerToken):
        assert offerToken == f'token-{modelId}'
        self.agreements.add(modelId)
        return {'modelId': modelId}


class FakeRuntime:
    def __init__(self, denied=None):
        self.calls = []
        self.denied = set(denied or [])

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs['modelId'] in self.denied:
            raise FakeAwsError('AccessDeniedException', 'not available for account')
        return {
            'output': {
                'message': {
                    'content': [{'text': '{"mlb_auto_opus_access":true}'}],
                },
            },
            'usage': {'inputTokens': 8, 'outputTokens': 6},
            'ResponseMetadata': {'RequestId': f"request-{len(self.calls)}"},
        }


def test_exact_opus_models_and_marketplace_products_are_scoped_to_mlb_auto():
    assert [row['runtime_model_id'] for row in OPUS_MODELS] == [
        'us.anthropic.claude-opus-4-8',
        'us.anthropic.claude-opus-4-7',
    ]
    assert [row['runtime_model_ids'][0] for row in OPUS_MODELS] == [
        'anthropic.claude-opus-4-8',
        'anthropic.claude-opus-4-7',
    ]
    assert [row['marketplace_product_id'] for row in OPUS_MODELS] == [
        'prod-bk5rjg4eo2pke',
        'prod-d2ik6zgct5hxi',
    ]


def test_enablement_creates_agreements_and_uses_documented_direct_ids_first():
    store = FakeStore()
    bedrock = FakeBedrock()
    runtime = FakeRuntime()

    result = ensure_opus_access(
        Store=lambda: store,
        bedrock_client=bedrock,
        runtime_client=runtime,
        sleep=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )

    assert result['ok'] is True
    assert result['scope'] == 'mlb_auto_only'
    assert result['all_models_ready'] is True
    assert result['all_invocations_ok'] is True
    assert result['access_policy'] == 'OPUS_4_8_AND_4_7_REQUIRED'
    assert result['runtime_id_policy'] == 'DIRECT_FOUNDATION_THEN_US_THEN_GLOBAL'
    assert result['selected_runtime_model_ids'] == [
        'anthropic.claude-opus-4-8',
        'anthropic.claude-opus-4-7',
    ]
    assert bedrock.agreements == {
        'anthropic.claude-opus-4-8',
        'anthropic.claude-opus-4-7',
    }
    assert [call['modelId'] for call in runtime.calls] == [
        'anthropic.claude-opus-4-8',
        'anthropic.claude-opus-4-7',
    ]
    assert 'additionalModelRequestFields' not in runtime.calls[0]
    assert runtime.calls[1]['additionalModelRequestFields'] == {
        'thinking': {'type': 'adaptive'},
        'output_config': {'effort': 'low'},
    }
    assert store.states[-1][0] == 'llm_model_access'
    assert store.states[-1][1]['last_attempt_ok'] is True


def test_runtime_probe_falls_back_from_direct_to_us_profile_when_needed():
    store = FakeStore()
    bedrock = FakeBedrock()
    runtime = FakeRuntime(denied={
        'anthropic.claude-opus-4-8',
        'anthropic.claude-opus-4-7',
    })

    result = ensure_opus_access(
        Store=lambda: store,
        bedrock_client=bedrock,
        runtime_client=runtime,
        sleep=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )

    assert result['ok'] is True
    assert result['selected_runtime_model_ids'] == [
        'us.anthropic.claude-opus-4-8',
        'us.anthropic.claude-opus-4-7',
    ]
    assert [call['modelId'] for call in runtime.calls] == [
        'anthropic.claude-opus-4-8',
        'us.anthropic.claude-opus-4-8',
        'anthropic.claude-opus-4-7',
        'us.anthropic.claude-opus-4-7',
    ]


def test_missing_anthropic_use_case_is_submitted_as_internal_mlb_auto_rd():
    store = FakeStore()
    bedrock = FakeBedrock(use_case_present=False)
    runtime = FakeRuntime()

    result = ensure_opus_access(
        Store=lambda: store,
        bedrock_client=bedrock,
        runtime_client=runtime,
        sleep=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )

    assert result['ok'] is True
    assert result['use_case']['submitted'] is True
    assert bedrock.submitted_form['companyName'] == 'Inqis'
    assert bedrock.submitted_form['intendedUsers'] == '0'
    assert 'MLB Auto' in bedrock.submitted_form['useCases']
    assert 'formData' not in result['use_case']
