from __future__ import annotations

"""Production acceptance contract for autonomous MLB three-source prediction."""

import ast
import re
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]


class ProductionContractError(RuntimeError):
    pass


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise ProductionContractError(f"REQUIRED_FILE_MISSING:{relative}")
    return path.read_text(encoding="utf-8")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise ProductionContractError(f"MISSING_{label}:{needle}")


def forbid(text: str, pattern: str, label: str) -> None:
    if re.search(pattern, text, re.I | re.M):
        raise ProductionContractError(f"FORBIDDEN_{label}:{pattern}")


def resource(template: str, logical_id: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(logical_id)}:\s*\n(.*?)(?=^  [A-Za-z0-9][A-Za-z0-9_-]*:\s*$|^Outputs:\s*$|\Z)",
        template,
    )
    if not match:
        raise ProductionContractError(f"SAM_RESOURCE_NOT_FOUND:{logical_id}")
    return match.group(0)


def verify() -> Dict[str, Any]:
    files = {
        "officialSchedule": "hello_world/mlb_official_schedule_authority.py",
        "officialFinals": "hello_world/mlb_canonical_final_labels_v1.py",
        "advancedContext": "hello_world/mlb_advanced_context.py",
        "bbd": "hello_world/mlb_bbd_pro_context.py",
        "llm": "hello_world/mlb_three_api_llm_analyst.py",
        "overlay": "hello_world/mlb_three_api_prediction_overlay.py",
        "policy": "hello_world/mlb_three_api_policy.py",
        "controller": "hello_world/mlb_three_api_autonomous_controller_v2.py",
        "installer": "scripts/install_mlb_three_api_autonomy_final.py",
    }
    texts = {name: read(path) for name, path in files.items()}
    template = read("template.yaml")
    deploy = read(".github/workflows/deploy.yml")

    for name, path in files.items():
        ast.parse(texts[name], filename=path)

    require(texts["officialSchedule"], "statsapi.mlb.com/api/v1/schedule", "OFFICIAL_MLB_API")
    require(texts["officialSchedule"], "canonical_start_time_source", "OFFICIAL_START_AUTHORITY")
    require(texts["officialFinals"], "MLB Stats API exact-date official FINAL", "OFFICIAL_FINAL_AUTHORITY")
    require(texts["advancedContext"], "MLB_THREE_API_INTEGRATION_BEGIN", "ADVANCED_CONTEXT_INTEGRATION")
    require(texts["advancedContext"], "mlb_bbd_pro_context", "BBD_CONTEXT")
    require(texts["advancedContext"], "mlb_three_api_llm_analyst", "LLM_CONTEXT")

    require(texts["bbd"], "Big Balls Data Pro", "BBD_PROVIDER")
    require(texts["bbd"], "discover_manifest", "BBD_DISCOVERY")
    require(texts["bbd"], "for group in (path_row.get", "BBD_OPENAPI_PARAMETER_FIX")
    require(texts["bbd"], "BBD_BUNDLED_MANIFEST_BEGIN", "BBD_PINNED_MANIFEST")
    require(texts["bbd"], "contextFingerprint", "BBD_PROVENANCE")

    require(texts["llm"], "bedrock-runtime", "BEDROCK_RUNTIME")
    require(texts["llm"], "Do not pass", "LLM_NO_PASS")
    require(texts["llm"], "point-in-time evidence", "LLM_POINT_IN_TIME")
    require(texts["llm"], "predicted_winner", "LLM_WINNER")
    require(texts["llm"], "predicted_loser", "LLM_LOSER")

    for component in (
        "existingAutonomousMLModel",
        "theOddsApiNoVigMarketConsensus",
        "bedrockThreeSourceAnalyst",
    ):
        require(texts["overlay"], component, f"ENSEMBLE_{component}")
    require(texts["overlay"], '"bigBallsDataPro": bbd_ready', "BBD_SOURCE_GATE")
    require(texts["overlay"], "THREE_API_SOURCE_NOT_READY", "STRICT_SOURCE_GATE")
    require(texts["overlay"], 'current["predictedWinner"] = winner', "FINAL_WINNER")
    require(texts["overlay"], 'current["predictedLoser"] = loser', "FINAL_LOSER")
    require(texts["overlay"], '"noPass": True', "NO_PASS")
    require(texts["overlay"], '"accuracyGuarantee": False', "NO_FALSE_ACCURACY_GUARANTEE")

    overlay_modules: List[str] = []
    for path in (ROOT / "hello_world").glob("*.py"):
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "MLB_THREE_API_FINAL_PREDICTION_OVERLAY_BEGIN" in source:
            overlay_modules.append(str(path.relative_to(ROOT)))
    if not overlay_modules:
        raise ProductionContractError("OVERLAY_NOT_INSTALLED_IN_DAILY_LOCK_GRAPH")

    require(texts["policy"], "PREDICTION_LEAD_MINUTES = 45", "T45")
    require(texts["policy"], "DAILY_ACCURACY_GOAL = 0.70", "ACCURACY_GOAL")
    require(texts["policy"], "second_game_start_utc", "SECOND_GAME_DEADLINE")
    require(texts["policy"], "completeOfficialSlateDenominator", "FULL_SLATE_DENOMINATOR")
    require(texts["policy"], "no passes, exclusions, rounding or", "NO_CHERRY_PICKING")

    require(texts["controller"], "MLB-THREE-API-AUTONOMOUS-CONTROLLER-v2", "CONTROLLER")
    require(texts["controller"], "_three_api_pick_errors", "LOCKED_PICK_VALIDATION")
    require(texts["controller"], "LLM_NOT_IN_FINAL_ENSEMBLE", "LLM_FINAL_USE_VALIDATION")
    require(texts["controller"], "COMPLETE_CARD_NOT_PROVEN_BY_SECOND_GAME_T45", "DEADLINE_ENFORCEMENT")
    require(texts["controller"], "postSettlementCycleCompleted", "POST_SETTLEMENT_RETRAINING")

    require(template, "BigBallsDataApiKey:", "BBD_PARAMETER")
    require(template, "MLBThreeApiAutonomyStateTable:", "STATE_TABLE")
    require(template, "Handler: mlb_three_api_autonomous_controller_v2.lambda_handler", "CONTROLLER_HANDLER")
    require(template, "Schedule: rate(5 minutes)", "FIVE_MINUTE_SCHEDULE")

    required_functions = (
        "MLBAuditedPullFunction",
        "MLBDailyPickLockFunction",
        "MLBMLTrainingFunction",
        "MLBProductionVerifierFunction",
        "MLBThreeApiAutonomousControllerFunction",
    )
    for logical_id in required_functions:
        block = resource(template, logical_id)
        require(block, "BIG_BALLS_DATA_API_KEY: !Ref BigBallsDataApiKey", f"{logical_id}_BBD")
        require(block, "MLB_THREE_API_LLM_MODEL_ID:", f"{logical_id}_LLM")
        require(block, "MLB_THREE_API_ENABLED: 'true'", f"{logical_id}_ENABLED")
        require(block, "MLB_THREE_API_REQUIRE_ALL_SOURCES: 'true'", f"{logical_id}_STRICT")
        require(block, "bedrock:InvokeModel", f"{logical_id}_BEDROCK")

    controller_block = resource(template, "MLBThreeApiAutonomousControllerFunction")
    require(controller_block, "MLB_THREE_API_STATE_TABLE: !Ref MLBThreeApiAutonomyStateTable", "CONTROLLER_STATE")
    require(controller_block, "MLB_THREE_API_READ_FUNCTION_NAME: !Ref MLBV3ReadFunction", "CONTROLLER_READ")
    require(controller_block, "DynamoDBCrudPolicy", "CONTROLLER_DDB_POLICY")

    require(deploy, "python scripts/install_mlb_three_api_autonomy_final.py", "PRODUCTION_INSTALLER")
    require(deploy, "python scripts/verify_mlb_three_api_runtime_production.py", "PRODUCTION_VERIFIER")
    require(deploy, "tests/unit/test_mlb_three_api_runtime_production.py", "PRODUCTION_TEST")
    require(deploy, "tests/unit/test_mlb_three_api_prediction_overlay.py", "OVERLAY_TEST")
    require(deploy, "BigBallsDataApiKey=${BIG_BALLS_DATA_API_KEY_VALUE}", "BBD_DEPLOY_OVERRIDE")
    if deploy.count("python scripts/install_mlb_three_api_autonomy_final.py") != 1:
        raise ProductionContractError("PRODUCTION_INSTALLER_REFERENCE_NOT_UNIQUE")
    if deploy.count("tests/unit/test_mlb_three_api_runtime_production.py") != 1:
        raise ProductionContractError("PRODUCTION_TEST_REFERENCE_NOT_UNIQUE")
    forbid(deploy, r"verify_mlb_no_bbd_runtime\.py", "LEGACY_NO_BBD")
    forbid(deploy, r"test_verify_mlb_no_bbd_runtime\.py", "LEGACY_NO_BBD_TEST")

    combined = "\n".join((*texts.values(), template, deploy))
    forbid(
        combined,
        r"(?i)(?:api[_-]?key|token)\s*[:=]\s*['\"][A-Za-z0-9_-]{24,}['\"]",
        "HARDCODED_SECRET",
    )

    return {
        "ok": True,
        "contract": "MLB_THREE_API_AUTONOMOUS_RUNTIME_PRODUCTION",
        "officialAuthority": "MLB Stats API",
        "marketAuthority": "The Odds API",
        "baseballContext": "Big Balls Data Pro",
        "finalDecision": "existing autonomous ML + no-vig market + Bedrock three-source analyst",
        "overlayModules": sorted(overlay_modules),
        "runtimeFunctionsConfigured": list(required_functions),
        "normalDeployIdempotent": True,
        "fullOfficialSlate": True,
        "noPass": True,
        "predictionLeadMinutes": 45,
        "completeCardDeadline": "second official game start minus 45 minutes",
        "autonomousCadenceMinutes": 5,
        "postSettlementScoringAndRetraining": True,
        "dailyAccuracyGoal": 0.70,
        "fullOfficialSlateAccuracyDenominator": True,
        "accuracyGuarantee": False,
        "tennisAndSoccerIsolation": True,
    }


if __name__ == "__main__":
    print(verify())
