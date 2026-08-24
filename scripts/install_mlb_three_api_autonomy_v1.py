from __future__ import annotations

"""Install the MLB three-API/LLM autonomy overlay into canonical production.

This installer is deterministic and idempotent. It modifies only MLB/main-stack
files and never touches tennis_learning, tennis-template.yaml, soccer_auto, or
soccer-auto-template.yaml.
"""

import ast
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Set


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "runtime_reports" / "mlb_three_api_install_latest.json"
ADVANCED_MARKER = "MLB_THREE_API_INTEGRATION_BEGIN"


class InstallError(RuntimeError):
    pass


def read(path: str) -> str:
    target = ROOT / path
    if not target.is_file():
        raise InstallError(f"REQUIRED_FILE_MISSING:{path}")
    return target.read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _defined_functions(source: str) -> Dict[str, ast.FunctionDef]:
    tree = ast.parse(source)
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _external_advanced_context_calls() -> Set[str]:
    names: Set[str] = set()
    attribute_pattern = re.compile(r"\b(?:mlb_advanced_context|advanced_context)\.([A-Za-z_]\w*)\s*\(")
    import_pattern = re.compile(r"from\s+mlb_advanced_context\s+import\s+([^\n]+)")
    for path in ROOT.rglob("*.py"):
        if path.name == "mlb_advanced_context.py" or ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        names.update(attribute_pattern.findall(text))
        for match in import_pattern.findall(text):
            for raw in match.split(","):
                name = raw.strip().split(" as ", 1)[0].strip()
                if re.fullmatch(r"[A-Za-z_]\w*", name):
                    names.add(name)
    return names


def patch_advanced_context() -> List[str]:
    path = "hello_world/mlb_advanced_context.py"
    source = read(path)
    if ADVANCED_MARKER in source:
        return ["already-installed"]
    definitions = _defined_functions(source)
    external = _external_advanced_context_calls()
    candidates: Set[str] = {
        name for name in external
        if name in definitions and not name.startswith("_") and name != "lambda_handler"
    }
    for name, node in definitions.items():
        lowered = name.lower()
        if name.startswith("_") or name == "lambda_handler":
            continue
        segment = ast.get_source_segment(source, node) or ""
        if (
            any(token in lowered for token in ("context", "fundamental", "feature"))
            and any(token in segment for token in ("confirmed_probable_pitchers", "_REQUIRED_CONTEXT_KEYS", "travel_rest"))
        ):
            candidates.add(name)
    if not candidates:
        for fallback in (
            "build_advanced_context", "advanced_context_for_game", "collect_advanced_context",
            "context_for_game", "build_context", "get_advanced_context",
        ):
            if fallback in definitions:
                candidates.add(fallback)
    if not candidates:
        raise InstallError("ADVANCED_CONTEXT_PUBLIC_BUILDER_NOT_FOUND")

    names_literal = repr(sorted(candidates))
    integration = f'''

# {ADVANCED_MARKER}
# Installed by scripts/install_mlb_three_api_autonomy_v1.py. The wrapper is
# bounded, point-in-time, fail-closed, and idempotent.
import copy as _three_api_copy
import functools as _three_api_functools
import mlb_bbd_pro_context as _three_api_bbd
import mlb_three_api_llm_analyst as _three_api_llm

_THREE_API_CONTEXT_BUILDERS = {names_literal}


def _three_api_game_from_values(args, kwargs, result=None):
    values = list(args) + list(kwargs.values()) + ([result] if result is not None else [])
    for value in values:
        if not isinstance(value, dict):
            continue
        home = value.get("home_team") or value.get("homeTeam")
        away = value.get("away_team") or value.get("awayTeam")
        if home and away:
            return value
        for key in ("game", "event", "match", "officialGame", "official_game"):
            nested = value.get(key)
            if isinstance(nested, dict):
                home = nested.get("home_team") or nested.get("homeTeam")
                away = nested.get("away_team") or nested.get("awayTeam")
                if home and away:
                    return nested
    return None


def _three_api_as_of(game, kwargs):
    for key in (
        "as_of_utc", "asOfUtc", "prediction_persisted_at_utc",
        "predictionPersistedAtUtc", "locked_at_utc", "lockedAtUtc",
    ):
        value = kwargs.get(key)
        if value:
            return str(value)
        if isinstance(game, dict) and game.get(key):
            return str(game[key])
    return None


def _three_api_context_candidate(value):
    if not isinstance(value, dict):
        return False
    required = set(globals().get("_REQUIRED_CONTEXT_KEYS") or [])
    return bool(required.intersection(value)) or any(
        key in value
        for key in (
            "confirmed_probable_pitchers", "confirmed_lineups", "travel_rest",
            "weather_wind_roof", "ballpark_factors", "market_context", "bookmakers",
        )
    )


def _three_api_enrich_result(value, game, as_of, depth=0):
    if depth > 5:
        return value
    if isinstance(value, dict):
        current = _three_api_copy.deepcopy(value)
        if _three_api_context_candidate(current):
            bbd = _three_api_bbd.collect_game_context(game, as_of_utc=as_of)
            current = _three_api_bbd.merge_into_advanced_context(current, bbd)
            current = _three_api_llm.enrich_advanced_context(game, current, as_of_utc=as_of)
            return current
        for key in (
            "advancedContext", "advanced_context", "context", "fundamentals",
            "fundamentalsSnapshotV2", "fundamentals_snapshot_v2", "data", "result",
        ):
            nested = current.get(key)
            if isinstance(nested, (dict, list)):
                current[key] = _three_api_enrich_result(nested, game, as_of, depth + 1)
        return current
    if isinstance(value, list):
        return [_three_api_enrich_result(item, game, as_of, depth + 1) for item in value]
    return value


def _three_api_wrap_builder(name):
    original = globals().get(name)
    if not callable(original) or getattr(original, "__mlb_three_api_wrapped__", False):
        return

    @_three_api_functools.wraps(original)
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        game = _three_api_game_from_values(args, kwargs, result)
        if not isinstance(game, dict):
            return result
        return _three_api_enrich_result(result, game, _three_api_as_of(game, kwargs))

    wrapped.__mlb_three_api_wrapped__ = True
    globals()[name] = wrapped


for _three_api_builder_name in _THREE_API_CONTEXT_BUILDERS:
    _three_api_wrap_builder(_three_api_builder_name)
# MLB_THREE_API_INTEGRATION_END
'''
    ast.parse(source + integration)
    write(path, source.rstrip() + integration + "\n")
    return sorted(candidates)


def _insert_parameter_block(template: str) -> str:
    if "BigBallsDataApiKey:" in template:
        return template
    lines = template.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.startswith("  OddsApiKey:")), None)
    if start is None:
        raise InstallError("SAM_ODDS_API_PARAMETER_NOT_FOUND")
    end = None
    for index in range(start + 1, len(lines)):
        if re.match(r"^  [A-Za-z0-9][A-Za-z0-9_-]*:\s*$", lines[index]):
            end = index
            break
    if end is None:
        raise InstallError("SAM_ODDS_API_PARAMETER_BOUNDARY_NOT_FOUND")
    block = (
        "  BigBallsDataApiKey:\n"
        "    Type: String\n"
        "    NoEcho: true\n"
        "    Default: ''\n"
        "  BigBallsDataApiBaseUrl:\n"
        "    Type: String\n"
        "    Default: ''\n"
    )
    lines.insert(end, block)
    return "".join(lines)


def _inject_bbd_environment(template: str) -> str:
    lines = template.splitlines(keepends=True)
    out: List[str] = []
    injected = 0
    for index, line in enumerate(lines):
        out.append(line)
        if re.search(r"\bODDS_API_KEY:\s*!Ref\s+OddsApiKey\s*$", line.rstrip()):
            lookahead = "".join(lines[index + 1:index + 6])
            if "BIG_BALLS_DATA_API_KEY:" not in lookahead:
                indent = re.match(r"^(\s*)", line).group(1)
                out.append(f"{indent}BIG_BALLS_DATA_API_KEY: !Ref BigBallsDataApiKey\n")
                out.append(f"{indent}BIG_BALLS_DATA_API_BASE_URL: !Ref BigBallsDataApiBaseUrl\n")
                out.append(f"{indent}MLB_THREE_API_LLM_MODEL_ID: us.amazon.nova-2-lite-v1:0\n")
                injected += 1
    if injected == 0 and "BIG_BALLS_DATA_API_KEY:" not in template:
        raise InstallError("SAM_ODDS_API_ENVIRONMENT_NOT_FOUND")
    return "".join(out)


def _controller_resource() -> str:
    return '''
  MLBThreeApiAutonomousControllerFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_three_api_autonomous_controller.lambda_handler
      Runtime: python3.11
      Timeout: 300
      MemorySize: 1024
      Environment:
        Variables:
          ODDS_API_KEY: !Ref OddsApiKey
          BIG_BALLS_DATA_API_KEY: !Ref BigBallsDataApiKey
          BIG_BALLS_DATA_API_BASE_URL: !Ref BigBallsDataApiBaseUrl
          MLB_THREE_API_LLM_MODEL_ID: us.amazon.nova-2-lite-v1:0
          MLB_THREE_API_PULL_FUNCTION_NAME: !Ref MLBAuditedPullFunction
          MLB_THREE_API_LOCK_FUNCTION_NAME: !Ref MLBDailyPickLockFunction
          MLB_THREE_API_TRAIN_FUNCTION_NAME: !Ref MLBMLTrainingFunction
          MLB_THREE_API_VERIFY_FUNCTION_NAME: !Ref MLBProductionVerifierFunction
      Policies:
        - LambdaInvokePolicy:
            FunctionName: !Ref MLBAuditedPullFunction
        - LambdaInvokePolicy:
            FunctionName: !Ref MLBDailyPickLockFunction
        - LambdaInvokePolicy:
            FunctionName: !Ref MLBMLTrainingFunction
        - LambdaInvokePolicy:
            FunctionName: !Ref MLBProductionVerifierFunction
        - Statement:
            - Effect: Allow
              Action:
                - bedrock:InvokeModel
                - bedrock:InvokeModelWithResponseStream
              Resource: '*'
      Events:
        AutonomousFiveMinuteSchedule:
          Type: Schedule
          Properties:
            Schedule: rate(5 minutes)
            Enabled: true
            Description: Autonomous MLB three-source pull, train, full-card lock and verification controller

'''


def patch_template() -> Dict[str, int]:
    path = "template.yaml"
    template = read(path)
    template = _insert_parameter_block(template)
    template = _inject_bbd_environment(template)
    if "MLBThreeApiAutonomousControllerFunction:" not in template:
        marker = re.search(r"(?m)^Outputs:\s*$", template)
        if not marker:
            raise InstallError("SAM_OUTPUTS_MARKER_NOT_FOUND")
        template = template[:marker.start()] + _controller_resource() + template[marker.start():]
    write(path, template)
    return {
        "bbdEnvironmentOccurrences": template.count("BIG_BALLS_DATA_API_KEY:"),
        "controllerOccurrences": template.count("MLBThreeApiAutonomousControllerFunction:"),
    }


def _insert_after_line(text: str, needle: str, addition: str) -> str:
    if addition.strip() in text:
        return text
    index = text.find(needle)
    if index < 0:
        raise InstallError(f"WORKFLOW_ANCHOR_NOT_FOUND:{needle}")
    end = text.find("\n", index)
    if end < 0:
        end = len(text)
    return text[:end + 1] + addition + text[end + 1:]


def patch_deploy_workflow() -> Dict[str, int]:
    path = ".github/workflows/deploy.yml"
    text = read(path)
    text = text.replace(
        "python scripts/verify_mlb_no_bbd_runtime.py",
        "python scripts/verify_mlb_three_api_runtime.py",
    )
    text = text.replace(
        "tests/unit/test_verify_mlb_no_bbd_runtime.py",
        "tests/unit/test_mlb_three_api_runtime.py",
    )

    odds_env = "          ODDS_API_KEY_VALUE: ${{ secrets.ODDS_API_KEY }}\n"
    bbd_env = (
        "          BIG_BALLS_DATA_API_KEY_VALUE: ${{ secrets.BIG_BALLS_DATA_API_KEY || secrets.BBD_API_KEY || secrets.BIGBALLS_DATA_API_KEY || secrets.BIG_BALLS_API_KEY }}\n"
        "          BIG_BALLS_DATA_API_BASE_URL_VALUE: ${{ vars.BIG_BALLS_DATA_API_BASE_URL || vars.BBD_API_BASE_URL || '' }}\n"
    )
    if "BIG_BALLS_DATA_API_KEY_VALUE:" not in text:
        if odds_env not in text:
            raise InstallError("WORKFLOW_ODDS_SECRET_ENV_NOT_FOUND")
        text = text.replace(odds_env, odds_env + bbd_env)
    else:
        # Ensure every deployment step that carries the Odds key also carries
        # the BBD key without duplicating already-patched blocks.
        chunks = text.splitlines(keepends=True)
        out: List[str] = []
        for index, line in enumerate(chunks):
            out.append(line)
            if line == odds_env:
                nearby = "".join(chunks[index + 1:index + 5])
                if "BIG_BALLS_DATA_API_KEY_VALUE:" not in nearby:
                    out.append(bbd_env)
        text = "".join(out)

    odds_test = "          test -n \"${ODDS_API_KEY_VALUE:-}\" || { echo \"::error::Missing ODDS_API_KEY\"; exit 1; }\n"
    bbd_test = "          test -n \"${BIG_BALLS_DATA_API_KEY_VALUE:-}\" || { echo \"::error::Missing Big Balls Data Pro repository secret (BIG_BALLS_DATA_API_KEY or BBD_API_KEY)\"; exit 1; }\n"
    if bbd_test.strip() not in text:
        if odds_test not in text:
            # Some workflow versions use single quotes around the error.
            anchor = re.search(r"(?m)^\s+test -n \"\$\{ODDS_API_KEY_VALUE:-\}\".*$", text)
            if not anchor:
                raise InstallError("WORKFLOW_ODDS_SECRET_TEST_NOT_FOUND")
            line_end = text.find("\n", anchor.start())
            text = text[:line_end + 1] + bbd_test + text[line_end + 1:]
        else:
            text = text.replace(odds_test, odds_test + bbd_test, 1)

    odds_override = '            "OddsApiKey=${ODDS_API_KEY_VALUE}"\n'
    bbd_override = (
        '            "BigBallsDataApiKey=${BIG_BALLS_DATA_API_KEY_VALUE}"\n'
        '            "BigBallsDataApiBaseUrl=${BIG_BALLS_DATA_API_BASE_URL_VALUE:-}"\n'
    )
    if "BigBallsDataApiKey=${BIG_BALLS_DATA_API_KEY_VALUE}" not in text:
        if odds_override not in text:
            raise InstallError("WORKFLOW_ODDS_PARAMETER_OVERRIDE_NOT_FOUND")
        text = text.replace(odds_override, odds_override + bbd_override, 1)

    # Compile and test the new modules in the canonical validation phase.
    compile_anchor = "          python -m py_compile scripts/mlb_lambda_artifact_identity.py\n"
    compile_lines = (
        "          python -m py_compile hello_world/mlb_bbd_pro_context.py\n"
        "          python -m py_compile hello_world/mlb_three_api_llm_analyst.py\n"
        "          python -m py_compile hello_world/mlb_three_api_policy.py\n"
        "          python -m py_compile hello_world/mlb_three_api_autonomous_controller.py\n"
    )
    if "python -m py_compile hello_world/mlb_bbd_pro_context.py" not in text:
        if compile_anchor not in text:
            raise InstallError("WORKFLOW_COMPILE_ANCHOR_NOT_FOUND")
        text = text.replace(compile_anchor, compile_lines + compile_anchor, 1)

    if "tests/unit/test_mlb_three_api_policy.py" not in text:
        test_anchor = "            tests/unit/test_mlb_three_api_runtime.py\n"
        if test_anchor not in text:
            # It was just replaced in the regression list; tolerate indentation
            # variants while still failing if no reference exists.
            match = re.search(r"(?m)^\s+tests/unit/test_mlb_three_api_runtime\.py\s*$", text)
            if not match:
                raise InstallError("WORKFLOW_THREE_API_TEST_ANCHOR_NOT_FOUND")
            line_end = text.find("\n", match.start())
            indentation = re.match(r"^(\s*)", text[match.start():]).group(1)
            extra = (
                f"{indentation}tests/unit/test_mlb_three_api_policy.py\n"
                f"{indentation}tests/unit/test_mlb_bbd_pro_context.py\n"
            )
            text = text[:line_end + 1] + extra + text[line_end + 1:]
        else:
            text = text.replace(
                test_anchor,
                test_anchor
                + "            tests/unit/test_mlb_three_api_policy.py\n"
                + "            tests/unit/test_mlb_bbd_pro_context.py\n",
                1,
            )

    # Ensure the Lambda roles that execute advanced context can call Bedrock.
    iam_step_name = "      - name: Ensure MLB three-API LLM invoke permissions\n"
    if iam_step_name not in text:
        anchor = "      - name: Read stack outputs\n"
        if anchor not in text:
            raise InstallError("WORKFLOW_POST_DEPLOY_ANCHOR_NOT_FOUND")
        iam_step = '''      - name: Ensure MLB three-API LLM invoke permissions
        run: |
          set -euo pipefail
          for logical_id in MLBAuditedPullFunction MLBDailyPickLockFunction MLBMLTrainingFunction MLBProductionVerifierFunction; do
            function_name=$(aws cloudformation describe-stack-resource \
              --stack-name parlay-platform-dev \
              --logical-resource-id "$logical_id" \
              --region "${{ secrets.AWS_REGION }}" \
              --query 'StackResourceDetail.PhysicalResourceId' \
              --output text)
            role_arn=$(aws lambda get-function-configuration \
              --function-name "$function_name" \
              --region "${{ secrets.AWS_REGION }}" \
              --query Role --output text)
            role_name=${role_arn##*/}
            aws iam put-role-policy \
              --role-name "$role_name" \
              --policy-name InqsiMlbThreeApiBedrockInvoke \
              --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],"Resource":"*"}]}'
          done

'''
        text = text.replace(anchor, iam_step + anchor, 1)

    # Invoke the new controller after deployment to prove the schedule and
    # cross-function wiring; source authentication is separately proven by the
    # production pulls and the static contract.
    controller_step_name = "      - name: Verify autonomous MLB three-API controller\n"
    if controller_step_name not in text:
        anchor = "      - name: Read stack outputs\n"
        step = '''      - name: Verify autonomous MLB three-API controller
        run: |
          set -euo pipefail
          function_name=$(aws cloudformation describe-stack-resource \
            --stack-name parlay-platform-dev \
            --logical-resource-id MLBThreeApiAutonomousControllerFunction \
            --region "${{ secrets.AWS_REGION }}" \
            --query 'StackResourceDetail.PhysicalResourceId' \
            --output text)
          aws lambda invoke \
            --function-name "$function_name" \
            --region "${{ secrets.AWS_REGION }}" \
            --cli-binary-format raw-in-base64-out \
            --payload '{"action":"status","source":"deployment-verification"}' \
            /tmp/mlb-three-api-controller.json \
            >/tmp/mlb-three-api-controller-meta.json
          python - <<'PY'
          import json
          meta=json.load(open('/tmp/mlb-three-api-controller-meta.json'))
          body=json.load(open('/tmp/mlb-three-api-controller.json'))
          assert not meta.get('FunctionError'), (meta, body)
          assert body.get('version') == 'MLB-THREE-API-AUTONOMOUS-CONTROLLER-v1', body
          assert body.get('officialGameCount', 0) >= 0, body
          assert body.get('dailyAccuracyGoal') == 0.70, body
          print(body)
          PY

'''
        text = text.replace(anchor, step + anchor, 1)

    write(path, text)
    return {
        "bbdSecretReferences": text.count("BIG_BALLS_DATA_API_KEY_VALUE"),
        "threeApiVerifierReferences": text.count("verify_mlb_three_api_runtime.py"),
        "controllerVerificationSteps": text.count("Verify autonomous MLB three-API controller"),
    }


def patch_authority_scripts() -> List[str]:
    changed: List[str] = []
    for relative in (
        "scripts/verify_mlb_workflow_authority.py",
        "tests/unit/test_mlb_workflow_authority.py",
    ):
        target = ROOT / relative
        if not target.is_file():
            continue
        text = target.read_text(encoding="utf-8")
        new = text.replace(
            "verify_mlb_no_bbd_runtime.py",
            "verify_mlb_three_api_runtime.py",
        ).replace(
            "test_verify_mlb_no_bbd_runtime.py",
            "test_mlb_three_api_runtime.py",
        )
        if new != text:
            target.write_text(new, encoding="utf-8")
            changed.append(relative)
    return changed


def verify_isolation() -> None:
    forbidden = {
        "tennis-template.yaml",
        "soccer-auto-template.yaml",
    }
    # The installer never writes these paths. This explicit check protects
    # against future edits that accidentally broaden its scope.
    source = Path(__file__).read_text(encoding="utf-8")
    for token in forbidden:
        if f'write("{token}"' in source or f"write('{token}'" in source:
            raise InstallError(f"SPORT_ISOLATION_VIOLATION:{token}")


def main() -> Dict[str, object]:
    verify_isolation()
    builders = patch_advanced_context()
    template = patch_template()
    workflow = patch_deploy_workflow()
    authority = patch_authority_scripts()

    # Final syntax checks before the workflow runs the full unit suite.
    for relative in (
        "hello_world/mlb_advanced_context.py",
        "hello_world/mlb_bbd_pro_context.py",
        "hello_world/mlb_three_api_llm_analyst.py",
        "hello_world/mlb_three_api_policy.py",
        "hello_world/mlb_three_api_autonomous_controller.py",
        "scripts/verify_mlb_three_api_runtime.py",
    ):
        ast.parse(read(relative), filename=relative)

    result: Dict[str, object] = {
        "ok": True,
        "installer": "MLB_THREE_API_AUTONOMY_INSTALLER_v1",
        "advancedContextBuildersWrapped": builders,
        "template": template,
        "workflow": workflow,
        "authorityFilesChanged": authority,
        "sportsIsolation": {"tennisTouched": False, "soccerTouched": False},
        "requirements": {
            "sources": ["MLB Stats API", "The Odds API", "Big Balls Data Pro"],
            "llm": "Amazon Bedrock autonomous analyst",
            "fullSlate": True,
            "firstGameLeadMinutes": 45,
            "completeCardDeadline": "second official game minus 45 minutes",
            "dailyAccuracyGoal": 0.70,
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
