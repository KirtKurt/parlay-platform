from __future__ import annotations

"""Fail-closed static contract for the autonomous MLB three-source runtime."""

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContractError(RuntimeError):
    pass


def _read(path: str) -> str:
    target = ROOT / path
    if not target.is_file():
        raise ContractError(f"REQUIRED_FILE_MISSING:{path}")
    return target.read_text(encoding="utf-8")


def _require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise ContractError(f"MISSING_{label}:{needle}")


def _forbid(text: str, pattern: str, label: str) -> None:
    if re.search(pattern, text, re.I | re.M):
        raise ContractError(f"FORBIDDEN_{label}:{pattern}")


def verify() -> dict:
    advanced = _read("hello_world/mlb_advanced_context.py")
    bbd = _read("hello_world/mlb_bbd_pro_context.py")
    llm = _read("hello_world/mlb_three_api_llm_analyst.py")
    controller = _read("hello_world/mlb_three_api_autonomous_controller.py")
    policy = _read("hello_world/mlb_three_api_policy.py")
    schedule = _read("hello_world/mlb_official_schedule_authority.py")
    finals = _read("hello_world/mlb_canonical_final_labels_v1.py")
    template = _read("template.yaml")
    deploy = _read(".github/workflows/deploy.yml")

    for path, text in (
        ("hello_world/mlb_bbd_pro_context.py", bbd),
        ("hello_world/mlb_three_api_llm_analyst.py", llm),
        ("hello_world/mlb_three_api_autonomous_controller.py", controller),
        ("hello_world/mlb_three_api_policy.py", policy),
    ):
        ast.parse(text, filename=path)

    _require(schedule, "statsapi.mlb.com/api/v1/schedule", "OFFICIAL_MLB_SCHEDULE_AUTHORITY")
    _require(finals, "MLB Stats API exact-date official FINAL", "OFFICIAL_FINAL_LABEL_AUTHORITY")
    _require(advanced, "MLB_THREE_API_INTEGRATION_BEGIN", "ADVANCED_CONTEXT_INTEGRATION")
    _require(advanced, "mlb_bbd_pro_context", "BBD_CONTEXT_IMPORT")
    _require(advanced, "mlb_three_api_llm_analyst", "LLM_ANALYST_IMPORT")
    _require(bbd, "Big Balls Data Pro", "BBD_PROVIDER")
    _require(bbd, "discover_manifest", "BBD_OPENAPI_DISCOVERY")
    _require(llm, "bedrock-runtime", "BEDROCK_RUNTIME")
    _require(llm, "Do not pass", "LLM_NO_PASS_POLICY")
    _require(policy, "DAILY_ACCURACY_GOAL = 0.70", "SEVENTY_PERCENT_GOAL")
    _require(policy, "PREDICTION_LEAD_MINUTES = 45", "T45_POLICY")
    _require(policy, "complete_card_deadline_utc", "SECOND_GAME_CARD_DEADLINE")
    _require(controller, "rate(5 minutes)" if "rate(5 minutes)" in controller else "Five-minute", "AUTONOMOUS_CADENCE")
    _require(controller, "requireAllOfficialGames", "FULL_SLATE_REQUIREMENT")

    _require(template, "BigBallsDataApiKey", "SAM_BBD_PARAMETER")
    _require(template, "BIG_BALLS_DATA_API_KEY", "SAM_BBD_ENVIRONMENT")
    _require(template, "MLBThreeApiAutonomousControllerFunction", "SAM_AUTONOMOUS_CONTROLLER")
    _require(template, "rate(5 minutes)", "SAM_FIVE_MINUTE_SCHEDULE")
    _require(template, "MLB_THREE_API_LLM_MODEL_ID", "SAM_LLM_MODEL")

    _require(deploy, "BIG_BALLS_DATA_API_KEY_VALUE", "DEPLOY_BBD_SECRET")
    _require(deploy, "BigBallsDataApiKey=${BIG_BALLS_DATA_API_KEY_VALUE}", "DEPLOY_BBD_PARAMETER_OVERRIDE")
    _require(deploy, "verify_mlb_three_api_runtime.py", "DEPLOY_THREE_API_VERIFIER")
    _forbid(
        deploy,
        r"python\s+scripts/verify_mlb_no_bbd_runtime\.py",
        "ACTIVE_NO_BBD_VERIFIER",
    )
    _forbid(
        deploy,
        r"tests/unit/test_verify_mlb_no_bbd_runtime\.py",
        "ACTIVE_NO_BBD_TEST",
    )

    # Secrets may only be referenced symbolically; plausible literal keys are
    # forbidden in all new modules and the template.
    combined = "\n".join((advanced, bbd, llm, controller, policy, template, deploy))
    _forbid(combined, r"(?i)(?:api[_-]?key|token)\s*[:=]\s*['\"][A-Za-z0-9_-]{24,}['\"]", "HARDCODED_SECRET")

    return {
        "ok": True,
        "contract": "MLB_THREE_API_AUTONOMOUS_RUNTIME",
        "officialAuthority": "MLB Stats API",
        "marketAuthority": "The Odds API",
        "baseballContext": "Big Balls Data Pro",
        "llm": "Amazon Bedrock",
        "fullSlate": True,
        "predictionLeadMinutes": 45,
        "completeCardDeadline": "second official game start minus 45 minutes",
        "dailyAccuracyGoal": 0.70,
    }


if __name__ == "__main__":
    print(verify())
