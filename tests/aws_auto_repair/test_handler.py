from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "aws_auto_repair" / "handler.py"


def load_module(monkeypatch, sport="tennis"):
    stack = {
        "tennis": "parlay-platform-tennis-learning",
        "soccer": "parlay-platform-soccer-auto",
        "nfl": "parlay-platform-nfl-auto",
        "mlb-auto": "parlay-platform-mlb-auto-llm",
    }[sport]
    monkeypatch.setenv("SPORT_NAME", sport)
    monkeypatch.setenv("TARGET_STACK_NAME", stack)
    monkeypatch.setenv("FUNCTION_NAME_PREFIX", stack)
    monkeypatch.setenv("RULE_NAME_PREFIX", stack)
    monkeypatch.setenv("REPAIR_STATE_TABLE", f"auto-repair-{sport}")
    name = f"aws_auto_repair_handler_{sport.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_static_configs_cover_all_algorithms_and_only_static_payloads(monkeypatch):
    module = load_module(monkeypatch)
    assert set(module.SPORT_CONFIGS) == {"mlb-auto", "tennis", "soccer", "nfl"}
    for config in module.SPORT_CONFIGS.values():
        for row in config["components"]:
            assert row["logical_id"]
            assert isinstance(row["payload"], dict)
            serialized = json.dumps(row["payload"]).lower()
            assert "function_name" not in serialized
            assert "promotion" not in serialized
            assert "winner" not in serialized
            assert "lock" not in serialized


def test_classifier_respects_protected_lease(monkeypatch):
    module = load_module(monkeypatch)
    payload = {
        "ok": False,
        "reason": "ExecutionLeaseUnavailable: another invocation holds the execution lease",
    }
    assert module.classify_result(payload) == "PROTECTED_DEFERRED"


def test_classifier_records_external_quota(monkeypatch):
    module = load_module(monkeypatch)
    payload = {
        "ok": False,
        "status": "DEFERRED_QUOTA",
        "reason": "DAILY_TOKEN_QUOTA too many tokens per day",
    }
    assert module.classify_result(payload) == "EXTERNAL_BLOCKER"


def test_classifier_records_data_contract_without_bypass(monkeypatch):
    module = load_module(monkeypatch)
    payload = {
        "ok": False,
        "reason": "ODDS_API_HISTORICAL_SCHEDULE_EVENT_AMBIGUOUS",
    }
    assert module.classify_result(payload) == "DATA_CONTRACT_BLOCKER"


def test_classifier_accepts_fail_closed_expected_states(monkeypatch):
    module = load_module(monkeypatch)
    assert (
        module.classify_result({"ok": True, "status": "HISTORICAL_ONLY"})
        == "SAFE_DEFERRED"
    )
    assert (
        module.classify_result({"ok": True, "status": "REJECTED_BY_GATE"})
        == "SAFE_DEFERRED"
    )
    assert (
        module.classify_result({"ok": True, "status": "READY"})
        == "SAFE_DEFERRED"
    )


def test_runtime_scope_rejects_cross_sport_prefix(monkeypatch):
    module = load_module(monkeypatch, sport="soccer")
    module.FUNCTION_NAME_PREFIX = "parlay-platform-tennis-learning"
    with pytest.raises(
        module.RepairConfigurationError,
        match="FUNCTION_PREFIX_NOT_TARGET_SCOPED",
    ):
        module._validate_runtime()


def test_unwraps_api_gateway_response(monkeypatch):
    module = load_module(monkeypatch)
    body, status = module._unwrap_lambda_payload(
        {"statusCode": 200, "body": '{"ok":true,"status":"READY"}'}
    )
    assert body == {"ok": True, "status": "READY"}
    assert status == 200


class FakeTable:
    def __init__(self):
        self.items = {}
        self.lease_held = False

    def update_item(self, **kwargs):
        key = (kwargs["Key"]["PK"], kwargs["Key"]["SK"])
        if key[0].startswith("LEASE#") and "ConditionExpression" in kwargs:
            if self.lease_held and "attribute_not_exists" in kwargs["ConditionExpression"]:
                from botocore.exceptions import ClientError

                raise ClientError(
                    {
                        "Error": {
                            "Code": "ConditionalCheckFailedException",
                            "Message": "held",
                        }
                    },
                    "UpdateItem",
                )
            self.lease_held = "REMOVE holder" not in kwargs.get(
                "UpdateExpression", ""
            )
        return {}

    def get_item(self, **kwargs):
        key = (kwargs["Key"]["PK"], kwargs["Key"]["SK"])
        return {"Item": self.items.get(key)} if key in self.items else {}

    def put_item(self, **kwargs):
        item = kwargs["Item"]
        self.items[(item["PK"], item["SK"])] = item
        return {}


class FakePayload:
    def __init__(self, value):
        self.value = value

    def read(self):
        return json.dumps(self.value).encode()


class FakeLambda:
    def __init__(self, prefix):
        self.prefix = prefix
        self.invocations = []

    def get_function_configuration(self, FunctionName):
        return {
            "FunctionArn": (
                "arn:aws:lambda:us-east-1:123456789012:function:"
                + FunctionName
            ),
            "State": "Active",
            "LastUpdateStatus": "Successful",
            "Runtime": "python3.11",
            "Timeout": 900,
            "MemorySize": 512,
        }

    def invoke(self, **kwargs):
        payload = json.loads(kwargs["Payload"].decode())
        self.invocations.append((kwargs["FunctionName"], payload))
        return {
            "Payload": FakePayload({"ok": True, "status": "READY"}),
            "ExecutedVersion": "$LATEST",
        }


class FakeCfn:
    def __init__(self, stack, logical_ids):
        self.stack = stack
        self.logical_ids = logical_ids

    def describe_stacks(self, **kwargs):
        return {
            "Stacks": [
                {"StackId": "arn:stack", "StackStatus": "UPDATE_COMPLETE"}
            ]
        }

    def list_stack_resources(self, **kwargs):
        rows = []
        for logical_id in self.logical_ids:
            rows.append(
                {
                    "LogicalResourceId": logical_id,
                    "PhysicalResourceId": (
                        f"{self.stack}-{logical_id}-ABC123"
                    ),
                    "ResourceType": "AWS::Lambda::Function",
                    "ResourceStatus": "UPDATE_COMPLETE",
                }
            )
        return {"StackResourceSummaries": rows}


class FakeCloudWatch:
    def __init__(self, invocations=0, errors=0):
        self.invocations = invocations
        self.errors = errors
        self.metrics = []

    def get_metric_statistics(self, **kwargs):
        value = (
            self.invocations
            if kwargs["MetricName"] == "Invocations"
            else self.errors
        )
        return {
            "Datapoints": (
                [
                    {
                        "Sum": value,
                        "Timestamp": datetime.now(timezone.utc),
                    }
                ]
                if value
                else []
            )
        }

    def put_metric_data(self, **kwargs):
        self.metrics.append(kwargs)
        return {}


class FakeEvents:
    def list_rule_names_by_target(self, **kwargs):
        return {"RuleNames": []}


class FakeSqs:
    pass


def test_dry_run_discovers_stale_tennis_without_invoking(monkeypatch):
    module = load_module(monkeypatch, sport="tennis")
    table = FakeTable()
    lamb = FakeLambda(module.FUNCTION_NAME_PREFIX)
    cfn = FakeCfn(
        module.TARGET_STACK_NAME,
        [
            "TennisAutonomousControllerFunction",
            "TennisLivePipelineFunction",
        ],
    )
    cw = FakeCloudWatch(invocations=0, errors=0)
    events = FakeEvents()
    module._TABLE = table
    module._CFN = cfn
    module._LAMBDA = lamb
    module._CLOUDWATCH = cw
    module._EVENTS = events
    module._SQS = FakeSqs()

    report = module.run_cycle(
        {"action": "cycle", "sport": "tennis", "dry_run": True}
    )
    assert report["ok"] is True
    assert report["status"] == "DRY_RUN"
    assert report["components"][0]["status"] == (
        "WOULD_INVOKE_SAFE_ENTRYPOINT"
    )
    assert lamb.invocations == []
    assert report["immutable_prediction_history_rewritten"] is False
    assert report["promotion_gate_changed"] is False
    assert report["winner_authority_changed"] is False
    assert report["other_sport_changed"] is False


def test_live_cycle_invokes_only_static_target_entrypoint(monkeypatch):
    module = load_module(monkeypatch, sport="nfl")
    table = FakeTable()
    lamb = FakeLambda(module.FUNCTION_NAME_PREFIX)
    ids = [
        "NflAutonomousFunction",
        "NflLiveFunction",
        "NflTrainingFunction",
    ]
    module._TABLE = table
    module._CFN = FakeCfn(module.TARGET_STACK_NAME, ids)
    module._LAMBDA = lamb
    module._CLOUDWATCH = FakeCloudWatch(invocations=0, errors=0)
    module._EVENTS = FakeEvents()
    module._SQS = FakeSqs()

    report = module.run_cycle({"action": "cycle", "sport": "nfl"})
    assert report["ok"] is True
    assert [payload for _, payload in lamb.invocations] == [
        {"action": "autonomous_tick"},
        {"action": "live_tick"},
        {"action": "train"},
    ]
    assert all(
        name.startswith(module.FUNCTION_NAME_PREFIX)
        for name, _ in lamb.invocations
    )
    assert report["metrics"]["RepairAttempts"] == 3
    assert report["metrics"]["RepairSuccesses"] == 3


def test_source_contains_no_target_mutation_apis():
    source = MODULE_PATH.read_text()
    forbidden = (
        ".update_function_configuration(",
        ".update_function_code(",
        ".put_function_concurrency(",
        ".delete_function_concurrency(",
        ".disable_rule(",
        "batch_write_item(",
        "transact_write_items(",
        "put_object(",
    )
    for token in forbidden:
        assert token not in source
