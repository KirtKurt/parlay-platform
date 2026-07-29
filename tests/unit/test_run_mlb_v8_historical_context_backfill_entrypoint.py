from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import mlb_v8_historical_context_overlay_v1 as overlay
import run_mlb_v8_historical_context_backfill_entrypoint as entrypoint


def test_weather_archive_contract_uses_single_runs_hres_model():
    module = SimpleNamespace(WEATHER_MODEL="ecmwf_ifs025")

    result = entrypoint.install_weather_archive_contract(module)

    assert result is module
    assert module.WEATHER_MODEL == "ecmwf_ifs"
    assert entrypoint.ARCHIVED_WEATHER_MODEL == "ecmwf_ifs"


def test_pointer_isolation_uses_distinct_target_context_partition():
    calls = {}

    class Table:
        def put_item(self, **kwargs):
            calls.update(kwargs)

    class S3:
        def put_object(self, **kwargs):
            calls["s3"] = kwargs
            return {"VersionId": "v1"}

    module = SimpleNamespace(
        overlay=SimpleNamespace(
            POINTER_PK="old",
            POINTER_SK="ACTIVE",
            VERSION="old-version",
            AUTHORITY=overlay.AUTHORITY,
        ),
        VERSION="old",
        REPORT_TYPE="old",
        _put_immutable=lambda _s3, _bucket, key, _body: {"key": key},
        _activate=lambda *_args: 0,
    )

    entrypoint.install_pointer_isolation(module)
    pointer = module._put_immutable(
        S3(), "bucket", "mlb/v8/historical-bbs/manifests/a.json", b"{}"
    )
    revision = module._activate(
        Table(),
        {"bucket": "bucket", "key": pointer["key"], "sha256": "x"},
        {
            "manifestDigest": "m",
            "processedGameCount": 1,
            "eligibleGameCount": 1,
        },
        0,
    )

    assert module.overlay.POINTER_PK == overlay.POINTER_PK
    assert pointer["key"] == "mlb/v8/historical/context/manifests/a.json".replace(
        "historical/context", "historical-context"
    )
    assert (
        calls["s3"]["Metadata"]["record-type"]
        == "mlb-v8-historical-context-manifest"
    )
    assert calls["Item"]["PK"] == overlay.POINTER_PK
    assert calls["Item"]["record_type"] == entrypoint.RECORD_TYPE
    assert revision == 1


def test_snapshot_contract_requires_weather_and_park_and_excludes_outcomes():
    def base_snapshot(*_args, **_kwargs):
        return {
            "snapshotRole": "old",
            "parkRunFactor": 1.02,
            "weatherRunFactor": None,
            "providerEvidence": {},
            "pointInTimeVerified": True,
            "postgameFieldsExcluded": True,
            "selectionUsedOutcomes": False,
            "trainingEligible": True,
            "eligibilityErrors": [],
            "productionAuthorityChanged": False,
        }

    module = SimpleNamespace(
        build_training_snapshot=base_snapshot,
        REQUIRED_RESOURCES=("pitchers",),
        OPTIONAL_RESOURCES=("weather", "park"),
        point_in_time_errors=lambda _resources, _lock: [],
        _effective_at=lambda envelope: datetime.fromisoformat(
            envelope["meta"]["asOfUtc"].replace("Z", "+00:00")
        ),
        _sha=lambda _value: "payload-sha",
        overlay=SimpleNamespace(snapshot_fingerprint=lambda value: str(sorted(value))),
    )
    entrypoint.install_snapshot_contract(module)
    resources = {
        name: {
            "data": {},
            "meta": {"asOfUtc": "2026-07-01T22:00:00Z", "source": "test"},
            "error": None,
        }
        for name in ("pitchers", "weather", "park")
    }

    value = module.build_training_snapshot(
        {"predictionLockAtUtc": "2026-07-01T22:15:00Z"},
        {},
        {},
        resources,
        retrieved_at=datetime.now(timezone.utc),
    )

    assert value["trainingEligible"] is False
    assert "weather_run_factor_missing" in value["eligibilityErrors"]
    assert value["targetGameOutcomeUsed"] is False
    assert value["sameDayResultsExcluded"] is True
    assert value["productionAuthorityChanged"] is False
    assert value["featureFamilies"][overlay.TARGET_FAMILY]["trainingEligible"] is False


def test_snapshot_contract_accepts_strictly_prior_projection_without_claiming_confirmation():
    def base_snapshot(*_args, **_kwargs):
        return {
            "snapshotRole": "old",
            "parkRunFactor": 1.02,
            "weatherRunFactor": 1.01,
            "providerEvidence": {},
            "pointInTimeVerified": True,
            "postgameFieldsExcluded": True,
            "selectionUsedOutcomes": False,
            "trainingEligible": False,
            "eligibilityErrors": [
                "confirmed_lineups_missing",
                "confirmed_starters_missing",
            ],
            "productionAuthorityChanged": False,
        }

    module = SimpleNamespace(
        build_training_snapshot=base_snapshot,
        REQUIRED_RESOURCES=("pitchers", "lineups"),
        OPTIONAL_RESOURCES=("weather", "park"),
        point_in_time_errors=lambda _resources, _lock: [],
        _effective_at=lambda envelope: datetime.fromisoformat(
            envelope["meta"]["asOfUtc"].replace("Z", "+00:00")
        ),
        _sha=lambda _value: "payload-sha",
        overlay=SimpleNamespace(snapshot_fingerprint=lambda value: str(sorted(value))),
    )
    entrypoint.install_snapshot_contract(module)
    resources = {
        name: {
            "data": {},
            "meta": {
                "asOfUtc": "2026-07-01T03:59:59Z",
                "source": "test",
                "complete": True,
                "pointInTimeProjectionVerified": name in {"pitchers", "lineups"},
            },
            "error": None,
        }
        for name in ("pitchers", "lineups", "weather", "park")
    }
    normalized = {
        "coverage": {
            "confirmedStarters": False,
            "confirmedLineups": False,
        }
    }

    value = module.build_training_snapshot(
        {"predictionLockAtUtc": "2026-07-01T22:15:00Z"},
        {},
        normalized,
        resources,
        retrieved_at=datetime.now(timezone.utc),
    )

    assert value["trainingEligible"] is True
    assert value["targetIdentityMode"] == "STRICTLY_PRIOR_PROJECTION"
    assert value["confirmedTargetStarters"] is False
    assert value["confirmedTargetLineups"] is False
    assert value["projectedTargetStarters"] is True
    assert value["projectedTargetLineups"] is True


def test_resource_shape_compatibility_uses_real_fundamentals_alias():
    captured = {}

    class Fundamentals:
        @staticmethod
        def normalize_match(match, captured_at, resources=None):
            captured.update(resources or {})
            return {"ok": True}

    module = SimpleNamespace(fundamentals=Fundamentals)
    entrypoint.install_resource_shape_compatibility(module)
    source = {
        "pitchers": {
            "data": {
                "away": {"recentThreeStarts": {"fip": 3.1}},
                "home": {"recentThreeStarts": {"fip": 3.5}},
            }
        }
    }

    value = module.fundamentals.normalize_match(
        {}, datetime.now(timezone.utc), source
    )

    assert value == {"ok": True}
    assert captured["pitchers"]["data"]["away"]["recent"] == {"fip": 3.1}
    assert captured["pitchers"]["data"]["home"]["recent"] == {"fip": 3.5}
    assert "recent" not in source["pitchers"]["data"]["away"]
