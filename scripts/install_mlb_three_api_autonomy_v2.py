from __future__ import annotations

"""Harden the v1 three-source installation for durable production use.

The v2 installer fixes OpenAPI parameter handling, installs the final-decision
ensemble into the actual dependency graph of MLBDailyPickLockFunction, upgrades
the controller to stateful v2, and teaches the normal deployment authority to
preserve the integration. It remains MLB-only.
"""

import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


ROOT = Path(__file__).resolve().parents[1]
V1_PATH = ROOT / "scripts" / "install_mlb_three_api_autonomy_v1.py"
REPORT_PATH = ROOT / "runtime_reports" / "mlb_three_api_install_v2_latest.json"
OVERLAY_MARKER = "MLB_THREE_API_FINAL_PREDICTION_OVERLAY_BEGIN"


class InstallV2Error(RuntimeError):
    pass


def _read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise InstallV2Error(f"REQUIRED_FILE_MISSING:{relative}")
    return path.read_text(encoding="utf-8")


def _write(relative: str, content: str) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_v1() -> Dict[str, Any]:
    spec = importlib.util.spec_from_file_location("mlb_three_api_installer_v1", V1_PATH)
    if spec is None or spec.loader is None:
        raise InstallV2Error("V1_INSTALLER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


def harden_bbd_adapter() -> Dict[str, bool]:
    relative = "hello_world/mlb_bbd_pro_context.py"
    text = _read(relative)
    original = text

    faulty = '''        parameters: List[Dict[str, Any]] = []
        for source in (path_row.get("parameters") or [], operation.get("parameters") or []):
            if isinstance(source, dict):
                parameters.append(copy.deepcopy(source))
'''
    fixed = '''        parameters: List[Dict[str, Any]] = []
        for group in (path_row.get("parameters") or [], operation.get("parameters") or []):
            if not isinstance(group, list):
                continue
            parameters.extend(
                copy.deepcopy(source)
                for source in group
                if isinstance(source, dict)
            )
'''
    if faulty in text:
        text = text.replace(faulty, fixed, 1)

    if "from pathlib import Path" not in text:
        anchor = "from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple\n"
        if anchor not in text:
            raise InstallV2Error("BBD_PATHLIB_IMPORT_ANCHOR_MISSING")
        text = text.replace(anchor, anchor + "from pathlib import Path\n", 1)

    if "BBD_BUNDLED_MANIFEST_BEGIN" not in text:
        old = '''    raw = _first_env(ENDPOINT_MANIFEST_ENV_NAMES)
    if not raw:
        return None
'''
        new = '''    raw = _first_env(ENDPOINT_MANIFEST_ENV_NAMES)
    # BBD_BUNDLED_MANIFEST_BEGIN
    if not raw:
        bundled = Path(__file__).with_name("bbd_mlb_endpoint_manifest.json")
        if bundled.is_file():
            parsed = json.loads(bundled.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                parsed = {"operations": parsed}
            if not isinstance(parsed, dict):
                raise RuntimeError("BBD_BUNDLED_ENDPOINT_MANIFEST_NOT_OBJECT")
            return parsed
        return None
    # BBD_BUNDLED_MANIFEST_END
'''
        if old not in text:
            raise InstallV2Error("BBD_MANIFEST_ANCHOR_MISSING")
        text = text.replace(old, new, 1)

    if '"x-rapidapi-key": key' not in text:
        anchor = '                "Authorization": f"Bearer {key}",\n'
        if anchor not in text:
            raise InstallV2Error("BBD_AUTH_HEADER_ANCHOR_MISSING")
        text = text.replace(
            anchor,
            anchor
            + '                "api-key": key,\n'
            + '                "x-rapidapi-key": key,\n',
            1,
        )

    ast.parse(text, filename=relative)
    if text != original:
        _write(relative, text)
    return {
        "parameterExtractionFixed": faulty not in text,
        "bundledManifestEnabled": "BBD_BUNDLED_MANIFEST_BEGIN" in text,
        "multiHeaderAuthenticationEnabled": '"x-rapidapi-key": key' in text,
    }


def _resource_block(template: str, logical_id: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(logical_id)}:\s*\n(.*?)(?=^  [A-Za-z0-9][A-Za-z0-9_-]*:\s*$|^Outputs:\s*$|\Z)",
        template,
    )
    if not match:
        raise InstallV2Error(f"SAM_RESOURCE_NOT_FOUND:{logical_id}")
    return match.group(0)


def _lock_handler_module(template: str) -> str:
    block = _resource_block(template, "MLBDailyPickLockFunction")
    match = re.search(r"(?m)^\s+Handler:\s*([A-Za-z_][A-Za-z0-9_.]*)\s*$", block)
    if not match:
        raise InstallV2Error("MLB_DAILY_PICK_LOCK_HANDLER_NOT_FOUND")
    handler = match.group(1)
    parts = handler.split(".")
    if len(parts) < 2:
        raise InstallV2Error(f"INVALID_LOCK_HANDLER:{handler}")
    return ".".join(parts[:-1])


def _local_module_path(module_name: str) -> Optional[Path]:
    candidate = ROOT / "hello_world" / (module_name.replace(".", "/") + ".py")
    if candidate.is_file():
        return candidate
    simple = ROOT / "hello_world" / (module_name.split(".")[-1] + ".py")
    return simple if simple.is_file() else None


def _local_dependencies(module_name: str) -> Set[str]:
    path = _local_module_path(module_name)
    if path is None:
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return set()
    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if _local_module_path(name) is not None:
                    found.add(name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            name = node.module
            if _local_module_path(name) is not None:
                found.add(name)
    return found


def _dependency_graph(root_module: str, max_modules: int = 120) -> List[str]:
    queue = [root_module]
    seen: Set[str] = set()
    while queue and len(seen) < max_modules:
        module = queue.pop(0)
        if module in seen:
            continue
        path = _local_module_path(module)
        if path is None:
            continue
        seen.add(module)
        for dependency in sorted(_local_dependencies(module)):
            if dependency not in seen:
                queue.append(dependency)
    return sorted(seen)


def _prediction_functions(path: Path) -> List[str]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except Exception:
        return []
    names: List[str] = []
    key_tokens = (
        "predictedWinner", "predicted_winner", "predictedSide", "predicted_side",
        "winProbability", "win_probability", "selectedTeam", "selected_team",
        "dailyPicks", "daily_picks", "prediction_count", "all_games_predicted",
    )
    name_tokens = (
        "predict", "select", "rank", "pick", "score_game", "build_card",
        "build_prediction", "lock_game", "lock_daily", "daily_card",
    )
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_three_api"):
            continue
        segment = ast.get_source_segment(source, node) or ""
        lower_name = node.name.lower()
        explicit = any(token in segment for token in key_tokens)
        semantic = any(token in lower_name for token in name_tokens) and (
            ("home" in segment.lower() and "away" in segment.lower())
            or "prediction" in segment.lower()
            or "pick" in segment.lower()
        )
        if explicit or semantic:
            names.append(node.name)
    return sorted(set(names))


def install_prediction_overlays() -> Dict[str, Any]:
    template = _read("template.yaml")
    root_module = _lock_handler_module(template)
    graph = _dependency_graph(root_module)
    patched: Dict[str, List[str]] = {}
    for module in graph:
        path = _local_module_path(module)
        if path is None:
            continue
        relative = str(path.relative_to(ROOT))
        if path.name.startswith("mlb_three_api_"):
            continue
        source = path.read_text(encoding="utf-8")
        if OVERLAY_MARKER in source:
            match = re.search(r"_three_api_prediction_functions\s*=\s*(\[[^\n]*\])", source)
            patched[relative] = json.loads(match.group(1).replace("'", '"')) if match else ["already-installed"]
            continue
        functions = _prediction_functions(path)
        if not functions:
            continue
        addition = f'''

# {OVERLAY_MARKER}
# Installed only along the MLBDailyPickLockFunction dependency graph.
import mlb_three_api_prediction_overlay as _three_api_prediction_overlay
_three_api_prediction_functions = {functions!r}
_three_api_prediction_overlay.install_named_overlays(
    globals(), __name__, _three_api_prediction_functions
)
# MLB_THREE_API_FINAL_PREDICTION_OVERLAY_END
'''
        combined = source.rstrip() + addition + "\n"
        ast.parse(combined, filename=relative)
        path.write_text(combined, encoding="utf-8")
        patched[relative] = functions
    if not patched:
        raise InstallV2Error(
            f"NO_LIVE_PREDICTION_FUNCTIONS_PATCHED:root={root_module}:graph={graph}"
        )
    return {
        "lockHandlerModule": root_module,
        "dependencyModules": graph,
        "patchedModules": patched,
        "patchedFunctionCount": sum(len(names) for names in patched.values()),
    }


def _insert_state_table(template: str) -> str:
    if "MLBThreeApiAutonomyStateTable:" in template:
        return template
    marker = re.search(r"(?m)^  MLBThreeApiAutonomousControllerFunction:\s*$", template)
    if not marker:
        raise InstallV2Error("CONTROLLER_RESOURCE_MARKER_MISSING")
    block = '''  MLBThreeApiAutonomyStateTable:
    Type: AWS::DynamoDB::Table
    DeletionPolicy: Retain
    UpdateReplacePolicy: Retain
    Properties:
      BillingMode: PAY_PER_REQUEST
      PointInTimeRecoverySpecification:
        PointInTimeRecoveryEnabled: true
      AttributeDefinitions:
        - AttributeName: PK
          AttributeType: S
        - AttributeName: SK
          AttributeType: S
      KeySchema:
        - AttributeName: PK
          KeyType: HASH
        - AttributeName: SK
          KeyType: RANGE

'''
    return template[:marker.start()] + block + template[marker.start():]


def _inject_runtime_flags(template: str) -> str:
    lines = template.splitlines(keepends=True)
    out: List[str] = []
    for index, line in enumerate(lines):
        out.append(line)
        if re.search(r"\bMLB_THREE_API_LLM_MODEL_ID:\s*", line):
            nearby = "".join(lines[index + 1:index + 6])
            indent = re.match(r"^(\s*)", line).group(1)
            if "MLB_THREE_API_ENABLED:" not in nearby:
                out.append(f"{indent}MLB_THREE_API_ENABLED: 'true'\n")
            if "MLB_THREE_API_REQUIRE_ALL_SOURCES:" not in nearby:
                out.append(f"{indent}MLB_THREE_API_REQUIRE_ALL_SOURCES: 'true'\n")
    return "".join(out)


def patch_template_v2() -> Dict[str, Any]:
    relative = "template.yaml"
    template = _read(relative)
    template = _insert_state_table(template)
    template = template.replace(
        "Handler: mlb_three_api_autonomous_controller.lambda_handler",
        "Handler: mlb_three_api_autonomous_controller_v2.lambda_handler",
    )
    template = _inject_runtime_flags(template)

    block = _resource_block(template, "MLBThreeApiAutonomousControllerFunction")
    new_block = block
    verify_line = "          MLB_THREE_API_VERIFY_FUNCTION_NAME: !Ref MLBProductionVerifierFunction\n"
    additions = (
        "          MLB_THREE_API_READ_FUNCTION_NAME: !Ref MLBV3ReadFunction\n"
        "          MLB_THREE_API_STATE_TABLE: !Ref MLBThreeApiAutonomyStateTable\n"
    )
    if "MLB_THREE_API_READ_FUNCTION_NAME:" not in new_block:
        if verify_line not in new_block:
            raise InstallV2Error("CONTROLLER_VERIFY_ENV_ANCHOR_MISSING")
        new_block = new_block.replace(verify_line, verify_line + additions, 1)

    statement_anchor = "        - Statement:\n"
    policies = (
        "        - LambdaInvokePolicy:\n"
        "            FunctionName: !Ref MLBV3ReadFunction\n"
        "        - DynamoDBCrudPolicy:\n"
        "            TableName: !Ref MLBThreeApiAutonomyStateTable\n"
    )
    if "TableName: !Ref MLBThreeApiAutonomyStateTable" not in new_block:
        if statement_anchor not in new_block:
            raise InstallV2Error("CONTROLLER_POLICY_ANCHOR_MISSING")
        new_block = new_block.replace(statement_anchor, policies + statement_anchor, 1)

    template = template.replace(block, new_block, 1)
    _write(relative, template)
    return {
        "controllerHandlerV2": "mlb_three_api_autonomous_controller_v2.lambda_handler" in template,
        "stateTable": "MLBThreeApiAutonomyStateTable:" in template,
        "fiveMinuteSchedule": "Schedule: rate(5 minutes)" in new_block,
        "strictSourceFlags": "MLB_THREE_API_REQUIRE_ALL_SOURCES: 'true'" in template,
        "readFunctionWired": "MLB_THREE_API_READ_FUNCTION_NAME: !Ref MLBV3ReadFunction" in new_block,
    }


def patch_normal_deploy_authority() -> Dict[str, Any]:
    relative = ".github/workflows/deploy.yml"
    text = _read(relative)
    original = text

    # Preserve the three-source overlay after the repository's legacy source
    # stabilizers and template patchers run.
    canonical_anchor = "          python scripts/patch_template_mlb_results_routes.py\n"
    v2_line = "          python scripts/install_mlb_three_api_autonomy_v2.py\n"
    if v2_line not in text:
        if canonical_anchor not in text:
            raise InstallV2Error("NORMAL_DEPLOY_CANONICAL_ANCHOR_MISSING")
        text = text.replace(canonical_anchor, canonical_anchor + v2_line, 1)

    # Expand accepted repository-secret names without exposing any value.
    text = re.sub(
        r"\$\{\{\s*secrets\.BIG_BALLS_DATA_API_KEY\s*\|\|\s*secrets\.BBD_API_KEY\s*\|\|\s*secrets\.BIGBALLS_DATA_API_KEY\s*\|\|\s*secrets\.BIG_BALLS_API_KEY\s*\}\}",
        "${{ secrets.BIG_BALLS_DATA_API_KEY || secrets.BBD_API_KEY || secrets.BIGBALLS_DATA_API_KEY || secrets.BIG_BALLS_API_KEY || secrets.BIGBALLS_API_KEY || secrets.BIGBALLSDATA_API_KEY || secrets.BIG_BALLS_DATA_KEY || secrets.BBD_API_TOKEN }}",
        text,
    )

    text = text.replace(
        "python scripts/verify_mlb_three_api_runtime.py",
        "python scripts/verify_mlb_three_api_runtime_v2.py",
    )
    text = text.replace(
        "tests/unit/test_mlb_three_api_runtime.py",
        "tests/unit/test_mlb_three_api_runtime_v2.py",
    )
    text = text.replace(
        "MLB-THREE-API-AUTONOMOUS-CONTROLLER-v1",
        "MLB-THREE-API-AUTONOMOUS-CONTROLLER-v2",
    )

    compile_anchor = "          python -m py_compile hello_world/mlb_three_api_autonomous_controller.py\n"
    compile_extra = (
        "          python -m py_compile hello_world/mlb_three_api_prediction_overlay.py\n"
        "          python -m py_compile hello_world/mlb_three_api_autonomous_controller_v2.py\n"
    )
    if "python -m py_compile hello_world/mlb_three_api_prediction_overlay.py" not in text:
        if compile_anchor not in text:
            raise InstallV2Error("NORMAL_DEPLOY_COMPILE_ANCHOR_MISSING")
        text = text.replace(compile_anchor, compile_anchor + compile_extra, 1)

    test_anchor = "            tests/unit/test_mlb_three_api_runtime_v2.py\n"
    test_extra = "            tests/unit/test_mlb_three_api_prediction_overlay.py\n"
    if test_extra.strip() not in text:
        if test_anchor not in text:
            raise InstallV2Error("NORMAL_DEPLOY_TEST_ANCHOR_MISSING")
        text = text.replace(test_anchor, test_anchor + test_extra, 1)

    if text != original:
        _write(relative, text)
    return {
        "v2CanonicalInstaller": v2_line in text,
        "v2Verifier": "verify_mlb_three_api_runtime_v2.py" in text,
        "v2ControllerExpected": "MLB-THREE-API-AUTONOMOUS-CONTROLLER-v2" in text,
        "overlayTest": "test_mlb_three_api_prediction_overlay.py" in text,
    }


def patch_workflow_authority_references() -> List[str]:
    changed: List[str] = []
    for relative in (
        "scripts/verify_mlb_workflow_authority.py",
        "tests/unit/test_mlb_workflow_authority.py",
    ):
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        new = text.replace(
            "verify_mlb_three_api_runtime.py",
            "verify_mlb_three_api_runtime_v2.py",
        ).replace(
            "test_mlb_three_api_runtime.py",
            "test_mlb_three_api_runtime_v2.py",
        )
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(relative)
    return changed


def verify_isolation() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for token in ("tennis_learning/", "tennis-template.yaml", "soccer_auto/", "soccer-auto-template.yaml"):
        if re.search(rf"_write\([^\n]*{re.escape(token)}", source):
            raise InstallV2Error(f"SPORT_ISOLATION_VIOLATION:{token}")


def main() -> Dict[str, Any]:
    verify_isolation()
    v1 = _run_v1()
    bbd = harden_bbd_adapter()
    overlay = install_prediction_overlays()
    template = patch_template_v2()
    workflow = patch_normal_deploy_authority()
    authority = patch_workflow_authority_references()

    for relative in (
        "hello_world/mlb_bbd_pro_context.py",
        "hello_world/mlb_three_api_prediction_overlay.py",
        "hello_world/mlb_three_api_autonomous_controller_v2.py",
        "scripts/install_mlb_three_api_autonomy_v2.py",
    ):
        ast.parse(_read(relative), filename=relative)

    result: Dict[str, Any] = {
        "ok": True,
        "installer": "MLB_THREE_API_AUTONOMY_INSTALLER_v2",
        "v1": v1,
        "bbdAdapter": bbd,
        "finalPredictionOverlay": overlay,
        "template": template,
        "normalDeployAuthority": workflow,
        "authorityFilesChanged": authority,
        "sportsIsolation": {"tennisTouched": False, "soccerTouched": False},
        "enforcedRequirements": {
            "officialMlbAuthority": True,
            "theOddsApiMarketComponent": True,
            "bigBallsDataProContext": True,
            "bedrockLlmMateriallyWeighted": True,
            "noPass": True,
            "fullOfficialSlate": True,
            "firstGameLeadMinutes": 45,
            "completeCardDeadline": "second official game minus 45 minutes",
            "autonomousCadenceMinutes": 5,
            "postSettlementRetraining": True,
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
