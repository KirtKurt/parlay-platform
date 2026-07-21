#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "template.yaml"
PARAMETER_NAME = "SportsDataIoApiKey"
APPROVED_FUNCTIONS = (
    "ApiFunction",
    "MLBAuditedPullFunction",
    "MLBSignalApiFunction",
)
ENVIRONMENT = (
    ("SPORTSDATAIO_API_KEY", f"!Ref {PARAMETER_NAME}"),
    ("INQSI_MLB_USE_SPORTSDATAIO_FUNDAMENTALS", "'true'"),
    ("INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE", "'false'"),
    ("SPORTSDATAIO_TIMEOUT_SECONDS", "'25'"),
)


def _resource_span(text: str, name: str) -> tuple[int, int]:
    start_match = re.search(rf"(?m)^  {re.escape(name)}:\n", text)
    if start_match is None:
        raise RuntimeError(f"missing SAM resource: {name}")
    next_match = re.search(r"(?m)^  [A-Za-z0-9]+:\n", text[start_match.end() :])
    end = len(text) if next_match is None else start_match.end() + next_match.start()
    return start_match.start(), end


def _remove_legacy_wiring(text: str) -> str:
    text = text.replace("SportsDataIOApiKey", PARAMETER_NAME)
    keys = "|".join(re.escape(key) for key, _ in ENVIRONMENT)
    return re.sub(rf"(?m)^\s+(?:{keys}):[^\n]*\n", "", text)


def _ensure_parameter(text: str) -> str:
    marker = f"  {PARAMETER_NAME}:\n"
    if marker in text:
        return text
    insertion = (
        f"  {PARAMETER_NAME}:\n"
        "    Type: String\n"
        "    NoEcho: true\n"
        '    Default: ""\n'
        "    Description: Optional SportsDataIO MLB fundamentals feed key\n"
    )
    anchor = "  InqsiAdminApiToken:\n"
    if anchor not in text:
        raise RuntimeError("missing InqsiAdminApiToken parameter anchor")
    return text.replace(anchor, insertion + anchor, 1)


def _wire_function(text: str, name: str) -> str:
    start, end = _resource_span(text, name)
    block = text[start:end]
    variables = "".join(f"          {key}: {value}\n" for key, value in ENVIRONMENT)
    variables_anchor = "        Variables:\n"
    if variables_anchor in block:
        block = block.replace(variables_anchor, variables_anchor + variables, 1)
    else:
        properties_anchor = "    Properties:\n"
        if properties_anchor not in block:
            raise RuntimeError(f"missing Properties block for {name}")
        block = block.replace(
            properties_anchor,
            properties_anchor + "      Environment:\n        Variables:\n" + variables,
            1,
        )
    return text[:start] + block + text[end:]


def patch_template(text: str) -> str:
    text = _ensure_parameter(_remove_legacy_wiring(text))
    for name in APPROVED_FUNCTIONS:
        text = _wire_function(text, name)
    return text


def main() -> int:
    original = TEMPLATE_PATH.read_text(encoding="utf-8")
    patched = patch_template(original)
    TEMPLATE_PATH.write_text(patched, encoding="utf-8")
    print(
        "SportsDataIO SAM wiring is canonical and scoped to "
        + ", ".join(APPROVED_FUNCTIONS)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
