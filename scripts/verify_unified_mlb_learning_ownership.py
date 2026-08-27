from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable


RECOVERY_WORKFLOW = Path(".github/workflows/unified-mlb-learning-recovery-once.yml")
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


def _push_enabled(trigger_block: str) -> bool:
    return bool(re.search(r"(?m)^  push:\s*$", trigger_block))


def _main_push_enabled(trigger_block: str) -> bool:
    if not _push_enabled(trigger_block):
        return False
    return bool(
        re.search(r"(?m)^    branches:\s*\[\s*main\s*\]\s*$", trigger_block)
        or re.search(r"(?m)^\s{6}-\s+main\s*$", trigger_block)
    )


def _scheduled(trigger_block: str) -> bool:
    return bool(re.search(r"(?m)^  schedule:\s*$", trigger_block))


def _workflow_run_enabled(trigger_block: str) -> bool:
    return bool(re.search(r"(?m)^  workflow_run:\s*$", trigger_block))


def _workflow_dispatch_enabled(trigger_block: str) -> bool:
    return bool(re.search(r"(?m)^  workflow_dispatch:\s*$", trigger_block))


def _push_paths(trigger_block: str) -> list[str]:
    lines = trigger_block.splitlines()
    inside_push = False
    inside_paths = False
    paths: list[str] = []
    for line in lines:
        if re.match(r"^  [A-Za-z_][A-Za-z0-9_-]*:\s*$", line):
            inside_push = line.strip() == "push:"
            inside_paths = False
            continue
        if not inside_push:
            continue
        if re.match(r"^    [A-Za-z_][A-Za-z0-9_-]*:\s*", line):
            inside_paths = line.strip() == "paths:"
            continue
        if inside_paths:
            match = re.match(r"^\s{6}-\s+(.+?)\s*$", line)
            if match:
                paths.append(match.group(1).strip().strip("'\""))
    return paths


def _self_only_main_push(path: Path, trigger_block: str) -> bool:
    return (
        _main_push_enabled(trigger_block)
        and _push_paths(trigger_block) == [path.as_posix()]
    )


def _invokes_training(text: str) -> bool:
    training_mode = bool(
        re.search(
            r"['\"]mode['\"]\s*:\s*['\"](?:scheduled|training)['\"]",
            text,
        )
    )
    trainer_identity = any(
        marker in text
        for marker in (
            "invoke_mlb_trainer_with_retry.py",
            "MLBMLTrainingFunction",
            "MLBMLTrainingFunctionArn",
            "MLB_ML_TRAINER",
            "MLB_TRAINER",
            "TRAINER_ARN",
        )
    ) or bool(re.search(r"(?m)^\s*(?:TRAINER|trainer)=", text))
    return training_mode and trainer_identity


def _automatic_trigger_types(trigger_block: str) -> list[str]:
    trigger_types: list[str] = []
    if _scheduled(trigger_block):
        trigger_types.append("schedule")
    if _workflow_run_enabled(trigger_block):
        trigger_types.append("workflow_run")
    if _push_enabled(trigger_block):
        trigger_types.append("push")
    return trigger_types


def _workflow_dispatch_targets(text: str, known_workflows: set[str]) -> set[str]:
    """Resolve static workflow-dispatch targets used by repository workflows."""
    assignments: dict[str, str] = {}
    for pattern in (
        r"(?m)^\s*(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
        r"['\"]([^'\"]+\.ya?ml)['\"]",
        r"(?m)^\s*([A-Z][A-Z0-9_]*)\s*:\s*['\"]?"
        r"([^\s'\"]+\.ya?ml)['\"]?\s*$",
    ):
        for match in re.finditer(pattern, text):
            assignments[match.group(1)] = Path(match.group(2)).name

    targets: set[str] = set()
    for pattern in (
        r"\bgh\s+workflow\s+run\s+['\"]?([^\s'\"\\]+\.ya?ml)",
        r"workflow_id\s*:\s*['\"]([^'\"]+\.ya?ml)['\"]",
        r"actions/workflows/([^/\s'\"}]+\.ya?ml)/dispatches",
    ):
        targets.update(Path(match.group(1)).name for match in re.finditer(pattern, text))

    for match in re.finditer(
        r"workflow_id\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", text
    ):
        resolved = assignments.get(match.group(1))
        if resolved:
            targets.add(resolved)
    for pattern in (
        r"\bgh\s+workflow\s+run\s+['\"]?\$\{?([A-Z][A-Z0-9_]*)\}?",
        r"actions/workflows/\$\{?([A-Z][A-Z0-9_]*)\}?/dispatches",
    ):
        for match in re.finditer(pattern, text):
            resolved = assignments.get(match.group(1))
            if resolved:
                targets.add(resolved)
    return targets & known_workflows


def _training_dispatch_path(
    source: str,
    *,
    workflows: dict[str, str],
    dispatch_graph: dict[str, set[str]],
    seen: tuple[str, ...] = (),
) -> list[str] | None:
    if source in seen:
        return None
    if _invokes_training(workflows[source]):
        return [source]
    for target in sorted(dispatch_graph[source]):
        path = _training_dispatch_path(
            target,
            workflows=workflows,
            dispatch_graph=dispatch_graph,
            seen=(*seen, source),
        )
        if path:
            return [source, *path]
    return None


def _automatic_training_dispatch_chains(paths: Iterable[Path]) -> list[str]:
    workflows = {
        path.name: path.read_text(encoding="utf-8") for path in sorted(paths)
    }
    known_workflows = set(workflows)
    dispatch_graph = {
        name: _workflow_dispatch_targets(text, known_workflows)
        for name, text in workflows.items()
    }
    chains: list[str] = []
    for name, text in workflows.items():
        trigger_types = _automatic_trigger_types(_trigger_block(text))
        if not trigger_types:
            continue
        path = _training_dispatch_path(
            name,
            workflows=workflows,
            dispatch_graph=dispatch_graph,
        )
        if path:
            chains.append(f"{'+'.join(trigger_types)}:{'->'.join(path)}")
    return sorted(chains)


def _has_unified_concurrency(text: str) -> bool:
    return bool(
        re.search(
            r"(?ms)^concurrency:\s*$.*?^  group:\s*unified-mlb-learning\s*$"
            r".*?^  cancel-in-progress:\s*false\s*$",
            text,
        )
    )


def _workflow_errors(paths: Iterable[Path]) -> tuple[list[str], int, list[str]]:
    paths = list(paths)
    errors: list[str] = []
    manual_trainers = 0
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        if not _invokes_training(text):
            continue
        if path == DEPLOY_WORKFLOW:
            continue

        trigger = _trigger_block(text)
        if path == RECOVERY_WORKFLOW:
            if _automatic_trigger_types(trigger):
                errors.append("github_recovery_must_be_workflow_dispatch_only")
            if not _workflow_dispatch_enabled(trigger):
                errors.append("recovery_manual_dispatch_missing")
            if not _has_unified_concurrency(text):
                errors.append("recovery_missing_unified_concurrency_group")
            continue

        manual_trainers += 1
        if _automatic_trigger_types(trigger):
            errors.append(f"automatic_duplicate_trainer_owner:{path}")
        if not _workflow_dispatch_enabled(trigger):
            errors.append(f"manual_recovery_dispatch_missing:{path}")
        if not _has_unified_concurrency(text):
            errors.append(f"manual_recovery_not_serialized:{path}")
    automatic_chains = _automatic_training_dispatch_chains(paths)
    errors.extend(f"automatic_trainer_dispatch_chain:{chain}" for chain in automatic_chains)
    return errors, manual_trainers, automatic_chains


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
        if "for outer in" in recovery:
            errors.append("ambiguous_outer_transport_retry_still_present")
        if "MIN_ACCEPTED_ROWS" in recovery or "accepted >= minimum" in recovery:
            errors.append("recovery_stale_row_growth_gate_present")
        if "TARGET_SLATE_DATE: '2026-08-25'" not in recovery:
            errors.append("recovery_exact_target_slate_missing")
        for required in (
            "finalizedGameSlateDates",
            "processedSlateDates",
            "blockedSlateDate",
            "production-before.json",
            "production-after.json",
            "HANDOFF_BASELINE_ACCEPTED_ROWS: '32'",
            "HANDOFF_BASELINE_REJECTED_ROWS: '1'",
            "HANDOFF_BASELINE_TRAIN_ROWS: '32'",
            "RECOVERY_TERMINAL_GAME_COUNT: '15'",
            "Capture signed pre-recovery learning baseline",
            "accepted == baseline_accepted",
            "rejected == baseline_rejected",
            "train_count == baseline_train",
            "finalized == baseline_finalized | {repair, target}",
            "processed == baseline_processed | {repair, target}",
            "finalizedSlateDiagnostics",
            "cooperativeCompletionReceipt",
            "protectedLockReplayCooperativeReceiptVerified",
            "missedLockValidPrelockQuarantineCount",
            "terminalNoPredictionCount",
            "terminalExcludedCount",
            "quarantinedRowsAdmittedToTraining",
            "automaticPromotionEnabled",
            "productionAuthorityChanged",
        ):
            if required not in recovery:
                errors.append(f"recovery_acceptance_contract_missing:{required}")

        workflow_errors, manual_trainer_count, automatic_chains = _workflow_errors(
            WORKFLOW_ROOT.glob("*.y*ml")
        )
        errors.extend(workflow_errors)

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
            "githubWorkflowRunRecoveryEnabled": _workflow_run_enabled(
                recovery_trigger
            ),
            "githubManualTrainerWorkflowCount": manual_trainer_count,
            "automaticTrainerDispatchChains": automatic_chains,
            "recoveryManualOnly": bool(
                _workflow_dispatch_enabled(recovery_trigger)
                and not _automatic_trigger_types(recovery_trigger)
            ),
            "recoveryPushSelfPathOnly": _self_only_main_push(
                RECOVERY_WORKFLOW, recovery_trigger
            ),
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
