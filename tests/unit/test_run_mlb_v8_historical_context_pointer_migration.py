from __future__ import annotations

from types import SimpleNamespace

import mlb_v8_historical_context_eligibility_v2 as eligibility
import migrate_v7_v10_stall_fixes as migration
import run_mlb_v8_historical_context_backfill_entrypoint as entrypoint


class _Table:
    def __init__(self, item):
        self.item = item

    def get_item(self, **_kwargs):
        return {"Item": self.item} if self.item is not None else {}


def _module(delegate):
    return SimpleNamespace(
        overlay=SimpleNamespace(
            POINTER_PK="old",
            POINTER_SK="ACTIVE",
            VERSION="old-version",
            AUTHORITY="old-authority",
        ),
        VERSION="old",
        REPORT_TYPE="old",
        _plain=lambda value: dict(value),
        _load_previous_manifest=delegate,
    )


def test_legacy_bbs_pointer_is_not_carried_into_official_context():
    called = []
    module = _module(lambda *_args: called.append(True) or ({"legacy": True}, 7))
    entrypoint.install_pointer_isolation(module)
    table = _Table(
        {
            "record_type": "mlb_v8_historical_bbs_active_manifest_v1",
            "revision": 59,
            "data": {
                "authority": "V8_HISTORICAL_BBS_SHADOW_ONLY",
                "provider": "bigballsdata_stored_confirmation_plus_official_prior_context",
            },
        }
    )

    manifest, revision = module._load_previous_manifest(table, object())

    assert manifest is None
    assert revision == 59
    assert called == []
    assert module._v8_context_replay_from_start is True


def test_old_official_policy_replays_instead_of_reusing_skipped_rows():
    called = []
    module = _module(lambda *_args: called.append(True) or ({"old": True}, 60))
    entrypoint.install_pointer_isolation(module)
    table = _Table(
        {
            "record_type": "mlb_v8_historical_official_context_active_manifest_v2",
            "revision": 60,
            "data": {
                "authority": entrypoint.AUTHORITY,
                "provider": "official_mlb_plus_internal_canonical",
            },
        }
    )

    manifest, revision = module._load_previous_manifest(table, object())

    assert manifest is None
    assert revision == 60
    assert called == []
    assert module._v8_context_replay_from_start is True


def test_current_policy_pointer_delegates_to_verified_manifest_loader():
    called = []

    def delegate(table, s3):
        called.append((table, s3))
        return {"official": True}, 61

    module = _module(delegate)
    entrypoint.install_pointer_isolation(module)
    table = _Table(
        {
            "record_type": entrypoint.RECORD_TYPE,
            "revision": 61,
            "data": {
                "authority": entrypoint.AUTHORITY,
                "provider": "official_mlb_plus_internal_canonical",
                "eligibilityPolicyVersion": eligibility.VERSION,
                "materializerVersion": eligibility.MATERIALIZER_VERSION,
            },
        }
    )
    s3 = object()

    manifest, revision = module._load_previous_manifest(table, s3)

    assert manifest == {"official": True}
    assert revision == 61
    assert called == [(table, s3)]
    assert module._v8_context_replay_from_start is False


def test_stall_migration_preserves_feature_aware_replay_contract():
    source = (
        'report["eligibilityPolicyVersion"] = eligibility.VERSION\n'
        'report["materializerVersion"] = eligibility.MATERIALIZER_VERSION\n'
        'report["replayFromStartApplied"] = True\n'
    )

    assert migration.patch_v8_entrypoint(source) == source


def test_stall_migration_preserves_provider_neutral_feature_bridge():
    source = (
        'VERSION = "MLB-HISTORICAL-V7-FEATURE-BRIDGE-v2-provider-neutral-official-primary"\n'
        '"primaryFeatureAuthority": context_overlay.AUTHORITY,\n'
        '"providerNeutralOfficialContextPrimary": True,\n'
        '"retiredBbsOverlayRequired": False,\n'
    )

    assert migration.patch_feature_bridge(source) == source


def test_stall_migration_preserves_current_feature_runner_contract():
    source = (
        'VERSION = "MLB-V7-V9-FEATURE-AWARE-SHADOW-RUNNER-v2-provider-neutral-complete-slate-aware"\n'
        'from scripts import run_mlb_historical_supervised_v9_shadow_cadence_v3 as cadence_v3\n'
        'cadence_v3.decide_cadence(\n'
        'cadence_v3.report_anchor_fields(\n'
        '"providerNeutralOfficialContextRequired": True,\n'
        '"retiredBbsOverlayRequired": False,\n'
        'MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED", "false\n'
        'MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED", "false\n'
    )

    assert migration.patch_feature_runner(source) == source


def test_stall_migration_preserves_current_test_wiring():
    source = (
        'package.run_mlb_historical_supervised_v9_shadow_cadence_v3 = cadence_v3\n'
        'module.cadence_v3.decide_cadence = decide\n'
        'module.cadence_v3.report_anchor_fields = anchors\n'
        'MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED"] == "false"\n'
        'MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED"] == "false"\n'
    )

    assert migration.patch_feature_runner_test(source) == source


def test_full_migration_retains_workflow_and_stabilizer_targets():
    assert migration.V9_WORKFLOW in migration.PATCHES
    assert migration.DEPLOY_WORKFLOW in migration.PATCHES
    assert migration.STABILIZER in migration.PATCHES
