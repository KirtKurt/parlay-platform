from __future__ import annotations

import copy
import hashlib
import io
import json

import pytest

import mlb_v8_historical_bbs_overlay_v1 as base
import mlb_v8_historical_context_overlay_v1 as context


def canonical():
    return {
        "slateDateEt": "2026-07-01",
        "officialGamePk": "123",
        "homeTeam": "New York Yankees",
        "awayTeam": "Boston Red Sox",
        "predictionLockAtUtc": "2026-07-01T22:15:00Z",
    }


def snapshot(role, *, home, away, park=None, weather=None, authority=None):
    value = {
        "version": context.SNAPSHOT_VERSION,
        "authority": authority
        or (
            base.AUTHORITY
            if role.startswith("BBD_STRICTLY_PRIOR")
            else context.AUTHORITY
        ),
        "snapshotRole": role,
        "createdAtUtc": "2026-07-29T00:00:00+00:00",
        "officialGamePk": "123",
        "providerMatchId": "official-123",
        "predictionLockAtUtc": "2026-07-01T22:15:00Z",
        "slateDateEt": "2026-07-01",
        "homeTeam": "New York Yankees",
        "awayTeam": "Boston Red Sox",
        "home": home,
        "away": away,
        "parkRunFactor": park,
        "weatherRunFactor": weather,
        "providerEvidence": {},
        "pointInTimeVerified": True,
        "postgameFieldsExcluded": True,
        "sameDayResultsExcluded": True,
        "targetGameOutcomeUsed": False,
        "selectionUsedOutcomes": False,
        "trainingEligible": True,
        "eligibilityErrors": [],
        "productionAuthorityChanged": False,
    }
    value["fingerprint"] = base.snapshot_fingerprint(value)
    return value


def manifest(target):
    value = {
        "version": context.MANIFEST_VERSION,
        "authority": context.AUTHORITY,
        "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False,
        "eligibleGameCount": 1,
        "records": [
            {
                **canonical(),
                "providerMatchId": "official-123",
                "trainingEligible": True,
                "eligibilityErrors": [],
                "snapshot": target,
            }
        ],
    }
    value["manifestDigest"] = base.manifest_digest(value)
    return value


def test_target_overlay_merges_without_erasing_prior_game_features():
    prior = snapshot(
        "BBD_STRICTLY_PRIOR_COMPLETED_GAME_FEATURES_AT_T_MINUS_45",
        home={"bbsHistoryGames": 30.0, "bbsWinRate10": 0.7},
        away={"bbsHistoryGames": 30.0, "bbsWinRate10": 0.4},
    )
    target = snapshot(
        context.TARGET_ROLE,
        home={
            "starterQuality": -3.1,
            "bullpenQuality": -3.4,
            "lineupQuality": 112.0,
        },
        away={
            "starterQuality": -4.1,
            "bullpenQuality": -4.4,
            "lineupQuality": 96.0,
        },
        park=1.04,
        weather=1.02,
    )
    record = {**canonical(), "frozenFundamentalsSnapshot": prior}

    rows, proof = context.apply_manifest([record], manifest(target))

    merged = rows[0]["frozenFundamentalsSnapshot"]
    assert merged["authority"] == context.AUTHORITY
    assert merged["home"]["bbsWinRate10"] == 0.7
    assert merged["home"]["starterQuality"] == -3.1
    assert merged["parkRunFactor"] == 1.04
    assert merged["weatherRunFactor"] == 1.02
    assert merged["featureFamilies"][context.PRIOR_FAMILY]["trainingEligible"] is True
    assert merged["featureFamilies"][context.TARGET_FAMILY]["trainingEligible"] is True
    assert merged["selectionUsedOutcomes"] is False
    assert merged["targetGameOutcomeUsed"] is False
    assert merged["fingerprint"] == base.snapshot_fingerprint(merged)
    assert proof["authority"] == context.AUTHORITY
    assert proof["appliedGameCount"] == 1


def test_target_overlay_rejects_missing_weather_context():
    target = snapshot(
        context.TARGET_ROLE,
        home={"starterQuality": -3.1},
        away={"starterQuality": -4.1},
        park=1.0,
        weather=None,
    )

    rows, proof = context.apply_manifest(
        [copy.deepcopy(canonical())], manifest(target)
    )

    assert "frozenFundamentalsSnapshot" not in rows[0]
    assert rows[0]["historicalTargetGameContext"]["trainingEligible"] is False
    assert (
        "target_context_weather_missing"
        in rows[0]["historicalTargetGameContext"]["errors"]
    )
    assert proof["invalidGameCount"] == 1


def test_retired_bbs_manifest_authority_is_rejected():
    target = snapshot(
        context.TARGET_ROLE,
        home={"starterQuality": -3.1},
        away={"starterQuality": -4.1},
        park=1.0,
        weather=1.0,
    )
    value = manifest(target)
    value["authority"] = base.AUTHORITY
    value["manifestDigest"] = base.manifest_digest(value)

    with pytest.raises(RuntimeError, match="manifest authority mismatch"):
        context.apply_manifest([canonical()], value)


def test_official_target_snapshot_does_not_require_bbs_authority():
    target = snapshot(
        context.TARGET_ROLE,
        home={"starterQuality": -3.1},
        away={"starterQuality": -4.1},
        park=1.0,
        weather=1.0,
    )

    valid, errors = context._validate_target_snapshot(target, canonical())

    assert valid is True
    assert errors == []
    assert target["authority"] == context.AUTHORITY
    assert target["authority"] != base.AUTHORITY


def test_load_requires_official_pointer_type_authority_and_provider(monkeypatch):
    target = snapshot(
        context.TARGET_ROLE,
        home={"starterQuality": -3.1},
        away={"starterQuality": -4.1},
        park=1.0,
        weather=1.0,
    )
    value = manifest(target)
    body = json.dumps(value).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()

    class Table:
        def get_item(self, **_kwargs):
            return {
                "Item": {
                    "PK": context.POINTER_PK,
                    "SK": context.POINTER_SK,
                    "record_type": context.POINTER_RECORD_TYPE,
                    "revision": 60,
                    "data": {
                        "authority": context.AUTHORITY,
                        "provider": "official_mlb_plus_internal_canonical",
                        "manifest": {
                            "bucket": "bucket",
                            "key": "manifest.json",
                            "sha256": digest,
                        },
                    },
                }
            }

    class DDB:
        def Table(self, _name):
            return Table()

    class S3:
        def get_object(self, **_kwargs):
            return {"Body": io.BytesIO(body)}

    monkeypatch.setenv("MLB_V8_HISTORICAL_CONTEXT_OVERLAY_ENABLED", "true")
    rows, proof = context.load_and_apply(
        [canonical()], ddb_resource=DDB(), s3_client=S3()
    )

    assert rows[0]["historicalTargetGameContext"]["trainingEligible"] is True
    assert proof["pointerRevision"] == 60
    assert proof["authority"] == context.AUTHORITY
