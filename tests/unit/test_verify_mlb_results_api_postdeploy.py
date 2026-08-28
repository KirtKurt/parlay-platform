from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
import json
from urllib.parse import quote
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts import verify_mlb_results_api_postdeploy as subject


FUNCTION_ARN = "arn:aws:lambda:us-east-1:123456789012:function:results"
RESULTS_RULE_ARN = "arn:aws:events:us-east-1:123456789012:rule/results-rule"
ROOT = Path(__file__).resolve().parents[2]


def _producer_provenance(window_start, *, event_offset_seconds=4, request_id=None):
    return {
        "schema_version": "MLB-RESULT-SIGNAL-PRODUCER-PROOF-v1",
        "authority": "NATIVE_EVENTBRIDGE_SCHEDULE_ENVELOPE",
        "lambda_request_id": (
            request_id or "11111111-1111-4111-8111-111111111111"
        ),
        "event_id": "33333333-3333-4333-8333-333333333333",
        "event_time_utc": (
            window_start + timedelta(seconds=event_offset_seconds)
        ).isoformat(),
        "event_source": "aws.events",
        "detail_type": "Scheduled Event",
        "rule_arn": RESULTS_RULE_ARN,
        "account": "123456789012",
        "region": "us-east-1",
    }


def _native_summary(window_start, *, event_offset_seconds=4):
    created = window_start + timedelta(minutes=5, seconds=1)
    return {
        "PK": "RESULT_SIGNAL#mlb#2026-08-27",
        "SK": f"SUMMARY#{created.isoformat()}",
        "entity_type": "MLB_RESULT_SIGNAL_LEARNING_SUMMARY",
        "sport": "mlb",
        "game_date_et": "2026-08-27",
        "version": "MLB-RESULT-SIGNAL-LEARNING-v1",
        "created_at": created.isoformat(),
        "stored_rows": 0,
        "summary": {},
        "producer_provenance": _producer_provenance(
            window_start,
            event_offset_seconds=event_offset_seconds,
        ),
    }


class FakeApiGateway:
    def __init__(self, route_methods, *, extra_results_route=None):
        self.route_methods = route_methods
        self.extra_results_route = extra_results_route

    def get_resources(self, **kwargs):
        items = []
        for index, (path, methods) in enumerate(sorted(self.route_methods.items())):
            items.append(
                {
                    "id": f"r{index}",
                    "path": path,
                    "resourceMethods": {method: {} for method in methods},
                }
            )
        if self.extra_results_route:
            path, method = self.extra_results_route
            items.append(
                {
                    "id": "extra",
                    "path": path,
                    "resourceMethods": {method: {}},
                }
            )
        return {"items": items}

    def get_integration(self, **kwargs):
        resource_id = kwargs["resourceId"]
        targets_results = resource_id != "unrelated"
        uri = (
            "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/"
            + (FUNCTION_ARN if targets_results else FUNCTION_ARN + "-other")
            + "/invocations"
        )
        return {
            "type": "AWS_PROXY",
            "httpMethod": "POST",
            "uri": uri,
        }


class FakeEvents:
    def __init__(self, *, schedule=subject.EXPECTED_SCHEDULE, input_selector=None):
        self.schedule = schedule
        self.input_selector = input_selector

    def list_rules(self, **kwargs):
        return {
            "Rules": [
                {
                    "Name": "results-rule",
                    "Arn": (
                        "arn:aws:events:us-east-1:123456789012:"
                        "rule/results-rule"
                    ),
                    "State": "ENABLED",
                    "ScheduleExpression": self.schedule,
                }
            ]
        }

    def list_targets_by_rule(self, **kwargs):
        target = {
            "Id": "Results",
            "Arn": FUNCTION_ARN,
            "RetryPolicy": {
                "MaximumEventAgeInSeconds": 300,
                "MaximumRetryAttempts": 0,
            },
        }
        if self.input_selector is not None:
            target[self.input_selector] = "{}"
        return {"Targets": [target]}


class FakeDeployedStage:
    def __init__(self, *, add_post=False, caching=False):
        self.add_post = add_post
        self.caching = caching

    def get_stage(self, **kwargs):
        return {
            "deploymentId": "deployment-1",
            "cacheClusterEnabled": self.caching,
            "methodSettings": {},
        }

    def _methods(self):
        methods = {
            path: {"GET": {}, "OPTIONS": {}}
            for path in subject.RESULT_PATHS
        }
        if self.add_post:
            methods[subject.RESULT_PATHS[-1]]["POST"] = {}
        return methods

    def get_deployment(self, **kwargs):
        return {"apiSummary": self._methods()}

    def get_export(self, **kwargs):
        paths = {}
        for path, methods in self._methods().items():
            paths[path] = {}
            for method in methods:
                paths[path][method.lower()] = {
                    "x-amazon-apigateway-integration": {
                        "type": "aws_proxy",
                        "uri": (
                            "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/"
                            f"functions/{FUNCTION_ARN}/invocations"
                        ),
                    }
                }
        return {"body": BytesIO(json.dumps({"openapi": "3.0.1", "paths": paths}).encode())}


def exact_methods():
    return {
        path: {"GET", "OPTIONS"}
        for path in subject.RESULT_PATHS
    }


def _zip_artifact(content=b"deployed-results-code"):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("mlb_results_scheduler.py", content)
    return buffer.getvalue()


def _deploy_artifact_proofs(artifact):
    content_manifest = subject.zip_content_manifest(artifact)
    code_sha = subject.lambda_code_sha256(artifact)
    build = {
        "schemaVersion": subject.MANIFEST_SCHEMA_VERSION,
        "expectedGitSha": "a" * 40,
        "expectedTemplateSha256": "b" * 64,
        "functions": {subject.RESULTS_LOGICAL_ID: content_manifest},
    }
    identity = {
        "ok": True,
        "expectedGitSha": "a" * 40,
        "expectedTemplateSha256": "b" * 64,
        "expectedDeployRunId": "123-1",
        "functions": {
            subject.RESULTS_LOGICAL_ID: {
                "identityMatches": True,
                "codeArtifactMatchesCleanBuild": True,
                "expectedCodeContentManifest": content_manifest,
                "deployedCodeContentManifest": content_manifest,
                "codeSha256": code_sha,
            }
        },
    }
    return build, identity


def test_current_lambda_zip_matches_exact_triggering_deploy_artifact(monkeypatch):
    artifact = _zip_artifact()
    build, identity = _deploy_artifact_proofs(artifact)
    monkeypatch.setattr(subject, "_download_lambda_artifact", lambda _url: artifact)
    function = {
        "Configuration": {
            "CodeSha256": subject.lambda_code_sha256(artifact),
            "Environment": {
                "Variables": {
                    "INQSI_DEPLOY_GIT_SHA": "a" * 40,
                    "INQSI_DEPLOY_TEMPLATE_SHA256": "b" * 64,
                    "INQSI_DEPLOY_RUN_ID": "123-1",
                }
            },
        },
        "Code": {"Location": "https://lambda.invalid/exact.zip"},
    }

    proof = subject.verify_deployed_code_artifact(
        function,
        expected_build_manifest=build,
        deploy_identity=identity,
        expected_deploy_sha="a" * 40,
        expected_template_sha256="b" * 64,
        expected_deploy_run_id="123-1",
    )

    assert proof["matchesExactTriggeringDeployArtifact"] is True


def test_matching_deploy_environment_cannot_hide_postdeploy_code_replacement(
    monkeypatch,
):
    deployed_artifact = _zip_artifact()
    replaced_artifact = _zip_artifact(b"replacement-preserving-environment")
    build, identity = _deploy_artifact_proofs(deployed_artifact)
    monkeypatch.setattr(
        subject,
        "_download_lambda_artifact",
        lambda _url: replaced_artifact,
    )
    function = {
        "Configuration": {
            "CodeSha256": subject.lambda_code_sha256(replaced_artifact),
            "Environment": {
                "Variables": {
                    "INQSI_DEPLOY_GIT_SHA": "a" * 40,
                    "INQSI_DEPLOY_TEMPLATE_SHA256": "b" * 64,
                    "INQSI_DEPLOY_RUN_ID": "123-1",
                }
            },
        },
        "Code": {"Location": "https://lambda.invalid/replaced.zip"},
    }

    with pytest.raises(subject.VerificationError, match="exact clean deploy build"):
        subject.verify_deployed_code_artifact(
            function,
            expected_build_manifest=build,
            deploy_identity=identity,
            expected_deploy_sha="a" * 40,
            expected_template_sha256="b" * 64,
            expected_deploy_run_id="123-1",
        )


def test_exact_live_api_surface_requires_five_get_and_five_options():
    proof = subject.verify_api_surface(
        FakeApiGateway(exact_methods()),
        rest_api_id="api-id",
        function_arn=FUNCTION_ARN,
    )

    assert proof["exactFiveGetFiveOptionsNoPost"] is True
    assert len(proof["resultsSchedulerIntegrations"]) == 10
    assert all(
        tuple(methods) == ("GET", "OPTIONS")
        for methods in proof["routeMethods"].values()
    )


def test_api_surface_rejects_post_even_when_get_and_options_remain():
    methods = exact_methods()
    methods[subject.RESULT_PATHS[-1]].add("POST")

    with pytest.raises(subject.VerificationError, match="method surface mismatch"):
        subject.verify_api_surface(
            FakeApiGateway(methods),
            rest_api_id="api-id",
            function_arn=FUNCTION_ARN,
        )


def test_api_surface_rejects_an_alias_integration_to_results_lambda():
    api = FakeApiGateway(
        exact_methods(),
        extra_results_route=("/legacy/results", "GET"),
    )

    with pytest.raises(subject.VerificationError, match="unexpected API integration"):
        subject.verify_api_surface(
            api,
            rest_api_id="api-id",
            function_arn=FUNCTION_ARN,
        )


def test_deployed_prod_export_binds_exact_read_only_integrations():
    proof = subject.verify_deployed_stage(
        FakeDeployedStage(),
        rest_api_id="api-id",
        function_arn=FUNCTION_ARN,
    )

    assert proof["exactFiveGetFiveOptionsNoPost"] is True
    assert len(proof["exportedResultsSchedulerIntegrations"]) == 10
    assert proof["cacheClusterEnabled"] is False


def test_deployed_prod_export_rejects_stale_post_or_cache():
    with pytest.raises(subject.VerificationError, match="method surface mismatch"):
        subject.verify_deployed_stage(
            FakeDeployedStage(add_post=True),
            rest_api_id="api-id",
            function_arn=FUNCTION_ARN,
        )
    with pytest.raises(subject.VerificationError, match="caching is enabled"):
        subject.verify_deployed_stage(
            FakeDeployedStage(caching=True),
            rest_api_id="api-id",
            function_arn=FUNCTION_ARN,
        )


def test_api_url_must_bind_exact_rest_api_region_and_prod_stage():
    proof = subject.verify_api_url(
        "https://abc.execute-api.us-east-1.amazonaws.com/Prod/",
        rest_api_id="abc",
        region="us-east-1",
        stage_name="Prod",
    )
    assert proof["exact"] is True

    with pytest.raises(subject.VerificationError, match="does not bind"):
        subject.verify_api_url(
            "https://abc.execute-api.us-west-2.amazonaws.com/Prod/",
            rest_api_id="abc",
            region="us-east-1",
            stage_name="Prod",
        )


def test_schedule_proof_requires_exact_enabled_event_and_retry_contract():
    proof = subject.verify_schedule(FakeEvents(), function_arn=FUNCTION_ARN)

    assert proof["exact"] is True
    assert proof["scheduleExpression"] == "cron(6/15 * * * ? *)"
    assert proof["inputMode"] == "NATIVE_EVENTBRIDGE_ENVELOPE"
    assert proof["targetInputSelectorsAbsent"] is True
    assert proof["ruleArn"].endswith(":rule/results-rule")


@pytest.mark.parametrize("selector", ("Input", "InputPath", "InputTransformer"))
def test_schedule_proof_rejects_any_native_envelope_suppressor(selector):
    with pytest.raises(subject.VerificationError, match="suppresses native"):
        subject.verify_schedule(
            FakeEvents(input_selector=selector),
            function_arn=FUNCTION_ARN,
        )


def test_schedule_proof_rejects_a_different_cadence():
    with pytest.raises(subject.VerificationError, match="cadence mismatch"):
        subject.verify_schedule(
            FakeEvents(schedule="rate(6 hours)"),
            function_arn=FUNCTION_ARN,
        )


def test_decimal_fingerprint_is_typed_deterministic_and_order_independent():
    rows_a = [
        {"PK": "A", "SK": "2", "value": Decimal("1.0")},
        {"PK": "A", "SK": "1", "value": Decimal("1")},
    ]
    rows_b = list(reversed(rows_a))

    sorted_a = sorted(rows_a, key=lambda row: (row["PK"], row["SK"]))
    sorted_b = sorted(rows_b, key=lambda row: (row["PK"], row["SK"]))
    assert subject._sha256(sorted_a) == subject._sha256(sorted_b)
    assert subject._sha256(Decimal("1.0")) != subject._sha256("1.0")
    assert subject._sha256(Decimal("1.0")) != subject._sha256(Decimal("1"))
    assert subject._sha256(Decimal("1")) != subject._sha256({"decimal": "1"})


def test_authoritative_partitions_are_exactly_the_pinned_historical_slate():
    result = subject.authoritative_tracked_partitions("2026-08-04")

    assert result == {
        "OutcomesTable": (
            "MLB_CANONICAL_FINAL_LABEL#2026-08-04",
            "OUTCOME#mlb#2026-08-04",
        ),
        "PredictionsTable": ("PRED#mlb#2026-08-04",),
        "SignalLedgerTable": ("RESULT_SIGNAL#mlb#2026-08-04",),
    }


def test_current_recent_partitions_are_separate_diagnostic_canaries():
    result = subject.diagnostic_current_partitions("2026-08-27")

    assert len(result["OutcomesTable"]) == 14
    assert len(result["PredictionsTable"]) == 7
    assert len(result["SignalLedgerTable"]) == 7
    assert "PRED#mlb#2026-08-27" in result["PredictionsTable"]
    assert "PRED#mlb#2026-08-21" in result["PredictionsTable"]
    assert "RESULT_SIGNAL#mlb#2026-08-27" in result["SignalLedgerTable"]
    assert "MLB_CANONICAL_FINAL_LABEL#2026-08-27" in result["OutcomesTable"]
    assert all("2026-08-04" not in pk for pks in result.values() for pk in pks)


def test_current_canary_query_failure_is_recorded_without_gating():
    class BrokenDiagnosticTable:
        name = "predictions"

        def query(self, **kwargs):
            raise RuntimeError("legitimate-current-partition-unavailable")

    result = subject.snapshot_partitions_diagnostic(
        {"PredictionsTable": BrokenDiagnosticTable()},
        {"PredictionsTable": ("PRED#mlb#2026-08-27",)},
    )

    row = result["PredictionsTable"]["partitions"]["PRED#mlb#2026-08-27"]
    assert row["available"] is False
    assert row["error"].startswith("RuntimeError:")
    assert result["PredictionsTable"]["authoritative"] is False


def test_integration_arn_parser_is_exact_not_a_prefix_match():
    prefix = "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/"
    assert subject._integration_lambda_arn(
        prefix + FUNCTION_ARN + "/invocations"
    ) == FUNCTION_ARN
    assert subject._integration_lambda_arn(
        prefix + quote(FUNCTION_ARN, safe="") + "/invocations"
    ) == FUNCTION_ARN
    assert subject._integration_lambda_arn(
        prefix + FUNCTION_ARN + "-other/invocations"
    ) != FUNCTION_ARN


def test_zero_write_proof_rejects_any_partition_fingerprint_change():
    before = {
        "OutcomesTable": {
            "tableName": "outcomes",
            "partitions": {"OUTCOME#mlb#2026-08-04": {"count": 1, "fingerprint": "a"}},
        }
    }
    after = {
        "OutcomesTable": {
            "tableName": "outcomes",
            "partitions": {"OUTCOME#mlb#2026-08-04": {"count": 1, "fingerprint": "b"}},
        }
    }

    with pytest.raises(subject.VerificationError, match="pinned historical protected"):
        subject.verify_snapshots_unchanged(before, after)


def test_zero_write_proof_accepts_exact_snapshot_equality():
    snapshot = {
        "SignalLedgerTable": {
            "tableName": "ledger",
            "partitions": {"RESULT_SIGNAL#mlb#2026-08-04": {"count": 0, "fingerprint": "a"}},
        }
    }

    proof = subject.verify_snapshots_unchanged(snapshot, snapshot)
    assert proof["protectedPartitionsUnchanged"] is True
    assert proof["tableWideWriteMetricsAuthoritative"] is False


def test_legitimate_unrelated_table_write_metric_does_not_invalidate_partition_proof():
    snapshot = {
        "PredictionsTable": {
            "tableName": "predictions",
            "partitions": {
                "PRED#mlb#2026-08-04": {"count": 7, "fingerprint": "same"}
            },
        }
    }
    unrelated_activity = {
        "tableName": "predictions",
        "operation": "PutItem",
        "sampleCount": 7,
        "attribution": "MLBAuditedPull",
    }

    proof = subject.verify_snapshots_unchanged(
        snapshot,
        snapshot,
        table_wide_write_diagnostic=unrelated_activity,
    )

    assert proof["protectedPartitionsUnchanged"] is True
    assert proof["tableWideWriteMetricsAuthoritative"] is False
    assert proof["nonAuthoritativeTableWideWriteDiagnostic"] == unrelated_activity


@pytest.mark.parametrize(
    "logical_id,partition",
    [
        ("PredictionsTable", "PRED#mlb#2026-08-27"),
        ("SignalLedgerTable", "RESULT_SIGNAL#mlb#2026-08-27"),
    ],
)
def test_current_partition_mutation_is_diagnostic_not_authoritative(
    logical_id, partition
):
    before = {
        logical_id: {
            "tableName": logical_id,
            "partitions": {partition: {"count": 1, "fingerprint": "before"}},
        }
    }
    after = {
        logical_id: {
            "tableName": logical_id,
            "partitions": {partition: {"count": 2, "fingerprint": "after"}},
        }
    }

    changes = subject.partition_snapshot_changes(before, after)

    assert len(changes) == 1
    assert changes[0]["partition"] == partition
    assert changes[0]["before"]["fingerprint"] == "before"
    assert changes[0]["after"]["fingerprint"] == "after"


def test_legitimate_current_writer_change_cannot_fail_pinned_slate_authority():
    pinned = {
        "PredictionsTable": {
            "tableName": "predictions",
            "partitions": {
                "PRED#mlb#2026-08-04": {"count": 15, "fingerprint": "pinned"}
            },
        }
    }
    current_before = {
        "PredictionsTable": {
            "tableName": "predictions",
            "partitions": {
                "PRED#mlb#2026-08-27": {"count": 7, "fingerprint": "before"}
            },
        }
    }
    current_after = {
        "PredictionsTable": {
            "tableName": "predictions",
            "partitions": {
                "PRED#mlb#2026-08-27": {"count": 8, "fingerprint": "after"}
            },
        }
    }

    authoritative = subject.verify_snapshots_unchanged(pinned, pinned)
    diagnostic = subject.partition_snapshot_changes(current_before, current_after)

    assert authoritative["protectedPartitionsUnchanged"] is True
    assert authoritative["pinnedHistoricalSlateOutsideRecurringWriterAuthority"] is True
    assert len(diagnostic) == 1


def test_natural_advance_requires_a_post_observation_summary():
    start = datetime(2026, 8, 28, 1, 0, tzinfo=timezone.utc)
    old = {
        "PK": "RESULT_SIGNAL#mlb#2026-08-27",
        "SK": "SUMMARY#2026-08-28T00:51:00+00:00",
        "entity_type": "MLB_RESULT_SIGNAL_LEARNING_SUMMARY",
        "created_at": "2026-08-28T00:51:00+00:00",
    }
    new = {
        "PK": "RESULT_SIGNAL#mlb#2026-08-27",
        "SK": "SUMMARY#2026-08-28T01:06:03+00:00",
        "entity_type": "MLB_RESULT_SIGNAL_LEARNING_SUMMARY",
        "created_at": "2026-08-28T01:06:03+00:00",
    }

    assert subject._new_summary_rows([old], [old, new], not_before=start) == [new]
    assert subject._new_summary_rows([old], [old], not_before=start) == []


def test_pre_fence_baseline_retries_when_query_crosses_cron_boundary(monkeypatch):
    class Clock:
        current = datetime(
            2026,
            8,
            28,
            1,
            5,
            59,
            900_000,
            tzinfo=timezone.utc,
        )

        def sleep(self, seconds):
            self.current += timedelta(seconds=seconds)

    clock = Clock()
    queries = []

    def fake_query(_table, pk):
        queries.append(pk)
        if len(queries) == 1:
            clock.current = datetime(
                2026,
                8,
                28,
                1,
                6,
                0,
                100_000,
                tzinfo=timezone.utc,
            )
            return [{"PK": pk, "SK": "crossed-fence"}]
        return [{"PK": pk, "SK": "clean-pre-fence"}]

    monkeypatch.setattr(subject, "_now", lambda: clock.current)
    monkeypatch.setattr(subject, "_bounded_query", fake_query)
    monkeypatch.setattr(subject.time, "sleep", clock.sleep)

    fence, baseline = subject.prepare_post_probe_schedule_observation(
        object(),
        probe_completed_at=clock.current,
    )

    assert fence["windowStartUtc"] == "2026-08-28T01:21:00Z"
    assert fence["baselineAttempt"] == 2
    assert fence["baselineCompletedStrictlyBeforeFence"] is True
    assert baseline == [{"PK": queries[-1], "SK": "clean-pre-fence"}]
    assert len(queries) == 2


def test_observation_skips_candidate_whose_delivery_horizon_crosses_et_midnight(
    monkeypatch,
):
    class Clock:
        current = datetime(
            2026,
            8,
            28,
            3,
            50,
            0,
            0,
            tzinfo=timezone.utc,
        )

        def sleep(self, seconds):
            self.current += timedelta(seconds=seconds)

    clock = Clock()
    queries = []

    def fake_query(_table, pk):
        queries.append(pk)
        return []

    monkeypatch.setattr(subject, "_now", lambda: clock.current)
    monkeypatch.setattr(subject, "_bounded_query", fake_query)
    monkeypatch.setattr(subject.time, "sleep", clock.sleep)

    fence, baseline = subject.prepare_post_probe_schedule_observation(
        object(),
        probe_completed_at=clock.current,
    )

    assert queries == ["RESULT_SIGNAL#mlb#2026-08-28"]
    assert fence["windowStartUtc"] == "2026-08-28T04:06:00Z"
    assert fence["scheduledSlateDateEt"] == "2026-08-28"
    assert fence["partition"] == "RESULT_SIGNAL#mlb#2026-08-28"
    assert fence["baselineAttempt"] == 1
    assert fence["midnightSensitiveCandidatesSkipped"] == [
        "2026-08-28T03:51:00Z"
    ]
    assert fence["maximumScheduleToHandlerAgeSeconds"] == 600
    assert fence["candidateAvoidsEtMidnightDeliveryHorizon"] is True
    assert baseline == []


def test_row_already_in_pre_fence_baseline_cannot_false_pass_advance(monkeypatch):
    started = datetime(2026, 8, 28, 1, 6, tzinfo=timezone.utc)
    already_present = _native_summary(started)
    monkeypatch.setattr(
        subject,
        "_bounded_query",
        lambda _table, _pk: [already_present],
    )

    with pytest.raises(subject.VerificationError, match="No new scheduled"):
        subject.wait_for_natural_schedule_advance(
            object(),
            baseline=[already_present],
            slate_date_et="2026-08-27",
            timeout_seconds=0,
            observation_start=started,
            expected_rule_arn=RESULTS_RULE_ARN,
        )


def test_new_native_summary_binds_exact_eventbridge_occurrence(monkeypatch):
    started = datetime(2026, 8, 28, 1, 6, tzinfo=timezone.utc)
    old = {
        "PK": "RESULT_SIGNAL#mlb#2026-08-27",
        "SK": "SUMMARY#2026-08-28T00:51:00+00:00",
        "entity_type": "MLB_RESULT_SIGNAL_LEARNING_SUMMARY",
        "created_at": "2026-08-28T00:51:00+00:00",
    }
    new = _native_summary(started, event_offset_seconds=70)
    monkeypatch.setattr(subject, "_bounded_query", lambda _table, _pk: [old, new])
    monkeypatch.setattr(
        subject,
        "_now",
        lambda: started + timedelta(minutes=6),
    )

    proof = subject.wait_for_natural_schedule_advance(
        object(),
        baseline=[old],
        slate_date_et="2026-08-27",
        timeout_seconds=1,
        observation_start=started,
        expected_rule_arn=RESULTS_RULE_ARN,
    )

    assert proof["newSummaryCount"] == 1
    assert proof["producerProvenance"]["lambda_request_id"] == (
        "11111111-1111-4111-8111-111111111111"
    )
    assert proof["causalBinding"] == (
        "NATIVE_EVENTBRIDGE_ENVELOPE_AND_LAMBDA_REQUEST_ID"
    )


def test_late_finishing_prior_occurrence_does_not_false_fail_selected_advance(
    monkeypatch,
):
    started = datetime(2026, 8, 28, 1, 6, tzinfo=timezone.utc)
    prior = _native_summary(started, event_offset_seconds=-15 * 60)
    prior_created = started + timedelta(minutes=1)
    prior["SK"] = f"SUMMARY#{prior_created.isoformat()}"
    prior["created_at"] = prior_created.isoformat()
    selected = _native_summary(started, event_offset_seconds=70)
    monkeypatch.setattr(
        subject,
        "_bounded_query",
        lambda _table, _pk: [prior, selected],
    )
    monkeypatch.setattr(subject, "_now", lambda: started + timedelta(minutes=6))

    proof = subject.wait_for_natural_schedule_advance(
        object(),
        baseline=[],
        slate_date_et="2026-08-27",
        timeout_seconds=1,
        observation_start=started,
        expected_rule_arn=RESULTS_RULE_ARN,
    )

    assert proof["newSummaryKey"]["SK"] == selected["SK"]
    assert proof["ignoredOtherOccurrenceSummaryCount"] == 1
    assert proof["ignoredOtherOccurrenceSummaries"] == [
        {
            "PK": prior["PK"],
            "SK": prior["SK"],
            "event_time_utc": "2026-08-28T00:51:00Z",
        }
    ]


def test_next_cron_occurrence_event_time_is_rejected():
    started = datetime(2026, 8, 28, 1, 6, tzinfo=timezone.utc)
    with pytest.raises(subject.VerificationError, match="selected cron occurrence"):
        subject._validated_native_schedule_provenance(
            _producer_provenance(started, event_offset_seconds=15 * 60),
            expected_rule_arn=RESULTS_RULE_ARN,
            window_start=started,
        )


@pytest.mark.parametrize(
    "probe_completed,expected",
    [
        ("2026-08-28T01:05:59.999999+00:00", "2026-08-28T01:06:00+00:00"),
        ("2026-08-28T01:06:00+00:00", "2026-08-28T01:21:00+00:00"),
        ("2026-08-28T01:20:59.999999+00:00", "2026-08-28T01:21:00+00:00"),
        ("2026-08-28T01:21:00+00:00", "2026-08-28T01:36:00+00:00"),
    ],
)
def test_schedule_fence_uses_a_strictly_later_cron_minute_bucket(
    probe_completed, expected
):
    assert subject._next_natural_schedule_boundary(
        datetime.fromisoformat(probe_completed)
    ) == datetime.fromisoformat(expected)


def test_schedule_slate_binding_uses_fence_time_across_et_midnight():
    probe_completed = datetime.fromisoformat("2026-08-28T03:58:00+00:00")
    schedule_window_start = subject._next_natural_schedule_boundary(
        probe_completed
    )

    assert subject._slate_date_et_at(probe_completed) == "2026-08-27"
    assert schedule_window_start == datetime.fromisoformat(
        "2026-08-28T04:06:00+00:00"
    )
    assert subject._slate_date_et_at(schedule_window_start) == "2026-08-28"


def test_prior_minute_http_probes_do_not_contaminate_schedule_metrics_or_logs(
    monkeypatch,
):
    window_start = datetime(2026, 8, 28, 1, 6, tzinfo=timezone.utc)

    class MetricClient:
        def __init__(self):
            self.requests = []

        def get_metric_statistics(self, **kwargs):
            self.requests.append(kwargs)
            points = {
                ("AWS/Lambda", "Invocations"): (
                    (window_start - timedelta(seconds=1), 19.0),
                    (window_start + timedelta(seconds=2), 2.0),
                ),
                ("AWS/Lambda", "Errors"): (
                    (window_start + timedelta(seconds=2), 1.0),
                ),
                ("AWS/Events", "Invocations"): (
                    (window_start + timedelta(seconds=1), 1.0),
                    (window_start + timedelta(minutes=15), 1.0),
                ),
                ("AWS/Events", "FailedInvocations"): (),
            }[(kwargs["Namespace"], kwargs["MetricName"])]
            return {
                "Datapoints": [
                    {"Timestamp": timestamp, "Sum": value}
                    for timestamp, value in points
                    if kwargs["StartTime"] <= timestamp < kwargs["EndTime"]
                ]
            }

    request_id = "11111111-1111-4111-8111-111111111111"
    prior_id = "22222222-2222-4222-8222-222222222222"
    event_id = "33333333-3333-4333-8333-333333333333"
    concurrent_id = "44444444-4444-4444-8444-444444444444"
    summary_key = {
        "PK": "RESULT_SIGNAL#mlb#2026-08-27",
        "SK": "SUMMARY#2026-08-28T01:11:01+00:00",
    }

    class LogClient:
        def filter_log_events(self, **kwargs):
            def event(at, message):
                return {"timestamp": int(at.timestamp() * 1000), "message": message}

            rows = [
                event(
                    window_start - timedelta(seconds=1),
                    f"START RequestId: {prior_id} Version: $LATEST",
                ),
                event(
                    window_start - timedelta(milliseconds=900),
                    f"END RequestId: {prior_id}",
                ),
                event(
                    window_start - timedelta(milliseconds=800),
                    f"REPORT RequestId: {prior_id} Duration: 1 ms",
                ),
                event(
                    window_start + timedelta(seconds=2),
                    f"START RequestId: {request_id} Version: $LATEST",
                ),
                event(
                    window_start + timedelta(seconds=3),
                    f"END RequestId: {request_id}",
                ),
                event(
                    window_start + timedelta(seconds=3),
                    f"START RequestId: {concurrent_id} Version: $LATEST",
                ),
                event(
                    window_start + timedelta(milliseconds=3500),
                    f"END RequestId: {concurrent_id}",
                ),
                event(
                    window_start + timedelta(milliseconds=3600),
                    f"REPORT RequestId: {concurrent_id} Duration: 500 ms",
                ),
                event(
                    window_start + timedelta(milliseconds=3900),
                    json.dumps(
                        {
                            "event": "MLB_RESULT_SIGNAL_SUMMARY_PERSISTED",
                            "schema_version": (
                                "MLB-RESULT-SIGNAL-PRODUCER-PROOF-v1"
                            ),
                            "lambda_request_id": request_id,
                            "event_id": event_id,
                            "rule_arn": RESULTS_RULE_ARN,
                            **summary_key,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
                event(
                    window_start + timedelta(seconds=4),
                    f"REPORT RequestId: {request_id} Duration: 1000 ms",
                ),
            ]
            return {
                "events": [
                    row
                    for row in rows
                    if kwargs["startTime"] <= row["timestamp"] < kwargs["endTime"]
                ]
            }

    monkeypatch.setattr(
        subject,
        "_now",
        lambda: window_start + timedelta(minutes=16),
    )
    metrics_client = MetricClient()
    metrics = subject.wait_for_schedule_metrics(
        metrics_client,
        function_name="results",
        rule_name="results-rule",
        start=window_start,
        timeout_seconds=1,
        publication_settle_seconds=0,
    )
    platform_log = subject.wait_for_request_bound_clean_lambda_log(
        LogClient(),
        function_name="results",
        start=window_start,
        end=window_start + timedelta(seconds=5),
        request_id=request_id,
        event_id=event_id,
        rule_arn=RESULTS_RULE_ARN,
        summary_key=summary_key,
        timeout_seconds=1,
    )

    assert metrics["lambdaInvocations"] == 2
    assert metrics["lambdaErrors"] == 1
    assert metrics["eventBridgeInvocations"] == 1
    assert metrics["lambdaAggregateMetricsAuthoritative"] is False
    assert metrics["windowStartUtc"] == "2026-08-28T01:06:00Z"
    assert all(
        request["StartTime"] == window_start
        for request in metrics_client.requests
    )
    assert all(
        request["EndTime"] == window_start + timedelta(minutes=15)
        for request in metrics_client.requests
    )
    assert platform_log["requestId"] == request_id
    assert platform_log["unrelatedRequestIds"] == [concurrent_id]
    assert platform_log["unrelatedRequestsGateVerification"] is False


def test_request_bound_timeout_report_cannot_pass_as_clean():
    window_start = datetime(2026, 8, 28, 1, 6, tzinfo=timezone.utc)
    request_id = "11111111-1111-4111-8111-111111111111"
    event_id = "33333333-3333-4333-8333-333333333333"
    summary_key = {
        "PK": "RESULT_SIGNAL#mlb#2026-08-27",
        "SK": "SUMMARY#2026-08-28T01:11:01+00:00",
    }

    class LogClient:
        def filter_log_events(self, **_kwargs):
            structured = {
                "event": "MLB_RESULT_SIGNAL_SUMMARY_PERSISTED",
                "schema_version": "MLB-RESULT-SIGNAL-PRODUCER-PROOF-v1",
                "lambda_request_id": request_id,
                "event_id": event_id,
                "rule_arn": RESULTS_RULE_ARN,
                **summary_key,
            }
            return {
                "events": [
                    {
                        "message": (
                            f"START RequestId: {request_id} Version: $LATEST"
                        )
                    },
                    {"message": f"END RequestId: {request_id}"},
                    {
                        "message": json.dumps(
                            structured,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    },
                    {
                        "message": (
                            f"REPORT RequestId: {request_id} Duration: 900000 ms "
                            "Status: timeout"
                        )
                    },
                ]
            }

    with pytest.raises(subject.VerificationError, match="non-success status"):
        subject.wait_for_request_bound_clean_lambda_log(
            LogClient(),
            function_name="results",
            start=window_start,
            end=window_start + timedelta(minutes=10),
            request_id=request_id,
            event_id=event_id,
            rule_arn=RESULTS_RULE_ARN,
            summary_key=summary_key,
            timeout_seconds=1,
        )


def test_historical_probe_duration_has_an_exact_non_schedule_boundary():
    started = datetime(2026, 8, 28, 1, 9, tzinfo=timezone.utc)
    exact = subject.verify_bounded_probe_duration(
        probe_started=started,
        probe_finished=datetime(2026, 8, 28, 1, 11, 30, tzinfo=timezone.utc),
        probe_budget_seconds=150,
    )
    assert exact["withinBudget"] is True
    assert exact["scheduleGapOrWriterQuiescenceAsserted"] is False

    with pytest.raises(subject.VerificationError, match="bounded budget"):
        subject.verify_bounded_probe_duration(
            probe_started=started,
            probe_finished=datetime(
                2026,
                8,
                28,
                1,
                11,
                30,
                1,
                tzinfo=timezone.utc,
            ),
            probe_budget_seconds=150,
        )


def test_ml_selection_and_training_writers_cannot_target_protected_partitions():
    proof = subject.verify_ml_training_protected_partition_isolation()

    assert proof["writerTableEnvironment"] == "SNAPSHOTS_TABLE"
    assert proof["outcomesAccess"] == "CANONICAL_LABEL_READ_ONLY"
    assert proof["canonicalLabelReaderMutationCalls"] == []
    assert proof["protectedOutcomesPredictionsResultSignalsOrLabelsWritable"] is False
    assert proof["allDynamoDbMutationCallsConfinedToAwsTrainingStore"] is True


def test_ml_training_source_proof_rejects_any_mutation_outside_store():
    path = "hello_world/mlb_ml_aws_training_v1_compat.py"
    escaped_source = (ROOT / path).read_text(encoding="utf-8") + (
        "\n\ndef escaped_writer(table):\n"
        "    table.put_item(Item={'PK': 'unrecognized-runtime-key'})\n"
    )

    with pytest.raises(subject.VerificationError, match="escaped AwsTrainingStore"):
        subject.verify_ml_training_protected_partition_isolation(
            source_overrides={path: escaped_source}
        )


def test_hostile_envelopes_serialize_one_unambiguous_method_source():
    verifier_source = (
        ROOT / "scripts" / "verify_mlb_results_api_postdeploy.py"
    ).read_text(encoding="utf-8")
    rest_block = verifier_source.split('f"rest-v1-post:{path}"', 1)[1].split(
        '"http-v2-post"', 1
    )[0]
    assert rest_block.count('httpMethod="POST"') == 1

    envelopes = subject.hostile_http_envelopes()
    assert len(envelopes) == len(subject.RESULT_PATHS) + 3
    assert len({label for label, _ in envelopes}) == len(envelopes)

    for label, event in envelopes:
        serialized = json.dumps(event, separators=(",", ":"))
        json.loads(serialized)
        if label.startswith("rest-v1-post:") or label == "alb-post":
            assert event["httpMethod"] == "POST"
            assert serialized.count('"httpMethod"') == 1
        elif label == "http-v2-post":
            assert "httpMethod" not in event
            assert event["requestContext"]["http"]["method"] == "POST"
            assert serialized.count('"method"') == 1
        else:
            assert label == "rest-method-missing"
            assert "httpMethod" not in event
            assert "http" not in event["requestContext"]


def _get_response(status, body):
    return {
        "status": status,
        "headers": {"content-type": "application/json"},
        "json": body,
    }


def test_public_final_scores_contract_requires_fetch_disabled():
    body = {
        "ok": True,
        "sport": "mlb",
        "slate_date_et": "2026-08-04",
        "fetch_report": {
            "ok": True,
            "skipped": True,
            "reason": "fetch_scores_false",
        },
        "final_scores": [],
    }
    proof = subject.verify_public_get_contract(
        "/v1/results/mlb/final-scores",
        _get_response(200, body),
        probe_slate_date="2026-08-04",
    )
    assert proof["valid"] is True

    body["fetch_report"] = {"ok": True, "stored": 1}
    with pytest.raises(subject.VerificationError, match="fetch was disabled"):
        subject.verify_public_get_contract(
            "/v1/results/mlb/final-scores",
            _get_response(200, body),
            probe_slate_date="2026-08-04",
        )


def test_public_settlement_contract_requires_legacy_disabled_and_no_creation():
    body = {
        "ok": False,
        "sport": "mlb",
        "slateDateEt": "2026-08-04",
        "immutablePregameRowsMutated": False,
        "labelCreatedCount": 0,
        "settlementAuthority": "CANONICAL_IMMUTABLE_LOCK_OFFICIAL_GAME_PK",
        "legacyDiagnosticIsAuthoritative": False,
        "legacyDiagnosticCompatibility": {
            "ok": True,
            "executed": False,
            "authoritative": False,
            "status": "LEGACY_DIAGNOSTIC_DISABLED",
        },
    }
    proof = subject.verify_public_get_contract(
        "/v1/results/mlb/settlement",
        _get_response(409, body),
        probe_slate_date="2026-08-04",
    )
    assert proof["valid"] is True

    body["legacyDiagnosticCompatibility"]["executed"] = True
    with pytest.raises(subject.VerificationError, match="hard-disable"):
        subject.verify_public_get_contract(
            "/v1/results/mlb/settlement",
            _get_response(409, body),
            probe_slate_date="2026-08-04",
        )


def test_public_result_signals_contract_rejects_mutating_build_schema():
    body = {
        "ok": True,
        "sport": "mlb",
        "game_date_et": "2026-08-04",
        "count": 0,
        "items": [],
        "stored_rows": 0,
    }
    with pytest.raises(subject.VerificationError, match="mutating build schema"):
        subject.verify_public_get_contract(
            "/v1/results/mlb/result-signals",
            _get_response(200, body),
            probe_slate_date="2026-08-04",
        )


def test_postdeploy_workflow_never_synthetically_invokes_the_scheduler():
    workflow = (
        ROOT
        / ".github"
        / "workflows"
        / "verify-mlb-results-api-read-only-postdeploy.yml"
    ).read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "workflow_dispatch" not in workflow
    assert "actions: read" in workflow
    assert "aws lambda invoke" not in workflow
    assert "put-rule" not in workflow
    assert "disable-rule" not in workflow
    assert "enable-rule" not in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert 'gh run download "$DEPLOY_RUN_ID"' in workflow
    download_step = workflow.split(
        "- name: Download exact triggering deploy artifact proof", 1
    )[1].split("- name: Setup Python 3.11", 1)[0]
    assert (
        "DEPLOY_RUN_ATTEMPT: ${{ github.event.workflow_run.run_attempt }}"
        in download_step
    )
    assert (
        'mlb-deployment-identity-${DEPLOY_RUN_ID}-${DEPLOY_RUN_ATTEMPT}'
        in download_step
    )
    assert workflow.count(
        "DEPLOY_RUN_ATTEMPT: ${{ github.event.workflow_run.run_attempt }}"
    ) == 2
    assert "mlb_lambda_build_manifest_deploy.json" in workflow
    assert "mlb_deploy_identity_latest.json" in workflow
    assert "--deploy-build-manifest" in workflow
    assert "--deploy-identity" in workflow
    assert "timeout-minutes: 75" in workflow
    assert workflow.index("git merge-base --is-ancestor") < workflow.index(
        "Configure AWS credentials"
    )
    assert "scripts/verify_mlb_results_api_postdeploy.py" in workflow


def test_scheduled_advance_evidence_does_not_claim_settlement_or_other_producer_health():
    verifier = (
        ROOT / "scripts" / "verify_mlb_results_api_postdeploy.py"
    ).read_text(encoding="utf-8")

    assert '"canonicalSettlementHttp200Asserted": False' in verifier
    assert '"otherProducerHealthAsserted": False' in verifier
    assert "NATURAL_METHODLESS_RESULTS_SCHEDULER_ADVANCEMENT_AND_PLATFORM_CLEANLINESS" in verifier
    assert '"hello_world/mlb_result_signals.py"' in verifier
