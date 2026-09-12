from __future__ import annotations

import json

import pytest

from engineering_agent.runtime import (
    _filter_superseded_evidence,
    _planner_payload,
    _validate_focus_domain,
    classify_task_domain,
    next_focus_domain,
    planner_attempt_budget,
    prioritized_focus_domains,
)


def _task(title: str, objective: str, *implementation: str) -> dict:
    return {
        "title": title,
        "objective": objective,
        "implementation": list(implementation) or ["Add focused diagnostics."],
        "likelyFiles": ["hello_world/mlb_successor_runtime_v1.py"],
        "acceptanceTests": ["Existing fail-closed behavior is unchanged."],
        "safetyReceipts": [
            "no_direct_production_deploy",
            "no_main_branch_write",
            "no_model_promotion",
            "no_secret_mutation",
            "no_other_sport_change",
        ],
        "evidenceBasis": ["Current MLB evidence supports this task."],
    }


def test_classifies_completed_deployment_identity_domain() -> None:
    task = _task(
        "Bind successor health to MLB-specific deployment identity",
        "Compare the MLB runtime identity rather than unrelated repository HEAD.",
    )
    assert classify_task_domain(task) == "deployment_identity"


def test_current_blockers_prioritize_measured_performance_before_accumulation() -> None:
    evidence = """
    {"reason":"insufficient_clean_rows"}
    {"blockers":["NO_POSITIVE_BRIER_SKILL","CALIBRATION_ERROR_TOO_HIGH"]}
    {"successor":{"blockers":["INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN"]}}
    """
    ranked = prioritized_focus_domains(evidence)
    assert ranked[0] == "calibration"
    assert "challenger_model" in ranked
    assert "clean_cohort" in ranked


def test_next_focus_skips_domain_already_rejected_this_cycle() -> None:
    evidence = (
        "insufficient_clean_rows "
        "INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN "
        "CALIBRATION_ERROR_TOO_HIGH"
    )
    rejected = [
        {
            "title": "Diagnose calibration deficit",
            "domain": "calibration",
            "reason": "schema invalid",
        }
    ]
    assert next_focus_domain(evidence, rejected) == "challenger_model"


def test_next_focus_returns_unconstrained_fallback_after_all_ranked_domains_rejected() -> None:
    evidence = (
        "insufficient_clean_rows "
        "INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN "
        "CALIBRATION_ERROR_TOO_HIGH"
    )
    rejected = [
        {"title": "Calibration task", "domain": "calibration", "reason": "blocked"},
        {"title": "Challenger task", "domain": "challenger_model", "reason": "blocked"},
        {"title": "Clean task", "domain": "clean_cohort", "reason": "blocked"},
    ]
    assert next_focus_domain(evidence, rejected) is None


def test_attempt_budget_keeps_one_safe_fallback_after_evidenced_domains() -> None:
    evidence = (
        "CALIBRATION_ERROR_TOO_HIGH "
        "INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN "
        "MISSING_T10_SNAPSHOTS"
    )
    assert prioritized_focus_domains(evidence) == [
        "data_capture",
        "calibration",
        "challenger_model",
    ]
    assert planner_attempt_budget(3, evidence) == 5


def test_attempt_budget_remains_bounded_to_initial_plus_focus_domains() -> None:
    evidence = " ".join(
        [
            "health failed",
            "MISSING_T10_SNAPSHOTS",
            "CALIBRATION_ERROR_TOO_HIGH",
            "INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN",
            "INSUFFICIENT_CLEAN_ROWS",
        ]
    )
    assert len(prioritized_focus_domains(evidence)) == 5
    assert planner_attempt_budget(3, evidence) == 6
    assert planner_attempt_budget(99, evidence) == 6


def test_focus_validation_rejects_cross_domain_retry() -> None:
    deployment_task = _task(
        "Bind successor health to deployment identity",
        "Repair implementation identity checks.",
    )
    with pytest.raises(ValueError, match="required=clean_cohort"):
        _validate_focus_domain(deployment_task, "clean_cohort")


def test_focus_validation_accepts_matching_clean_cohort_task() -> None:
    clean_task = _task(
        "Diagnose source-honest MLB clean-cohort admission bottlenecks",
        "Explain why immutable fundamentals snapshots are quarantined from clean rows.",
        "Add read-only admission diagnostics without rewriting locks, labels, or feature vectors.",
    )
    assert _validate_focus_domain(clean_task, "clean_cohort") == "clean_cohort"


def test_retry_prompt_carries_deterministic_focus_and_excludes_rejected_domains() -> None:
    payload = json.loads(
        _planner_payload(
            "insufficient_clean_rows",
            {"decisions": []},
            [
                {
                    "title": "Bind successor health to MLB-specific deployment identity",
                    "domain": "deployment_identity",
                    "reason": "completed",
                },
                {
                    "title": "Old clean task",
                    "domain": "clean_cohort",
                    "reason": "stale evidence",
                },
            ],
            required_focus_domain=None,
        )
    )
    assert payload["requiredFocusDomain"] is None
    assert "deployment_identity" in payload["excludedFocusDomains"]
    assert "clean_cohort" in payload["excludedFocusDomains"]
    assert "Do not return work from excludedFocusDomains" in payload["instruction"]
    assert set(payload["requiredSafetyReceipts"]) == {
        "no_direct_production_deploy",
        "no_main_branch_write",
        "no_model_promotion",
        "no_secret_mutation",
        "no_other_sport_change",
    }


def test_retired_diagnostic_optimizer_reports_cannot_drive_planner_focus() -> None:
    evidence = "\n".join(
        [
            json.dumps(
                {
                    "kind": "current_runtime_report",
                    "path": "runtime_reports/mlb_ml_outcome_challenger_latest.json",
                    "observedEpoch": 9999999999,
                    "content": '{"ok":false,"reason":"insufficient_clean_rows"}',
                }
            ),
            json.dumps(
                {
                    "kind": "current_runtime_report",
                    "path": "runtime_reports/mlb_successor_runtime_health_latest.json",
                    "observedEpoch": 9999999999,
                    "content": '{"status":"ACCUMULATING_TEAM_CONTEXT_DEVELOPMENT_DATA"}',
                }
            ),
        ]
    )
    filtered = _filter_superseded_evidence(evidence)
    assert "mlb_ml_outcome_challenger_latest.json" not in filtered
    assert "mlb_successor_runtime_health_latest.json" in filtered


def test_healthy_capture_words_do_not_create_false_data_capture_priority() -> None:
    evidence = (
        '{"ingestion":{"health":"HEALTHY","errors":[]},"statcastDays":30,'
        '"snapshotWrites":15,"missedT10":[]}'
    )
    assert "data_capture" not in prioritized_focus_domains(evidence)


def test_focus_scan_must_use_full_filtered_evidence_not_prompt_tail() -> None:
    early_blocker = json.dumps(
        {
            "kind": "current_runtime_report",
            "path": "runtime_reports/mlb_successor_v2_verification_20260909.json",
            "observedEpoch": 9999999999,
            "content": '{"blockers":["INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN"]}',
        }
    )
    late_large_report = json.dumps(
        {
            "kind": "current_runtime_report",
            "path": "runtime_reports/mlb_scoring_fix_post_deploy_latest.json",
            "observedEpoch": 9999999999,
            "content": "x" * 32000,
        }
    )
    filtered = _filter_superseded_evidence(early_blocker + "\n" + late_large_report)
    assert next_focus_domain(filtered, []) == "challenger_model"
    assert next_focus_domain(filtered[-28000:], []) is None


# Issue #781 acceptance evidence. These tests intentionally expose current
# defects; do not mark them xfail or merge a failing controller contract.
# Only external I/O is substituted. Production validation and run() stay real.


def _acceptance_task(title: str, objective: str) -> dict:
    value = _task(title, objective, "Report read-only metrics.")
    value["likelyFiles"] = ["hello_world/mlb_metric_diagnostics.py"]
    value["evidenceBasis"] = ["Synthetic offline fixture; not production evidence."]
    return value


def _acceptance_response(task) -> dict:
    return {
        "ok": True,
        "mode": "engineering_plan",
        "text": task if isinstance(task, str) else json.dumps(task),
        "routeId": "offline-fixture",
        "decisionAuthority": "PLANNING_ONLY",
        "bedrockAvailable": True,
    }


def _offline_cycle(monkeypatch, tmp_path, replies, *, evidence="", history=None):
    import copy
    import engineering_agent.runtime as runtime

    prompts = []
    pending = iter(copy.deepcopy(replies))
    monkeypatch.setattr(runtime, "load_decision_history", lambda: copy.deepcopy(history or {"decisions": []}))
    monkeypatch.setattr(runtime, "_function_name", lambda *_: "offline-fixture-only")

    def invoke(_function_name, prompt):
        prompts.append(json.loads(prompt))
        try:
            return next(pending)
        except StopIteration:
            raise AssertionError("controller exceeded the supplied offline attempt sequence") from None

    monkeypatch.setattr(runtime, "_invoke", invoke)
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text(evidence)
    kwargs = {
        "evidence_path": evidence_path,
        "output_path": tmp_path / "next-task.json",
        "response_path": tmp_path / "response.json",
        "attempts_path": tmp_path / "attempts.json",
        "stack_name": "offline-no-aws",
        "logical_id": "offline-no-aws",
        "max_attempts": 3,
    }
    return runtime, kwargs, prompts


@pytest.mark.parametrize("returned_domain", ["deployment_identity", "other"])
def test_781_failed_requested_domain_is_exhausted_even_for_wrong_or_malformed_reply(returned_domain):
    evidence = "CALIBRATION_ERROR_TOO_HIGH INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN"
    rejected = [{
        "title": "Unusable fixture proposal",
        "domain": returned_domain,
        "requiredFocusDomain": "calibration",
        "reason": "wrong-domain or malformed response",
    }]
    assert next_focus_domain(evidence, rejected) == "challenger_model"


def test_781_unconstrained_fallback_cannot_accept_a_rejected_domain(monkeypatch, tmp_path):
    first = _acceptance_task("Assess Brier bins", "Read-only diagnostic counts.")
    first["safetyReceipts"].remove("no_secret_mutation")
    second = _acceptance_task("Inspect calibration error histogram", "Read-only histogram totals.")
    eligible = _acceptance_task("Summarize residual bins", "Read-only numeric summaries.")
    assert classify_task_domain(first) == classify_task_domain(second) == "calibration"
    assert classify_task_domain(eligible) == "other"
    runtime, kwargs, prompts = _offline_cycle(
        monkeypatch, tmp_path,
        [_acceptance_response(t) for t in (first, second, eligible)],
        evidence="CALIBRATION_ERROR_TOO_HIGH",
    )
    result = runtime.run(**kwargs)
    assert result["title"] == eligible["title"], "prompt exclusions must also govern acceptance"
    assert result["plannerAttempt"] == 3
    assert "calibration" in prompts[1]["excludedFocusDomains"]
    ledger = json.loads(kwargs["attempts_path"].read_text())
    assert "rejected" in ledger[1] and ledger[2]["accepted"] is True


def test_781_post_normalization_domain_failure_is_recorded_and_replanned(monkeypatch, tmp_path):
    closed = _acceptance_task("Previously closed deployment identity probe", "Read-only deployment identity report.")
    filtered = _acceptance_task("Inspect membership counts", "Read-only sample summaries.")
    # No path is read or written. Removing this non-allowlisted fixture path
    # changes the classifier result and must follow the normal rejection path.
    filtered["likelyFiles"] = ["outside_allowlist/clean_cohort_admission_quarantine.txt"]
    eligible = _acceptance_task("Summarize residual bins", "Read-only numeric summaries.")
    assert classify_task_domain(filtered) == "clean_cohort"
    history = {"decisions": [{"title": closed["title"], "status": "COMPLETED"}]}
    runtime, kwargs, _ = _offline_cycle(
        monkeypatch, tmp_path,
        [_acceptance_response(t) for t in (closed, filtered, eligible)],
        evidence="insufficient_clean_rows", history=history,
    )
    result = runtime.run(**kwargs)
    assert result["title"] == eligible["title"]
    ledger = json.loads(kwargs["attempts_path"].read_text())
    assert len(ledger) == 3 and "rejected" in ledger[1]
    assert ledger[2]["accepted"] is True


@pytest.mark.parametrize("receipt", [
    "no_direct_production_deploy", "no_main_branch_write", "no_model_promotion",
    "no_secret_mutation", "no_other_sport_change",
])
def test_781_missing_authority_receipt_never_publishes_a_task(monkeypatch, tmp_path, receipt):
    proposed = _acceptance_task("Read-only health counters", "Record heartbeat totals without writes.")
    proposed["safetyReceipts"].remove(receipt)
    runtime, kwargs, prompts = _offline_cycle(
        monkeypatch, tmp_path, [_acceptance_response(proposed) for _ in range(3)],
    )
    with pytest.raises(RuntimeError, match="no acceptable planner task"):
        runtime.run(**kwargs)
    assert not kwargs["output_path"].exists()
    ledger = json.loads(kwargs["attempts_path"].read_text())
    assert len(prompts) == len(ledger) == 3
    assert receipt in ledger[0]["rejected"]
    assert all("accepted" not in attempt for attempt in ledger)


def test_781_exhaustion_remains_bounded_and_never_fabricates_an_accepted_task(monkeypatch, tmp_path):
    evidence = (
        "health failed MISSING_T10_SNAPSHOTS CALIBRATION_ERROR_TOO_HIGH "
        "INSUFFICIENT_OBSERVED_TEAM_CONTEXT_TRAIN INSUFFICIENT_CLEAN_ROWS"
    )
    runtime, kwargs, prompts = _offline_cycle(
        monkeypatch, tmp_path, [_acceptance_response("not-json") for _ in range(6)],
        evidence=evidence,
    )
    kwargs["max_attempts"] = 999
    with pytest.raises(RuntimeError, match="no acceptable planner task after 6 bounded attempts"):
        runtime.run(**kwargs)
    assert len(prompts) == 6
    assert not kwargs["output_path"].exists()
    ledger = json.loads(kwargs["attempts_path"].read_text())
    assert len(ledger) == 6
    assert all("rejected" in attempt and "accepted" not in attempt for attempt in ledger)


def test_781_distinct_valid_task_keeps_all_five_authority_restrictions(monkeypatch, tmp_path):
    task = _acceptance_task("Summarize residual bins", "Read-only numeric summaries.")
    runtime, kwargs, prompts = _offline_cycle(monkeypatch, tmp_path, [_acceptance_response(task)])
    accepted = runtime.run(**kwargs)
    assert accepted["plannerAttempt"] == 1 and len(prompts) == 1
    assert accepted["runtimeDecisionAuthority"] == "PLANNING_ONLY"
    for key in ("noDirectProductionDeploy", "noMainBranchWrite", "noModelPromotion", "noSecretMutation", "noOtherSportChange"):
        assert accepted[key] is True
    ledger = json.loads(kwargs["attempts_path"].read_text())
    assert len(ledger) == 1 and ledger[0]["accepted"] is True


@pytest.mark.parametrize("file_fields", [
    pytest.param({}, id="missing"),
    pytest.param({"likelyFiles": None}, id="null"),
    pytest.param({"likelyFiles": []}, id="empty"),
    pytest.param({"likelyFiles": ["", "  "]}, id="blank"),
    pytest.param({"likelyFiles": ["template.yaml"]}, id="forbidden-exact"),
    pytest.param({"likelyFiles": ["engineering_agent/credentials.py"]}, id="forbidden-fragment"),
    pytest.param({"likelyFiles": ["tennis_auto_llm/runtime.py", "soccer_auto_llm/runtime.py", "arb/runtime.py"]}, id="other-sports"),
    pytest.param({"likelyFiles": [".github/workflows/mlb-engineering-planner.yml"]}, id="workflow-only"),
])
def test_817_rejects_proposal_without_allowed_files_after_normalization(file_fields):
    from engineering_agent.planner import validate

    proposed = _acceptance_task("Summarize residual bins", "Read-only numeric summaries.")
    proposed.pop("likelyFiles")
    proposed.update(file_fields)
    with pytest.raises(ValueError, match="likelyFiles must contain at least one allowed path after policy normalization"):
        validate(proposed, decision_history={"decisions": []})


def test_817_mixed_proposal_keeps_allowed_mlb_paths_and_authority_restrictions():
    from engineering_agent.planner import validate

    proposed = _acceptance_task("Summarize residual bins", "Read-only numeric summaries.")
    proposed["likelyFiles"] = [
        " hello_world/mlb_metric_diagnostics.py ",
        "template.yaml",
        "engineering_agent/credentials.py",
        "tennis_auto_llm/runtime.py",
        "soccer_auto_llm/runtime.py",
        "arb/runtime.py",
        ".github/workflows/mlb-engineering-planner.yml",
        "tests/unit/test_mlb_engineering_runtime.py",
    ]
    accepted = validate(proposed, decision_history={"decisions": []})
    assert accepted["likelyFiles"] == [
        "hello_world/mlb_metric_diagnostics.py",
        "tests/unit/test_mlb_engineering_runtime.py",
    ]
    for key in ("noDirectProductionDeploy", "noMainBranchWrite", "noModelPromotion", "noSecretMutation", "noOtherSportChange"):
        assert accepted[key] is True


def test_817_normalization_rejection_is_recorded_then_distinct_task_is_accepted(monkeypatch, tmp_path):
    rejected = _acceptance_task("Inspect membership counts", "Read-only sample summaries.")
    rejected["likelyFiles"] = ["outside_allowlist/metrics.py"]
    eligible = _acceptance_task("Summarize residual bins", "Read-only numeric summaries.")
    runtime, kwargs, prompts = _offline_cycle(
        monkeypatch, tmp_path, [_acceptance_response(t) for t in (rejected, eligible)],
    )
    accepted = runtime.run(**kwargs)
    assert accepted["title"] == eligible["title"]
    assert accepted["plannerAttempt"] == len(prompts) == 2
    ledger = json.loads(kwargs["attempts_path"].read_text())
    assert len(ledger) == 2
    assert "after policy normalization" in ledger[0]["rejected"]
    assert "accepted" not in ledger[0] and ledger[1]["accepted"] is True
    assert json.loads(kwargs["output_path"].read_text())["title"] == eligible["title"]


def test_817_normalization_rejections_cannot_exceed_six_attempts_or_publish(monkeypatch, tmp_path):
    # Distinct, unclassified titles reach the real normalizer on every attempt;
    # neither duplicate-title nor focus-domain rejection supplies this proof.
    proposals = [
        _acceptance_task(title, "Read-only numeric summaries.")
        for title in ("Alpha bins", "Bravo totals", "Charlie counts", "Delta samples", "Echo buckets", "Foxtrot values")
    ]
    for proposed in proposals:
        proposed["likelyFiles"] = ["outside_allowlist/metrics.py"]
    runtime, kwargs, prompts = _offline_cycle(
        monkeypatch, tmp_path, [_acceptance_response(t) for t in proposals],
    )
    kwargs["max_attempts"] = 999
    with pytest.raises(RuntimeError, match="no acceptable planner task after 6 bounded attempts"):
        runtime.run(**kwargs)
    ledger = json.loads(kwargs["attempts_path"].read_text())
    assert len(prompts) == len(ledger) == 6
    assert [attempt["attempt"] for attempt in ledger] == list(range(1, 7))
    assert all(attempt["attemptBudget"] == 6 for attempt in ledger)
    assert all("after policy normalization" in attempt["rejected"] for attempt in ledger)
    assert all("accepted" not in attempt for attempt in ledger)
    assert not kwargs["output_path"].exists()
    assert not kwargs["response_path"].exists()
