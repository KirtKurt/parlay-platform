from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def _insert_after(text: str, marker: str, addition: str, *, label: str) -> str:
    if addition.strip() in text:
        return text
    if marker not in text:
        raise RuntimeError(f"{label} marker missing: {marker!r}")
    return text.replace(marker, marker + addition, 1)


def _insert_before(text: str, marker: str, addition: str, *, label: str) -> str:
    if addition.strip() in text:
        return text
    if marker not in text:
        raise RuntimeError(f"{label} marker missing: {marker!r}")
    return text.replace(marker, addition + marker, 1)


def _set_env_default(text: str, name: str, old_default: str, new_default: str) -> str:
    old = f'os.environ.get("{name}", "{old_default}")'
    new = f'os.environ.get("{name}", "{new_default}")'
    return text.replace(old, new)


def _patch_trainer_continuity(text: str) -> str:
    """Turn stop-first-unresolved continuity into independent slate quarantine.

    The current trainer contains one deliberate break after persisting an
    OFFICIAL_SLATE_UNRESOLVED blocker. Replace only that break and relax only the
    subsequent wait return when at least one independently finalized slate was
    accepted. Any ambiguous source shape fails the installer.
    """
    if "MLB_AUTO_CONTINUITY_V3_INSTALLED = True" in text:
        return text

    unresolved_index = text.find("OFFICIAL_SLATE_UNRESOLVED")
    if unresolved_index < 0:
        raise RuntimeError("trainer OFFICIAL_SLATE_UNRESOLVED marker missing")
    break_match = re.search(r"(?m)^(?P<indent>\s*)break\s*$", text[unresolved_index:])
    if not break_match or break_match.start() > 2500:
        raise RuntimeError("trainer unresolved-slate break not found in bounded region")
    absolute_start = unresolved_index + break_match.start()
    absolute_end = unresolved_index + break_match.end()
    indent = break_match.group("indent")
    replacement = (
        f'{indent}# MLB AUTO v3: quarantine this exact slate and continue evaluating later dates.\n'
        f'{indent}canonical_slate_continuity.setdefault("quarantinedSlateDates", []).append(slate_date)\n'
        f'{indent}canonical_slate_continuity.setdefault("quarantineReasons", {{}})[slate_date] = canonical_slate_continuity.get("blocker")\n'
        f'{indent}continue'
    )
    text = text[:absolute_start] + replacement + text[absolute_end:]

    wait_index = text.find("WAITING_FOR_CANONICAL_SLATE_CONTINUITY")
    if wait_index < 0:
        raise RuntimeError("trainer continuity wait status marker missing")
    prefix = text[:wait_index]
    if_matches = list(re.finditer(r"(?m)^(?P<indent>\s*)if\s+(?P<test>[^\n:]+):\s*$", prefix))
    if not if_matches:
        raise RuntimeError("trainer continuity wait condition missing")
    target = if_matches[-1]
    test = target.group("test")
    if "canonical" not in test.lower() and "continuity" not in test.lower():
        raise RuntimeError(f"trainer wait condition is ambiguous: {test}")
    new_test = (
        f'{target.group("indent")}if ({test}) and not '
        'canonical_slate_continuity.get("finalizedGameSlateDates"): '
    ).rstrip()
    text = text[: target.start()] + new_test + text[target.end() :]

    old_policy = (
        "Training stops at the first unresolved official slate; only an exact "
        "official zero-game schedule may be crossed as an off-day."
    )
    new_policy = (
        "Each exact official slate is evaluated independently. Unresolved dates "
        "are quarantined and cannot train, but they do not prevent later exact "
        "finalized slates from being evaluated."
    )
    text = text.replace(old_policy, new_policy)
    text += "\n\nMLB_AUTO_CONTINUITY_V3_INSTALLED = True\n"
    return text


def patch_training() -> None:
    path = "hello_world/mlb_ml_aws_training_v1.py"
    text = _read(path)
    text = _insert_after(
        text,
        "import mlb_ml_promotion_policy_v2 as promotion_policy\n",
        "import mlb_ml_canonical_continuity_v3 as canonical_continuity_v3\n"
        "import mlb_ml_deployment_identity_v1 as deployment_identity_v1\n"
        "import mlb_ml_llm_hypothesis_v1 as llm_hypothesis_v1\n"
        "import mlb_ml_v2_inference_consumer as v2_inference_consumer\n",
        label="trainer autonomy imports",
    )
    text = _set_env_default(text, "INQSI_MLB_ML_AUTO_PROMOTE", "false", "true")
    text = _set_env_default(text, "INQSI_MLB_ML_AUTO_PROMOTE", "False", "true")
    text = _patch_trainer_continuity(text)
    text = text.replace('"automaticPromotionEnabled": False', '"automaticPromotionEnabled": True')
    text = text.replace('"firstPromotionRequiresManualReview": True', '"firstPromotionRequiresManualReview": False')
    text = text.replace('"v2InferenceConsumerInstalled": False', '"v2InferenceConsumerInstalled": True')

    wrapper = r'''

# MLB AUTO autonomy-chain status wrapper. Candidate training and artifact creation
# continue below the 90% production-authority threshold; only authority/playability
# remain gated. The original trainer remains the sole supervised learner.
_MLB_AUTO_ORIGINAL_LAMBDA_HANDLER = lambda_handler


def lambda_handler(event, context):
    payload = dict(event or {}) if isinstance(event, dict) else {}
    result = _MLB_AUTO_ORIGINAL_LAMBDA_HANDLER(event, context)
    if not isinstance(result, dict):
        return result
    result["mlbAutoAutonomyChain"] = {
        "version": "MLB-AUTO-AUTONOMY-CHAIN-v1",
        "automaticPromotionEnabled": True,
        "firstPromotionRequiresManualReview": False,
        "learningContinuesBelow90Pct": True,
        "accuracyTargetAffectsCandidateTraining": False,
        "accuracyTargetAffectsProductionAuthorityOnly": True,
        "canonicalContinuityVersion": canonical_continuity_v3.VERSION,
        "deploymentIdentity": deployment_identity_v1.current_identity(),
        "v2InferenceConsumer": v2_inference_consumer.status(),
        "llmHypothesisVersion": llm_hypothesis_v1.VERSION,
        "llmDirectProductionAuthority": False,
    }
    continuity = result.get("canonicalSlateContinuity")
    if isinstance(continuity, dict):
        continuity.setdefault("version", canonical_continuity_v3.VERSION)
        continuity.setdefault("unresolvedSlateStopsLaterEvaluation", False)
        continuity.setdefault("quarantineIsNonAuthoritative", True)
    return result
'''
    if "_MLB_AUTO_ORIGINAL_LAMBDA_HANDLER" not in text:
        text += wrapper
    _write(path, text)


def patch_experiment() -> None:
    path = "hello_world/mlb_ml_experiment_v2.py"
    text = _read(path)
    text = text.replace('"firstPromotionRequiresManualReview": True', '"firstPromotionRequiresManualReview": False')
    text = text.replace('"automaticPromotionEnabled": False', '"automaticPromotionEnabled": True')
    constants = '''

# MLB AUTO learns continuously in shadow. The 90% target gates production
# authority/playability, never challenger creation or evaluation.
AUTOMATIC_PROMOTION_ENABLED = True
FIRST_PROMOTION_REQUIRES_MANUAL_REVIEW = False
LEARNING_CONTINUES_BELOW_AUTHORITY_TARGET = True
ACCURACY_TARGET_AFFECTS_CANDIDATE_TRAINING = False
ACCURACY_TARGET_AFFECTS_PRODUCTION_AUTHORITY_ONLY = True
'''
    if "LEARNING_CONTINUES_BELOW_AUTHORITY_TARGET" not in text:
        text += constants
    _write(path, text)


def patch_promotion_policy() -> None:
    path = "hello_world/mlb_ml_promotion_policy_v2.py"
    text = _read(path)
    text = text.replace('"firstPromotionRequiresManualReview": True', '"firstPromotionRequiresManualReview": False')
    text = text.replace('"automaticPromotionEnabled": False', '"automaticPromotionEnabled": True')
    addition = r'''

# Candidate learning is intentionally separated from production authority.
# Integrity-clean candidates may always be trained, persisted and evaluated.
# The 90% accuracy requirement remains mandatory only for direction/playability
# authority and automatic promotion.
def learning_gate(*, integrity_clean_row_count: int, minimum_rows: int = 140, **_: object):
    errors = []
    if int(integrity_clean_row_count or 0) < int(minimum_rows):
        errors.append("insufficient_integrity_clean_rows_for_learning")
    return {
        "ok": not errors,
        "version": "MLB-ML-LEARNING-GATE-v1-independent-of-90pct-authority",
        "candidateTrainingAllowed": not errors,
        "candidatePersistenceAllowed": not errors,
        "accuracyTargetPct": None,
        "errors": errors,
    }


def authority_target_contract():
    return {
        "version": "MLB-ML-AUTHORITY-TARGET-CONTRACT-v1",
        "automaticPromotionEnabled": True,
        "firstPromotionRequiresManualReview": False,
        "learningContinuesBelow90Pct": True,
        "accuracyTargetAffectsCandidateTraining": False,
        "accuracyTargetAffectsProductionAuthorityOnly": True,
        "minimumOutcomeUntouchedAccuracyPct": 90.0,
        "minimumSelectedPlayabilityAccuracyPct": 90.0,
        "chronologicalWalkForwardRequired": True,
        "untouchedHoldoutRequired": True,
        "nonDegradingCalibrationRequired": True,
        "immutableT45EvidenceRequired": True,
    }
'''
    if "def authority_target_contract" not in text:
        text += addition
    _write(path, text)


def patch_runtime_install() -> None:
    path = "hello_world/mlb_ml_runtime_install_v3.py"
    text = _read(path)
    if "_MLB_AUTO_V2_ORIGINAL_INSTALL" in text:
        return
    wrapper = r'''

# Install the V2 gate-promoted inference consumer alongside the existing runtime.
# With no valid champion it is a transparent no-op; once a champion passes every
# authority gate it becomes the sole V2 directional overlay.
_MLB_AUTO_V2_ORIGINAL_INSTALL = install


def install(*args, **kwargs):
    result = _MLB_AUTO_V2_ORIGINAL_INSTALL(*args, **kwargs)
    try:
        import mlb_ml_v2_inference_consumer as _v2_consumer
        consumer = _v2_consumer.install()
    except Exception as exc:
        consumer = {
            "ok": False,
            "installed": False,
            "version": "MLB-ML-V2-INFERENCE-CONSUMER-v1-gate-promoted-ddb-only",
            "errors": [f"consumer_install_failed:{type(exc).__name__}"],
        }
    if isinstance(result, dict):
        result["v2InferenceConsumer"] = consumer
        steps = result.setdefault("steps", {})
        if isinstance(steps, dict):
            steps["v2InferenceConsumerInstalled"] = consumer.get("installed") is True
        result["v2InferenceConsumerInstalled"] = consumer.get("installed") is True
    return result
'''
    text += wrapper
    _write(path, text)


def patch_compat_handler() -> None:
    path = "hello_world/mlb_ml_aws_training_v1_compat.py"
    text = _read(path)
    if "_MLB_AUTO_COMPAT_ORIGINAL_HANDLER" in text:
        return
    wrapper = r'''

# Normalize stale status handling: an old selection-capture identity triggers a
# fresh recapture request but cannot make the current trainer deployment unhealthy.
_MLB_AUTO_COMPAT_ORIGINAL_HANDLER = lambda_handler


def lambda_handler(event, context):
    result = _MLB_AUTO_COMPAT_ORIGINAL_HANDLER(event, context)
    if not isinstance(result, dict):
        return result
    try:
        import mlb_ml_deployment_identity_v1 as _identity
        import mlb_ml_v2_inference_consumer as _consumer
        result["deploymentIdentity"] = _identity.current_identity()
        result["v2InferenceConsumer"] = _consumer.status()
    except Exception as exc:
        result["autonomyStatusErrorType"] = type(exc).__name__
    if result.get("status") == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY":
        continuity = result.get("canonicalSlateContinuity")
        if isinstance(continuity, dict):
            continuity["unresolvedSlateStopsLaterEvaluation"] = False
            continuity["quarantineIsNonAuthoritative"] = True
    return result
'''
    text += wrapper
    _write(path, text)


def _ensure_global_env(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^\s{{8}}{re.escape(key)}:\s*.*$")
    replacement = f"        {key}: {value}"
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    marker = "        ODDS_API_KEY: !Ref OddsApiKey\n"
    if marker not in text:
        raise RuntimeError(f"global environment marker missing for {key}")
    return text.replace(marker, marker + replacement + "\n", 1)


def patch_template() -> None:
    path = "template.yaml"
    text = _read(path)
    for key, value in (
        ("INQSI_MLB_ML_AUTO_PROMOTE", "'true'"),
        ("MLB_LLM_HYPOTHESIS_ENABLED", "'true'"),
        ("MLB_LLM_HYPOTHESIS_MODEL_ID", "'amazon.nova-lite-v1:0'"),
        ("MLB_AUTO_CONTINUE_AFTER_UNRESOLVED_SLATE", "'true'"),
    ):
        text = _ensure_global_env(text, key, value)

    resource = '''
  MLBLLMHypothesisFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: hello_world/
      Handler: mlb_ml_llm_hypothesis_v1.lambda_handler
      Timeout: 300
      MemorySize: 1024
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref SnapshotsTable
        - Statement:
            - Effect: Allow
              Action:
                - bedrock:InvokeModel
                - bedrock:InvokeModelWithResponseStream
              Resource: '*'
      Events:
        MLBLLMHypothesisEvery6Hours:
          Type: Schedule
          Properties:
            Schedule: rate(6 hours)
            Input: '{"sport":"mlb","mode":"hypothesis_research","productionAuthority":false}'

'''
    if "  MLBLLMHypothesisFunction:" not in text:
        marker = "  MLBResultsSchedulerFunction:\n"
        if marker not in text:
            raise RuntimeError("MLB results scheduler resource marker missing")
        text = text.replace(marker, resource + marker, 1)
    _write(path, text)


def patch_deploy_workflow() -> None:
    path = ".github/workflows/deploy.yml"
    text = _read(path)
    if "Synchronize MLB training selection and inference identity" in text:
        return
    marker = "      - name: Smoke test live health endpoint\n"
    if marker not in text:
        raise RuntimeError("deploy health smoke marker missing")
    block = r'''      - name: Synchronize MLB training selection and inference identity
        env:
          AWS_REGION: ${{ secrets.AWS_REGION }}
        run: |
          set -euo pipefail
          FUNCTION_NAME=$(aws cloudformation describe-stack-resource \
            --stack-name parlay-platform-dev \
            --region "$AWS_REGION" \
            --logical-resource-id MLBMLTrainingFunction \
            --query 'StackResourceDetail.PhysicalResourceId' \
            --output text)
          test -n "$FUNCTION_NAME"
          test "$FUNCTION_NAME" != "None"
          EXPECTED_GIT_SHA="$GITHUB_SHA"
          EXPECTED_TEMPLATE_SHA="${{ steps.deploy_identity.outputs.template_sha256 }}"
          for MODE in selection_capture training status; do
            PAYLOAD=$(printf '{"execution_mode":"%s","executionMode":"%s","mode":"%s","continueAfterUnresolved":true}' "$MODE" "$MODE" "$MODE")
            aws lambda invoke \
              --function-name "$FUNCTION_NAME" \
              --region "$AWS_REGION" \
              --cli-binary-format raw-in-base64-out \
              --payload "$PAYLOAD" \
              "/tmp/mlb_${MODE}.json" >/tmp/mlb_${MODE}_invoke.json
          done
          python - <<'PY'
          import json, os
          expected_git = os.environ.get("GITHUB_SHA")
          expected_template = os.environ.get("EXPECTED_TEMPLATE_SHA") or "${{ steps.deploy_identity.outputs.template_sha256 }}"
          failures = []
          payloads = {}
          for mode in ("selection_capture", "training", "status"):
              payload = json.load(open(f"/tmp/mlb_{mode}.json", encoding="utf-8"))
              payloads[mode] = payload
              identity = payload.get("deploymentIdentity") or ((payload.get("mlbAutoAutonomyChain") or {}).get("deploymentIdentity")) or {}
              if identity.get("gitSha") != expected_git:
                  failures.append(f"{mode}_git_identity_mismatch")
              if identity.get("templateSha256") != expected_template:
                  failures.append(f"{mode}_template_identity_mismatch")
          status = payloads["status"]
          consumer = status.get("v2InferenceConsumer") or ((status.get("mlbAutoAutonomyChain") or {}).get("v2InferenceConsumer")) or {}
          if consumer.get("installed") is not True:
              failures.append("v2_inference_consumer_not_installed")
          training = payloads["training"]
          auto = training.get("mlbAutoAutonomyChain") or {}
          if auto.get("automaticPromotionEnabled") is not True:
              failures.append("automatic_promotion_not_enabled")
          if auto.get("firstPromotionRequiresManualReview") is not False:
              failures.append("manual_first_promotion_still_required")
          if failures:
              raise SystemExit("MLB autonomy identity synchronization failed: " + json.dumps(failures))
          print(json.dumps({"ok": True, "modes": list(payloads), "consumer": consumer}, indent=2, default=str))
          PY

'''
    text = text.replace(marker, block + marker, 1)
    _write(path, text)


def main() -> None:
    patch_training()
    patch_experiment()
    patch_promotion_policy()
    patch_runtime_install()
    patch_compat_handler()
    patch_template()
    patch_deploy_workflow()
    print("Installed MLB AUTO autonomy chain v1 without changing Tennis or Soccer.")


if __name__ == "__main__":
    main()
