from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import mlb_v8_historical_context_eligibility_v2 as eligibility
import run_mlb_v8_historical_context_backfill_entrypoint as entrypoint

LOCK = "2026-08-05T12:00:00+00:00"


def _envelope(data, *, verified=True, error=None):
    return {
        "data": data,
        "meta": {
            "complete": True,
            "pointInTimeProjectionVerified": verified,
            "asOfUtc": "2026-08-05T11:00:00+00:00",
            "source": "official_mlb_prior_context",
        },
        "error": error,
    }


def _resources():
    return {
        "pitchers": _envelope({"home": {}, "away": {}}),
        "bullpens": _envelope({"home": {}, "away": {}}),
        "team_context": _envelope({"home": {}, "away": {}}),
        "lineups": _envelope({"home": {}, "away": {}}),
        "injuries": _envelope({}),
        "park": _envelope({"runFactor": 1.0}),
        "weather": _envelope({"runFactor": 1.0}),
    }


def test_snapshot_contract_keeps_verified_core_when_optional_lineup_is_missing():
    def base_snapshot(*_args, **_kwargs):
        return {
            "home": {"lineupQuality": 100.0},
            "away": {"lineupQuality": 100.0},
            "trainingEligible": False,
            "eligibilityErrors": ["confirmed_lineups_missing"],
        }

    module = SimpleNamespace(
        build_training_snapshot=base_snapshot,
        REQUIRED_RESOURCES=(
            "pitchers",
            "bullpens",
            "lineups",
            "injuries",
            "team_context",
        ),
        OPTIONAL_RESOURCES=("park", "weather"),
        _effective_at=lambda envelope: datetime.fromisoformat(
            envelope["meta"]["asOfUtc"]
        )
        if envelope.get("meta", {}).get("asOfUtc")
        else None,
        _sha=lambda _value: "payload-sha",
        overlay=SimpleNamespace(snapshot_fingerprint=lambda _value: "snapshot-sha"),
    )
    entrypoint.install_snapshot_contract(module)
    resources = _resources()
    resources["lineups"] = {"data": None, "meta": {}, "error": "missing"}

    value = module.build_training_snapshot(
        {"officialGamePk": "1", "predictionLockAtUtc": LOCK},
        {},
        {},
        resources,
    )

    assert value["trainingEligible"] is True
    assert value["trainingEligibleCore"] is True
    assert value["featureEligibility"]["lineups"] is False
    assert value["home"]["lineupQuality"] is None
    assert value["away"]["lineupQuality"] is None
    assert "lineups_resource_unavailable" in value["eligibilityWarnings"]
    assert value["eligibilityPolicyVersion"] == eligibility.VERSION
    assert value["fingerprint"] == "snapshot-sha"


def test_old_pointer_policy_forces_replay_without_mutating_old_manifest():
    class Table:
        def get_item(self, **_kwargs):
            return {
                "Item": {
                    "record_type": "mlb_v8_historical_official_context_active_manifest_v2",
                    "revision": 66,
                    "data": {
                        "authority": entrypoint.AUTHORITY,
                        "provider": "official_mlb_plus_internal_canonical",
                    },
                }
            }

    module = SimpleNamespace(
        overlay=SimpleNamespace(
            POINTER_PK="old",
            POINTER_SK="ACTIVE",
            VERSION="old-version",
            AUTHORITY="old-authority",
        ),
        VERSION="old",
        REPORT_TYPE="old",
        _load_previous_manifest=lambda _table, _s3: (_ for _ in ()).throw(
            AssertionError("old manifest must not be reused")
        ),
    )
    entrypoint.install_pointer_isolation(module)

    manifest, revision = module._load_previous_manifest(Table(), object())

    assert manifest is None
    assert revision == 66
    assert module._v8_context_replay_from_start is True


def test_current_pointer_policy_reuses_current_manifest():
    class Table:
        def get_item(self, **_kwargs):
            return {
                "Item": {
                    "record_type": entrypoint.RECORD_TYPE,
                    "revision": 67,
                    "data": {
                        "authority": entrypoint.AUTHORITY,
                        "provider": "official_mlb_plus_internal_canonical",
                        "eligibilityPolicyVersion": eligibility.VERSION,
                        "materializerVersion": eligibility.MATERIALIZER_VERSION,
                    },
                }
            }

    sentinel = {"manifest": "current"}
    module = SimpleNamespace(
        overlay=SimpleNamespace(
            POINTER_PK="old",
            POINTER_SK="ACTIVE",
            VERSION="old-version",
            AUTHORITY="old-authority",
        ),
        VERSION="old",
        REPORT_TYPE="old",
        _load_previous_manifest=lambda _table, _s3: (sentinel, 67),
    )
    entrypoint.install_pointer_isolation(module)

    manifest, revision = module._load_previous_manifest(Table(), object())

    assert manifest is sentinel
    assert revision == 67
    assert module._v8_context_replay_from_start is False


def test_run_report_contains_reason_histogram_and_domain_coverage(tmp_path):
    def base_run(*_args, **_kwargs):
        good = eligibility.evaluate(_resources(), LOCK)
        partial_resources = _resources()
        partial_resources["lineups"] = {
            "data": None,
            "meta": {},
            "error": "missing",
        }
        partial = eligibility.evaluate(partial_resources, LOCK)
        entrypoint._BATCH_DIAGNOSTICS.update({"1": good, "2": partial})
        return {
            "ok": True,
            "newRecordCount": 2,
            "eligibleGameCount": 2,
            "blockers": [],
        }

    module = SimpleNamespace(run=base_run, _v8_context_replay_from_start=True)
    entrypoint.install_run_contract(module)
    output = tmp_path / "report.json"

    value = module.run(output=output)

    assert value["replayFromStartApplied"] is True
    assert value["diagnosedGameCount"] == 2
    assert value["coreEligibleGameCount"] == 2
    assert value["domainCoverage"]["lineups"]["eligibleGameCount"] == 1
    assert value["eligibilityReasonCounts"]["lineups_resource_unavailable"] == 1
    assert value["eligibilityReasonsByGame"]["2"]
    assert output.exists()
