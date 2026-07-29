from __future__ import annotations

from types import SimpleNamespace

import mlb_official_point_in_time_fundamentals_v1 as official
import mlb_v8_historical_bbs_overlay_v1 as base
import mlb_v8_historical_bbs_overlay_v2 as overlay_v2
import mlb_v8_historical_bbs_prior_game_features_v1 as prior_features
import run_mlb_v8_historical_point_in_time_backfill as entrypoint


def _record():
    return {
        "slateDateEt": "2026-07-20",
        "officialGamePk": "777001",
        "predictionLockAtUtc": "2026-07-20T22:15:00+00:00",
    }


def _snapshot(role: str, **extra):
    value = {
        "version": base.SNAPSHOT_VERSION,
        "authority": base.AUTHORITY,
        "snapshotRole": role,
        "officialGamePk": "777001",
        "predictionLockAtUtc": "2026-07-20T22:15:00+00:00",
        "home": {},
        "away": {},
        "pointInTimeVerified": True,
        "postgameFieldsExcluded": True,
        "selectionUsedOutcomes": False,
        "trainingEligible": True,
        **extra,
    }
    value["fingerprint"] = base.snapshot_fingerprint(value)
    return value


def _manifest(snapshot):
    value = {
        "version": base.MANIFEST_VERSION,
        "authority": base.AUTHORITY,
        "productionAuthorityChanged": False,
        "selectionUsedOutcomes": False,
        "eligibleGameCount": 1,
        "records": [
            {
                "officialGamePk": "777001",
                "predictionLockAtUtc": "2026-07-20T22:15:00+00:00",
                "trainingEligible": True,
                "snapshot": snapshot,
            }
        ],
    }
    value["manifestDigest"] = base.manifest_digest(value)
    return value


def test_target_backfill_uses_separate_pointer():
    module = SimpleNamespace(overlay=SimpleNamespace(POINTER_PK="shared", POINTER_SK="ACTIVE"))
    entrypoint.install_target_manifest_isolation(module)
    assert module.overlay.POINTER_PK == entrypoint.TARGET_POINTER_PK
    assert module.overlay.POINTER_SK == entrypoint.TARGET_POINTER_SK
    assert module.overlay.POINTER_PK != overlay_v2.PRIOR_POINTER_PK


def test_overlay_routes_target_and_prior_snapshots_to_different_fields():
    prior = _snapshot(
        overlay_v2.PRIOR_ROLE,
        priorCompletedGamesUsed=True,
        sameDayResultsExcluded=True,
        targetGameOutcomeUsed=False,
    )
    target = _snapshot(overlay_v2.TARGET_ROLE)
    with_prior, prior_proof = overlay_v2._apply(
        [_record()],
        _manifest(prior),
        expected_role=overlay_v2.PRIOR_ROLE,
        destination="historicalBbsPriorGameSnapshot",
        evidence_key="historicalBbsPriorGame",
    )
    enriched, target_proof = overlay_v2._apply(
        with_prior,
        _manifest(target),
        expected_role=overlay_v2.TARGET_ROLE,
        destination="frozenFundamentalsSnapshot",
        evidence_key="historicalPointInTimeFundamentals",
    )
    assert prior_proof["appliedGameCount"] == 1
    assert target_proof["appliedGameCount"] == 1
    assert enriched[0]["historicalBbsPriorGameSnapshot"]["snapshotRole"] == overlay_v2.PRIOR_ROLE
    assert enriched[0]["frozenFundamentalsSnapshot"]["snapshotRole"] == overlay_v2.TARGET_ROLE


def test_prior_features_never_read_target_fundamentals_snapshot():
    target = _snapshot(overlay_v2.TARGET_ROLE)
    target["home"] = {"bbsHistoryGames": 30, "bbsWinRate5": 1.0}
    target["away"] = {"bbsHistoryGames": 30, "bbsWinRate5": 0.0}
    target["priorCompletedGamesUsed"] = True
    target["sameDayResultsExcluded"] = True
    target["targetGameOutcomeUsed"] = False
    row = {**_record(), "frozenFundamentalsSnapshot": target}
    values = prior_features.feature_map(row)
    assert values["bbs_prior_available"] == 0.0
    assert values["bbs_prior_win_rate5_diff"] == 0.0


def test_official_timecode_resource_has_all_required_domains():
    feed = {
        "gameData": {
            "probablePitchers": {
                "away": {"id": 11, "fullName": "Away Starter"},
                "home": {"id": 21, "fullName": "Home Starter"},
            },
            "teams": {
                "away": {"id": 1, "name": "Away", "record": {"wins": 50}},
                "home": {"id": 2, "name": "Home", "record": {"wins": 55}},
            },
            "venue": {
                "id": 10,
                "name": "Park",
                "fieldInfo": {"leftLine": "330", "center": "400", "rightLine": "330"},
            },
            "weather": {"temp": 82, "wind": "8 mph, Out To RF"},
            "players": {
                "ID11": {"id": 11, "fullName": "Away Starter", "active": True},
                "ID21": {"id": 21, "fullName": "Home Starter", "active": True},
            },
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {
                        "battingOrder": list(range(101, 110)),
                        "players": {
                            "ID11": {"person": {"id": 11, "fullName": "Away Starter"}, "position": {"type": "Pitcher"}, "seasonStats": {"pitching": {"era": "3.50", "inningsPitched": "100", "gamesStarted": 20}}},
                            **{f"ID{i}": {"person": {"id": i, "fullName": f"Away {i}"}, "position": {"type": "Infielder"}, "seasonStats": {"batting": {"ops": ".750"}}} for i in range(101, 110)},
                            "ID12": {"person": {"id": 12, "fullName": "Away Reliever"}, "position": {"type": "Pitcher"}, "seasonStats": {"pitching": {"era": "3.20"}}},
                        },
                    },
                    "home": {
                        "battingOrder": list(range(201, 210)),
                        "players": {
                            "ID21": {"person": {"id": 21, "fullName": "Home Starter"}, "position": {"type": "Pitcher"}, "seasonStats": {"pitching": {"era": "3.00", "inningsPitched": "120", "gamesStarted": 20}}},
                            **{f"ID{i}": {"person": {"id": i, "fullName": f"Home {i}"}, "position": {"type": "Outfielder"}, "seasonStats": {"batting": {"ops": ".780"}}} for i in range(201, 210)},
                            "ID22": {"person": {"id": 22, "fullName": "Home Reliever"}, "position": {"type": "Pitcher"}, "seasonStats": {"pitching": {"era": "2.90"}}},
                        },
                    },
                }
            }
        },
    }
    as_of = "2026-07-20T22:15:00+00:00"
    for name in ("pitchers", "bullpens", "lineups", "injuries", "team_context", "weather", "park"):
        envelope = official._resource(feed, name, as_of)
        assert envelope["error"] is None
        assert envelope["meta"]["sourceEffectiveAtUtc"] == as_of
        assert envelope["meta"]["pointInTimeQuery"] is True
    assert official._resource(feed, "lineups", as_of)["data"]["home"]["confirmed"] is True
    assert official._resource(feed, "weather", as_of)["data"]["weatherRunFactor"] != 1.0
    assert official._resource(feed, "bullpens", as_of)["data"]["home"]["freshnessScore"] is None
    assert official._resource(feed, "team_context", as_of)["data"]["home"]["travel"] is None
