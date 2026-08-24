#!/usr/bin/env python3
"""Install the capability-first Bedrock gateway into the isolated MLB AUTO stack."""

from __future__ import annotations

from pathlib import Path


MODEL_LIST = (
    "openai.gpt-5.6-sol,anthropic.claude-opus-4-8,anthropic.claude-opus-4-7,"
    "anthropic.claude-opus-4-6-v1,anthropic.claude-sonnet-4-6-v1,"
    "us.amazon.nova-2-lite-v1:0,global.amazon.nova-2-lite-v1:0,"
    "us.amazon.nova-pro-v1:0,us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0"
)
OLD_MODEL_LIST = (
    "us.amazon.nova-2-lite-v1:0,global.amazon.nova-2-lite-v1:0,"
    "us.amazon.nova-pro-v1:0,us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0"
)


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def _write_if_changed(path: Path, value: str) -> bool:
    original = path.read_text(encoding="utf-8")
    if original == value:
        return False
    path.write_text(value, encoding="utf-8")
    return True


def repair_handler() -> bool:
    path = Path("mlb_auto_llm/handler.py")
    source = path.read_text(encoding="utf-8")
    original = source

    if "from model_gateway import invoke_chain_text" not in source:
        source = _replace_once(
            source,
            "import boto3\n",
            "import boto3\n\nfrom model_gateway import invoke_chain_text\n",
            "model gateway import",
        )

    old = '''    errors: List[Dict[str, str]] = []
    for model_id in BEDROCK_MODELS:
        try:
            response = BEDROCK.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 900, "temperature": 0.15, "topP": 0.9},
            )
            blocks = (((response.get("output") or {}).get("message") or {}).get("content") or [])
            text = "\\n".join(str(block.get("text") or "") for block in blocks if isinstance(block, dict))
            parsed = _extract_json(text)
            winner = str(parsed.get("winner") or "")
            if winner not in {home, away}:
                raise RuntimeError("LLM_WINNER_NOT_EXACT_TEAM")
            loser = away if winner == home else home
            probability = float(parsed.get("probability") or 0.5)
            probability = min(max(probability, 0.50), 0.95)
            return {
                "ok": True,
                "authority": "BEDROCK_LLM",
                "modelId": model_id,
                "winner": winner,
                "loser": loser,
                "probability": round(probability, 6),
                "confidence": str(parsed.get("confidence") or "MODEL"),
                "rationale": parsed.get("rationale"),
                "sourceWeights": parsed.get("source_weights") or {},
                "disagreements": parsed.get("disagreements") or [],
                "errorsBeforeSuccess": errors,
            }
        except Exception as exc:
            errors.append({"modelId": model_id, "error": type(exc).__name__})
'''
    new = '''    result = invoke_chain_text(
        prompt,
        BEDROCK_MODELS,
        client=BEDROCK,
        max_tokens=900,
        temperature=0.15,
        top_p=0.9,
    )
    errors: List[Dict[str, str]] = list(
        result.get("errorsBeforeSuccess") or result.get("errors") or []
    )
    if result.get("ok") is True:
        parsed = _extract_json(str(result.get("text") or ""))
        winner = str(parsed.get("winner") or "")
        if winner not in {home, away}:
            errors.append(
                {
                    "modelId": str(result.get("modelId") or ""),
                    "endpointFamily": str(result.get("endpointFamily") or ""),
                    "errorCode": "LLM_WINNER_NOT_EXACT_TEAM",
                    "message": "The model response did not name one exact scheduled team.",
                }
            )
        else:
            loser = away if winner == home else home
            probability = float(parsed.get("probability") or 0.5)
            probability = min(max(probability, 0.50), 0.95)
            return {
                "ok": True,
                "authority": "BEDROCK_LLM",
                "modelId": result.get("modelId"),
                "endpointFamily": result.get("endpointFamily"),
                "winner": winner,
                "loser": loser,
                "probability": round(probability, 6),
                "confidence": str(parsed.get("confidence") or "MODEL"),
                "rationale": parsed.get("rationale"),
                "sourceWeights": parsed.get("source_weights") or {},
                "disagreements": parsed.get("disagreements") or [],
                "usage": result.get("usage") or {},
                "errorsBeforeSuccess": errors,
            }
'''
    if "result = invoke_chain_text(" not in source:
        source = _replace_once(source, old, new, "Bedrock decision loop")

    default_old = (
        '"us.amazon.nova-2-lite-v1:0,us.amazon.nova-lite-v1:0,'
        'us.amazon.nova-micro-v1:0"'
    )
    default_new = f'"{MODEL_LIST}"'
    if default_old in source:
        source = source.replace(default_old, default_new, 1)

    return _write_if_changed(path, source) if source != original else False


def repair_template() -> bool:
    path = Path("mlb-auto-llm-template.yaml")
    source = path.read_text(encoding="utf-8")
    original = source
    source = source.replace(OLD_MODEL_LIST, MODEL_LIST)
    action_block = '''                - bedrock:InvokeModel
                - bedrock:InvokeModelWithResponseStream
'''
    replacement = action_block + '''                - bedrock:CallWithBearerToken
                - bedrock-mantle:CallWithBearerToken
'''
    if source.count("bedrock:CallWithBearerToken") == 0:
        count = source.count(action_block)
        if count != 2:
            raise RuntimeError(f"Bedrock policy blocks: expected two, found {count}")
        source = source.replace(action_block, replacement)
    return _write_if_changed(path, source) if source != original else False


def repair_workflow() -> bool:
    path = Path(".github/workflows/deploy-mlb-auto-llm.yml")
    source = path.read_text(encoding="utf-8")
    original = source
    source = source.replace(OLD_MODEL_LIST, MODEL_LIST)
    source = source.replace(
        "run: python -m pip install --quiet 'boto3>=1.34,<2'",
        "run: python -m pip install --quiet 'boto3>=1.34,<2' 'pytest>=8,<9'",
    )
    compile_old = (
        "python -m py_compile mlb_auto_llm/handler.py "
        "mlb_auto_llm/orchestrator.py mlb_auto_llm/bedrock_smoke.py"
    )
    compile_new = compile_old + " mlb_auto_llm/model_gateway.py"
    if compile_new not in source:
        source = _replace_once(source, compile_old, compile_new, "gateway compilation")
    test_anchor = "          grep -q 'teamRecentForm' mlb_auto_llm/orchestrator.py\n"
    test_line = (
        test_anchor
        + "          python -m pytest -q tests/unit/test_mlb_auto_model_gateway.py\n"
    )
    if "test_mlb_auto_model_gateway.py" not in source:
        source = _replace_once(source, test_anchor, test_line, "gateway unit test")
    return _write_if_changed(path, source) if source != original else False


def main() -> int:
    changed = {
        "handler": repair_handler(),
        "template": repair_template(),
        "workflow": repair_workflow(),
    }
    print(changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
