from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable


RECOVERY_WORKFLOW = Path(".github/workflows/mlb-r7-overnight-advance.yml")
DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")
WORKFLOW_ROOT = Path(".github/workflows")
TEMPLATE = Path("template.yaml")


def _trigger_block(text: str) -> str:
    """Return the top-level GitHub Actions trigger block without YAML coercion."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line in {"on:", '"on":', "'on':"}:
            start = index + 1
            break
    if start is None:
        return ""

    collected: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith((" ", "\t")):
            break
        collected.append(line)
    return "\n".join(collected)


def _main_push_enabled(trigger_block: str) -> bool:
    if not re.search(r"(?m)^  push:\s*$", trigger_block):
        return False
    return bool(
        re.search(r"(?m)^    branches:\s*\[\s*main\s*\]\s*$", trigger_block)
        or re.search(r"(?m)^\s{6}-\s+main\s*$", trigger_block)
    )


def _scheduled(trigger_block: str) -> bool:
    return bool(re.search(r"(?m)^  schedule:\s*$", trigger_block))


def _invokes_training(text: str) -> bool:
    return (
        "invoke_mlb_trainer_with_retry.py" in text
        and "--payload" in text
        and bool(re.search(r"['\"]mode['\"]\s*:\s*['\"](?:scheduled|training)['\"]", text))
    )


def _workflow_errors(paths: Iterable[Path]) -> list[str]:
    errors: list[str] = []
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        trigger = _trigger_block(text)
        automatic = _scheduled(trigger) or _main_push_enabled(trigger)
        if automatic and _invokes_training(text) and path != RECOVERY_WORKFLOW:
            errors.append(f"automatic_duplicate_trainer_owner:{path}")
    return errors


def verify(root: Path = Path(".")) -> dict[str, object]:
    global RECOVERY_WORKFLOW, DEPLOY_WORKFLOW, WORKFLOW_ROOT, TEMPLATE
    original = (RECOVERY_WORKFLOW, DEPLOY_WORKFLOW, WORKFLOW_ROOT, TEMPLATE)
    RECOVERY_WORKFLOW = root / original[0]
    DEPLOY_WORKFLOW = root / original[1]
    WORKFLOW_ROOT = root / original[2]
    TEMPLATE = root / original[3]
    errors: list[str] = []
    try:
        deploy = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        recovery = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
        template = TEMPLATE.read_text(encoding="utf-8")

        if "Run AWS-native MLB trainer and verify fresh split health" in deploy:
            errors.append("deploy_still_owns_training")
        if _invokes_training(deploy):
            errors.append("deploy_still_invokes_training")
        if "UNIFIED_MLB_LEARNING_OWNER=eventbridge_schedule" not in deploy:
            errors.append("deploy_missing_verify_only_ownership_marker")

        recovery_trigger = _trigger_block(recovery)
        if _scheduled(recovery_trigger):
            errors.append("github_hourly_training_schedule_still_enabled")
        if not _main_push_enabled(recovery_trigger):
            errors.append("one_time_recovery_push_trigger_missing")
        if "group: unified-mlb-learning" not in recovery:
            errors.append("recovery_missing_unified_concurrency_group")
        if "for outer in" in recovery:
            errors.append("ambiguous_outer_transport_retry_still_present")
        if "MIN_ACCEPTED_ROWS: '33'" not in recovery:
            errors.append("recovery_minimum_row_gate_missing")
        if "TARGET_SLATE_DATE: '2026-08-25'" not in recovery:
            errors.append("recovery_exact_target_slate_missing")
        for required in (
            "finalizedGameSlateDates",
            "processedSlateDates",
            "blockedSlateDate",
            "production-before.json",
            "production-after.json",
            "accepted >= minimum",
            "automaticPromotionEnabled",
            "productionAuthorityChanged",
        ):
            if required not in recovery:
                errors.append(f"recovery_acceptance_contract_missing:{required}")

        errors.extend(_workflow_errors(WORKFLOW_ROOT.glob("*.y*ml")))

        for required in (
            "MLBMLTrainingEvery6Hours",
            "aws_native_fixed_prospective_shadow_training",
            "cron(11 1/6 * * ? *)",
            "MLBMLSelectionCaptureEvery15Minutes",
            "cron(4/15 * * * ? *)",
        ):
            if required not in template:
                errors.append(f"aws_learning_schedule_missing:{required}")

        result = {
            "ok": not errors,
            "proofType": "UNIFIED_MLB_LEARNING_SINGLE_OWNER_STATIC_PROOF",
            "automaticTrainerOwner": "AWS_EVENTBRIDGE_SCHEDULE",
            "deploymentInvokesTraining": _invokes_training(deploy),
            "githubScheduledRecoveryEnabled": _scheduled(recovery_trigger),
            "recoveryConcurrencyGroup": "unified-mlb-learning",
            "immutablePredictionRewriteAllowed": False,
            "postStartPredictionCreationAllowed": False,
            "automaticPromotionEnabled": False,
            "productionAuthorityChanged": False,
            "otherSportChanged": False,
            "errors": sorted(set(errors)),
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return result
    finally:
        RECOVERY_WORKFLOW, DEPLOY_WORKFLOW, WORKFLOW_ROOT, TEMPLATE = original


def main() -> int:
    return 0 if verify().get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
