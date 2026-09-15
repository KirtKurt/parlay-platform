#!/usr/bin/env python3
"""Authorize the isolated three-source MLB AUTO Lambda without weakening root MLB.

The canonical/root MLB stack remains provider-neutral.  The separately deployed
MLB AUTO LLM function is allowed to carry the Big Balls Sports Data Pro secret
only when its full isolated identity contract is present and no root authority
tables or artifacts are attached.
"""

from __future__ import annotations

import ast
from pathlib import Path


VERIFIER = Path("scripts/verify_mlb_deploy_identity.py")

_HARDENED_STRING_CONSTANTS = {
    "ISOLATED_THREE_SOURCE_FUNCTION_NAME_TOKEN": (
        "PARLAYPLATFORMMLBAUTOLLMMLBAUTOLLMFUNCTION"
    ),
    "ISOLATED_THREE_SOURCE_HANDLER": "orchestrator.lambda_handler",
    "ISOLATED_THREE_SOURCE_HANDLERS": (
        "orchestrator.lambda_handler",
        "orchestrator_v2.lambda_handler",
        "orchestrator_v3.lambda_handler",
    ),
    "ISOLATED_THREE_SOURCE_REQUIRED_ENVIRONMENT": (
        "MLB_AUTO_TABLE",
        "ODDS_API_KEY",
        "BBS_API_SECRET_ARN",
        "MLB_AUTO_FIRST_GAME_SAFETY_MINUTES",
        "MLB_AUTO_BEDROCK_MODELS",
    ),
    "ISOLATED_THREE_SOURCE_BOUNDARY_ENVIRONMENT": (
        "MLB_AUTO_TABLE",
        "BBS_API_SECRET_ARN",
    ),
    "ISOLATED_THREE_SOURCE_FORBIDDEN_ROOT_ENVIRONMENT": (
        "SNAPSHOTS_TABLE",
        "SIGNALS_TABLE",
        "SIGNAL_LEDGER_TABLE",
        "PREDICTIONS_TABLE",
        "OUTCOMES_TABLE",
        "MLB_ML_ARTIFACTS_BUCKET",
    ),
}
_HARDENED_REGEX_CONSTANTS = {
    "ISOLATED_THREE_SOURCE_FUNCTION_NAME_PATTERN": (
        r"^parlay-platform-mlb-auto-llm-MLBAutoLLMFunction-[A-Za-z0-9]+$"
    ),
    "ISOLATED_THREE_SOURCE_TABLE_NAME_PATTERN": (
        r"^parlay-platform-mlb-auto-llm-MLBAutoLLMTable-[A-Za-z0-9]+$"
    ),
    "ISOLATED_THREE_SOURCE_SECRET_ARN_PATTERN": (
        r"^arn:(?:aws|aws-us-gov|aws-cn):secretsmanager:[a-z0-9-]+:"
        r"[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+$"
    ),
}


def _static_string_value(node: ast.AST, known: dict[str, object] | None = None):
    known = known or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return known.get(node.id)
    if isinstance(node, ast.Tuple):
        values = tuple(_static_string_value(item, known) for item in node.elts)
        return values if all(value is not None for value in values) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string_value(node.left, known)
        right = _static_string_value(node.right, known)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
    return None


def _hardened_constant_values(source: str) -> dict[str, object]:
    values: dict[str, object] = {}
    try:
        module = ast.parse(source)
    except SyntaxError as error:
        raise RuntimeError(
            "hardened isolated authority contract is incomplete: invalid source"
        ) from error
    for node in module.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if target.id in _HARDENED_STRING_CONSTANTS:
            values[target.id] = _static_string_value(node.value, values)
        elif target.id in _HARDENED_REGEX_CONSTANTS:
            if isinstance(node.value, ast.Call) and node.value.args:
                values[target.id] = _static_string_value(node.value.args[0], values)
    return values


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def repair(path: Path = VERIFIER) -> bool:
    source = path.read_text(encoding="utf-8")
    original = source

    hardened_contract_expressions = (
        "ISOLATED_THREE_SOURCE_FUNCTION_NAME_PATTERN.fullmatch(name)",
        "handler in ISOLATED_THREE_SOURCE_HANDLERS",
        "ISOLATED_THREE_SOURCE_TABLE_NAME_PATTERN.fullmatch(isolated_table)",
        "and forbidden_absent",
        "and unexpected_provider_authority_absent",
        "ISOLATED_THREE_SOURCE_SECRET_ARN_PATTERN.fullmatch(secret_arn)",
        "isolated_writer_functions_by_arn",
        '"authorizedIsolatedWriterFunctions"',
        "if target_is_unqualified\n                    else None",
        "ISOLATED_THREE_SOURCE_FUNCTION_NAME_TOKEN\n"
        "                            in _authority_text(",
    )
    constant_values = _hardened_constant_values(source)
    constants_match = all(
        constant_values.get(name) == expected
        for name, expected in {
            **_HARDENED_STRING_CONSTANTS,
            **_HARDENED_REGEX_CONSTANTS,
        }.items()
    )
    if (
        constants_match
        and all(
            expression in source
            for expression in hardened_contract_expressions
        )
    ):
        return False
    if (
        "ISOLATED_THREE_SOURCE_FUNCTION_NAME_PATTERN" in source
        or "isolated_writer_functions_by_arn" in source
    ):
        missing = [
            expression
            for expression in hardened_contract_expressions
            if expression not in source
        ]
        missing.extend(
            f"{name}={constant_values.get(name)!r}"
            for name, expected in {
                **_HARDENED_STRING_CONSTANTS,
                **_HARDENED_REGEX_CONSTANTS,
            }.items()
            if constant_values.get(name) != expected
        )
        raise RuntimeError(
            "hardened isolated authority contract is incomplete: "
            + ",".join(missing)
        )

    constants_marker = '''HISTORICAL_NONCANONICAL_WRITER_TOKENS = (
    "HISTORICALOPTIMIZER",
    "HISTORICALOPTIMIZERV7RECOVERYENTRYPOINT",
)
'''
    constants_replacement = constants_marker + '''
ISOLATED_THREE_SOURCE_FUNCTION_NAME_TOKEN = (
    "PARLAYPLATFORMMLBAUTOLLMMLBAUTOLLMFUNCTION"
)
ISOLATED_THREE_SOURCE_HANDLER = "orchestrator.lambda_handler"
ISOLATED_THREE_SOURCE_REQUIRED_ENVIRONMENT = (
    "MLB_AUTO_TABLE",
    "ODDS_API_KEY",
    "BBS" + "_API_SECRET_ARN",
    "MLB_AUTO_FIRST_GAME_SAFETY_MINUTES",
    "MLB_AUTO_BEDROCK_MODELS",
)
ISOLATED_THREE_SOURCE_FORBIDDEN_ROOT_ENVIRONMENT = (
    "SNAPSHOTS_TABLE",
    "OUTCOMES_TABLE",
    "MLB_ML_ARTIFACTS_BUCKET",
)
'''
    if "ISOLATED_THREE_SOURCE_FUNCTION_NAME_TOKEN" not in source:
        source = _replace_once(
            source,
            constants_marker,
            constants_replacement,
            "isolated three-source constants",
        )

    helper_anchor = '''    return "MLB" in text and any(token in text for token in MLB_WRITER_TOKENS)


def _base_lambda_arn(value: Any) -> str:
'''
    helper_replacement = '''    return "MLB" in text and any(token in text for token in MLB_WRITER_TOKENS)


def _is_authorized_isolated_three_source_auto(function: Any) -> bool:
    """Recognize only the separately deployed, fully isolated MLB AUTO Lambda."""

    if not isinstance(function, dict):
        return False
    name = str(function.get("FunctionName") or "")
    arn = str(function.get("FunctionArn") or "")
    handler = str(function.get("Handler") or "")
    environment = (function.get("Environment") or {}).get("Variables") or {}
    if not isinstance(environment, dict):
        return False

    required_present = all(
        str(environment.get(key) or "").strip()
        for key in ISOLATED_THREE_SOURCE_REQUIRED_ENVIRONMENT
    )
    forbidden_absent = all(
        not str(environment.get(key) or "").strip()
        for key in ISOLATED_THREE_SOURCE_FORBIDDEN_ROOT_ENVIRONMENT
    )
    secret_arn = str(environment.get("BBS" + "_API_SECRET_ARN") or "")
    return bool(
        ISOLATED_THREE_SOURCE_FUNCTION_NAME_TOKEN in _authority_text(name)
        and arn
        and handler == ISOLATED_THREE_SOURCE_HANDLER
        and required_present
        and forbidden_absent
        and environment.get("MLB_AUTO_FIRST_GAME_SAFETY_MINUTES") == "10"
        and secret_arn.startswith("arn:")
        and ":secretsmanager:" in secret_arn
    )


def _root_authority_lambda_functions(lambdas: Any) -> List[Dict[str, Any]]:
    """Exclude only the positively identified isolated authority from root scans."""

    return [
        function
        for function in _all_lambda_functions(lambdas)
        if not _is_authorized_isolated_three_source_auto(function)
    ]


def _base_lambda_arn(value: Any) -> str:
'''
    if "def _is_authorized_isolated_three_source_auto" not in source:
        source = _replace_once(
            source,
            helper_anchor,
            helper_replacement,
            "isolated three-source helper",
        )

    old_loop = "        for function in _all_lambda_functions(lambdas):\n"
    new_loop = "        for function in _root_authority_lambda_functions(lambdas):\n"
    if new_loop not in source:
        source = _replace_once(
            source,
            old_loop,
            new_loop,
            "root authority Lambda inventory",
        )

    if source != original:
        path.write_text(source, encoding="utf-8")
        return True
    return False


def main() -> int:
    changed = repair()
    print("MLB isolated authority boundary repaired" if changed else "MLB isolated authority boundary already repaired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
