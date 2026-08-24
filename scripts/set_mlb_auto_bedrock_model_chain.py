#!/usr/bin/env python3
"""Install the live Bedrock profile chain in the isolated MLB AUTO stack."""

from __future__ import annotations

from pathlib import Path


LIVE_MODELS = (
    "us.anthropic.claude-sonnet-4-20250514-v1:0,"
    "us.meta.llama3-3-70b-instruct-v1:0,"
    "meta.llama3-3-70b-instruct-v1:0,"
    "mistral.mistral-large-2402-v1:0,"
    "mistral.mistral-small-2402-v1:0,"
    "cohere.command-r-v1:0,"
    "cohere.command-r-plus-v1:0,"
    "openai.gpt-5.6-sol,"
    "anthropic.claude-opus-4-8,"
    "anthropic.claude-opus-4-7,"
    "anthropic.claude-opus-4-6-v1,"
    "anthropic.claude-sonnet-4-6-v1,"
    "us.amazon.nova-2-lite-v1:0,"
    "global.amazon.nova-2-lite-v1:0,"
    "us.amazon.nova-pro-v1:0,"
    "us.amazon.nova-lite-v1:0,"
    "us.amazon.nova-micro-v1:0"
)

OLD_TEMPLATE_MODELS = (
    "openai.gpt-5.6-sol,anthropic.claude-opus-4-8,"
    "anthropic.claude-opus-4-7,anthropic.claude-opus-4-6-v1,"
    "anthropic.claude-sonnet-4-6-v1,us.amazon.nova-2-lite-v1:0,"
    "global.amazon.nova-2-lite-v1:0,us.amazon.nova-pro-v1:0,"
    "us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0"
)
OLD_WORKFLOW_MODELS = (
    "us.amazon.nova-2-lite-v1:0,global.amazon.nova-2-lite-v1:0,"
    "us.amazon.nova-pro-v1:0,us.amazon.nova-lite-v1:0,"
    "us.amazon.nova-micro-v1:0"
)


def _update(path: Path, replacements: list[tuple[str, str]]) -> bool:
    source = path.read_text(encoding="utf-8")
    original = source
    for old, new in replacements:
        if old in source:
            source = source.replace(old, new)
    if source != original:
        path.write_text(source, encoding="utf-8")
        return True
    return False


def main() -> int:
    template_changed = _update(
        Path("mlb-auto-llm-template.yaml"),
        [(OLD_TEMPLATE_MODELS, LIVE_MODELS), (OLD_WORKFLOW_MODELS, LIVE_MODELS)],
    )

    workflow = Path(".github/workflows/deploy-mlb-auto-llm.yml")
    source = workflow.read_text(encoding="utf-8")
    original = source
    source = source.replace(OLD_WORKFLOW_MODELS, LIVE_MODELS)
    old_compile = (
        "python -m py_compile mlb_auto_llm/handler.py "
        "mlb_auto_llm/orchestrator.py mlb_auto_llm/bedrock_smoke.py"
    )
    new_compile = (
        old_compile
        + " mlb_auto_llm/orchestrator_v2.py mlb_auto_llm/model_gateway.py"
    )
    if new_compile not in source:
        if source.count(old_compile) != 1:
            raise RuntimeError("deployment compile marker missing or ambiguous")
        source = source.replace(old_compile, new_compile, 1)
    grep_anchor = "          grep -q 'teamRecentForm' mlb_auto_llm/orchestrator.py\n"
    validation = (
        grep_anchor
        + "          grep -q 'orchestrator_v2.lambda_handler' mlb-auto-llm-template.yaml\n"
        + "          grep -q 'us.anthropic.claude-sonnet-4-20250514-v1:0' mlb-auto-llm-template.yaml\n"
        + "          grep -q 'bedrock-mantle:CreateInference' mlb-auto-llm-template.yaml\n"
    )
    if "grep -q 'orchestrator_v2.lambda_handler'" not in source:
        if source.count(grep_anchor) != 1:
            raise RuntimeError("deployment validation marker missing or ambiguous")
        source = source.replace(grep_anchor, validation, 1)
    workflow_changed = False
    if source != original:
        workflow.write_text(source, encoding="utf-8")
        workflow_changed = True

    print(
        {
            "templateChanged": template_changed,
            "workflowChanged": workflow_changed,
            "modelCount": len(LIVE_MODELS.split(",")),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
