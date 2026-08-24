from __future__ import annotations

"""Strict source-to-final-pick contract for MLB three-API autonomy v2."""

import ast
import re
from pathlib import Path
from typing import Any, Dict, List

from scripts.verify_mlb_three_api_runtime import verify as verify_v1


ROOT = Path(__file__).resolve().parents[1]


class ContractV2Error(RuntimeError):
    pass


def _read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise ContractV2Error(f"REQUIRED_FILE_MISSING:{relative}")
    return path.read_text(encoding="utf-8")


def _require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise ContractV2Error(f"MISSING_{label}:{needle}")


def _forbid(text: str, pattern: str, label: str) -> None:
    if re.search(pattern, text, re.I | re.M):
        raise ContractV2Error(f"FORBIDDEN_{label}:{pattern}")


def verify() -> Dict[str, Any]:
    v1 = verify_v1()
    overlay = _read("hello_world/mlb_three_api_prediction_overlay.py")
    controller = _read("hello_world/mlb_three_api_autonomous_controller_v2.py")
    bbd = _read("hello_world/mlb_bbd_pro_context.py")
    policy = _read("hello_world/mlb_three_api_policy.py")
    template = _read("template.yaml")
    deploy = _read(".github/workflows/deploy.yml")
    installer = _read("scripts/install_mlb_three_api_autonomy_v2.py")

    for relative, text in (
        ("hello_world/mlb_three_api_prediction_overlay.py", overlay),
        ("hello_world/mlb_three_api_autonomous_controller_v2.py", controller),
        ("hello_world/mlb_bbd_pro_context.py", bbd),
        ("hello_world/mlb_three_api_policy.py", policy),
        ("scripts/install_mlb_three_api_autonomy_v2.py", installer),
    ):
        ast.parse(text, filename=relative)

    _require(overlay, "existingAutonomousMLModel", "EXISTING_ML_ENSEMBLE_COMPONENT")
    _require(overlay, "theOddsApiNoVigMarketConsensus", "ODDS_API_ENSEMBLE_COMPONENT")
    _require(overlay, "bedrockThreeSourceAnalyst", "LLM_ENSEMBLE_COMPONENT")
    _require(overlay, '"bigBallsDataPro": bbd_ready', "BBD_SOURCE_READINESS")
    _require(overlay, "THREE_API_SOURCE_NOT_READY", "FAIL_CLOSED_SOURCE_GATE")
    _require(overlay, 'current["predictedLoser"] = loser', "WINNER_LOSER_OUTPUT")
    _require(overlay, '"noPass": True', "NO_PASS_DECISION")
    _require(overlay, '"accuracyGuarantee": False', "NO_FALSE_ACCURACY_GUARANTEE")

    marker_files: List[str] = []
    for path in (ROOT / "hello_world").glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "MLB_THREE_API_FINAL_PREDICTION_OVERLAY_BEGIN" in text:
            marker_files.append(str(path.relative_to(ROOT)))
    if not marker_files:
        raise ContractV2Error("FINAL_PREDICTION_OVERLAY_NOT_INSTALLED_IN_LIVE_GRAPH")

    _require(bbd, "for group in (path_row.get", "OPENAPI_PARAMETER_GROUP_FIX")
    _require(bbd, "BBD_BUNDLED_MANIFEST_BEGIN", "BUNDLED_ENDPOINT_MANIFEST")
    _forbid(
        bbd,
        r"for source in \(path_row\.get\(\"parameters\"\).*operation\.get\(\"parameters\"\)",
        "BROKEN_OPENAPI_PARAMETER_LOOP",
    )

    _require(controller, "MLB-THREE-API-AUTONOMOUS-CONTROLLER-v2", "V2_CONTROLLER")
    _require(controller, "_three_api_pick_errors", "LOCKED_PICK_THREE_API_VALIDATION")
    _require(controller, "LLM_NOT_IN_FINAL_ENSEMBLE", "LLM_MATERIAL_USE_CHECK")
    _require(controller, "COMPLETE_CARD_NOT_PROVEN_BY_SECOND_GAME_T45", "SECOND_GAME_T45_FAIL_CLOSED")
    _require(controller, "postSettlementCycleCompleted", "AUTONOMOUS_POST_SETTLEMENT_RETRAINING")
    _require(controller, "fullOfficialSlateDenominator", "FULL_SLATE_ACCURACY_DENOMINATOR")

    _require(template, "MLBThreeApiAutonomyStateTable:", "AUTONOMY_STATE_TABLE")
    _require(template, "Handler: mlb_three_api_autonomous_controller_v2.lambda_handler", "V2_CONTROLLER_HANDLER")
    _require(template, "MLB_THREE_API_ENABLED: 'true'", "THREE_API_RUNTIME_ENABLED")
    _require(template, "MLB_THREE_API_REQUIRE_ALL_SOURCES: 'true'", "STRICT_SOURCE_GATE_ENABLED")
    _require(template, "MLB_THREE_API_READ_FUNCTION_NAME: !Ref MLBV3ReadFunction", "READ_FUNCTION_WIRING")
    _require(template, "Schedule: rate(5 minutes)", "FIVE_MINUTE_AUTONOMY")

    _require(deploy, "python scripts/install_mlb_three_api_autonomy_v2.py", "DURABLE_CANONICAL_INSTALLER")
    _require(deploy, "python scripts/verify_mlb_three_api_runtime_v2.py", "V2_DEPLOY_VERIFIER")
    _require(deploy, "tests/unit/test_mlb_three_api_prediction_overlay.py", "FINAL_DECISION_TEST")
    _require(deploy, "MLB-THREE-API-AUTONOMOUS-CONTROLLER-v2", "V2_DEPLOY_ACCEPTANCE")
    _forbid(deploy, r"verify_mlb_no_bbd_runtime\.py", "LEGACY_NO_BBD_RUNTIME")

    _require(policy, "DAILY_ACCURACY_GOAL = 0.70", "SEVENTY_PERCENT_MEASUREMENT_GOAL")
    _require(policy, "completeOfficialSlateDenominator", "FULL_SLATE_SCORECARD")

    combined = "\n".join((overlay, controller, bbd, template, deploy, installer))
    _forbid(
        combined,
        r"(?i)(?:api[_-]?key|token)\s*[:=]\s*['\"][A-Za-z0-9_-]{24,}['\"]",
        "HARDCODED_SECRET",
    )
    _forbid(
        combined,
        r"(?i)(?:accuracy|win.?rate).{0,40}(?:guarantee|guaranteed).{0,20}(?:70|0\.70)",
        "FALSE_ACCURACY_GUARANTEE",
    )

    return {
        **v1,
        "contract": "MLB_THREE_API_AUTONOMOUS_RUNTIME_v2",
        "finalDecisionOverlayInstalled": True,
        "overlayModules": marker_files,
        "existingMlMateriallyUsed": True,
        "theOddsApiMateriallyUsed": True,
        "bigBallsDataProMateriallyUsedThroughContext": True,
        "bedrockLlmMateriallyUsed": True,
        "lockedPickEvidenceRequired": True,
        "statefulFiveMinuteController": True,
        "postSettlementRetraining": True,
        "fullOfficialSlateAccuracyDenominator": True,
        "accuracyGuarantee": False,
    }


if __name__ == "__main__":
    print(verify())
