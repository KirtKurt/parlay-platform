from __future__ import annotations

import json

from mlb_auto import mantle_client


class Response:
    headers = {'x-amzn-requestid': 'request-1'}

    def raise_for_status(self):
        return None

    def json(self):
        return {
            'output': [{
                'content': [{
                    'type': 'output_text',
                    'text': json.dumps({
                        'hypothesis': 'test',
                        'features': [{
                            'name': 'rd_test',
                            'op': 'product',
                            'left': 'a',
                            'right': 'b',
                            'rationale': 'test',
                        }],
                        'architecture_notes': 'test',
                    }),
                }],
            }],
            'usage': {'input_tokens': 10, 'output_tokens': 20},
        }


def test_gpt56_sol_mantle_request_is_ephemeral_and_scoped():
    seen = {}

    def post(url, **kwargs):
        seen['url'] = url
        seen.update(kwargs)
        return Response()

    proposal, usage, metadata = mantle_client.invoke(
        'prompt',
        token_provider=lambda: 'ephemeral-test-value',
        http_post=post,
    )

    assert proposal['features'][0]['name'] == 'rd_test'
    assert usage['input_tokens'] == 10
    assert metadata['llm_model_id'] == 'openai.gpt-5.6-sol'
    assert metadata['llm_api'] == 'bedrock_mantle_responses'
    assert seen['url'].endswith('/openai/v1/responses')
    assert seen['json']['model'] == 'openai.gpt-5.6-sol'
    assert seen['json']['store'] is False
