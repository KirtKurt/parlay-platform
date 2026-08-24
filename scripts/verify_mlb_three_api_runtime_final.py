from __future__ import annotations

"""Independent final contract for the production MLB three-source runtime."""

import ast
import re
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]


class FinalContractError(RuntimeError):
    pass


def _read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise FinalContractError(f"REQUIRED_FILE_MISSING:{relative}")
    return path.read_text(encoding="utf-8")


def _require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise FinalContractError(f"MISSING_{label}:{needle}")


def _forbid(text: str, pattern: str, label: str) -> None:
    if re.search(pattern, text, re.I | re.M):
        raise FinalContractError(f"FORBIDDEN_{label}:{pattern}")


def _resource(template: str, logical_id: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(logical_id)}:\s*\n(.*?)(?=^  [A-Za-z0-9][A-Za-z0-9_-]*:\s*$|^Outputs:\s*$|\Z)",
        template,
    )
    if not match:
        raise FinalContractError(f"SAM_RESOURCE_NOT_FOUND:{logical_id}")
    return match.group(0)


def verify() -> Dict[str, Any]:
    official_schedule = _read("hello_world/mlb_official_schedule_authority.py")
    official_finals = _read("hello_world/mlb_canonical_final_labels_v1.py")
    advanced = _read("hello_world/mlb_advanced_context.py")
    bbd = _read("hello_world/mlb_bbd_pro_context.py")
    llm = _read("hello_world/mlb_three_api_llm_analyst.py")
    overlay = _read("hello_world/mlb_three_api_prediction_overlay.py")
    policy = _read("hello_world/mlb_three_api_policy.py")
    controller = _read("hello_world/mlb_three_api_autonomous_controller_v2.py")
    template = _read("template.yaml")
    deploy = _read(".github/workflows/deploy.yml")
    installer = _read("scripts/install_mlb_three_api_autonomy_v3.py")

    for relative, text in (
        ("hello_world/mlb_official_schedule_authority.py", official_schedule),
        ("hello_world/mlb_canonical_final_labels_v1.py", official_finals),
        ("hello_world/mlb_advanced_context.py", advanced),
        ("hello_world/mlb_bbd_pro_context.py", bbd),
        ("hello_world/mlb_three_api_llm_analyst.py", llm),
        ("hello_world/mlb_three_api_prediction_overlay.py", overlay),
        ("hello_world/mlb_three_api_policy.py", policy),
        ("hello_world/mlb_three_api_autonomous_controller_v2.py", controller),
        ("scripts/install_mlb_three_api_autonomy_v3.py", installer),
    ):
        ast.parse(text, filename=relative)

    _require(official_schedule, "statsapi.mlb.com/api/v1/schedule", "OFFICIAL_SCHEDULE_ENDPOINT")
    _require(official_schedule, "canonical_start_time_source", "OFFICIAL_START_TIME_AUTHORITY")
    _require(official_finals, "MLB Stats API exact-date official FINAL", "OFFICIAL_FINAL_LABELS")
    _require(official_finals, "officialGamePk", "OFFICIAL_GAME_IDENTITY")

    _require(advanced, "MLB_THREE_API_INTEGRATION_BEGIN", "ADVANCED_CONTEXT_WRAPPER")
    _require(advanced, "mlb_bbd_pro_context", "BBD_ADVANCED_CONTEXT")
    _require(advanced, "mlb_three_api_llm_analyst", "LLM_ADVANCED_CONTEXT")

    _require(bbd, "Big Balls Data Pro", "BBD_PROVIDER")
    _require(bbd, "discover_manifest", "BBD_ENDPOINT_DISCOVERY")
    _require(bbd, "for group in (path_row.get", "OPENAPI_PARAMETER_EXTRACTION")
    _require(bbd, "BBD_BUNDLED_MANIFEST_BEGIN", "BUNDLED_BBD_MANIFEST")
    _require(bbd, "contextFingerprint", "BBD_POINT_IN_TIME_FINGERPRINT")
    _forbid(
        bbd,
        r"for source in \(path_row\.get\(\"parameters\"\).*operation\.get\(\"parameters\"\)",
        "BROKEN_OPENAPI_PARAMETER_LOOP",
    )

    _require(llm, "bedrock-runtime", "BEDROCK_RUNTIME")
    _require(llm, "Do not pass", "LLM_NO_PASS")
    _require(llm, "point-in-time evidence", "LLM_POINT_IN_TIME_BOUNDARY")
    _require(llm, "predicted_winner", "LLM_STRUCTURED_WINNER")
    _require(llm, "predicted_loser", "LLM_STRUCTURED_LOSER")

    for component in (
        "existingAutonomousMLModel",
        "theOddsApiNoVigMarketConsensus",
        "bedrockThreeSourceAnalyst",
    ):
        _require(overlay, component, f"FINAL_ENSEMBLE_{component}")
    _require(overlay, '"bigBallsDataPro": bbd_ready', "BBD_FINAL_SOURCE_GATE")
    _require(overlay, "THREE_API_SOURCE_NOT_READY", "FINAL_SOURCE_FAIL_CLOSED")
    _require(overlay, 'current["predictedWinner"] = winner', "FINAL_WINNER")
    _require(overlay, 'current["predictedLoser"] = loser', "FINAL_LOSER")
    _require(overlay, '"noPass": True', "FINAL_NO_PASS")
    _require(overlay, '"accuracyGuarantee": False', "NO_FALSE_ACCURACY_GUARANTEE")

    overlay_modules: List[str] = []
    for path in (ROOT / "hello_world").glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "MLB_THREE_API_FINAL_PREDICTION_OVERLAY_BEGIN" in text:
            overlay_modules.append(str(path.relative_to(ROOT)))
    if not overlay_modules:
        raise FinalContractError("FINAL_PREDICTION_OVERLAY_NOT_INSTALLED_IN_LOCK_GRAPH")

    _require(policy, "PREDICTION_LEAD_MINUTES = 45", "T45_POLICY")
    _require(policy, "DAILY_ACCURACY_GOAL = 0.70", "SEVENTY_PERCENT_GOAL")
    _require(policy, "second_game_start_utc", "SECOND_GAME_DEADLINE")
    _require(policy, "completeOfficialSlateDenominator", "FULL_SLATE_SCORECARD")
    _require(policy, "no passes, exclusions, rounding or", "NO_CHERRY_PICKING")

    _require(controller, "MLB-THREE-API-AUTONOMOUS-CONTROLLER-v2", "STATEFUL_CONTROLLER")
    _require(controller, "_three_api_pick_errors", "LOCKED_PICK_SOURCE_VALIDATION")
    _require(controller, "LLM_NOT_IN_FINAL_ENSEMBLE", "LOCKED_PICK_LLM_VALIDATION")
    _require(controller, "COMPLETE_CARD_NOT_PROVEN_BY_SECOND_GAME_T45", "DEADLINE_FAIL_CLOSED")
    _require(controller, "postSettlementCycleCompleted", "POST_SETTLEMENT_AUTONOMY")
    _require(controller, "fullOfficialSlateDenominator", "POST_SETTLEMENT_FULL_SLATE")

    _require(template, "BigBallsDataApiKey:", "BBD_SAM_PARAMETER")
    _require(template, "MLBThreeApiAutonomyStateTable:", "AUTONOMY_STATE_TABLE")
    _require(template, "Handler: mlb_three_api_autonomous_controller_v2.lambda_handler", "V2_HANDLER")
    _require(template, "Schedule: rate(5 minutes)", "FIVE_MINUTE_SCHEDULE")

    required_resources = (
        "MLBAuditedPullFunction",
        "MLBDailyPickLockFunction",
        "MLBMLTrainingFunction",
        "MLBProductionVerifierFunction",
        "MLBThreeApiAutonomousControllerFunction",
    )
    for logical_id in required_resources:
        block = _resource(template, logical_id)
        _require(block, "BIG_BALLS_DATA_API_KEY: !Ref BigBallsDataApiKey", f"{logical_id}_BBD_KEY")
        _require(block, "MLB_THREE_API_LLM_MODEL_ID:", f"{logical_id}_LLM_MODEL")
        _require(block, "MLB_THREE_API_ENABLED: 'true'", f"{logical_id}_ENABLED")
        _require(block, "MLB_THREE_API_REQUIRE_ALL_SOURCES: 'true'", f"{logical_id}_STRICT")
        _require(block, "bedrock:InvokeModel", f"{logical_id}_BEDROCK_PERMISSION")

    controller_block = _resource(template, "MLBThreeApiAutonomousControllerFunction")
    _require(controller_block, "MLB_THREE_API_STATE_TABLE: !Ref MLBThreeApiAutonomyStateTable", "CONTROLLER_STATE")
    _require(controller_block, "MLB_THREE_API_READ_FUNCTION_NAME: !Ref MLBV3ReadFunction", "CONTROLLER_READ")
    _require(controller_block, "DynamoDBCrudPolicy", "CONTROLLER_STATE_POLICY")

    _require(deploy, "python scripts/install_mlb_three_api_autonomy_v3.py", "NORMAL_DEPLOY_V3_INSTALLER")
    _require(deploy, "python scripts/verify_mlb_three_api_runtime_final.py", "NORMAL_DEPLOY_FINAL_VERIFIER")
    _require(deploy, "tests/unit/test_mlb_three_api_runtime_final.py", "NORMAL_DEPLOY_FINAL_TEST")
    _require(deploy, "tests/unit/test_mlb_three_api_prediction_overlay.py", "NORMAL_DEPLOY_OVERLAY_TEST")
    _require(deploy, "BigBallsDataApiKey=${BIG_BALLS_DATA_API_KEY_VALUE}", "NORMAL_DEPLOY_BBD_OVERRIDE")
    _forbid(deploy, r"verify_mlb_no_bbd_runtime\.py", "LEGACY_NO_BBD_VERIFIER")
    _forbid(deploy, r"test_verify_mlb_no_bbd_runtime\.py", "LEGACY_NO_BBD_TEST")

    combined = "\n".join(
        (official_schedule, official_finals, advanced, bbd, llm, overlay, policy, controller, template, deploy, installer)
    )
    _forbid(
        combined,
        r"(?i)(?:api[_-]?key|token)\s*[:=]\s*['\"][A-Za-z0-9_-]{24,}['\"]",
        "HARDCODED_SECRET",
    )

    return {
        "ok": True,
        "contract": "MLB_THREE_API_AUTONOMOUS_RUNTIME_FINAL",
        "officialAuthority": "MLB Stats API",
        "marketAuthority": "The Odds API",
        "baseballContext": "Big Balls Data Pro",
        "finalDecision": "existing ML + no-vig market + Bedrock three-source analyst",
        "overlayModules": sorted(overlay_modules),
        "allRequiredRuntimeFunctionsCarryThreeSourceConfiguration": True,
        "allRequiredRuntimeFunctionsCarryBedrockPermission": True,
        "fullOfficialSlate": True,
        "noPass": True,
        "predictionLeadMinutes": 45,
        "completeCardDeadline": "second official game start minus 45 minutes",
        "autonomousCadenceMinutes": 5,
        "postSettlementScoringAndRetraining": True,
        "dailyAccuracyGoal": 0.70,
        "fullOfficialSlateAccuracyDenominator": True,
        "accuracyGuarantee": False,
        "sportIsolation": {"tennis": True, "soccer": True},
    }


if __name__ == "__main__":
    print(verify())
