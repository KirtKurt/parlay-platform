from __future__ import annotations

from mlb_auto_llm import model_gateway


class _Runtime:
    def converse(self, **kwargs):
        assert kwargs["modelId"] == "us.amazon.nova-lite-v1:0"
        return {
            "output": {"message": {"content": [{"text": "OK"}]}},
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }


def test_converse_path_returns_nonempty_text() -> None:
    result = model_gateway.invoke_text(
        "us.amazon.nova-lite-v1:0",
        "Return only OK",
        client=_Runtime(),
        max_tokens=16,
    )

    assert result["text"] == "OK"
    assert result["endpointFamily"] == "bedrock-runtime-converse"


def test_chain_falls_through_to_next_model(monkeypatch) -> None:
    attempts = []

    def fake_invoke(model_id, prompt, **kwargs):
        attempts.append(model_id)
        if model_id == "openai.gpt-5.6-sol":
            raise RuntimeError("quota")
        return {
            "text": "OK",
            "usage": {},
            "endpointFamily": "bedrock-mantle-anthropic",
        }

    monkeypatch.setattr(model_gateway, "_PREFERRED_MODEL", None)
    monkeypatch.setattr(model_gateway, "invoke_text", fake_invoke)
    result = model_gateway.invoke_chain_text(
        "Return only OK",
        ["openai.gpt-5.6-sol", "anthropic.claude-sonnet-4-6-v1"],
    )

    assert result["ok"] is True
    assert result["modelId"] == "anthropic.claude-sonnet-4-6-v1"
    assert attempts == ["openai.gpt-5.6-sol", "anthropic.claude-sonnet-4-6-v1"]
    assert len(result["errorsBeforeSuccess"]) == 1


def test_preferred_success_is_reused_first(monkeypatch) -> None:
    attempts = []

    def fake_invoke(model_id, prompt, **kwargs):
        attempts.append(model_id)
        return {
            "text": "OK",
            "usage": {},
            "endpointFamily": "bedrock-runtime-converse",
        }

    monkeypatch.setattr(model_gateway, "_PREFERRED_MODEL", "second")
    monkeypatch.setattr(model_gateway, "invoke_text", fake_invoke)
    result = model_gateway.invoke_chain_text("x", ["first", "second"])

    assert result["ok"] is True
    assert result["modelId"] == "second"
    assert attempts == ["second"]
