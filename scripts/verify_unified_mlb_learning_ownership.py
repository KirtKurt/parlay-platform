from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable


RECOVERY_WORKFLOW = Path(".github/workflows/unified-mlb-learning-recovery-once.yml")
DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")
WORKFLOW_ROOT = Path(".github/workflows")
TEMPLATE = Path("template.yaml")
MANUAL_ONLY_ARTIFACT_HARDENED_MUTATION_WORKFLOWS = {
    "mlb-historical-v7-recovery.yml",
    "mlb-r8-credit-cap-recovery-now.yml",
    "mlb-v7-settled-horizon-resume-deploy.yml",
    "mlb-v8-incremental-range-extension-deploy.yml",
    "mlb-v8-range-extension-live-proof-once.yml",
}


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



_SECRET_BEARING_LAMBDA_CONFIGURATION_OPERATIONS = (
    "get-function-configuration",
    "update-function-configuration",
    "update-function-code",
    "get-function",
)


def _continued_shell_commands(text: str) -> list[str]:
    lines = text.splitlines()
    commands: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "aws lambda " not in line:
            index += 1
            continue
        parts = [line.removesuffix("\\").rstrip()]
        while line.endswith("\\") and index + 1 < len(lines):
            index += 1
            line = lines[index].strip()
            parts.append(line.removesuffix("\\").rstrip())
        commands.append(" ".join(parts))
        index += 1
    return commands


def _uploaded_artifact_paths(text: str) -> set[str]:
    lines = text.splitlines()
    uploaded: set[str] = set()
    for index, line in enumerate(lines):
        if not re.search(r"\buses:\s*actions/upload-artifact@", line):
            continue
        step_indent = len(line) - len(line.lstrip())
        cursor = index + 1
        while cursor < len(lines):
            candidate = lines[cursor]
            stripped = candidate.strip()
            indent = len(candidate) - len(candidate.lstrip())
            if stripped.startswith("- ") and indent <= step_indent:
                break
            path_match = re.match(r"\s*path:\s*(.*?)\s*$", candidate)
            if not path_match:
                cursor += 1
                continue
            value = path_match.group(1).strip()
            if value in {"|", "|-", ">", ">-"}:
                path_indent = indent
                cursor += 1
                while cursor < len(lines):
                    item = lines[cursor]
                    item_indent = len(item) - len(item.lstrip())
                    if item.strip() and item_indent <= path_indent:
                        break
                    item_value = item.strip()
                    if item_value and not item_value.startswith(("#", "!")):
                        uploaded.add(item_value.strip("'\""))
                    cursor += 1
                continue
            if value and not value.startswith("!"):
                uploaded.add(value.strip("'\""))
            cursor += 1
    return uploaded


def _artifact_path_contains(output_path: str, upload_path: str) -> bool:
    output = output_path.strip("'\"").rstrip("/")
    uploaded = upload_path.strip("'\"").rstrip("/")
    if not output or not uploaded:
        return False
    if any(marker in uploaded for marker in ("*", "?", "[")):
        return fnmatch(output, uploaded)
    return output == uploaded or output.startswith(uploaded + "/")


def _lambda_configuration_query_is_allowlisted(command: str) -> bool:
    match = re.search(
        r"(?:^|\s)--query\s+(?P<query>'[^']*'|\"[^\"]*\"|[^\s]+)",
        command,
    )
    if not match:
        return False
    query = match.group("query").strip("'\"")
    if re.fullmatch(
        r"(?i)(?:Configuration(?:\.Environment)?|Environment(?:\.Variables)?)",
        query,
    ):
        return False
    for pattern in (
        r"(?i)(?:^|[{,])\s*Configuration\s*:\s*Configuration\s*(?:[,}]|$)",
        r"(?i)(?:^|[{,])\s*Environment\s*:\s*Environment\s*(?:[,}]|$)",
        r"(?i)(?:^|[{,])\s*Variables\s*:\s*Environment\.Variables\s*(?:[,}]|$)",
        r"(?i)(?:api[_-]?key|secret|token|password|credential)",
    ):
        if re.search(pattern, query):
            return False
    without_leaf_references = re.sub(
        r"Environment\.Variables\.[A-Za-z_][A-Za-z0-9_]*",
        "",
        query,
    )
    return "Environment.Variables" not in without_leaf_references


def _workflow_artifact_lambda_configuration_exposures(
    paths: Iterable[Path],
) -> list[str]:
    exposures: list[str] = []
    operation_pattern = "|".join(
        re.escape(value)
        for value in _SECRET_BEARING_LAMBDA_CONFIGURATION_OPERATIONS
    )
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        uploaded = _uploaded_artifact_paths(text)
        if not uploaded:
            continue
        for command in _continued_shell_commands(text):
            operation = re.search(
                rf"\baws\s+lambda\s+(?P<operation>{operation_pattern})\b",
                command,
            )
            if not operation:
                continue
            redirect = re.search(
                r"(?:^|\s)(?:[12]?>)\s*(?P<path>[^\s]+)",
                command,
            )
            if not redirect:
                continue
            output_path = redirect.group("path").strip("'\"")
            if not any(
                _artifact_path_contains(output_path, upload_path)
                for upload_path in uploaded
            ):
                continue
            if _lambda_configuration_query_is_allowlisted(command):
                continue
            exposures.append(
                f"{path.name}:{operation.group('operation')}:{output_path}"
            )
    return sorted(exposures)

def _invokes_training(text: str) -> bool:
    # Workflow shell payloads commonly escape JSON quotes (for example,
    # {\"mode\":\"scheduled\"}). Normalize one-or-more escape characters
    # before scanning so automatic owners cannot hide behind shell quoting.
    payload_scan = re.sub(r"\\+(?=[\"'])", "", text)
    training_mode = bool(
        re.search(
            r"['\"]mode['\"]\s*:\s*['\"](?:scheduled|training)['\"]",
            payload_scan,
            flags=re.IGNORECASE,
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



def _all_automatic_trigger_types(trigger_block: str) -> list[str]:
    return sorted(
        {
            match.group(1)
            for match in re.finditer(
                r"(?m)^  ([A-Za-z_][A-Za-z0-9_-]*):\s*$",
                trigger_block,
            )
            if match.group(1) != "workflow_dispatch"
        }
    )


def _manual_only_mutation_workflow_errors(paths: Iterable[Path]) -> list[str]:
    workflows = {path.name: path for path in paths}
    errors: list[str] = []
    for name in sorted(MANUAL_ONLY_ARTIFACT_HARDENED_MUTATION_WORKFLOWS):
        path = workflows.get(name)
        if path is None:
            errors.append(f"manual_only_mutation_workflow_missing:{name}")
            continue
        trigger = _trigger_block(path.read_text(encoding="utf-8"))
        if not _workflow_dispatch_enabled(trigger):
            errors.append(f"manual_only_mutation_dispatch_missing:{name}")
        automatic = _all_automatic_trigger_types(trigger)
        if automatic:
            errors.append(
                f"automatic_mutation_trigger:{name}:{'+'.join(automatic)}"
            )
    return errors

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
        if "MIN_ACCEPTED_ROWS: '39'" not in recovery:
            errors.append("recovery_minimum_row_gate_missing")
        for numeric_contract in (
            "counts_advanced = (",
            "counts_unchanged = (",
            "exact_slates_already_settled_before = (",
            "numeric_progress_satisfied = (",
            "assert accepted >= before_accepted",
            "assert train_count >= before_train_count",
            "assert numeric_progress_satisfied",
        ):
            if numeric_contract not in recovery:
                errors.append(
                    "recovery_before_after_continuity_gate_missing:"
                    + numeric_contract
                )
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

        workflow_paths = list(WORKFLOW_ROOT.glob("*.y*ml"))
        workflow_errors, manual_trainer_count, automatic_chains = _workflow_errors(
            workflow_paths
        )
        errors.extend(workflow_errors)
        artifact_configuration_exposures = (
            _workflow_artifact_lambda_configuration_exposures(workflow_paths)
        )
        errors.extend(
            "workflow_artifact_lambda_configuration_exposure:" + exposure
            for exposure in artifact_configuration_exposures
        )
        manual_only_mutation_errors = (
            _manual_only_mutation_workflow_errors(workflow_paths)
        )
        errors.extend(manual_only_mutation_errors)

        for required in (
            "MLBMLTrainingEvery6Hours",
            "aws_native_fixed_prospective_shadow_training",
            "cron(11 1/6 * * ? *)",
            "MLBMLSelectionCaptureEvery2Minutes",
            "cron(1/2 * * * ? *)",
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
            "workflowArtifactLambdaConfigurationExposures": (
                artifact_configuration_exposures
            ),
            "manualOnlyMutationWorkflowErrors": (
                manual_only_mutation_errors
            ),
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
