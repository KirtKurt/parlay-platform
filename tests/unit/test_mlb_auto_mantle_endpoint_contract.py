from __future__ import annotations

from mlb_auto_llm import model_gateway


def test_legacy_openai_route_uses_documented_mantle_responses_path(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(model_gateway, "_bearer_token", lambda: "token")

    def fake_post(url, headers, payload, timeout=75):
        captured["url"] = url
        captured["payload"] = payload
        return {"output_text": "OK", "usage": {}}

    monkeypatch.setattr(model_gateway, "_post_json", fake_post)
    result = model_gateway._invoke_openai(
        "openai.gpt-5.6-sol",
        "Return only OK",
        max_tokens=8,
        region="us-east-1",
    )
    assert result["text"] == "OK"
    assert captured["url"] == "https://bedrock-mantle.us-east-1.api.aws/v1/responses"
    assert "/openai/v1/" not in captured["url"]
