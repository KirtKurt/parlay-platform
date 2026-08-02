from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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


def test_pointer_isolation_uses_official_context_authority():
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

    assert module.overlay.POINTER_PK == overlay.POINTER_PK
    assert module.overlay.AUTHORITY == entrypoint.AUTHORITY
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


def test_run_contract_forces_official_client_and_no_bbd_evidence(tmp_path):
    captured = {}

    def base_run(*_args, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    module = SimpleNamespace(run=base_run)
    entrypoint.install_run_contract(module)
    output = tmp_path / "report.json"

    value = module.run(output=output)

    assert captured["client_factory"] is entrypoint.OfficialContextClient
    assert value["provider"] == "official_mlb_plus_internal_canonical_context"
    assert value["bbsApiUsed"] is False
    assert value["bbsCredentialRead"] is False
    assert value["productionAuthorityChanged"] is False
    assert value["automaticWagerAllowed"] is False
    assert output.exists()
