from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from botocore.exceptions import ClientError

from scripts import verify_mlb_release_activation_predeploy as gate


PHYSICAL_TABLE = "private-snapshots-physical-name"


class FakeCloudFormation:
    def __init__(self, *, stack_present=True, detail=None):
        self.stack_present = stack_present
        self.detail = detail or {
            "LogicalResourceId": gate.SNAPSHOTS_LOGICAL_RESOURCE_ID,
            "PhysicalResourceId": PHYSICAL_TABLE,
            "ResourceType": "AWS::DynamoDB::Table",
        }
        self.calls = []

    def describe_stacks(self, **kwargs):
        self.calls.append(("describe_stacks", kwargs))
        if not self.stack_present:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ValidationError",
                        "Message": "Stack does not exist: redacted-stack-detail",
                    }
                },
                "DescribeStacks",
            )
        return {"Stacks": [{"StackName": kwargs["StackName"]}]}

    def describe_stack_resource(self, **kwargs):
        self.calls.append(("describe_stack_resource", kwargs))
        return {"StackResourceDetail": copy.deepcopy(self.detail)}


class FakeTable:
    def __init__(self, item=None, failure=None):
        self.item = copy.deepcopy(item)
        self.failure = failure
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return {"Item": copy.deepcopy(self.item)} if self.item else {}


class FakeDynamoDB:
    def __init__(self, item=None, failure=None):
        self.table = FakeTable(item, failure)
        self.requested_table_names = []

    def Table(self, table_name):
        self.requested_table_names.append(table_name)
        return self.table


def _manifest(*, created_at="2026-07-21T20:00:00+00:00"):
    value = {
        "ok": True,
        "version": gate.EXPERIMENT_VERSION,
        "experimentId": gate.EXPERIMENT_ID,
        "releaseContractId": gate.RELEASE_CONTRACT_ID,
        "releaseCutoffUtc": gate.RELEASE_CUTOFF_UTC,
        "createdAtUtc": created_at,
        "revision": 0,
        "phase": "ACCUMULATING_TRAIN",
        "partitions": {"train": {"rowCount": 0}},
        "numericProof": Decimal("1.25"),
    }
    value["manifestDigest"] = gate.manifest_digest(value)
    return value


def _activation(*, activated_at="2026-07-22T03:00:00+00:00"):
    return {
        "version": gate.RELEASE_ACTIVATION_VERSION,
        "experimentId": gate.EXPERIMENT_ID,
        "releaseContractId": gate.RELEASE_CONTRACT_ID,
        "releaseCutoffUtc": gate.RELEASE_CUTOFF_UTC,
        "activatedAtUtc": activated_at,
        "deploymentIdentity": {
            "gitSha": "a" * 40,
            "templateSha256": "b" * 64,
        },
        "immutable": True,
    }


def _with_activation(manifest=None, **activation_kwargs):
    value = copy.deepcopy(manifest or _manifest())
    value["releaseActivation"] = _activation(**activation_kwargs)
    value["manifestDigest"] = gate.manifest_digest(value)
    return value


def _envelope(manifest):
    return {
        "PK": gate.MANIFEST_PK,
        "SK": gate.MANIFEST_SK,
        "record_type": gate.MANIFEST_RECORD_TYPE,
        "revision": manifest["revision"],
        "manifestDigest": manifest["manifestDigest"],
        "updated_at": manifest["createdAtUtc"],
        "data": copy.deepcopy(manifest),
    }


def _verify(item=None, *, now, cloudformation=None, failure=None):
    cloudformation = cloudformation or FakeCloudFormation()
    dynamodb = FakeDynamoDB(item, failure)
    report = gate.verify_predeploy(
        cloudformation=cloudformation,
        dynamodb=dynamodb,
        stack_name="parlay-platform-dev",
        now=now,
    )
    return report, cloudformation, dynamodb


def test_lead_window_is_explicit_ninety_minutes() -> None:
    assert gate.FIRST_ACTIVATION_LEAD_SECONDS == 90 * 60
    assert gate.FIRST_ACTIVATION_LEAD_SECONDS >= 20 * 60
    assert gate.FIRST_ACTIVATION_DEADLINE == (
        gate.RELEASE_CUTOFF - timedelta(minutes=90)
    )


def test_missing_manifest_is_allowed_strictly_before_lead_deadline_read_only() -> None:
    checked = gate.FIRST_ACTIVATION_DEADLINE - timedelta(microseconds=1)
    report, cloudformation, dynamodb = _verify(now=checked)

    assert report["ok"] is True
    assert report["decision"] == "ALLOW_FIRST_ACTIVATION"
    assert report["manifestState"] == "MANIFEST_MISSING"
    assert report["firstActivationLeadSeconds"] == 5400
    assert cloudformation.calls == [
        ("describe_stacks", {"StackName": "parlay-platform-dev"}),
        (
            "describe_stack_resource",
            {
                "StackName": "parlay-platform-dev",
                "LogicalResourceId": gate.SNAPSHOTS_LOGICAL_RESOURCE_ID,
            },
        ),
    ]
    assert dynamodb.requested_table_names == [PHYSICAL_TABLE]
    assert dynamodb.table.calls == [
        {
            "Key": {"PK": gate.MANIFEST_PK, "SK": gate.MANIFEST_SK},
            "ConsistentRead": True,
        }
    ]
    assert PHYSICAL_TABLE not in json.dumps(report)


def test_missing_manifest_fails_at_exact_lead_deadline() -> None:
    report, _, _ = _verify(now=gate.FIRST_ACTIVATION_DEADLINE)

    assert report["ok"] is False
    assert report["decision"] == "BLOCK"
    assert report["errors"] == ["first_activation_lead_deadline_reached"]


def test_markerless_manifest_uses_same_strict_lead_boundary() -> None:
    item = _envelope(_manifest())
    allowed, _, _ = _verify(
        item,
        now=gate.FIRST_ACTIVATION_DEADLINE - timedelta(seconds=1),
    )
    blocked, _, _ = _verify(item, now=gate.FIRST_ACTIVATION_DEADLINE)

    assert allowed["decision"] == "ALLOW_FIRST_ACTIVATION"
    assert allowed["manifestState"] == "MARKERLESS_MANIFEST"
    assert allowed["manifestDigestValidated"] is True
    assert blocked["ok"] is False
    assert blocked["errors"] == ["first_activation_lead_deadline_reached"]


def test_missing_stack_is_only_allowed_before_lead_deadline() -> None:
    missing_stack = FakeCloudFormation(stack_present=False)
    allowed, _, dynamodb = _verify(
        now=gate.FIRST_ACTIVATION_DEADLINE - timedelta(seconds=1),
        cloudformation=missing_stack,
    )
    blocked, _, _ = _verify(
        now=gate.FIRST_ACTIVATION_DEADLINE,
        cloudformation=FakeCloudFormation(stack_present=False),
    )

    assert allowed["manifestState"] == "STACK_AND_MANIFEST_MISSING"
    assert allowed["decision"] == "ALLOW_FIRST_ACTIVATION"
    assert dynamodb.requested_table_names == []
    assert blocked["ok"] is False
    assert blocked["errors"] == ["first_activation_lead_deadline_reached"]


def test_valid_marker_allows_future_deploy_after_cutoff() -> None:
    manifest = _with_activation()
    report, _, _ = _verify(
        _envelope(manifest),
        now=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )

    assert report["ok"] is True
    assert report["decision"] == "ALLOW_EXISTING_ACTIVATION"
    assert report["manifestState"] == "VALID_RELEASE_ACTIVATION"
    assert report["manifestDigestValidated"] is True
    assert report["releaseActivationValidated"] is True
    assert report["activationRecordedAtUtc"] == "2026-07-22T03:00:00+00:00"
    assert "deploymentIdentity" not in report


def test_tampered_marker_fails_manifest_digest_before_deadline() -> None:
    manifest = _with_activation()
    manifest["releaseActivation"]["deploymentIdentity"]["gitSha"] = "c" * 40
    report, _, _ = _verify(
        _envelope(manifest),
        now=gate.FIRST_ACTIVATION_DEADLINE - timedelta(hours=1),
    )

    assert report["ok"] is False
    assert report["manifestState"] == "INVALID"
    assert "manifest_digest_mismatch" in report["errors"]


def test_malformed_marker_fails_closed_with_recomputed_digest() -> None:
    manifest = _with_activation()
    manifest["releaseActivation"]["deploymentIdentity"]["gitSha"] = "wrong"
    manifest["manifestDigest"] = gate.manifest_digest(manifest)
    item = _envelope(manifest)
    report, _, _ = _verify(
        item,
        now=gate.RELEASE_CUTOFF + timedelta(hours=1),
    )

    assert report["ok"] is False
    assert report["manifestState"] == "INVALID_RELEASE_ACTIVATION"
    assert report["errors"] == ["release_activation_git_identity_invalid"]


def test_activation_at_exact_release_cutoff_fails_closed() -> None:
    manifest = _with_activation(activated_at=gate.RELEASE_CUTOFF_UTC)
    report, _, _ = _verify(
        _envelope(manifest),
        now=gate.RELEASE_CUTOFF + timedelta(hours=1),
    )

    assert report["ok"] is False
    assert report["errors"] == [
        "release_activation_not_strictly_before_cutoff"
    ]


def test_activation_cannot_predate_manifest_creation() -> None:
    manifest = _manifest(created_at="2026-07-21T23:30:00+00:00")
    manifest = _with_activation(
        manifest,
        activated_at="2026-07-21T23:29:59+00:00",
    )
    report, _, _ = _verify(
        _envelope(manifest),
        now=gate.FIRST_ACTIVATION_DEADLINE - timedelta(hours=1),
    )

    assert report["ok"] is False
    assert report["errors"] == ["release_activation_predates_manifest_creation"]


def test_future_dated_manifest_or_activation_fails_closed() -> None:
    checked = datetime(2026, 7, 21, 22, 0, tzinfo=timezone.utc)
    future_manifest = _manifest(created_at="2026-07-21T22:00:01+00:00")
    manifest_report, _, _ = _verify(
        _envelope(future_manifest),
        now=checked,
    )
    future_activation = _with_activation(
        _manifest(created_at="2026-07-21T20:00:00+00:00"),
        activated_at="2026-07-21T22:00:01+00:00",
    )
    activation_report, _, _ = _verify(
        _envelope(future_activation),
        now=checked,
    )

    assert manifest_report["errors"] == ["manifest_created_at_from_future"]
    assert activation_report["errors"] == [
        "release_activation_timestamp_from_future"
    ]


def test_invalid_manifest_envelope_never_becomes_markerless_allowance() -> None:
    item = _envelope(_manifest())
    item["record_type"] = "legacy_or_wrong"
    report, _, _ = _verify(
        item,
        now=gate.FIRST_ACTIVATION_DEADLINE - timedelta(hours=1),
    )

    assert report["ok"] is False
    assert report["manifestState"] == "INVALID"
    assert report["errors"] == ["manifest_envelope_record_type_mismatch"]


def test_cloudformation_must_resolve_exact_logical_dynamodb_resource() -> None:
    wrong = FakeCloudFormation(
        detail={
            "LogicalResourceId": "SomeOtherTable",
            "PhysicalResourceId": PHYSICAL_TABLE,
            "ResourceType": "AWS::DynamoDB::Table",
        }
    )
    report, _, dynamodb = _verify(
        now=gate.FIRST_ACTIVATION_DEADLINE - timedelta(hours=1),
        cloudformation=wrong,
    )

    assert report["ok"] is False
    assert report["errors"] == [
        "cloudformation_snapshots_logical_identity_mismatch"
    ]
    assert dynamodb.requested_table_names == []


def test_aws_read_failure_is_redacted_and_fails_closed() -> None:
    secret = "bbs_live_NEVER_EXPOSE_THIS"
    report, _, _ = _verify(
        now=gate.FIRST_ACTIVATION_DEADLINE - timedelta(hours=1),
        failure=RuntimeError(f"read failed for {PHYSICAL_TABLE} with {secret}"),
    )
    rendered = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert report["errors"] == ["dynamodb_manifest_read_failed"]
    assert report["redacted"] is True
    assert secret not in rendered
    assert PHYSICAL_TABLE not in rendered
