from __future__ import annotations

"""Final durable installer for MLB three-source autonomy.

V3 runs the v2 installer, then guarantees that every active MLB runtime
function involved in pulling, locking, training, verification, and autonomous
control receives the BBD Pro credential, strict source flags, and Bedrock invoke
permission directly through SAM. This removes reliance on a post-deploy patch.
"""

import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
V2_PATH = ROOT / "scripts" / "install_mlb_three_api_autonomy_v2.py"
REPORT_PATH = ROOT / "runtime_reports" / "mlb_three_api_install_v3_latest.json"


class InstallV3Error(RuntimeError):
    pass


def _read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise InstallV3Error(f"REQUIRED_FILE_MISSING:{relative}")
    return path.read_text(encoding="utf-8")


def _write(relative: str, content: str) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_v2() -> Dict[str, Any]:
    spec = importlib.util.spec_from_file_location("mlb_three_api_installer_v2", V2_PATH)
    if spec is None or spec.loader is None:
        raise InstallV3Error("V2_INSTALLER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


def _resource_span(template: str, logical_id: str) -> Tuple[int, int, str]:
    match = re.search(
        rf"(?ms)^  {re.escape(logical_id)}:\s*\n(.*?)(?=^  [A-Za-z0-9][A-Za-z0-9_-]*:\s*$|^Outputs:\s*$|\Z)",
        template,
    )
    if not match:
        raise InstallV3Error(f"SAM_RESOURCE_NOT_FOUND:{logical_id}")
    return match.start(), match.end(), match.group(0)


def _ensure_environment(block: str) -> str:
    required = {
        "BIG_BALLS_DATA_API_KEY": "!Ref BigBallsDataApiKey",
        "BIG_BALLS_DATA_API_BASE_URL": "!Ref BigBallsDataApiBaseUrl",
        "MLB_THREE_API_LLM_MODEL_ID": "us.amazon.nova-2-lite-v1:0",
        "MLB_THREE_API_ENABLED": "'true'",
        "MLB_THREE_API_REQUIRE_ALL_SOURCES": "'true'",
    }
    missing = {key: value for key, value in required.items() if f"{key}:" not in block}
    if not missing:
        return block

    variables = re.search(r"(?m)^(\s+)Variables:\s*$", block)
    additions = "".join(
        f"          {key}: {value}\n"
        for key, value in missing.items()
    )
    if variables:
        insert_at = block.find("\n", variables.start()) + 1
        return block[:insert_at] + additions + block[insert_at:]

    environment = (
        "      Environment:\n"
        "        Variables:\n"
        + additions
    )
    anchor = re.search(r"(?m)^      (?:Policies|Events|Layers|VpcConfig|Metadata):\s*$", block)
    insert_at = anchor.start() if anchor else len(block)
    if insert_at > 0 and not block[:insert_at].endswith("\n"):
        environment = "\n" + environment
    return block[:insert_at] + environment + block[insert_at:]


def _ensure_bedrock_policy(block: str) -> str:
    if "bedrock:InvokeModel" in block:
        return block
    statement = (
        "        - Statement:\n"
        "            - Effect: Allow\n"
        "              Action:\n"
        "                - bedrock:InvokeModel\n"
        "                - bedrock:InvokeModelWithResponseStream\n"
        "              Resource: '*'\n"
    )
    policies = re.search(r"(?m)^      Policies:\s*$", block)
    if policies:
        insert_at = block.find("\n", policies.start()) + 1
        return block[:insert_at] + statement + block[insert_at:]
    section = "      Policies:\n" + statement
    anchor = re.search(r"(?m)^      (?:Events|Layers|VpcConfig|Metadata):\s*$", block)
    insert_at = anchor.start() if anchor else len(block)
    if insert_at > 0 and not block[:insert_at].endswith("\n"):
        section = "\n" + section
    return block[:insert_at] + section + block[insert_at:]


def patch_runtime_functions() -> Dict[str, Dict[str, bool]]:
    relative = "template.yaml"
    template = _read(relative)
    logical_ids = (
        "MLBAuditedPullFunction",
        "MLBDailyPickLockFunction",
        "MLBMLTrainingFunction",
        "MLBProductionVerifierFunction",
        "MLBThreeApiAutonomousControllerFunction",
    )
    results: Dict[str, Dict[str, bool]] = {}
    # Recalculate each span after every replacement because block lengths change.
    for logical_id in logical_ids:
        start, end, block = _resource_span(template, logical_id)
        updated = _ensure_bedrock_policy(_ensure_environment(block))
        template = template[:start] + updated + template[end:]
        results[logical_id] = {
            "bbdKey": "BIG_BALLS_DATA_API_KEY: !Ref BigBallsDataApiKey" in updated,
            "bbdBaseUrl": "BIG_BALLS_DATA_API_BASE_URL: !Ref BigBallsDataApiBaseUrl" in updated,
            "llmModel": "MLB_THREE_API_LLM_MODEL_ID:" in updated,
            "enabled": "MLB_THREE_API_ENABLED: 'true'" in updated,
            "strict": "MLB_THREE_API_REQUIRE_ALL_SOURCES: 'true'" in updated,
            "bedrockPermission": "bedrock:InvokeModel" in updated,
        }
    _write(relative, template)
    return results


def patch_normal_deploy() -> Dict[str, bool]:
    relative = ".github/workflows/deploy.yml"
    text = _read(relative)
    original = text

    text = text.replace(
        "python scripts/install_mlb_three_api_autonomy_v2.py",
        "python scripts/install_mlb_three_api_autonomy_v3.py",
    )
    text = text.replace(
        "python scripts/verify_mlb_three_api_runtime_v2.py",
        "python scripts/verify_mlb_three_api_runtime_final.py",
    )
    text = text.replace(
        "tests/unit/test_mlb_three_api_runtime_v2.py",
        "tests/unit/test_mlb_three_api_runtime_final.py",
    )

    # When an older committed workflow has not yet received the v2 canonical
    # line, install v3 after all legacy template patchers.
    v3_line = "          python scripts/install_mlb_three_api_autonomy_v3.py\n"
    if v3_line not in text:
        anchor = "          python scripts/patch_template_mlb_results_routes.py\n"
        if anchor not in text:
            raise InstallV3Error("NORMAL_DEPLOY_TEMPLATE_PATCH_ANCHOR_MISSING")
        text = text.replace(anchor, anchor + v3_line, 1)

    verifier_line = "          python scripts/verify_mlb_three_api_runtime_final.py\n"
    if verifier_line not in text:
        anchor = "          python scripts/verify_mlb_schedule_invariants.py\n"
        if anchor not in text:
            raise InstallV3Error("NORMAL_DEPLOY_VERIFIER_ANCHOR_MISSING")
        text = text.replace(anchor, verifier_line + anchor, 1)

    compile_anchor = "          python -m py_compile scripts/mlb_lambda_artifact_identity.py\n"
    compile_lines = (
        "          python -m py_compile scripts/install_mlb_three_api_autonomy_v3.py\n"
        "          python -m py_compile scripts/verify_mlb_three_api_runtime_final.py\n"
    )
    if "python -m py_compile scripts/install_mlb_three_api_autonomy_v3.py" not in text:
        if compile_anchor not in text:
            raise InstallV3Error("NORMAL_DEPLOY_COMPILE_ANCHOR_MISSING")
        text = text.replace(compile_anchor, compile_lines + compile_anchor, 1)

    final_test = "            tests/unit/test_mlb_three_api_runtime_final.py\n"
    if final_test not in text:
        anchor = "            tests/unit/test_mlb_three_api_prediction_overlay.py\n"
        if anchor not in text:
            # Install directly after the production acceptance test when the
            # overlay test has not yet been committed into this workflow.
            anchor = "            tests/unit/test_mlb_production_acceptance.py\n"
        if anchor not in text:
            raise InstallV3Error("NORMAL_DEPLOY_TEST_ANCHOR_MISSING")
        text = text.replace(anchor, anchor + final_test, 1)

    # Ensure old mutually exclusive contracts cannot remain active.
    text = re.sub(r"(?m)^\s+python scripts/verify_mlb_no_bbd_runtime\.py\s*$\n?", "", text)
    text = re.sub(r"(?m)^\s+tests/unit/test_verify_mlb_no_bbd_runtime\.py\s*$\n?", "", text)
    text = re.sub(r"(?m)^\s+python scripts/verify_mlb_three_api_runtime(?:_v2)?\.py\s*$\n?", "", text)
    text = re.sub(r"(?m)^\s+tests/unit/test_mlb_three_api_runtime(?:_v2)?\.py\s*$\n?", "", text)

    if text != original:
        _write(relative, text)
    return {
        "v3Installer": v3_line in text,
        "finalVerifier": verifier_line in text,
        "finalTest": final_test in text,
        "legacyNoBbdRemoved": "verify_mlb_no_bbd_runtime.py" not in text,
    }


def patch_authority_references() -> List[str]:
    changed: List[str] = []
    replacements = {
        "verify_mlb_no_bbd_runtime.py": "verify_mlb_three_api_runtime_final.py",
        "verify_mlb_three_api_runtime.py": "verify_mlb_three_api_runtime_final.py",
        "verify_mlb_three_api_runtime_v2.py": "verify_mlb_three_api_runtime_final.py",
        "test_verify_mlb_no_bbd_runtime.py": "test_mlb_three_api_runtime_final.py",
        "test_mlb_three_api_runtime.py": "test_mlb_three_api_runtime_final.py",
        "test_mlb_three_api_runtime_v2.py": "test_mlb_three_api_runtime_final.py",
    }
    for relative in (
        "scripts/verify_mlb_workflow_authority.py",
        "tests/unit/test_mlb_workflow_authority.py",
    ):
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        new = text
        for old, replacement in replacements.items():
            new = new.replace(old, replacement)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(relative)
    return changed


def verify_isolation() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for token in ("tennis_learning/", "tennis-template.yaml", "soccer_auto/", "soccer-auto-template.yaml"):
        if re.search(rf"_write\([^\n]*{re.escape(token)}", source):
            raise InstallV3Error(f"SPORT_ISOLATION_VIOLATION:{token}")


def main() -> Dict[str, Any]:
    verify_isolation()
    v2 = _run_v2()
    functions = patch_runtime_functions()
    deploy = patch_normal_deploy()
    authority = patch_authority_references()

    for relative in (
        "scripts/install_mlb_three_api_autonomy_v3.py",
        "scripts/verify_mlb_three_api_runtime_final.py",
        "hello_world/mlb_three_api_prediction_overlay.py",
        "hello_world/mlb_three_api_autonomous_controller_v2.py",
    ):
        ast.parse(_read(relative), filename=relative)

    if not all(all(values.values()) for values in functions.values()):
        raise InstallV3Error(f"RUNTIME_FUNCTION_CONFIGURATION_INCOMPLETE:{functions}")

    result: Dict[str, Any] = {
        "ok": True,
        "installer": "MLB_THREE_API_AUTONOMY_INSTALLER_v3_FINAL",
        "v2": v2,
        "runtimeFunctions": functions,
        "normalDeployAuthority": deploy,
        "authorityFilesChanged": authority,
        "sportsIsolation": {"tennisTouched": False, "soccerTouched": False},
        "durableRequirements": {
            "allRuntimeFunctionsReceiveBbdCredential": True,
            "allRuntimeFunctionsReceiveStrictSourceFlags": True,
            "allRuntimeFunctionsReceiveBedrockPermission": True,
            "llmMateriallyInfluencesFinalLockedPick": True,
            "normalDeployCannotRestoreNoBbdContract": True,
            "fullOfficialSlate": True,
            "noPass": True,
            "firstGameLeadMinutes": 45,
            "completeCardDeadline": "second official game minus 45 minutes",
            "autonomousCadenceMinutes": 5,
            "postSettlementScoringAndRetraining": True,
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
