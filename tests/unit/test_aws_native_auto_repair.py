from __future__ import annotations

import importlib
import io
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class CloudFormationLoader(yaml.SafeLoader):
    pass


def _construct_intrinsic(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node)}
    return {tag_suffix: loader.construct_mapping(node)}


CloudFormationLoader.add_multi_constructor("!", _construct_intrinsic)


def load_module(monkeypatch: pytest.MonkeyPatch, sport: str = "tennis"):
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("SPORT_NAME", sport)
    monkeypatch.setenv("TARGET_STACK_NAME", f"target-{sport}")
    monkeypatch.setenv("FUNCTION_NAME_PREFIX", f"target-{sport}-")
    monkeypatch.setenv("RULE_NAME_PREFIX", f"target-{sport}-")
    monkeypatch.setenv("REPAIR_STATE_TABLE", f"repair-{sport}")
    sys.modules.pop("aws_auto_repair.handler", None)
    return importlib.import_module("aws_auto_repair.handler")


def test_every_active_algorithm_has_an_isolated_aws_repair_config(monkeypatch):
    module = load_module(monkeypatch)
    assert set(module.SPORT_CONFIGS) == {
        "mlb-core",
        "mlb-auto",
        "tennis",
        "soccer",
        "nfl",
    }
    for sport, components in module.SPORT_CONFIGS.items():
        assert components, sport
        assert len({component[0] for component in components}) == len(components)
        for component in components:
            serialized = json.dumps(component[2], sort_keys=True).lower()
            assert "force_publish" not in serialized
            assert "clear_lease" not in serialized
            assert "bypass" not in serialized
            assert "winner" not in serialized
            assert component[1]


def test_external_capacity_and_execution_leases_defer_without_bypass(monkeypatch):
    module = load_module(monkeypatch)
    status, cooldown, _ = module._classification(
        {"ok": False, "error": "ExecutionLeaseUnavailable: lease held"}, None
    )
    assert status == "DEFERRED_ACTIVE_LEASE"
    assert cooldown == module.TRANSIENT_COOLDOWN_SECONDS

    status, cooldown, _ = module._classification(
        {"ok": False, "status": "DEFERRED_SHARED_QUOTA_RESERVE"}, None
    )
    assert status == "DEFERRED_EXTERNAL_CAPACITY"
    assert cooldown == module.EXTERNAL_COOLDOWN_SECONDS


def test_missing_authoritative_data_is_blocked_not_fabricated(monkeypatch):
    module = load_module(monkeypatch)
    status, cooldown, _ = module._classification(
        {"ok": False, "reason": "BBD_KICKOFF_MISSING"}, None
    )
    assert status == "BLOCKED_AUTHORITATIVE_DATA_CONTRACT"
    assert cooldown == module.DATA_CONTRACT_COOLDOWN_SECONDS


def test_api_gateway_wrapped_target_response_is_unwrapped(monkeypatch):
    module = load_module(monkeypatch)
    result = module._unwrap_lambda_payload(
        {"statusCode": 200, "body": json.dumps({"ok": True, "status": "HEALTHY"})}
    )
    assert result == {"ok": True, "status": "HEALTHY", "http_status_code": 200}


def test_schedule_repair_enables_only_canonical_target_rules(monkeypatch):
    module = load_module(monkeypatch)
    module.RULE_PREFIX = "target-tennis-"

    class FakeEvents:
        def __init__(self):
            self.enabled = []

        def list_rule_names_by_target(self, **kwargs):
            assert kwargs["TargetArn"].endswith(":function:target-tennis-controller")
            return {
                "RuleNames": [
                    "target-tennis-controller-schedule",
                    "other-sport-rule",
                ]
            }

        def describe_rule(self, *, Name):
            return {"State": "DISABLED" if Name.startswith("target-tennis-") else "ENABLED"}

        def list_targets_by_rule(self, *, Rule):
            return {
                "Targets": [
                    {"Arn": "arn:aws:lambda:us-east-1:123456789012:function:target-tennis-controller"}
                ]
            }

        def enable_rule(self, *, Name):
            self.enabled.append(Name)

    fake = FakeEvents()
    monkeypatch.setattr(module, "EVENTS", fake)
    result = module._repair_schedules(
        "arn:aws:lambda:us-east-1:123456789012:function:target-tennis-controller",
        dry_run=False,
    )
    assert result["enabled_rules"] == ["target-tennis-controller-schedule"]
    assert result["noncanonical_rules_ignored"] == ["other-sport-rule"]
    assert fake.enabled == ["target-tennis-controller-schedule"]


def test_target_function_resolution_fails_closed_on_cross_sport_name(monkeypatch):
    module = load_module(monkeypatch, "soccer")
    module.FUNCTION_PREFIX = "parlay-platform-soccer-auto-"

    class FakeCfn:
        @staticmethod
        def describe_stack_resource(**kwargs):
            return {
                "StackResourceDetail": {
                    "PhysicalResourceId": "parlay-platform-tennis-learning-controller"
                }
            }

    monkeypatch.setattr(module, "CFN", FakeCfn())
    component = module.SPORT_CONFIGS["soccer"][0]
    with pytest.raises(RuntimeError, match="TARGET_FUNCTION_ISOLATION_VIOLATION"):
        module._resolve_function(component)


def test_lambda_invoke_requires_no_function_error(monkeypatch):
    module = load_module(monkeypatch)

    class FakeLambda:
        @staticmethod
        def invoke(**kwargs):
            return {
                "FunctionError": "Unhandled",
                "Payload": io.BytesIO(json.dumps({"errorMessage": "boom"}).encode()),
            }

    monkeypatch.setattr(module, "LAMBDA", FakeLambda())
    with pytest.raises(RuntimeError, match="TARGET_FUNCTION_ERROR"):
        module._invoke("target-tennis-controller", {"action": "autonomous_cycle"})


def test_template_is_iam_compatible_and_has_no_target_sport_table_writes():
    template_path = ROOT / "aws-auto-repair-template.yaml"
    template = yaml.load(template_path.read_text(), Loader=CloudFormationLoader)
    resources = template["Resources"]
    function = resources["AutoRepairFunction"]["Properties"]
    assert function["Handler"] == "wrapper.lambda_handler"
    assert function["Environment"]["Variables"]["REPAIR_LEASE_SECONDS"]
    policy_text = json.dumps(function["Policies"], sort_keys=True)
    assert "DynamoDBCrudPolicy" in policy_text
    assert "RepairStateTable" in policy_text
    for forbidden in (
        "PredictionsTable",
        "LocksTable",
        "SettlementsTable",
        "ModelsTable",
        "SnapshotsTable",
        "OutcomesTable",
        "secretsmanager:GetSecretValue",
        "bedrock:InvokeModel",
    ):
        assert forbidden not in policy_text
    assert all(
        resource.get("Type") not in {"AWS::SQS::Queue", "AWS::SNS::Topic"}
        for resource in resources.values()
    )
    schedule = function["Events"]["RepairCycle"]["Properties"]
    assert schedule["Schedule"] == "rate(5 minutes)"
    assert schedule["Enabled"] is True
    assert schedule["RetryPolicy"]["MaximumRetryAttempts"] == 0
    assert "DeadLetterConfig" not in schedule
    assert "AutoRepairUnresolvedFailureAlarm" in resources


def test_wrapper_disables_automatic_retries_for_mutating_target_invocations():
    source = (ROOT / "aws_auto_repair" / "wrapper.py").read_text()
    assert "read_timeout=840" in source
    assert '"total_max_attempts": 1' in source
    assert 'core.LAMBDA = boto3.client("lambda"' in source


def test_source_contains_explicit_non_authority_guards():
    source = (ROOT / "aws_auto_repair" / "handler.py").read_text()
    for marker in (
        '"production_authority_changed": False',
        '"direct_sport_table_writes": False',
        '"post_start_prediction_creation_allowed": False',
        '"immutable_prediction_rewrite_allowed": False',
        '"execution_lease_bypass_allowed": False',
        '"gate_relaxation_allowed": False',
    ):
        assert marker in source
