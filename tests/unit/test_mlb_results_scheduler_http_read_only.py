from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_results_scheduler as subject


FINAL_PATHS = (
    "/v1/mlb/scores/final",
    "/v1/results/mlb/final-scores",
)
PROOF_PATHS = (
    "/v1/results/mlb/proof",
    "/v1/mlb/settlement/proof_report",
)
SETTLEMENT_PATHS = (
    "/v1/results/mlb/settlement",
    "/v1/mlb/settlement/slate",
)
LEARNING_PATHS = (
    "/v1/results/mlb/signal-learning",
    "/v1/mlb/signal-learning",
)
RESULT_SIGNAL_PATHS = (
    "/v1/results/mlb/result-signals",
    "/v1/mlb/result-signals",
)
ALL_HTTP_PATHS = (
    *FINAL_PATHS,
    *PROOF_PATHS,
    *SETTLEMENT_PATHS,
    *LEARNING_PATHS,
    *RESULT_SIGNAL_PATHS,
)
PUBLIC_SAM_PATHS = (
    "/v1/results/mlb/final-scores",
    "/v1/results/mlb/settlement",
    "/v1/results/mlb/proof",
    "/v1/results/mlb/signal-learning",
    "/v1/results/mlb/result-signals",
)
DENIED_HTTP_METHODS = (
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
    "CONNECT",
    "TRACE",
)
HTTP_EVENT_VERSIONS = ("v1", "v2", "alb")
EXPECTED_SAM_API_EVENTS = {
    ("MLBFinalScoresGet", "/v1/results/mlb/final-scores", "GET"),
    ("MLBFinalScoresOptions", "/v1/results/mlb/final-scores", "OPTIONS"),
    ("MLBSettlementGet", "/v1/results/mlb/settlement", "GET"),
    ("MLBSettlementOptions", "/v1/results/mlb/settlement", "OPTIONS"),
    ("MLBSettlementProofGet", "/v1/results/mlb/proof", "GET"),
    ("MLBSettlementProofOptions", "/v1/results/mlb/proof", "OPTIONS"),
    ("MLBSignalLearningGet", "/v1/results/mlb/signal-learning", "GET"),
    ("MLBSignalLearningOptions", "/v1/results/mlb/signal-learning", "OPTIONS"),
    ("MLBResultSignalsGet", "/v1/results/mlb/result-signals", "GET"),
    ("MLBResultSignalsOptions", "/v1/results/mlb/result-signals", "OPTIONS"),
}

RULE_ARN = (
    "arn:aws:events:us-east-1:123456789012:"
    "rule/parlay-MLBResultsEvery6Hours-abc"
)
EVENT_ID = "11111111-1111-4111-8111-111111111111"
REQUEST_ID = "22222222-2222-4222-8222-222222222222"


class _LambdaContext:
    aws_request_id = REQUEST_ID


def _native_schedule_event():
    return {
        "version": "0",
        "id": EVENT_ID,
        "detail-type": "Scheduled Event",
        "source": "aws.events",
        "account": "123456789012",
        "time": "2026-08-28T01:06:04Z",
        "region": "us-east-1",
        "resources": [RULE_ARN],
        "detail": {},
    }


def _body(response):
    return json.loads(response["body"])


def _http_event(method, path, *, version="v1"):
    query = {
        "date": "2026-08-04",
        "days_from": "3",
        "fetch_scores": "true",
        "store": "true",
        "build": "true",
        "legacy_diagnostic": "true",
    }
    body = json.dumps(
        {
            **query,
            "days_from": "3",
        }
    )
    if version == "v2":
        return {
            "version": "2.0",
            "rawPath": path,
            "requestContext": {
                "apiId": "public-api",
                "http": {"method": method, "path": path},
            },
            "queryStringParameters": query,
            "body": body,
        }
    if version == "alb":
        return {
            "httpMethod": method,
            "path": path,
            "requestContext": {
                "elb": {
                    "targetGroupArn": (
                        "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
                        "targetgroup/public/abc"
                    )
                }
            },
            "queryStringParameters": query,
            "body": body,
        }
    assert version == "v1"
    return {
        "httpMethod": method,
        "path": path,
        "requestContext": {"apiId": "public-api"},
        "queryStringParameters": query,
        "body": body,
    }


def _forbid_all_dependencies(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP method gate invoked an application dependency")

    for name in (
        "final_mlb_scores_report",
        "build_signal_learning_report",
        "build_result_signals",
        "latest_result_signals",
        "legacy_settle_mlb_slate",
        "legacy_settlement_proof_report",
    ):
        monkeypatch.setattr(subject, name, forbidden)
    monkeypatch.setattr(
        subject.canonical_settlement,
        "settle_mlb_slate",
        forbidden,
    )
    monkeypatch.setattr(
        subject.canonical_settlement,
        "settle_recent_mlb_slates",
        forbidden,
    )
    monkeypatch.setattr(
        subject.canonical_settlement,
        "settlement_proof_report",
        forbidden,
    )


@pytest.mark.parametrize("version", HTTP_EVENT_VERSIONS)
@pytest.mark.parametrize("method", DENIED_HTTP_METHODS)
@pytest.mark.parametrize("path", ALL_HTTP_PATHS)
def test_public_non_get_routes_fail_closed_before_payload_or_dependencies(
    monkeypatch,
    version,
    method,
    path,
):
    _forbid_all_dependencies(monkeypatch)
    event = _http_event(method, path, version=version)
    event["body"] = '{"days_from":"not-an-integer","store":true,"build":true}'

    response = subject.lambda_handler(event, None)

    assert response["statusCode"] == 405
    assert response["headers"]["allow"] == "GET,OPTIONS"
    assert response["headers"]["access-control-allow-methods"] == "GET,OPTIONS"
    assert _body(response)["method"] == method


@pytest.mark.parametrize(
    "event",
    (
        {
            "rawPath": "/v1/results/mlb/result-signals",
            "requestContext": {
                "apiId": "public-api",
                "http": {"path": "/v1/results/mlb/result-signals"},
            },
        },
        {
            "path": "/v1/results/mlb/result-signals",
            "requestContext": {
                "apiId": "public-api",
                "resourceId": "result-signals",
            },
        },
        {
            "path": "/v1/results/mlb/result-signals",
            "requestContext": {
                "elb": {
                    "targetGroupArn": (
                        "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
                        "targetgroup/public/abc"
                    )
                }
            },
        },
    ),
    ids=("http-api-v2", "rest-api-v1", "alb"),
)
def test_methodless_public_event_cannot_fall_into_scheduled_writes(
    monkeypatch,
    event,
):
    _forbid_all_dependencies(monkeypatch)
    hostile_event = {
        **event,
        "body": '{"store":true,"build":true}',
    }

    response = subject.lambda_handler(hostile_event, None)

    assert response["statusCode"] == 405
    assert _body(response)["method"] == "MISSING"


@pytest.mark.parametrize("version", HTTP_EVENT_VERSIONS)
@pytest.mark.parametrize("path", PUBLIC_SAM_PATHS)
def test_options_advertises_only_read_only_http_methods(
    monkeypatch,
    version,
    path,
):
    _forbid_all_dependencies(monkeypatch)
    response = subject.lambda_handler(
        _http_event("OPTIONS", path, version=version),
        None,
    )

    assert response["statusCode"] == 200
    assert response["headers"]["access-control-allow-origin"] == "*"
    assert response["headers"]["access-control-allow-headers"] == "content-type"
    assert response["headers"]["access-control-allow-methods"] == "GET,OPTIONS"
    assert "POST" not in response["headers"]["access-control-allow-methods"]


@pytest.mark.parametrize("path", FINAL_PATHS)
def test_final_scores_get_forces_stored_outcomes_read_mode(monkeypatch, path):
    calls = []

    def final_report(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "final_scores": []}

    monkeypatch.setattr(subject, "final_mlb_scores_report", final_report)
    response = subject.lambda_handler(_http_event("GET", path), None)

    assert response["statusCode"] == 200
    assert len(calls) == 1
    assert calls[0]["fetch_scores"] is False


@pytest.mark.parametrize("path", PROOF_PATHS)
def test_proof_get_cannot_enable_legacy_mutating_diagnostic(monkeypatch, path):
    calls = []

    def canonical_proof(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "status": "CANONICAL_PROOF"}

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy diagnostic must be disabled on HTTP GET")

    monkeypatch.setattr(
        subject.canonical_settlement,
        "settlement_proof_report",
        canonical_proof,
    )
    monkeypatch.setattr(subject, "legacy_settlement_proof_report", forbidden)
    monkeypatch.setattr(subject, "legacy_settle_mlb_slate", forbidden)

    response = subject.lambda_handler(_http_event("GET", path), None)
    body = _body(response)

    assert response["statusCode"] == 200
    assert len(calls) == 1
    assert "store" not in calls[0]
    assert body["legacyDiagnosticCompatibility"] == {
        "ok": True,
        "executed": False,
        "authoritative": False,
        "status": "LEGACY_DIAGNOSTIC_DISABLED",
    }


@pytest.mark.parametrize("version", ("v1", "v2"))
@pytest.mark.parametrize("path", SETTLEMENT_PATHS)
def test_settlement_get_forces_canonical_dry_run_and_disables_legacy(
    monkeypatch,
    version,
    path,
):
    calls = []

    def canonical_settle(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "status": "WOULD_CREATE"}

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy settlement must be disabled on HTTP GET")

    monkeypatch.setattr(
        subject.canonical_settlement,
        "settle_mlb_slate",
        canonical_settle,
    )
    monkeypatch.setattr(subject, "legacy_settle_mlb_slate", forbidden)
    monkeypatch.setattr(subject, "legacy_settlement_proof_report", forbidden)

    response = subject.lambda_handler(
        _http_event("GET", path, version=version),
        None,
    )
    body = _body(response)

    assert response["statusCode"] == 200
    assert len(calls) == 1
    assert calls[0]["fetch_scores"] is True
    assert calls[0]["store"] is False
    assert body["legacyDiagnosticCompatibility"]["executed"] is False


@pytest.mark.parametrize("path", LEARNING_PATHS)
def test_signal_learning_get_cannot_trigger_score_ingestion(monkeypatch, path):
    calls = []

    def learning(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "learning_status": "OBSERVE_ONLY"}

    monkeypatch.setattr(subject, "build_signal_learning_report", learning)
    response = subject.lambda_handler(_http_event("GET", path), None)

    assert response["statusCode"] == 200
    assert len(calls) == 1
    assert calls[0]["fetch_scores"] is False


@pytest.mark.parametrize("path", RESULT_SIGNAL_PATHS)
def test_result_signals_get_ignores_hostile_build_and_store_flags(
    monkeypatch,
    path,
):
    latest_calls = []

    def latest(slate_date):
        latest_calls.append(slate_date)
        return {"ok": True, "items": []}

    def forbidden(*args, **kwargs):
        raise AssertionError("GET must not build or store result signals")

    monkeypatch.setattr(subject, "latest_result_signals", latest)
    monkeypatch.setattr(subject, "build_result_signals", forbidden)

    response = subject.lambda_handler(_http_event("GET", path), None)

    assert response["statusCode"] == 200
    assert latest_calls == ["2026-08-04"]


def test_canonical_proof_official_fetch_is_dry_run_then_stored_read(monkeypatch):
    calls = []

    def canonical_settle(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "status": "READ_ONLY"}

    monkeypatch.setattr(
        subject.canonical_settlement,
        "settle_mlb_slate",
        canonical_settle,
    )

    report = subject.canonical_settlement.settlement_proof_report(
        slate_date="2026-08-04",
        days_from=3,
        fetch_scores=True,
    )

    assert report["readOnlyProof"] is True
    assert calls == [
        {
            "slate_date": "2026-08-04",
            "days_from": 3,
            "fetch_scores": True,
            "store": False,
        },
        {
            "slate_date": "2026-08-04",
            "days_from": 3,
            "fetch_scores": False,
            "store": False,
        },
    ]


def test_methodless_scheduled_event_remains_authoritative_write_path(monkeypatch):
    settlement_calls = []
    result_signal_calls = []

    def canonical_settle(**kwargs):
        settlement_calls.append(kwargs)
        return {
            "ok": True,
            "slateDateEt": "2026-08-04",
            "status": "CANONICAL_FINAL_LABELS_COMPLETE",
        }

    def build_signals(slate_date, **kwargs):
        result_signal_calls.append((slate_date, kwargs))
        return {"ok": True, "stored_rows": 2}

    def forbidden(*args, **kwargs):
        raise AssertionError("scheduled path must not invoke legacy settlement")

    monkeypatch.setattr(
        subject.canonical_settlement,
        "settle_recent_mlb_slates",
        canonical_settle,
    )
    monkeypatch.setattr(
        subject,
        "build_signal_learning_report",
        lambda **kwargs: {"ok": True},
    )
    monkeypatch.setattr(subject, "build_result_signals", build_signals)
    monkeypatch.setattr(subject, "legacy_settle_mlb_slate", forbidden)
    monkeypatch.setattr(subject, "legacy_settlement_proof_report", forbidden)

    response = subject.lambda_handler(_native_schedule_event(), _LambdaContext())

    assert response["statusCode"] == 200
    assert settlement_calls == [
        {
            "days_from": 3,
            "fetch_scores": True,
            "store": True,
        }
    ]
    assert result_signal_calls == [
        (
            "2026-08-04",
            {
                "fetch_scores": False,
                "store": True,
                "producer_provenance": {
                    "schema_version": "MLB-RESULT-SIGNAL-PRODUCER-PROOF-v1",
                    "authority": "NATIVE_EVENTBRIDGE_SCHEDULE_ENVELOPE",
                    "lambda_request_id": REQUEST_ID,
                    "event_id": EVENT_ID,
                    "event_time_utc": "2026-08-28T01:06:04Z",
                    "event_source": "aws.events",
                    "detail_type": "Scheduled Event",
                    "rule_arn": RULE_ARN,
                    "account": "123456789012",
                    "region": "us-east-1",
                },
            },
        )
    ]


def test_incomplete_native_schedule_fails_before_any_dependency(monkeypatch):
    _forbid_all_dependencies(monkeypatch)
    event = _native_schedule_event()
    event.pop("id")

    response = subject.lambda_handler(event, _LambdaContext())

    assert response["statusCode"] == 500
    assert "Native EventBridge envelope is incomplete" in _body(response)["error"]


def test_sam_results_scheduler_exposes_exact_get_and_options_surface():
    template = (ROOT / "template.yaml").read_text()
    start = template.index("  MLBResultsSchedulerFunction:")
    end = template.index("\n  MLBMLTrainingFunction:", start)
    block = template[start:end]
    event_pattern = re.compile(
        r"^        (?P<name>[A-Za-z0-9]+):\n"
        r"          Type: Api\n"
        r"          Properties:\n"
        r"            Path: (?P<path>\S+)\n"
        r"            Method: (?P<method>\S+)$",
        re.MULTILINE,
    )
    deployed_events = {
        (match.group("name"), match.group("path"), match.group("method"))
        for match in event_pattern.finditer(block)
    }

    assert deployed_events == EXPECTED_SAM_API_EVENTS
    assert "MLBResultSignalsPost:" not in block
    assert "Method: POST" not in block
    assert block.count("Type: Api") == 10
    assert block.count("Method: GET") == 5
    assert block.count("Method: OPTIONS") == 5
    assert block.count("Type: Schedule") == 1
    schedule_block = block.split("        MLBResultsEvery6Hours:", 1)[1]
    assert "\n            Input:" not in schedule_block
    assert "\n            InputPath:" not in schedule_block
    assert "\n            InputTransformer:" not in schedule_block
    assert "Handler: mlb_result_signals.lambda_handler" not in template


def test_deploy_transform_rebuilds_exact_read_only_surface_idempotently():
    patcher = (
        ROOT / "scripts" / "patch_template_mlb_results_routes.py"
    ).read_text()

    assert '"MLBResultSignalsPost",' in patcher
    assert 'ensure_results_event("MLBResultSignalsPost"' not in patcher
    assert 'normalized_method not in {"GET", "OPTIONS"}' in patcher
    assert "for logical_name, _, _ in RESULTS_API_EVENTS:" in patcher
    assert "for logical_name, path, method in RESULTS_API_EVENTS:" in patcher
    for logical_name, path, method in EXPECTED_SAM_API_EVENTS:
        expected_literal = f'("{logical_name}", "{path}", "{method}")'
        assert expected_literal in patcher


def test_mlb_source_contract_executes_http_read_only_regressions():
    workflow = (
        ROOT / ".github" / "workflows" / "mlb-production-source-contract.yml"
    ).read_text()

    assert "tests/unit/test_mlb_results_scheduler_http_read_only.py" in workflow
