from __future__ import annotations

import mlb_v8_historical_bbs_overlay_v1 as overlay


def snapshot():
    value = {
        "version": overlay.SNAPSHOT_VERSION,
        "authority": overlay.AUTHORITY,
        "trainingEligible": True,
        "pointInTimeVerified": True,
        "postgameFieldsExcluded": True,
        "selectionUsedOutcomes": False,
        "officialGamePk": "123",
        "predictionLockAtUtc": "2026-07-01T22:15:00Z",
        "home": {"starterQuality": -3.2},
        "away": {"starterQuality": -4.1},
    }
    value["fingerprint"] = overlay.snapshot_fingerprint(value)
    return value


def manifest():
    value = {
        "version": overlay.MANIFEST_VERSION,
        "authority": overlay.AUTHORITY,
        "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False,
        "eligibleGameCount": 1,
        "records": [
            {
                "officialGamePk": "123",
                "predictionLockAtUtc": "2026-07-01T22:15:00Z",
                "providerMatchId": "bbs-1",
                "trainingEligible": True,
                "snapshot": snapshot(),
            }
        ],
    }
    value["manifestDigest"] = overlay.manifest_digest(value)
    return value


def test_apply_manifest_adds_frozen_snapshot_only_to_exact_identity():
    records = [
        {
            "officialGamePk": "123",
            "predictionLockAtUtc": "2026-07-01T22:15:00Z",
        }
    ]

    enriched, proof = overlay.apply_manifest(records, manifest())

    assert enriched[0]["frozenFundamentalsSnapshot"]["officialGamePk"] == "123"
    assert enriched[0]["historicalBbsFundamentals"]["providerMatchId"] == "bbs-1"
    assert proof["appliedGameCount"] == 1
    assert proof["coverage"] == 1.0


def test_tampered_snapshot_is_not_applied():
    value = manifest()
    value["records"][0]["snapshot"]["home"]["starterQuality"] = 999
    value["manifestDigest"] = overlay.manifest_digest(value)
    records = [
        {
            "officialGamePk": "123",
            "predictionLockAtUtc": "2026-07-01T22:15:00Z",
        }
    ]

    enriched, proof = overlay.apply_manifest(records, value)

    assert "frozenFundamentalsSnapshot" not in enriched[0]
    assert proof["invalidGameCount"] == 1
