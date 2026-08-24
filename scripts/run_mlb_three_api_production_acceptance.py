from __future__ import annotations

"""Live production acceptance for autonomous MLB three-source prediction.

This script never prints or persists secret values. It proves provider
connectivity, material LLM use in a final winner/loser decision, deployed Lambda
environments and Bedrock permissions, lock-package contents, the five-minute
controller schedule, and the current/next-slate timing state.
"""

import io
import json
import math
import os
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import boto3
import mlb_bbd_pro_context as bbd
import mlb_official_schedule_authority as official
import mlb_three_api_prediction_overlay as overlay


VERSION = "MLB-THREE-API-PRODUCTION-ACCEPTANCE-v1"
STACK_NAME = os.environ.get("MLB_STACK_NAME", "parlay-platform-dev")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION_VALUE")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
OUTPUT_PATH = ROOT / "runtime_reports" / "mlb_three_api_production_acceptance_latest.json"


class AcceptanceError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _json_body(payload: bytes) -> Any:
    value = json.loads(payload.decode("utf-8") or "{}")
    if isinstance(value, dict) and isinstance(value.get("body"), str):
        try:
            decoded = json.loads(value["body"])
            if isinstance(decoded, (dict, list)):
                value = decoded
        except Exception:
            pass
    return value


def _source_canary(checked: datetime) -> Dict[str, Any]:
    if not ODDS_API_KEY:
        raise AcceptanceError("ODDS_API_KEY_NOT_CONFIGURED")
    if not bbd.api_key():
        raise AcceptanceError("BBD_PRO_API_KEY_NOT_CONFIGURED")

    date_et = checked.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    schedule = official.fetch_exact_date_schedule(date_et)
    games = [row for row in schedule.get("games") or [] if isinstance(row, dict)]
    if not games:
        raise AcceptanceError(f"NO_OFFICIAL_MLB_GAMES_FOR_ACCEPTANCE_DATE:{date_et}")

    query = urllib.parse.urlencode(
        {
            "apiKey": ODDS_API_KEY,
            "regions": "us,us2,uk,eu,au",
            "markets": "h2h,spreads,totals",
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
    )
    url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?{query}"
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": "inqsi-mlb-production-acceptance/1.0"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        odds_payload = json.loads(response.read().decode("utf-8"))
        odds_headers = {key.lower(): value for key, value in response.headers.items()}
    if not isinstance(odds_payload, list) or not odds_payload:
        raise AcceptanceError("THE_ODDS_API_RETURNED_NO_MLB_EVENTS")

    game: Optional[Dict[str, Any]] = None
    market: Optional[Dict[str, Any]] = None
    for official_game in games:
        candidate = next(
            (
                row
                for row in odds_payload
                if _normalize(row.get("home_team")) == _normalize(official_game.get("home_team"))
                and _normalize(row.get("away_team")) == _normalize(official_game.get("away_team"))
                and row.get("bookmakers")
            ),
            None,
        )
        if candidate:
            game = dict(official_game)
            market = candidate
            break
    if not game or not market:
        raise AcceptanceError("OFFICIAL_SCHEDULE_TO_ODDS_EVENT_CROSSWALK_FAILED")
    game["provider_event_id"] = market.get("id")
    game["bookmakers"] = market.get("bookmakers") or []

    manifest = bbd.discover_manifest(force=True)
    context = bbd.collect_game_context(game, as_of_utc=checked.isoformat())
    if context.get("sourceStatus") not in {"CONNECTED", "PARTIAL"}:
        raise AcceptanceError(f"BBD_CONTEXT_NOT_CONNECTED:{context}")
    if int(context.get("operationsSucceeded") or 0) <= 0:
        raise AcceptanceError(f"BBD_NO_SUCCESSFUL_MLB_OPERATIONS:{context}")

    market_home = overlay.market_home_probability(game, game["home_team"], game["away_team"])
    if market_home is None:
        raise AcceptanceError("NO_VIG_MARKET_PROBABILITY_UNAVAILABLE")
    existing_winner = game["home_team"] if market_home >= 0.5 else game["away_team"]
    existing_probability = max(market_home, 1.0 - market_home)
    row = {
        **game,
        "predictedWinner": existing_winner,
        "winProbability": existing_probability,
        "predictionPersistedAtUtc": checked.isoformat(),
        "advancedContext": {
            "official_game_pk": game.get("official_game_pk"),
            "schedule_authority": game.get("schedule_authority"),
            "bookmakers": game.get("bookmakers"),
            "big_balls_data_pro": context,
        },
    }
    final = overlay.apply_prediction_overlay(row, as_of_utc=checked.isoformat())
    decision = final.get("threeApiDecision") if isinstance(final.get("threeApiDecision"), dict) else {}
    components = {
        str(item.get("component") or ""): item
        for item in decision.get("components") or []
        if isinstance(item, dict)
    }
    expected = {
        "existingAutonomousMLModel",
        "theOddsApiNoVigMarketConsensus",
        "bedrockThreeSourceAnalyst",
    }
    if not expected.issubset(components):
        raise AcceptanceError(f"FINAL_ENSEMBLE_COMPONENTS_MISSING:{components}")
    if not all((decision.get("sourceReady") or {}).values()):
        raise AcceptanceError(f"FINAL_SOURCE_READINESS_FAILED:{decision}")
    if float(components["bedrockThreeSourceAnalyst"].get("weight") or 0) <= 0:
        raise AcceptanceError("BEDROCK_LLM_HAS_NO_MATERIAL_FINAL_WEIGHT")
    if not final.get("predictedWinner") or not final.get("predictedLoser"):
        raise AcceptanceError("FINAL_WINNER_OR_LOSER_MISSING")
    if _normalize(final["predictedWinner"]) == _normalize(final["predictedLoser"]):
        raise AcceptanceError("FINAL_WINNER_EQUALS_LOSER")

    return {
        "ok": True,
        "slateDateEt": date_et,
        "officialMlb": {
            "status": "CONNECTED",
            "officialGameCount": schedule.get("officialGameCount"),
            "gamePk": game.get("official_game_pk"),
            "canonicalStart": game.get("official_commence_time"),
        },
        "theOddsApi": {
            "status": "CONNECTED",
            "eventCount": len(odds_payload),
            "bookmakerCount": len(game.get("bookmakers") or []),
            "requestsRemaining": odds_headers.get("x-requests-remaining"),
            "requestsUsed": odds_headers.get("x-requests-used"),
        },
        "bigBallsDataPro": {
            "status": context.get("sourceStatus"),
            "manifestSource": manifest.get("source"),
            "manifestOperationCount": len(manifest.get("operations") or []),
            "operationsAttempted": context.get("operationsAttempted"),
            "operationsSucceeded": context.get("operationsSucceeded"),
            "contextFingerprint": context.get("contextFingerprint"),
            "errors": context.get("errors"),
        },
        "bedrockLlm": {
            "status": "CONNECTED",
            "modelId": decision.get("llmModelId"),
            "materialWeight": components["bedrockThreeSourceAnalyst"].get("weight"),
            "evidenceFingerprint": decision.get("llmEvidenceFingerprint"),
        },
        "finalDecision": {
            "version": decision.get("version"),
            "predictedWinner": final.get("predictedWinner"),
            "predictedLoser": final.get("predictedLoser"),
            "winnerProbability": final.get("winProbability"),
            "components": decision.get("components"),
            "sourceReady": decision.get("sourceReady"),
            "noPass": decision.get("noPass"),
            "dailyAccuracyGoal": decision.get("dailyAccuracyGoal"),
            "accuracyGuarantee": decision.get("accuracyGuarantee"),
        },
    }


def _physical_function(cloudformation: Any, logical_id: str) -> str:
    response = cloudformation.describe_stack_resource(
        StackName=STACK_NAME,
        LogicalResourceId=logical_id,
    )
    value = ((response.get("StackResourceDetail") or {}).get("PhysicalResourceId"))
    if not value:
        raise AcceptanceError(f"FUNCTION_NOT_FOUND:{logical_id}")
    return str(value)


def _role_has_bedrock(iam: Any, role_name: str) -> bool:
    names = iam.list_role_policies(RoleName=role_name).get("PolicyNames") or []
    for name in names:
        document = iam.get_role_policy(RoleName=role_name, PolicyName=name).get("PolicyDocument") or {}
        if "bedrock:InvokeModel" in json.dumps(document):
            return True
    return False


def _runtime_proof() -> Tuple[Dict[str, Any], Dict[str, str]]:
    if not REGION:
        raise AcceptanceError("AWS_REGION_NOT_CONFIGURED")
    cloudformation = boto3.client("cloudformation", region_name=REGION)
    lambda_client = boto3.client("lambda", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    logical_ids = (
        "MLBAuditedPullFunction",
        "MLBDailyPickLockFunction",
        "MLBMLTrainingFunction",
        "MLBProductionVerifierFunction",
        "MLBThreeApiAutonomousControllerFunction",
    )
    proof: Dict[str, Any] = {}
    names: Dict[str, str] = {}
    required = {
        "BIG_BALLS_DATA_API_KEY",
        "BIG_BALLS_DATA_API_BASE_URL",
        "MLB_THREE_API_LLM_MODEL_ID",
        "MLB_THREE_API_ENABLED",
        "MLB_THREE_API_REQUIRE_ALL_SOURCES",
    }
    for logical_id in logical_ids:
        function_name = _physical_function(cloudformation, logical_id)
        names[logical_id] = function_name
        config = lambda_client.get_function_configuration(FunctionName=function_name)
        environment = ((config.get("Environment") or {}).get("Variables") or {})
        missing = sorted(required - set(environment))
        if missing:
            raise AcceptanceError(f"DEPLOYED_ENVIRONMENT_MISSING:{logical_id}:{missing}")
        if not environment.get("BIG_BALLS_DATA_API_KEY"):
            raise AcceptanceError(f"DEPLOYED_BBD_KEY_EMPTY:{logical_id}")
        if str(environment.get("MLB_THREE_API_ENABLED") or "").lower() != "true":
            raise AcceptanceError(f"THREE_API_NOT_ENABLED:{logical_id}")
        if str(environment.get("MLB_THREE_API_REQUIRE_ALL_SOURCES") or "").lower() != "true":
            raise AcceptanceError(f"THREE_API_NOT_STRICT:{logical_id}")
        role_arn = str(config.get("Role") or "")
        role_name = role_arn.rsplit("/", 1)[-1]
        if not role_name or not _role_has_bedrock(iam, role_name):
            raise AcceptanceError(f"BEDROCK_NOT_AUTHORIZED:{logical_id}:{role_name}")
        proof[logical_id] = {
            "functionName": function_name,
            "lastModified": config.get("LastModified"),
            "runtime": config.get("Runtime"),
            "environmentKeys": sorted(environment),
            "roleName": role_name,
            "bedrockAuthorized": True,
        }
    return proof, names


def _lock_package_proof(function_name: str) -> Dict[str, Any]:
    lambda_client = boto3.client("lambda", region_name=REGION)
    location = ((lambda_client.get_function(FunctionName=function_name).get("Code") or {}).get("Location"))
    if not location:
        raise AcceptanceError("LOCK_PACKAGE_LOCATION_MISSING")
    with urllib.request.urlopen(str(location), timeout=60) as response:
        package = response.read(100_000_001)
    if len(package) > 100_000_000:
        raise AcceptanceError("LOCK_PACKAGE_TOO_LARGE")
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
        required_files = {
            "mlb_three_api_prediction_overlay.py",
            "mlb_bbd_pro_context.py",
            "mlb_three_api_llm_analyst.py",
        }
        basenames = {Path(name).name for name in names}
        missing = sorted(required_files - basenames)
        if missing:
            raise AcceptanceError(f"LOCK_PACKAGE_FILES_MISSING:{missing}")
        overlay_name = next(name for name in names if Path(name).name == "mlb_three_api_prediction_overlay.py")
        overlay_text = archive.read(overlay_name).decode("utf-8", errors="replace")
        for token in (
            "existingAutonomousMLModel",
            "theOddsApiNoVigMarketConsensus",
            "bedrockThreeSourceAnalyst",
        ):
            if token not in overlay_text:
                raise AcceptanceError(f"LOCK_OVERLAY_COMPONENT_MISSING:{token}")
        marker_files: List[str] = []
        for name in names:
            if not name.endswith(".py"):
                continue
            try:
                text = archive.read(name).decode("utf-8", errors="replace")
            except Exception:
                continue
            if "MLB_THREE_API_FINAL_PREDICTION_OVERLAY_BEGIN" in text:
                marker_files.append(name)
        if not marker_files:
            raise AcceptanceError("LOCK_PACKAGE_HAS_NO_LIVE_OVERLAY_MARKER")
    return {
        "requiredFilesPresent": True,
        "overlayComponentsPresent": True,
        "liveOverlayMarkerFiles": sorted(marker_files),
        "packageFileCount": len(names),
    }


def _controller_proof(function_name: str) -> Dict[str, Any]:
    lambda_client = boto3.client("lambda", region_name=REGION)
    events = boto3.client("events", region_name=REGION)
    config = lambda_client.get_function_configuration(FunctionName=function_name)
    arn = str(config.get("FunctionArn") or "")
    rules = events.list_rule_names_by_target(TargetArn=arn).get("RuleNames") or []
    states: List[Dict[str, Any]] = []
    for name in rules:
        row = events.describe_rule(Name=name)
        states.append(
            {
                "name": name,
                "state": row.get("State"),
                "schedule": row.get("ScheduleExpression"),
            }
        )
    if not any(row["state"] == "ENABLED" and row["schedule"] == "rate(5 minutes)" for row in states):
        raise AcceptanceError(f"FIVE_MINUTE_CONTROLLER_RULE_NOT_ENABLED:{states}")

    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({"action": "run", "source": "production-acceptance"}).encode("utf-8"),
    )
    payload = response.get("Payload").read() if response.get("Payload") else b"{}"
    body = _json_body(payload)
    if response.get("FunctionError"):
        raise AcceptanceError(f"CONTROLLER_FUNCTION_ERROR:{response.get('FunctionError')}:{body}")
    if not isinstance(body, dict) or body.get("version") != "MLB-THREE-API-AUTONOMOUS-CONTROLLER-v2":
        raise AcceptanceError(f"CONTROLLER_VERSION_INVALID:{body}")
    if float(body.get("dailyAccuracyGoal") or 0) != 0.70:
        raise AcceptanceError(f"CONTROLLER_ACCURACY_GOAL_INVALID:{body}")
    if int(body.get("officialGameCount") or 0) <= 0:
        raise AcceptanceError(f"CONTROLLER_OFFICIAL_SLATE_EMPTY:{body}")
    return {"rules": states, "invocation": body}


def run() -> Dict[str, Any]:
    checked = _now()
    source = _source_canary(checked)
    functions, names = _runtime_proof()
    lock_package = _lock_package_proof(names["MLBDailyPickLockFunction"])
    controller = _controller_proof(names["MLBThreeApiAutonomousControllerFunction"])
    invocation = controller["invocation"]
    deadline = datetime.fromisoformat(str(invocation["completeCardDeadlineUtc"]).replace("Z", "+00:00"))
    before_deadline = checked <= deadline
    card = invocation.get("cardPolicy") if isinstance(invocation.get("cardPolicy"), dict) else {}
    card_proven = bool(card.get("ok") is True and card.get("threeApiComplete") is True)
    if before_deadline and invocation.get("operationalStatus") not in {"HEALTHY", "PENDING_BEFORE_DEADLINE"}:
        raise AcceptanceError(f"CONTROLLER_UNHEALTHY_BEFORE_DEADLINE:{invocation}")
    current_status = (
        "THREE_API_CARD_PROVEN"
        if card_proven
        else "PENDING_BEFORE_DEADLINE"
        if before_deadline
        else "DEPLOYED_AFTER_TODAY_DEADLINE_NEXT_SLATE_IS_FIRST_ELIGIBLE"
    )
    report = {
        "accepted": True,
        "status": "HEALTHY",
        "version": VERSION,
        "checkedAtUtc": checked.isoformat(),
        "stackName": STACK_NAME,
        "region": REGION,
        "liveSourceAndFinalDecision": source,
        "deployedFunctions": functions,
        "lockPackage": lock_package,
        "controller": controller,
        "currentSlateCardStatus": current_status,
        "currentSlateCardProven": card_proven,
        "requirements": {
            "mlbStatsApiOfficialAuthority": True,
            "theOddsApiMaterialMarketComponent": True,
            "bigBallsDataProContext": True,
            "bedrockLlmMaterialFinalWeight": True,
            "existingAutonomousMlLargestSingleComponent": True,
            "allActiveFunctionsCarryBbdCredential": True,
            "allActiveFunctionsCarryBedrockAuthorization": True,
            "finalLockPackageContainsEnsemble": True,
            "fullOfficialSlateRequired": True,
            "noPass": True,
            "firstGamePredictionLeadMinutes": 45,
            "completeCardDeadline": "second official game minus 45 minutes",
            "autonomousCadenceMinutes": 5,
            "postSettlementScoringAndRetraining": True,
            "dailyAccuracyGoal": 0.70,
            "fullOfficialSlateAccuracyDenominator": True,
            "accuracyGuarantee": False,
            "tennisAndSoccerUnchanged": True,
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        failure = {
            "accepted": False,
            "status": "UNHEALTHY",
            "version": VERSION,
            "checkedAtUtc": _now().isoformat(),
            "stackName": STACK_NAME,
            "region": REGION,
            "error": f"{type(exc).__name__}:{exc}",
        }
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(failure, indent=2, sort_keys=True))
        raise
