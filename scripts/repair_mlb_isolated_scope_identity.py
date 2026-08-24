#!/usr/bin/env python3
"""Keep root-provider scans scoped to root authority, not isolated readiness.

The root deployment identity verifier must recognize the isolated MLB AUTO
function by ownership/isolation.  Provider readiness (Odds, BBD, Bedrock and
T-10 configuration) is verified by the isolated deployment acceptance.  A
missing readiness field must not cause the isolated function to be reclassified
as a legacy/root Lambda and falsely prohibit its required BBD secret.
"""

from __future__ import annotations

from pathlib import Path


PATH = Path("scripts/verify_mlb_deploy_identity.py")


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return source.replace(old, new, 1)


def main() -> int:
    source = PATH.read_text(encoding="utf-8")
    original = source

    old_constants = '''ISOLATED_THREE_SOURCE_REQUIRED_ENVIRONMENT = (
    "MLB_AUTO_TABLE",
    "ODDS_API_KEY",
    "BBS" + "_API_SECRET_ARN",
    "MLB_AUTO_FIRST_GAME_SAFETY_MINUTES",
    "MLB_AUTO_BEDROCK_MODELS",
)
'''
    new_constants = old_constants + '''ISOLATED_THREE_SOURCE_BOUNDARY_ENVIRONMENT = (
    "MLB_AUTO_TABLE",
    "BBS" + "_API_SECRET_ARN",
)
'''
    if "ISOLATED_THREE_SOURCE_BOUNDARY_ENVIRONMENT" not in source:
        source = _replace_once(
            source,
            old_constants,
            new_constants,
            "isolated boundary constants",
        )

    old_required = '''    required_present = all(
        str(environment.get(key) or "").strip()
        for key in ISOLATED_THREE_SOURCE_REQUIRED_ENVIRONMENT
    )
'''
    new_required = '''    boundary_present = all(
        str(environment.get(key) or "").strip()
        for key in ISOLATED_THREE_SOURCE_BOUNDARY_ENVIRONMENT
    )
'''
    if new_required not in source:
        source = _replace_once(
            source,
            old_required,
            new_required,
            "isolated boundary presence",
        )

    old_return = '''        and required_present
        and forbidden_absent
        and environment.get("MLB_AUTO_FIRST_GAME_SAFETY_MINUTES") == "10"
        and secret_arn.startswith("arn:")
'''
    new_return = '''        and boundary_present
        and forbidden_absent
        and secret_arn.startswith("arn:")
'''
    if new_return not in source:
        source = _replace_once(
            source,
            old_return,
            new_return,
            "isolated boundary predicate",
        )

    if source != original:
        PATH.write_text(source, encoding="utf-8")
        print("Separated isolated scope identity from isolated readiness")
    else:
        print("Isolated scope identity is already separated from readiness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
