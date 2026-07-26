from datetime import timedelta

from scripts import verify_mlb_release_activation_predeploy as gate
from tests.unit.test_mlb_release_activation_predeploy import FakeCloudFormation, FakeDynamoDB


def test_absent_legacy_manifest_is_allowed_after_retirement_grace() -> None:
    report = gate.verify_predeploy(
        cloudformation=FakeCloudFormation(),
        dynamodb=FakeDynamoDB(),
        stack_name="parlay-platform-dev",
        now=gate.LEGACY_CONTRACT_RETIREMENT_AT + timedelta(seconds=1),
    )

    assert report["ok"] is True
    assert report["decision"] == "ALLOW_RETIRED_LEGACY_CONTRACT_ABSENT"
    assert report["manifestState"] == "MANIFEST_MISSING"
    assert report["legacyContractRetired"] is True


def test_markerless_existing_manifest_still_fails_closed_after_retirement() -> None:
    from tests.unit.test_mlb_release_activation_predeploy import _envelope, _manifest

    report = gate.verify_predeploy(
        cloudformation=FakeCloudFormation(),
        dynamodb=FakeDynamoDB(_envelope(_manifest())),
        stack_name="parlay-platform-dev",
        now=gate.LEGACY_CONTRACT_RETIREMENT_AT + timedelta(seconds=1),
    )

    assert report["ok"] is False
    assert report["manifestState"] == "MARKERLESS_MANIFEST"
    assert report["errors"] == ["first_activation_lead_deadline_reached"]
