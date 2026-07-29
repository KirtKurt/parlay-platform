from __future__ import annotations

import copy

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


def snapshot(role, *, home, away, park=None, weather=None):
    value = {
        "version": base.SNAPSHOT_VERSION,
        "authority": base.AUTHORITY,
        "snapshotRole": role,
        "createdAtUtc": "2026-07-29T00:00:00+00:00",
        "officialGamePk": "123",
        "providerMatchId": "bbs-1",
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
    manifest = {
        "version": base.MANIFEST_VERSION,
        "authority": base.AUTHORITY,
        "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False,
        "eligibleGameCount": 1,
        "records": [
            {
                **canonical(),
                "providerMatchId": "bbs-1",
                "trainingEligible": True,
                "eligibilityErrors": [],
                "snapshot": target,
            }
        ],
    }
    manifest["manifestDigest"] = base.manifest_digest(manifest)
    record = {**canonical(), "frozenFundamentalsSnapshot": prior}

    rows, proof = context.apply_manifest([record], manifest)

    merged = rows[0]["frozenFundamentalsSnapshot"]
    assert merged["home"]["bbsWinRate10"] == 0.7
    assert merged["home"]["starterQuality"] == -3.1
    assert merged["parkRunFactor"] == 1.04
    assert merged["weatherRunFactor"] == 1.02
    assert merged["featureFamilies"][context.PRIOR_FAMILY]["trainingEligible"] is True
    assert merged["featureFamilies"][context.TARGET_FAMILY]["trainingEligible"] is True
    assert merged["selectionUsedOutcomes"] is False
    assert merged["targetGameOutcomeUsed"] is False
    assert merged["fingerprint"] == base.snapshot_fingerprint(merged)
    assert proof["appliedGameCount"] == 1


def test_target_overlay_rejects_missing_weather_context():
    target = snapshot(
        context.TARGET_ROLE,
        home={"starterQuality": -3.1},
        away={"starterQuality": -4.1},
        park=1.0,
        weather=None,
    )
    manifest = {
        "version": base.MANIFEST_VERSION,
        "authority": base.AUTHORITY,
        "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False,
        "eligibleGameCount": 1,
        "records": [
            {
                **canonical(),
                "trainingEligible": True,
                "eligibilityErrors": [],
                "snapshot": target,
            }
        ],
    }
    manifest["manifestDigest"] = base.manifest_digest(manifest)

    rows, proof = context.apply_manifest([copy.deepcopy(canonical())], manifest)

    assert "frozenFundamentalsSnapshot" not in rows[0]
    assert rows[0]["historicalTargetGameContext"]["trainingEligible"] is False
    assert (
        "target_context_weather_missing"
        in rows[0]["historicalTargetGameContext"]["errors"]
    )
    assert proof["invalidGameCount"] == 1
