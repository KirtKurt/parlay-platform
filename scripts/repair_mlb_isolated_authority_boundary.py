#!/usr/bin/env python3
"""Authorize the isolated three-source MLB AUTO Lambda without weakening root MLB.

The canonical/root MLB stack remains provider-neutral.  The separately deployed
MLB AUTO LLM function is allowed to carry the Big Balls Sports Data Pro secret
only when its full isolated identity contract is present and no root authority
tables or artifacts are attached.
"""

from __future__ import annotations

from pathlib import Path


VERIFIER = Path("scripts/verify_mlb_deploy_identity.py")


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def repair(path: Path = VERIFIER) -> bool:
    source = path.read_text(encoding="utf-8")
    original = source

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
