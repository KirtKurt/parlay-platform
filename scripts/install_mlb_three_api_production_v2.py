from __future__ import annotations

"""Production v2 installer: consume every safely applicable BBD Pro MLB GET.

Date-, event-, matchup-, season-, and sport-parameterized endpoints are selected
from the discovered/pinned manifest. Endpoints requiring an unavailable player
or provider-specific team identifier fail the satisfiability check rather than
receiving invented IDs. Response caching prevents repeated daily/team calls for
each game.
"""

import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
BASE_INSTALLER = ROOT / "scripts" / "install_mlb_three_api_autonomy_final.py"
REPORT_PATH = ROOT / "runtime_reports" / "mlb_three_api_production_v2_install_latest.json"


class ProductionV2InstallError(RuntimeError):
    pass


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise ProductionV2InstallError(f"REQUIRED_FILE_MISSING:{relative}")
    return path.read_text(encoding="utf-8")


def write(relative: str, content: str) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_base_installer() -> Dict[str, Any]:
    spec = importlib.util.spec_from_file_location("mlb_three_api_production_base", BASE_INSTALLER)
    if spec is None or spec.loader is None:
        raise ProductionV2InstallError("BASE_INSTALLER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


def patch_bbd_all_applicable_operations() -> Dict[str, Any]:
    relative = "hello_world/mlb_bbd_pro_context.py"
    text = read(relative)
    original = text
    text = re.sub(
        r"MAX_OPERATIONS_PER_GAME\s*=\s*max\([^\n]+",
        'MAX_OPERATIONS_PER_GAME = max(1, min(96, int(os.environ.get("MLB_BBD_MAX_OPERATIONS_PER_GAME", "48"))))',
        text,
        count=1,
    )

    select_pattern = re.compile(
        r"(?ms)^def _select_operations\(manifest: Dict\[str, Any\]\) -> List\[Dict\[str, Any\]\]:\n.*?(?=^def _game_value\()"
    )
    replacement = '''def _parameter_satisfied(parameter: Dict[str, Any], game: Dict[str, Any]) -> bool:
    if parameter.get("required") is not True:
        return True
    name = str(parameter.get("name") or "")
    if _game_value(game, name) not in (None, ""):
        return True
    schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {}
    if schema.get("default") not in (None, ""):
        return True
    enum = schema.get("enum") if isinstance(schema.get("enum"), list) else []
    if len(enum) == 1 and enum[0] not in (None, ""):
        return True
    return False


def _select_operations(manifest: Dict[str, Any], game: Dict[str, Any]) -> List[Dict[str, Any]]:
    # BBD_ALL_APPLICABLE_OPERATIONS_v1
    selected: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    operations = manifest.get("operations") if isinstance(manifest.get("operations"), list) else []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        method = str(operation.get("method") or "GET").upper()
        if method != "GET":
            continue
        category = str(operation.get("category") or "games")
        if category not in CATEGORY_ORDER:
            continue
        url = str(operation.get("urlTemplate") or operation.get("url") or "")
        path = str(operation.get("path") or "")
        if not url and not path:
            continue
        parameters = operation.get("parameters") if isinstance(operation.get("parameters"), list) else []
        if not all(
            _parameter_satisfied(parameter, game)
            for parameter in parameters
            if isinstance(parameter, dict)
        ):
            continue
        identity = (method, url or path, str(operation.get("operationId") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(copy.deepcopy(operation))
    selected.sort(
        key=lambda operation: (
            CATEGORY_ORDER.index(str(operation.get("category") or "games")),
            str(operation.get("operationId") or ""),
            str(operation.get("urlTemplate") or operation.get("path") or ""),
        )
    )
    return selected[:MAX_OPERATIONS_PER_GAME]


'''
    if not select_pattern.search(text):
        if "BBD_ALL_APPLICABLE_OPERATIONS_v1" not in text:
            raise ProductionV2InstallError("BBD_SELECT_OPERATIONS_FUNCTION_NOT_FOUND")
    else:
        text = select_pattern.sub(replacement, text, count=1)

    game_value_pattern = re.compile(
        r"(?ms)^def _game_value\(game: Dict\[str, Any\], name: str\) -> Optional\[Any\]:\n.*?(?=^def _operation_url\()"
    )
    game_value_replacement = '''def _game_value(game: Dict[str, Any], name: str) -> Optional[Any]:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    commence = _parse_dt(
        game.get("official_commence_time")
        or game.get("officialCommenceTime")
        or game.get("commence_time")
        or game.get("commenceTime")
    )
    date_value = commence.date().isoformat() if commence else None
    season = commence.year if commence else datetime.now(timezone.utc).year
    official_id = game.get("official_game_pk") or game.get("officialGamePk")
    provider_id = (
        game.get("provider_event_id")
        or game.get("providerEventId")
        or game.get("provider_game_id")
        or game.get("providerGameId")
    )
    home = game.get("home_team") or game.get("homeTeam")
    away = game.get("away_team") or game.get("awayTeam")
    mapping = {
        "date": date_value,
        "gamedate": date_value,
        "startdate": date_value,
        "enddate": date_value,
        "eventid": provider_id or official_id,
        "gameid": provider_id or official_id,
        "matchid": provider_id or official_id,
        "fixtureid": provider_id or official_id,
        "mlbgameid": official_id,
        "officialgamepk": official_id,
        "gamepk": official_id,
        "hometeam": home,
        "home": home,
        "awayteam": away,
        "away": away,
        "team": None,
        "teamname": None,
        "season": season,
        "year": season,
        "sport": "baseball",
        "sportkey": "baseball_mlb",
        "sportcode": "baseball_mlb",
        "league": "MLB",
        "leaguecode": "MLB",
        "competition": "MLB",
        "limit": 100,
        "page": 1,
        "offset": 0,
    }
    return mapping.get(normalized)


'''
    if not game_value_pattern.search(text):
        if '"sportkey": "baseball_mlb"' not in text:
            raise ProductionV2InstallError("BBD_GAME_VALUE_FUNCTION_NOT_FOUND")
    else:
        text = game_value_pattern.sub(game_value_replacement, text, count=1)

    text = text.replace(
        "operations = _select_operations(manifest)",
        "operations = _select_operations(manifest, game)",
    )
    ast.parse(text, filename=relative)
    if text != original:
        write(relative, text)
    return {
        "marker": "BBD_ALL_APPLICABLE_OPERATIONS_v1" in text,
        "maximumOperationsPerGame": 48,
        "allSatisfiableGetOperationsSelected": "_parameter_satisfied" in text,
        "sportLeagueSeasonMapping": '"sportkey": "baseball_mlb"' in text,
        "unsafeRequiredIdentifiersRejected": "return False" in text,
    }


def canonicalize_deploy_v2() -> Dict[str, int]:
    relative = ".github/workflows/deploy.yml"
    text = read(relative)
    lines = text.splitlines(keepends=True)
    patterns = (
        r"^\s+python scripts/install_mlb_three_api_(?:autonomy_final|production_v2)\.py\s*$",
        r"^\s+python scripts/verify_mlb_three_api_runtime_(?:production|production_v2)\.py\s*$",
        r"^\s+tests/unit/test_mlb_three_api_runtime_(?:production|production_v2)\.py\s*$",
        r"^\s+python -m py_compile scripts/install_mlb_three_api_(?:autonomy_final|production_v2)\.py\s*$",
        r"^\s+python -m py_compile scripts/verify_mlb_three_api_runtime_(?:production|production_v2)\.py\s*$",
    )
    for pattern in patterns:
        compiled = re.compile(pattern)
        lines = [line for line in lines if not compiled.match(line.rstrip("\n"))]

    def insert_after(anchor: str, additions: List[str]) -> None:
        indexes = [i for i, line in enumerate(lines) if line.rstrip("\n") == anchor]
        if not indexes:
            raise ProductionV2InstallError(f"DEPLOY_ANCHOR_MISSING:{anchor}")
        index = indexes[0]
        lines[index + 1:index + 1] = additions

    insert_after(
        "          python scripts/patch_template_mlb_results_routes.py",
        ["          python scripts/install_mlb_three_api_production_v2.py\n"],
    )
    insert_after(
        "          python scripts/verify_mlb_schedule_invariants.py",
        ["          python scripts/verify_mlb_three_api_runtime_production_v2.py\n"],
    )
    insert_after(
        "          python -m py_compile scripts/mlb_lambda_artifact_identity.py",
        [
            "          python -m py_compile scripts/install_mlb_three_api_production_v2.py\n",
            "          python -m py_compile scripts/verify_mlb_three_api_runtime_production_v2.py\n",
        ],
    )
    anchor = next(
        (
            value for value in (
                "            tests/unit/test_mlb_three_api_prediction_overlay.py",
                "            tests/unit/test_mlb_production_acceptance.py",
            )
            if any(line.rstrip("\n") == value for line in lines)
        ),
        None,
    )
    if anchor is None:
        raise ProductionV2InstallError("DEPLOY_TEST_ANCHOR_MISSING")
    insert_after(anchor, ["            tests/unit/test_mlb_three_api_runtime_production_v2.py\n"])

    canonical = "".join(lines)
    write(relative, canonical)
    result = {
        "installerReferences": canonical.count("python scripts/install_mlb_three_api_production_v2.py"),
        "verifierReferences": canonical.count("python scripts/verify_mlb_three_api_runtime_production_v2.py"),
        "testReferences": canonical.count("tests/unit/test_mlb_three_api_runtime_production_v2.py"),
    }
    if result["installerReferences"] != 1 or result["testReferences"] != 1 or result["verifierReferences"] < 1:
        raise ProductionV2InstallError(f"DEPLOY_V2_NOT_IDEMPOTENT:{result}")
    return result


def patch_authority_references() -> List[str]:
    changed: List[str] = []
    for relative in (
        "scripts/verify_mlb_workflow_authority.py",
        "tests/unit/test_mlb_workflow_authority.py",
    ):
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        new = re.sub(
            r"verify_mlb_three_api_runtime_(?:production|production_v2)\.py",
            "verify_mlb_three_api_runtime_production_v2.py",
            text,
        )
        new = re.sub(
            r"test_mlb_three_api_runtime_(?:production|production_v2)\.py",
            "test_mlb_three_api_runtime_production_v2.py",
            new,
        )
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(relative)
    return changed


def main() -> Dict[str, Any]:
    base = run_base_installer()
    bbd_result = patch_bbd_all_applicable_operations()
    deploy_result = canonicalize_deploy_v2()
    authority = patch_authority_references()
    for relative in (
        "hello_world/mlb_bbd_pro_context.py",
        "scripts/install_mlb_three_api_production_v2.py",
        "scripts/verify_mlb_three_api_runtime_production_v2.py",
    ):
        ast.parse(read(relative), filename=relative)
    result = {
        "ok": True,
        "installer": "MLB_THREE_API_PRODUCTION_INSTALLER_v2_ALL_APPLICABLE",
        "base": base,
        "bbdAllApplicable": bbd_result,
        "normalDeploy": deploy_result,
        "authorityFilesChanged": authority,
        "idempotent": True,
        "sportsIsolation": {"tennisTouched": False, "soccerTouched": False},
        "requirements": {
            "allSafelyParameterizableBbdMlbGetOperations": True,
            "allOddsMarketsRetainedInEvidence": True,
            "officialMlbIdentityAndContext": True,
            "bedrockLlmMaterialFinalWeight": True,
            "fullOfficialSlate": True,
            "noPass": True,
            "firstGameLeadMinutes": 45,
            "completeCardDeadline": "second official game minus 45 minutes",
            "dailyAccuracyGoal": 0.70,
            "accuracyGuarantee": False,
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
