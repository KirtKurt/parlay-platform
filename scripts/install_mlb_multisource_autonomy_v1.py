#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import pathlib
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


ROOT = pathlib.Path(__file__).resolve().parents[1]
MARKER = "MLB-MULTISOURCE-AUTONOMY-WRAPPER-v1"
BRANCH_WORKFLOW = ".github/workflows/install-mlb-multisource-autonomy-v1.yml"
SELF_PATH = "scripts/install_mlb_multisource_autonomy_v1.py"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def write(relative: str, content: str) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def patch_llm_client_creation() -> None:
    path = "hello_world/mlb_autonomous_llm_decision_v1.py"
    text = read(path)
    old = '    client = bedrock_client or boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))\n    errors: List[Dict[str, Any]] = []\n'
    new = '''    errors: List[Dict[str, Any]] = []
    if bedrock_client is not None:
        client = bedrock_client
    else:
        try:
            client = boto3.client(
                "bedrock-runtime",
                region_name=(
                    os.environ.get("AWS_REGION")
                    or os.environ.get("AWS_DEFAULT_REGION")
                    or "us-east-1"
                ),
            )
        except Exception as exc:
            return {
                "ok": False,
                "modelId": None,
                "response": {},
                "usage": {},
                "errors": [
                    {
                        "modelId": None,
                        "errorType": type(exc).__name__,
                        "error": str(exc)[:500],
                    }
                ],
            }
'''
    require(old in text or "region_name=(" in text, "LLM Bedrock client anchor not found")
    if old in text:
        text = text.replace(old, new, 1)
    write(path, text)


def patch_deadline_test_import() -> None:
    path = "tests/unit/test_mlb_daily_card_deadline_v1.py"
    text = read(path)
    old = "from datetime import datetime, timezone\n\nfrom hello_world import mlb_daily_card_deadline_v1 as policy\n"
    new = '''from datetime import datetime, timezone
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))

import mlb_daily_card_deadline_v1 as policy
'''
    if old in text:
        text = text.replace(old, new, 1)
    write(path, text)


def _section_bounds(lines: List[str], key: str) -> Tuple[int, int]:
    start = next((index for index, line in enumerate(lines) if line == f"{key}:"), -1)
    require(start >= 0, f"top-level section missing: {key}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index] and not lines[index].startswith((" ", "\t", "#")) and re.match(r"^[A-Za-z0-9_-]+:\s*$", lines[index]):
            end = index
            break
    return start, end


def _resource_bounds(lines: List[str], resource: str) -> Tuple[int, int]:
    resources_start, resources_end = _section_bounds(lines, "Resources")
    target = f"  {resource}:"
    start = next((index for index in range(resources_start + 1, resources_end) if lines[index] == target), -1)
    require(start >= 0, f"SAM resource missing: {resource}")
    end = resources_end
    for index in range(start + 1, resources_end):
        if re.match(r"^  [A-Za-z0-9][A-Za-z0-9_-]*:\s*$", lines[index]):
            end = index
            break
    return start, end


def _ensure_env(lines: List[str], resource: str, variables: Dict[str, str]) -> List[str]:
    start, end = _resource_bounds(lines, resource)
    block = lines[start:end]
    properties_index = next((index for index, line in enumerate(block) if line == "    Properties:"), -1)
    require(properties_index >= 0, f"Properties missing for {resource}")
    environment_index = next((index for index, line in enumerate(block) if line == "      Environment:"), -1)
    if environment_index < 0:
        insertion = ["      Environment:", "        Variables:"] + [f"          {name}: {value}" for name, value in variables.items()]
        block[properties_index + 1:properties_index + 1] = insertion
    else:
        variables_index = next((index for index in range(environment_index + 1, len(block)) if block[index] == "        Variables:"), -1)
        if variables_index < 0:
            block[environment_index + 1:environment_index + 1] = ["        Variables:"] + [f"          {name}: {value}" for name, value in variables.items()]
        else:
            insertion_at = len(block)
            for index in range(variables_index + 1, len(block)):
                line = block[index]
                if line and len(line) - len(line.lstrip(" ")) <= 8:
                    insertion_at = index
                    break
            existing = {
                match.group(1)
                for line in block[variables_index + 1:insertion_at]
                for match in [re.match(r"^          ([A-Za-z0-9_]+):", line)]
                if match
            }
            additions = [f"          {name}: {value}" for name, value in variables.items() if name not in existing]
            block[insertion_at:insertion_at] = additions
    return lines[:start] + block + lines[end:]


def _ensure_bedrock_policy(lines: List[str], resource: str) -> List[str]:
    start, end = _resource_bounds(lines, resource)
    block = lines[start:end]
    if any("bedrock:InvokeModel" in line for line in block):
        return lines
    properties_index = next((index for index, line in enumerate(block) if line == "    Properties:"), -1)
    require(properties_index >= 0, f"Properties missing for {resource}")
    policies_index = next((index for index, line in enumerate(block) if line == "      Policies:"), -1)
    statement = [
        "        - Statement:",
        "            - Effect: Allow",
        "              Action:",
        "                - bedrock:InvokeModel",
        "                - bedrock:InvokeModelWithResponseStream",
        "              Resource: '*'",
    ]
    if policies_index >= 0:
        insertion_at = len(block)
        for index in range(policies_index + 1, len(block)):
            line = block[index]
            if line and len(line) - len(line.lstrip(" ")) <= 6:
                insertion_at = index
                break
        block[insertion_at:insertion_at] = statement
    else:
        # Place policies after any Environment block and before the next
        # six-space property. Inserting immediately after Properties is valid
        # regardless of property order.
        block[properties_index + 1:properties_index + 1] = ["      Policies:"] + statement
    return lines[:start] + block + lines[end:]


def patch_template() -> None:
    path = "template.yaml"
    lines = read(path).splitlines()
    parameters_start, _ = _section_bounds(lines, "Parameters")
    if not any(line == "  BigBallsDataApiKey:" for line in lines):
        parameter_block = [
            "  BigBallsDataApiKey:",
            "    Type: String",
            "    NoEcho: true",
            "    Default: ''",
            "    Description: Big Balls Data Pro API key for MLB pregame context",
            "  MlbLlmModelId:",
            "    Type: String",
            "    Default: us.amazon.nova-2-lite-v1:0",
            "    Description: Bedrock model used by the autonomous MLB decision layer",
        ]
        lines[parameters_start + 1:parameters_start + 1] = parameter_block

    required_resources = ("MLBDailyPickLockFunction", "MLBAuditedPullFunction")
    for resource in required_resources:
        _resource_bounds(lines, resource)

    lines = _ensure_env(
        lines,
        "MLBDailyPickLockFunction",
        {
            "BIG_BALLS_DATA_API_KEY": "!Ref BigBallsDataApiKey",
            "MLB_LLM_MODEL_ID": "!Ref MlbLlmModelId",
            "MLB_AUTONOMOUS_LLM_ENABLED": "'true'",
            "MLB_DAILY_ACCURACY_TARGET": "'0.70'",
            "MLB_CARD_LEAD_MINUTES": "'45'",
        },
    )
    lines = _ensure_bedrock_policy(lines, "MLBDailyPickLockFunction")
    lines = _ensure_env(
        lines,
        "MLBAuditedPullFunction",
        {
            "BIG_BALLS_DATA_API_KEY": "!Ref BigBallsDataApiKey",
            "MLB_CARD_LEAD_MINUTES": "'45'",
        },
    )
    for optional in ("MLBMLTrainingFunction", "MLBProductionVerifierFunction"):
        try:
            _resource_bounds(lines, optional)
        except RuntimeError:
            continue
        lines = _ensure_env(
            lines,
            optional,
            {
                "BIG_BALLS_DATA_API_KEY": "!Ref BigBallsDataApiKey",
                "MLB_DAILY_ACCURACY_TARGET": "'0.70'",
            },
        )

    if not any(line == "  MLBAutonomousOrchestratorFunction:" for line in lines):
        _, resources_end = _section_bounds(lines, "Resources")
        orchestrator = [
            "  MLBAutonomousOrchestratorFunction:",
            "    Type: AWS::Serverless::Function",
            "    Properties:",
            "      CodeUri: hello_world/",
            "      Handler: mlb_autonomous_orchestrator_v1.lambda_handler",
            "      Runtime: python3.11",
            "      Timeout: 180",
            "      MemorySize: 512",
            "      Environment:",
            "        Variables:",
            "          MLB_DAILY_PICK_LOCK_FUNCTION: !Ref MLBDailyPickLockFunction",
            "          MLB_AUDITED_PULL_FUNCTION: !Ref MLBAuditedPullFunction",
            "          MLB_DAILY_ACCURACY_TARGET: '0.70'",
            "          MLB_CARD_LEAD_MINUTES: '45'",
            "          MLB_COLLECTION_START_HOURS: '12'",
            "      Policies:",
            "        - Statement:",
            "            - Effect: Allow",
            "              Action:",
            "                - lambda:InvokeFunction",
            "              Resource:",
            "                - !GetAtt MLBDailyPickLockFunction.Arn",
            "                - !GetAtt MLBAuditedPullFunction.Arn",
            "      Events:",
            "        FiveMinuteAutonomy:",
            "          Type: Schedule",
            "          Properties:",
            "            Schedule: rate(5 minutes)",
            "            Enabled: true",
            "",
        ]
        lines[resources_end:resources_end] = orchestrator

    write(path, "\n".join(lines))


def _external_calls(lock_tree: ast.AST, module_name: str) -> Set[str]:
    aliases: Set[str] = set()
    direct: Set[str] = set()
    for node in ast.walk(lock_tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name == module_name:
                    aliases.add(item.asname or module_name)
        elif isinstance(node, ast.ImportFrom) and node.module == module_name:
            for item in node.names:
                direct.add(item.asname or item.name)
    called: Set[str] = set()
    for node in ast.walk(lock_tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name) and function.value.id in aliases:
            called.add(function.attr)
        elif isinstance(function, ast.Name) and function.id in direct:
            called.add(function.id)
    return called


def _candidate_functions(module_tree: ast.Module, externally_called: Set[str]) -> List[str]:
    definitions = {
        node.name: node
        for node in module_tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    selected = [name for name in externally_called if name in definitions and name != "lambda_handler"]
    if selected:
        return sorted(selected)
    priorities = ("card", "slate", "rank", "predict", "generate", "build")
    fallback = [
        name
        for name in definitions
        if name != "lambda_handler" and any(token in name.lower() for token in priorities)
    ]
    fallback.sort(
        key=lambda name: (
            not any(token in name.lower() for token in ("card", "slate")),
            not any(token in name.lower() for token in ("rank", "predict")),
            name,
        )
    )
    return fallback[:2]


def _append_wrappers(path: pathlib.Path, functions: Sequence[str]) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return True
    if not functions:
        return False
    lines = [
        "",
        f"# {MARKER}",
        "# Applied only in the production Lambda where the SAM template enables",
        "# MLB_AUTONOMOUS_LLM_ENABLED. Existing unit/diagnostic callers retain the",
        "# original deterministic result unless they explicitly enable the layer.",
        "import functools as _mlb_autonomy_functools",
        "import os as _mlb_autonomy_os",
        "from mlb_autonomous_llm_decision_v1 import apply_to_prediction_payload as _mlb_apply_autonomous_decision",
    ]
    for name in functions:
        lines.extend(
            [
                f"_mlb_autonomy_original_{name} = {name}",
                f"@_mlb_autonomy_functools.wraps(_mlb_autonomy_original_{name})",
                f"def {name}(*args, __mlb_original=_mlb_autonomy_original_{name}, **kwargs):",
                "    original_result = __mlb_original(*args, **kwargs)",
                "    enabled = str(_mlb_autonomy_os.environ.get('MLB_AUTONOMOUS_LLM_ENABLED', 'false')).lower() in {'1', 'true', 'yes', 'on'}",
                "    if not enabled:",
                "        return original_result",
                "    try:",
                "        return _mlb_apply_autonomous_decision(original_result)",
                "    except Exception as exc:",
                "        # Preserve all-game operational coverage while surfacing the",
                "        # LLM failure to the enclosing payload whenever possible.",
                "        if isinstance(original_result, dict):",
                "            original_result.setdefault('mlbAutonomousDecision', {})",
                "            original_result['mlbAutonomousDecision'].update({",
                "                'status': 'DEGRADED_LLM_WRAPPER_ERROR',",
                "                'errorType': type(exc).__name__,",
                "                'error': str(exc)[:500],",
                "            })",
                "        return original_result",
            ]
        )
    path.write_text(text.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    ast.parse(path.read_text(encoding="utf-8"))
    return True


def patch_predictors() -> List[str]:
    lock_path = ROOT / "hello_world/mlb_daily_per_game_lock_patch.py"
    require(lock_path.is_file(), "daily per-game lock module missing")
    lock_tree = ast.parse(lock_path.read_text(encoding="utf-8"))
    imported_modules: Set[str] = {"mlb_ranked_primary_v15_10"}
    for node in ast.walk(lock_tree):
        if isinstance(node, ast.Import):
            imported_modules.update(item.name for item in node.names if item.name.startswith("mlb_"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("mlb_"):
            imported_modules.add(node.module)

    exclusions = ("audit", "official", "settlement", "training", "daily_per_game_lock", "autonomous", "canonical", "schedule")
    candidates = [
        name
        for name in imported_modules
        if any(token in name for token in ("rank", "predict", "model", "primary", "winner", "selection"))
        and not any(token in name for token in exclusions)
    ]
    wrapped: List[str] = []
    for module_name in sorted(set(candidates)):
        path = ROOT / "hello_world" / f"{module_name.rsplit('.', 1)[-1]}.py"
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = _candidate_functions(tree, _external_calls(lock_tree, module_name))
        if _append_wrappers(path, functions):
            wrapped.append(str(path.relative_to(ROOT)))
    require(wrapped, f"no eligible MLB predictor found among {sorted(imported_modules)}")
    return wrapped


def patch_deploy_workflow() -> None:
    path = ".github/workflows/deploy.yml"
    text = read(path)
    secret_expression = "${{ secrets.BIG_BALLS_DATA_API_KEY || secrets.BIG_BALLS_API_KEY || secrets.BBD_API_KEY || secrets.BIGBALLS_API_KEY || secrets.BBD_PRO_API_KEY }}"
    if "      BIG_BALLS_DATA_API_KEY:" not in text:
        anchor = "  deploy:\n    runs-on: ubuntu-latest\n"
        require(anchor in text, "deploy job anchor missing")
        text = text.replace(
            anchor,
            "  deploy:\n    env:\n      BIG_BALLS_DATA_API_KEY: " + secret_expression + "\n    runs-on: ubuntu-latest\n",
            1,
        )
    if 'test -n "${BIG_BALLS_DATA_API_KEY:-}"' not in text:
        anchor = '          test -n "${ODDS_API_KEY_VALUE:-}" || { echo "::error::Missing ODDS_API_KEY"; exit 1; }\n'
        require(anchor in text, "Odds API secret validation anchor missing")
        text = text.replace(
            anchor,
            anchor + '          test -n "${BIG_BALLS_DATA_API_KEY:-}" || { echo "::error::Missing Big Balls Data Pro API key repository secret"; exit 1; }\n',
            1,
        )
    text = text.replace(
        "python scripts/verify_mlb_no_bbd_runtime.py",
        "PYTHONPATH=hello_world python scripts/verify_mlb_three_source_authority.py --require-live --output runtime_reports/mlb_three_source_deploy_verification_latest.json",
    )
    text = text.replace(
        "python -m pytest -q tests/unit/test_verify_mlb_no_bbd_runtime.py",
        "python -m pytest -q tests/unit/test_verify_mlb_no_bbd_runtime.py tests/unit/test_mlb_bbd_pro_context.py tests/unit/test_mlb_daily_card_deadline_v1.py tests/unit/test_mlb_autonomous_llm_decision_v1.py tests/unit/test_mlb_autonomous_orchestrator_v1.py",
    )
    if "tests/unit/test_mlb_bbd_pro_context.py" not in text:
        anchor = "          python -m pytest -q tests/unit/test_mlb_production_acceptance.py\n"
        require(anchor in text, "production acceptance test anchor missing")
        text = text.replace(
            anchor,
            anchor + "          python -m pytest -q tests/unit/test_mlb_bbd_pro_context.py tests/unit/test_mlb_daily_card_deadline_v1.py tests/unit/test_mlb_autonomous_llm_decision_v1.py tests/unit/test_mlb_autonomous_orchestrator_v1.py\n          PYTHONPATH=hello_world python scripts/verify_mlb_three_source_authority.py --require-live --output runtime_reports/mlb_three_source_deploy_verification_latest.json\n",
            1,
        )
    if '"BigBallsDataApiKey=${BIG_BALLS_DATA_API_KEY}"' not in text:
        anchor = '            "OddsApiKey=${ODDS_API_KEY_VALUE}"\n'
        require(anchor in text, "SAM OddsApiKey parameter override anchor missing")
        text = text.replace(anchor, anchor + '            "BigBallsDataApiKey=${BIG_BALLS_DATA_API_KEY}"\n', 1)
    write(path, text)

    # Any legacy workflow that still executes the obsolete exclusion verifier
    # is migrated to the new static three-source authority verifier.
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        workflow_text = workflow.read_text(encoding="utf-8")
        if "verify_mlb_no_bbd_runtime.py" in workflow_text:
            workflow_text = workflow_text.replace(
                "python scripts/verify_mlb_no_bbd_runtime.py",
                "PYTHONPATH=hello_world python scripts/verify_mlb_three_source_authority.py",
            )
            workflow.write_text(workflow_text, encoding="utf-8")


def retire_legacy_bbd_exclusion() -> None:
    compatibility = '''#!/usr/bin/env python3
"""Compatibility entry point retained for old references.

The old contract prohibited Big Balls Data because production access did not
exist. Pro access is now required, so this entry point verifies the replacement
three-source authority instead of removing BBD from MLB.
"""
from verify_mlb_three_source_authority import main

if __name__ == "__main__":
    raise SystemExit(main())
'''
    write("scripts/verify_mlb_no_bbd_runtime.py", compatibility)

    compatibility_test = '''import pathlib


def test_legacy_no_bbd_guard_has_been_replaced_by_three_source_authority():
    root = pathlib.Path(__file__).resolve().parents[2]
    deploy = (root / ".github/workflows/deploy.yml").read_text()
    template = (root / "template.yaml").read_text()
    llm = (root / "hello_world/mlb_autonomous_llm_decision_v1.py").read_text()
    assert "verify_mlb_no_bbd_runtime.py" not in deploy
    assert "verify_mlb_three_source_authority.py" in deploy
    assert "BIG_BALLS_DATA_API_KEY" in template
    assert "BIG_BALLS_DATA_PRO_SUPPLEMENTAL_BASEBALL_CONTEXT" in llm
'''
    write("tests/unit/test_verify_mlb_no_bbd_runtime.py", compatibility_test)

    retired_workflow = ROOT / ".github/workflows/mlb-remove-bbd-active-runtime-once.yml"
    if retired_workflow.exists():
        retired_workflow.write_text(
            '''name: RETIRED - Big Balls Data removal is prohibited

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  retired:
    runs-on: ubuntu-latest
    steps:
      - name: Refuse obsolete removal
        run: |
          echo "This workflow is retired. MLB now requires the validated Big Balls Data Pro integration."
          exit 1
''',
            encoding="utf-8",
        )


def write_documentation(wrapped_predictors: Sequence[str]) -> None:
    document = f'''# Autonomous MLB Three-Source Prediction Authority

Version: `MLB-AUTONOMOUS-LLM-DECISION-v1-three-source-no-pass`

## Source hierarchy

1. **MLB Stats API** is authoritative for the official slate, `gamePk`, teams,
   start times, game state, and final labels.
2. **The Odds API** is authoritative for sportsbook prices, market inventory,
   movement, disagreement, period markets, alternates, totals, and available
   props already captured by the MLB ingestion pipeline.
3. **Big Balls Data Pro** supplies pregame baseball context discovered from its
   official OpenAPI contract. All resolvable MLB GET operations classified as
   pregame-safe are attempted within the configured call budget. Live scores,
   final results, box scores, play-by-play, and pitch-by-pitch operations are
   excluded from prediction evidence.

## Autonomous operation

`MLBAutonomousOrchestratorFunction` runs every five minutes. It begins the
pregame collection window twelve hours before the first official game, invokes
an audited market pull, and then invokes the immutable daily winner-card
pipeline. The first game must have a pick by T-45; the complete daily card must
exist by T-45 for the second official game. A one-game slate uses that game's
T-45 deadline. No post-start recomputation is permitted.

## Decision policy

The existing calibrated MLB model remains the largest blend component. The
Bedrock analyst consumes the official MLB identity/schedule record, The Odds
API market context, and BBD Pro context, then returns one winner for every game.
PASS/abstention is not permitted. Provider failures are surfaced as degraded
health while the existing model preserves full-game coverage; they are never
silently presented as three-source-ready.

The daily correctness objective is **70%**, tracked as an objective rather than
represented as a guarantee. Immutable pregame evidence and official final labels
remain the basis of every accuracy audit.

Wrapped production predictors:
{chr(10).join(f'- `{path}`' for path in wrapped_predictors)}
'''
    write("docs/MLB_THREE_SOURCE_AUTONOMY.md", document)


def main() -> int:
    patch_llm_client_creation()
    patch_deadline_test_import()
    patch_template()
    wrapped = patch_predictors()
    patch_deploy_workflow()
    retire_legacy_bbd_exclusion()
    write_documentation(wrapped)

    manifest = {
        "version": "MLB-THREE-SOURCE-INSTALLER-v1",
        "wrappedPredictors": wrapped,
        "newRuntimeFiles": [
            "hello_world/mlb_bbd_pro_context.py",
            "hello_world/mlb_autonomous_llm_decision_v1.py",
            "hello_world/mlb_autonomous_orchestrator_v1.py",
            "hello_world/mlb_daily_card_deadline_v1.py",
        ],
        "dailyAccuracyGoal": 0.70,
        "cardDeadline": "second official game minus 45 minutes",
    }
    write("runtime_reports/mlb_three_source_install_manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
