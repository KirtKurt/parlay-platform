from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

MANUAL_ONLY = {
    "bootstrap-unified-mlb-r7-recovery-now.yml",
    "finalize-mlb-root-settlement-training-now.yml",
    "hotfix-mlb-prospective-trainer-skip-aug4.yml",
    "launch-mlb-v5-30-day-repair-now.yml",
    "launch-mlb-v5-after-repaired-root-now.yml",
    "mlb-historical-hourly-liveness-v1.yml",
    "mlb-historical-status-snapshot.yml",
    "mlb-historical-supervised-v9-shadow.yml",
    "mlb-historical-watchdog.yml",
    "mlb-r7-overnight-advance.yml",
    "mlb-rolling-24h-audit.yml",
    "mlb-trainer-function-error-diagnostic.yml",
    "mlb-v1-pull-guard.yml",
    "mlb-v10-autonomous-signal-discovery.yml",
    "mlb-v10-material-handoff.yml",
    "mlb-v8-autonomous-controller.yml",
    "mlb-v8-hourly-status.yml",
    "mlb-v8-repeat-runtime-proof.yml",
    "probe-mlb-r7-official-fundamentals-bootstrap.yml",
    "repair-mlb-canonical-label-policy-drift-now.yml",
    "repair-mlb-official-status-normalization-now.yml",
    "repair-mlb-r7-runtime-admission-now.yml",
    "repair-mlb-root-lifecycle-concurrency-once.yml",
    "trigger-mlb-prospective-v5-now.yml",
    "trigger-mlb-prospective-v5-repair-now.yml",
    "trigger-mlb-root-finalizer-now.yml",
    "trigger-mlb-root-lifecycle-continuity-once.yml",
    "unified-mlb-learning-recovery-once.yml",
}

RETIRED_WORKFLOWS = {
    "apply-mlb-auto-bedrock-gateway-now.yml",
    "apply-mlb-bedrock-capacity-iam-now.yml",
    "apply-mlb-live-bedrock-chain-now.yml",
    "apply-mlb-production-chain-repair-v2.yml",
    "complete-mlb-production-repair-now.yml",
    "correct-mlb-auto-live-model-chain-now.yml",
    "deploy-mlb-auto-llm.yml",
    "diagnose-deploy-workflow.yml",
    "diagnose-mlb-r7-aug4-after-lease.yml",
    "diagnose-mlb-r7-aug4-terminal-repair.yml",
    "dispatch-mlb-r7-continuity-after-timeout-fix-once.yml",
    "dispatch-mlb-r7-continuity-chatgpt-20260825-01.yml",
    "dispatch-mlb-bedrock-repair-deploy-20260824.yml",
    "dispatch-mlb-production-repair-deployments.yml",
    "finalize-mlb-auto-production-deploy-now.yml",
    "finalize-mlb-auto-production-once.yml",
    "fix-mlb-continuity-exact-sha-checkout-once.yml",
    "fix-mlb-r7-continuity-timeout-now.yml",
    "fix-mlb-r7-lock-timeout-lease-contract-now.yml",
    "fix-mlb-r7-lock-timeout-lease-contract-v2-now.yml",
    "fix-mlb-r7-lock-timeout-lease-contract-v3-now.yml",
    "fix-mlb-r7-status-consistency-retry-v1-now.yml",
    "fix-mlb-r7-terminal-identity-now.yml",
    "install-mlb-titan-native-recovery-v2.yml",
    "install-mlb-titan-native-recovery-v3.yml",
    "install-mlb-titan-native-recovery-v4.yml",
    "mlb-auto-target-80-enforcer.yml",
    "patch-mlb-comprehensive-repair-to-v5.yml",
    "probe-mlb-r7-aug4-status.yml",
    "repair-mlb-auto-deploy-contract-now.yml",
    "repair-mlb-auto-direct-read-once.yml",
    "repair-mlb-auto-mantle-iam-now.yml",
    "repair-mlb-auto-ml-authority-fallback-once.yml",
    "repair-mlb-auto-ml-authority-fallback-v2.yml",
    "repair-mlb-auto-postdeadline-proof-once.yml",
    "repair-mlb-auto-pregame-odds-replay-once.yml",
    "repair-mlb-auto-target-80.yml",
    "repair-mlb-bounded-smoke-and-watch-now.yml",
    "repair-mlb-bounded-smoke-and-watch-v2.yml",
    "repair-mlb-bounded-smoke-v3.yml",
    "repair-mlb-production-now.yml",
    "repair-mlb-production-chain-20260824.yml",
    "repair-mlb-r7-read-admission-now.yml",
    "repair-mlb-training-continuity-now.yml",
    "repair-mlb-terminal-durability-once.yml",
    "snapshot-mlb-completion-controller-now.yml",
    "snapshot-mlb-auto-deploy-2205.yml",
    "snapshot-mlb-finalization-runs.yml",
    "snapshot-mlb-regional-failover-run-now.yml",
    "snapshot-mlb-repair-runs-now.yml",
    "retrigger-mlb-deploy-after-titan.yml",
    "retire-mlb-v15-10-final.yml",
    "retire-mlb-v15-10-from-auto-now.yml",
    "trigger-correct-mlb-auto-live-model-chain-again.yml",
    "trigger-mlb-r7-terminal-recovery-now.yml",
    "trigger-repair-mlb-production-now.yml",
    "verify-mlb-auto-production-chain.yml",
    "verify-mlb-production-repair-end-to-end.yml",
}

SCHEDULED_MLB_WORKFLOWS = {
    "mlb-30m-progress-pulse.yml",
    "mlb-daily-yesterday-audit.yml",
    "mlb-fresh-audit-publisher.yml",
    "mlb-scoring-guard.yml",
    "mlb-three-source-runtime-watch-v3.yml",
}

RETIRED_PATCH_SCRIPTS = {
    "apply_mlb_bounded_smoke_v3.py",
    "repair_mlb_auto_bedrock_gateway.py",
    "repair_mlb_auto_pregame_odds_replay_once.py",
    "repair_mlb_bounded_smoke_and_watch.py",
    "repair_mlb_production_chain_20260824.py",
    "repair_mlb_production_chain_20260824_v2.py",
    "set_mlb_auto_bedrock_model_chain.py",
}

RETIRED_WORKFLOW_REFERENCES = RETIRED_WORKFLOWS | {
    "Repair MLB training continuity now",
}


def _load(path: Path) -> dict[str, Any]:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(document, dict), f"{path.name} must contain a YAML mapping"
    return document


def _triggers(path: Path) -> dict[str, Any]:
    triggers = _load(path).get("on")
    assert isinstance(triggers, dict), f"{path.name} must have mapped triggers"
    return triggers


def _mlb_workflows() -> list[Path]:
    return sorted(
        path
        for pattern in ("*mlb*.yml", "*mlb*.yaml")
        for path in WORKFLOWS.glob(pattern)
    )


def test_retired_mlb_workflows_are_not_registered() -> None:
    for filename in RETIRED_WORKFLOWS:
        assert not (WORKFLOWS / filename).exists(), filename


def test_remaining_workflows_do_not_reference_retired_mlb_workflows() -> None:
    for workflow in WORKFLOWS.glob("*.yml"):
        source = workflow.read_text(encoding="utf-8")
        references = sorted(
            reference
            for reference in RETIRED_WORKFLOW_REFERENCES
            if reference in source
        )
        assert references == [], f"{workflow.name}: {references}"


def test_retired_patch_generators_have_no_remaining_script_callers() -> None:
    script_root = ROOT / "scripts"
    for filename in RETIRED_PATCH_SCRIPTS:
        assert not (script_root / filename).exists(), filename

    retired_modules = {Path(filename).stem for filename in RETIRED_PATCH_SCRIPTS}
    for script in script_root.glob("*.py"):
        source = script.read_text(encoding="utf-8")
        references = sorted(module for module in retired_modules if module in source)
        assert references == [], f"{script.name}: {references}"
        assert "deploy-mlb-auto-llm.yml" not in source, script.name


def test_all_remaining_mlb_workflows_are_valid_yaml() -> None:
    for workflow in _mlb_workflows():
        _load(workflow)


def test_obsolete_mlb_proofs_repairs_and_dispatchers_are_manual_only() -> None:
    for filename in MANUAL_ONLY:
        assert set(_triggers(WORKFLOWS / filename)) == {"workflow_dispatch"}, filename


def test_all_r7_recovery_mutators_and_dispatchers_are_manual_only() -> None:
    recovery_markers = {
        "prospective_terminal_backlog_reconciliation_v5",
        "repair-mlb-training-continuity-now.yml",
        "unified-mlb-learning-recovery-once.yml",
    }
    for workflow in WORKFLOWS.glob("*.yml"):
        source = workflow.read_text(encoding="utf-8")
        matched = sorted(marker for marker in recovery_markers if marker in source)
        if not matched:
            continue
        assert set(_triggers(workflow)) == {"workflow_dispatch"}, (
            f"{workflow.name} has automatic R7 recovery marker(s): {matched}"
        )


def test_no_mlb_workflow_has_an_unscoped_push_trigger() -> None:
    for workflow in _mlb_workflows():
        push = _triggers(workflow).get("push")
        if push is None:
            continue
        assert isinstance(push, dict), f"{workflow.name} has a global push trigger"
        assert {"paths", "paths-ignore"} & set(push), (
            f"{workflow.name} push must be path scoped"
        )


def test_prospective_deploy_is_the_only_writer_for_mlb_auto_stack() -> None:
    stack_writers: list[str] = []
    for workflow in WORKFLOWS.glob("*.yml"):
        source = workflow.read_text(encoding="utf-8")
        if "STACK_NAME: parlay-platform-mlb-auto-llm" not in source:
            continue
        if not re.search(
            r"sam deploy\b[\s\S]*?--stack-name\s+[\"']?\$STACK_NAME",
            source,
        ):
            continue
        stack_writers.append(workflow.name)

    assert stack_writers == ["deploy-mlb-auto-prospective.yml"]
    assert "push" in _triggers(WORKFLOWS / stack_writers[0])


def test_only_pulse_and_independent_producers_keep_recurring_mlb_schedules() -> None:
    scheduled = {
        workflow.name
        for workflow in _mlb_workflows()
        if "schedule" in _triggers(workflow)
    }
    assert scheduled == SCHEDULED_MLB_WORKFLOWS

    expected_crons = {
        "mlb-30m-progress-pulse.yml": ["11,41 * * * *"],
        "mlb-daily-yesterday-audit.yml": ["0 9 * * *"],
        "mlb-fresh-audit-publisher.yml": ["35 5 * * *", "35 6 * * *"],
        "mlb-scoring-guard.yml": ["7/15 * * * *"],
        "mlb-three-source-runtime-watch-v3.yml": ["*/15 * * * *"],
    }
    for filename, crons in expected_crons.items():
        schedule = _triggers(WORKFLOWS / filename)["schedule"]
        assert [entry["cron"] for entry in schedule] == crons

    # 243 MLB-scoped runs/day plus the existing shared official hourly job.
    assert _triggers(WORKFLOWS / "official-hourly-parlays.yml")["schedule"] == [
        {"cron": "7 * * * *"}
    ]


def test_pulse_keeps_independent_staleness_gated_fallback_producers() -> None:
    pulse = _triggers(WORKFLOWS / "mlb-30m-progress-pulse.yml")
    workflow_run = pulse["workflow_run"]
    assert workflow_run["workflows"] == [
        "MLB Canonical Runtime Health Watch",
        "MLB Scoring Guard",
        "Deploy SAM to AWS",
        "MLB Production Source Contract",
        "Unified MLB learning recovery once",
    ]
    assert workflow_run["types"] == ["completed"]
    assert pulse["push"]["paths"][-1] == "runtime_reports/mlb_*.json"

    source = (WORKFLOWS / "mlb-30m-progress-pulse.yml").read_text(encoding="utf-8")
    assert '[ "$EVENT_NAME" = "workflow_run" ] || [ "$EVENT_NAME" = "push" ]' in source


def test_trainer_deploy_diagnostic_is_manual_read_only_status() -> None:
    workflow = WORKFLOWS / "mlb-trainer-deploy-health-diagnostic-once.yml"
    triggers = _triggers(workflow)
    source = workflow.read_text(encoding="utf-8")

    assert set(triggers) == {"pull_request", "workflow_dispatch"}
    assert "github.event_name == 'workflow_dispatch'" in source
    assert source.count("aws lambda invoke") == 1
    assert "--payload '{\"sport\":\"mlb\",\"mode\":\"status\"}'" in source
    assert '"mode":"scheduled"' not in source
    assert "--status-only" in source
    assert "trainerInvocationPerformed') is False" in source
