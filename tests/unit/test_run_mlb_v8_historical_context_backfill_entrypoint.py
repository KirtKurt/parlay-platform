from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import mlb_v8_historical_context_overlay_v1 as overlay
import run_mlb_v8_historical_context_backfill_entrypoint as entrypoint


def _official_game():
    return {
        "gamePk": 123,
        "gameDate": "2026-07-01T23:05:00Z",
        "teams": {
            "away": {"team": {"name": "Away Club"}},
            "home": {"team": {"name": "Home Club"}},
        },
    }


def test_official_client_lists_direct_official_game_identity(monkeypatch):
    client = entrypoint.OfficialContextClient()
    monkeypatch.setattr(
        client.source,
        "schedule",
        lambda _start, _end: {"dates": [{"games": [_official_game()]}]},
    )

    value = client.list_mlb_matches("2026-07-01")

    assert value["error"] is None
    assert value["meta"]["provider"] == "official_mlb"
    assert value["data"] == [
        {
            "id": "123",
            "match_id": "123",
            "officialGamePk": "123",
            "gamePk": "123",
            "startTime": "2026-07-01T23:05:00Z",
            "home": {"name": "Home Club"},
            "away": {"name": "Away Club"},
        }
    ]


def test_official_client_returns_resource_bundle_without_bbd(monkeypatch):
    client = entrypoint.OfficialContextClient()
    client.games[("2026-07-01", "123")] = {
        "id": "123",
        "match_id": "123",
        "officialGamePk": "123",
        "gamePk": "123",
        "startTime": "2026-07-01T23:05:00Z",
        "home": {"name": "Home Club"},
        "away": {"name": "Away Club"},
    }
    captured = {}

    def build_bundle(canonical, _stored_pitchers, _stored_lineups):
        captured.update(canonical)
        return {
            "pitchers": {
                "data": {"away": {}, "home": {}},
                "meta": {
                    "source": "MLB Stats API strictly prior rotation projection",
                    "complete": True,
                    "pointInTimeProjectionVerified": True,
                },
                "error": None,
            }
        }

    monkeypatch.setattr(client.source, "build_bundle", build_bundle)

    value = client.get_mlb_match_resource(
        "123",
        "pitchers",
        game_date="2026-07-01",
        as_of="2026-07-01T22:20:00Z",
    )

    assert captured["officialGamePk"] == "123"
    assert captured["predictionLockAtUtc"] == "2026-07-01T22:20:00Z"
    assert value["meta"]["provider"] == "official_mlb"
    assert value["error"] is None


def test_pointer_isolation_does_not_relabel_retired_compatibility_overlay():
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
            AUTHORITY="old-authority",
        ),
        VERSION="old",
        REPORT_TYPE="old",
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

    assert module.overlay.POINTER_PK == "old"
    assert module.overlay.AUTHORITY == "old-authority"
    assert overlay.AUTHORITY == entrypoint.AUTHORITY
    assert overlay.base.AUTHORITY == entrypoint.RETIRED_BBS_AUTHORITY
    assert pointer["key"] == "mlb/v8/historical-context/manifests/a.json"
    assert calls["s3"]["Metadata"]["provider"] == "official-mlb"
    assert calls["Item"]["record_type"] == entrypoint.RECORD_TYPE
    assert calls["Item"]["data"]["provider"] == "official_mlb_plus_internal_canonical"
    assert revision == 1


def test_snapshot_contract_rewrites_provider_evidence_and_excludes_outcomes():
    def base_snapshot(*_args, **_kwargs):
        return {
            "authority": "old",
            "providerEvidence": {"pitchers": {"source": "legacy"}},
            "selectionUsedOutcomes": False,
            "productionAuthorityChanged": False,
        }

    module = SimpleNamespace(
        build_training_snapshot=base_snapshot,
        REQUIRED_RESOURCES=("pitchers",),
        OPTIONAL_RESOURCES=("weather",),
        _effective_at=lambda envelope: datetime.fromisoformat(
            envelope["meta"]["asOfUtc"].replace("Z", "+00:00")
        ),
        _sha=lambda _value: "payload-sha",
        overlay=SimpleNamespace(snapshot_fingerprint=lambda _value: "snapshot-sha"),
    )
    entrypoint.install_snapshot_contract(module)
    resources = {
        name: {
            "data": {},
            "meta": {
                "asOfUtc": "2026-07-01T22:00:00Z",
                "source": "official_mlb_prior_context",
            },
            "error": None,
        }
        for name in ("pitchers", "weather")
    }

    value = module.build_training_snapshot({}, {}, {}, resources)

    assert value["authority"] == entrypoint.AUTHORITY
    assert value["providerEvidence"]["pitchers"]["provider"] == "official_mlb"
    assert value["targetGameOutcomeUsed"] is False
    assert value["sameDayResultsExcluded"] is True
    assert value["productionAuthorityChanged"] is False
    assert value["fingerprint"] == "snapshot-sha"


def test_run_contract_scopes_official_authority_and_restores_it(tmp_path):
    captured = {}
    fake_overlay = SimpleNamespace(AUTHORITY="fake-retired-authority")
    module = SimpleNamespace(overlay=fake_overlay)

    def base_run(*_args, **kwargs):
        captured.update(kwargs)
        captured["overlayAuthorityDuringRun"] = fake_overlay.AUTHORITY
        captured["baseAuthorityDuringRun"] = overlay.base.AUTHORITY
        captured["targetAuthorityDuringRun"] = overlay.AUTHORITY
        return {"ok": True}

    module.run = base_run
    entrypoint.install_run_contract(module)
    output = tmp_path / "report.json"

    value = module.run(output=output)

    assert captured["client_factory"] is entrypoint.OfficialContextClient
    assert captured["overlayAuthorityDuringRun"] == entrypoint.AUTHORITY
    assert captured["baseAuthorityDuringRun"] == entrypoint.AUTHORITY
    assert captured["targetAuthorityDuringRun"] == entrypoint.AUTHORITY
    assert fake_overlay.AUTHORITY == "fake-retired-authority"
    assert overlay.base.AUTHORITY == entrypoint.RETIRED_BBS_AUTHORITY
    assert value["provider"] == "official_mlb_plus_internal_canonical_context"
    assert value["bbsApiUsed"] is False
    assert value["bbsCredentialRead"] is False
    assert value["productionAuthorityChanged"] is False
    assert value["automaticWagerAllowed"] is False
    assert output.exists()


def test_run_contract_restores_authority_after_failure():
    fake_overlay = SimpleNamespace(AUTHORITY="fake-retired-authority")
    module = SimpleNamespace(overlay=fake_overlay)

    def base_run(*_args, **_kwargs):
        assert fake_overlay.AUTHORITY == entrypoint.AUTHORITY
        assert overlay.base.AUTHORITY == entrypoint.AUTHORITY
        raise RuntimeError("expected failure")

    module.run = base_run
    entrypoint.install_run_contract(module)

    with pytest.raises(RuntimeError, match="expected failure"):
        module.run()

    assert fake_overlay.AUTHORITY == "fake-retired-authority"
    assert overlay.AUTHORITY == entrypoint.AUTHORITY
    assert overlay.base.AUTHORITY == entrypoint.RETIRED_BBS_AUTHORITY


def test_zero_eligible_official_batch_advances_cursor_without_entering_training(
    monkeypatch, tmp_path
):
    manifest = {
        "manifestDigest": "manifest-digest",
        "processedGameCount": 5,
        "eligibleGameCount": 0,
        "records": [
            {
                "officialGamePk": str(index),
                "trainingEligible": False,
                "snapshot": None,
            }
            for index in range(5)
        ],
    }
    pointer = {
        "bucket": "bucket",
        "key": "mlb/v8/historical-context/manifests/manifest.json",
        "sha256": "sha",
    }
    activated = {}

    class S3:
        def get_object(self, **_kwargs):
            return {"Body": io.BytesIO(json.dumps(manifest).encode("utf-8"))}

    class DDB:
        def Table(self, name):
            activated["tableName"] = name
            return "table"

    monkeypatch.setattr(
        entrypoint.boto3,
        "client",
        lambda service, **_kwargs: S3() if service == "s3" else None,
    )
    monkeypatch.setattr(
        entrypoint.boto3,
        "resource",
        lambda service, **_kwargs: DDB() if service == "dynamodb" else None,
    )

    def base_run(*_args, **_kwargs):
        return {
            "ok": False,
            "manifest": pointer,
            "activePointerRevision": 59,
            "newRecordCount": 5,
            "newEligibleGameCount": 0,
            "eligibleGameCount": 0,
            "remainingGameCount": 4109,
            "blockers": [
                "no_training_eligible_point_in_time_bbs_rows",
                "current_batch_added_zero_training_eligible_rows",
            ],
        }

    def activate(table, received_pointer, received_manifest, revision):
        activated.update(
            {
                "table": table,
                "pointer": received_pointer,
                "manifest": received_manifest,
                "revision": revision,
            }
        )
        return revision + 1

    module = SimpleNamespace(
        overlay=SimpleNamespace(AUTHORITY=entrypoint.RETIRED_BBS_AUTHORITY),
        run=base_run,
        _activate=activate,
    )
    entrypoint.install_run_contract(module)
    output = tmp_path / "report.json"

    value = module.run(
        region="us-east-1",
        table_name="snapshots",
        output=output,
    )

    assert activated["tableName"] == "snapshots"
    assert activated["revision"] == 59
    assert activated["manifest"]["eligibleGameCount"] == 0
    assert value["activePointerRevision"] == 60
    assert value["cursorAdvanced"] is True
    assert value["progressMade"] is True
    assert value["ok"] is True
    assert value["blockers"] == []
    assert "cursor_advanced_across_training_ineligible_historical_rows" in value[
        "warnings"
    ]
    assert all(record["trainingEligible"] is False for record in manifest["records"])
    assert all(record["snapshot"] is None for record in manifest["records"])
    assert overlay.base.AUTHORITY == entrypoint.RETIRED_BBS_AUTHORITY
