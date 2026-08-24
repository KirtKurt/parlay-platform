#!/usr/bin/env python3
"""Verify the MLB provider boundary and the isolated three-source MLB AUTO stack.

The legacy/root MLB stack remains provider-neutral and must not acquire a BBD/BBS
credential or endpoint. The isolated ``mlb-auto-llm`` stack is intentionally the
opposite: it must include MLB Stats API, The Odds API, Big Balls Sports Data Pro,
Bedrock decision authority, per-game source coverage, and an immutable T-10 card.

The historical filename is retained because the canonical root deployment and
migration checks call it. Its contract is no longer a repository-wide ban on BBD.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]

# Build tokens in pieces so this verifier does not flag its own source file.
FORBIDDEN = (
    "BBS" + "_API_KEY",
    "BBS" + "_API_SECRET_ARN",
    "BBS" + "_SHADOW_CAPTURE_ENABLED",
    "Bbs" + "ApiKey",
    "Bbs" + "ApiSecret",
    "api." + "bigballsdata.com",
    "verify_" + "bbs_api_live_contract.py",
    "verify_mlb_" + "bbs_sam_wiring.py",
    "mlb/providers/" + "bbs/",
)

# These are the only files governed by the legacy provider-neutral contract.
# Isolated MLB AUTO files are verified positively below and must never be added
# to this tuple.
LEGACY_PROVIDER_NEUTRAL_FILES = (
    Path("template.yaml"),
    Path(".github/workflows/deploy.yml"),
    Path(".github/workflows/mlb-v8-historical-context-backfill.yml"),
    Path("scripts/stabilize_mlb_deploy_source.py"),
    Path("scripts/verify_mlb_deploy_identity.py"),
    Path("scripts/run_mlb_v8_historical_context_backfill_entrypoint.py"),
)

# Compatibility alias used by existing tests and migration tooling.
ACTIVE_FILES = LEGACY_PROVIDER_NEUTRAL_FILES

ISOLATED_THREE_SOURCE_REQUIREMENTS = {
    Path("mlb_auto_llm/handler.py"): (
        'VERSION = "MLB-AUTO-LLM-v1-three-source-autonomous"',
        "https://statsapi.mlb.com/api/v1/schedule",
        "https://api.the-odds-api.com/v4/sports/baseball_mlb",
        "https://api.bigballsdata.com",
        'boto3.client("bedrock-runtime")',
        "FIRST_GAME_SAFETY_MINUTES",
        'condition="attribute_not_exists(PK)"',
        '"authority": "MLB_AUTO_LLM_PRIMARY"',
    ),
    Path("mlb_auto_llm/orchestrator.py"): (
        "THREE_SOURCE_GAME_COVERAGE_INCOMPLETE",
        "BEDROCK_DECISION_REQUIRED",
        "threeSourceCoverageComplete",
        "teamRecentForm",
        "playerRollingStats",
        "bbsLeagueContext",
    ),
    Path("mlb-auto-llm-template.yaml"): (
        "OddsApiKey:",
        "BbsApiKey:",
        "BBS_API_SECRET_ARN",
        "bedrock:InvokeModel",
        "MLB_AUTO_FIRST_GAME_SAFETY_MINUTES: '10'",
        "Schedule: cron(2/5 * * * ? *)",
        "DeletionPolicy: Retain",
    ),
    Path(".github/workflows/deploy-mlb-auto-llm.yml"): (
        "secrets.ODDS_API_KEY",
        "secrets.BBS_API_KEY",
        "api.bigballsdata.com/v1/user/me",
        'BbsApiKey="${BBS_API_KEY_VALUE}"',
        "Prove Bedrock through deployed MLB Lambda role",
    ),
}

# Step titles may be clarified over time. The stable runtime invocation marker is
# authoritative; the historical titles remain accepted for compatibility.
ISOLATED_PROVIDER_CYCLE_MARKERS = (
    "deployment_provider_smoke",
    "Prove autonomous provider collection in AWS",
    "Prove autonomous three-source production cycle",
)


def _read(path: Path) -> str:
    resolved = ROOT / path
    if not resolved.is_file():
        raise RuntimeError(f"required active file is missing: {path}")
    return resolved.read_text(encoding="utf-8")


def _verify_legacy_provider_neutral(paths: Iterable[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        try:
            text = _read(path)
        except RuntimeError:
            errors.append(f"legacy_provider_neutral_file_missing:{path}")
            continue
        for token in FORBIDDEN:
            if token in text:
                errors.append(f"active_bbd_reference:{path}:{token}")
    return errors


def _verify_isolated_three_source() -> list[str]:
    errors: list[str] = []
    for path, required_markers in ISOLATED_THREE_SOURCE_REQUIREMENTS.items():
        try:
            text = _read(path)
        except RuntimeError:
            errors.append(f"isolated_three_source_file_missing:{path}")
            continue
        for marker in required_markers:
            if marker not in text:
                errors.append(
                    f"isolated_three_source_marker_missing:{path}:{marker}"
                )

    workflow_path = Path(".github/workflows/deploy-mlb-auto-llm.yml")
    try:
        workflow = _read(workflow_path)
    except RuntimeError:
        workflow = ""
    if workflow and not any(
        marker in workflow for marker in ISOLATED_PROVIDER_CYCLE_MARKERS
    ):
        errors.append(
            "isolated_three_source_marker_missing:"
            f"{workflow_path}:provider_cycle_runtime_invocation"
        )
    return errors


def verify_files(paths: Iterable[Path] = ACTIVE_FILES) -> list[str]:
    errors = _verify_legacy_provider_neutral(paths)

    try:
        context = _read(
            Path("scripts/run_mlb_v8_historical_context_backfill_entrypoint.py")
        )
    except RuntimeError:
        context = ""
    required_legacy_context = (
        "OfficialContextClient",
        '"official_mlb"',
        '"bbsApiUsed": False',
        '"bbsCredentialRead": False',
        '"productionAuthorityChanged": False',
    )
    for token in required_legacy_context:
        if token not in context:
            errors.append(f"official_context_contract_missing:{token}")

    errors.extend(_verify_isolated_three_source())
    return sorted(set(errors))


def main() -> int:
    errors = verify_files()
    if errors:
        for error in errors:
            print(error)
        return 1
    print(
        "MLB provider boundary is valid: the legacy root stack is provider-neutral; "
        "the isolated MLB AUTO stack positively requires MLB Stats API, The Odds API, "
        "Big Balls Sports Data Pro, Bedrock, complete per-game coverage, and an "
        "immutable T-10 card."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
