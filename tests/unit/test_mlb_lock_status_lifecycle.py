from __future__ import annotations

import copy

from tests.unit.test_mlb_daily_per_game_lock import (
    FakeTable,
    G1,
    SLATE,
    build_module,
    dt,
    game,
    persist_candidate,
    pull,
    staged_items,
)

import mlb_daily_per_game_lock_patch as patch


def _playable(row):
    row["playable"] = True
    row["playablePick"] = True
    row["actionablePick"] = True
    row["playabilityStatus"] = "PLAYABLE"
    row["tags"] = sorted(set(row.get("tags") or []) | {"ACTIONABLE_PICK", "PLAYABLE_PREDICTION"})


def test_tminus60_and_tminus50_readiness_are_write_once_and_visible():
    p1700 = pull("2026-07-13T17:00:00+00:00", [G1], "readiness-1700")
    module = build_module([p1700], "2026-07-13T17:00:10+00:00")

    first = module.run_lock(SLATE)
    status = first["perGameLockProgress"]["games"][0]
    assert status["readiness"]["tMinus60"]["recorded"] is True
    assert status["readiness"]["tMinus60"]["candidateReady"] is True
    assert status["readiness"]["tMinus50"]["recorded"] is False

    p1710 = pull("2026-07-13T17:10:00+00:00", [G1], "readiness-1710")
    module.history.pulls.append(p1710)
    persist_candidate(module, G1, p1710)
    module.now = dt("2026-07-13T17:10:10+00:00")
    second = module.run_lock(SLATE)
    status = second["perGameLockProgress"]["games"][0]
    assert status["readiness"]["tMinus50"]["recorded"] is True
    assert status["readiness"]["tMinus50"]["status"] == "READY"

    readiness = [
        item
        for item in module.TABLE.items.values()
        if item.get("record_type") == patch.READINESS_RECORD_TYPE
    ]
    assert len(readiness) == 2
    assert {item["checkpoint"] for item in readiness} == {"T_MINUS_60", "T_MINUS_50"}


def test_low_confidence_prediction_locks_while_playability_stays_separate():
    source = pull("2026-07-13T17:15:00+00:00", [G1], "low-confidence")
    module = build_module([source], "2026-07-13T17:15:05+00:00", seed=False)

    def low_confidence(row):
        row["score"] = 10.0
        row["winProbability"] = 0.51
        row["winProbabilityPct"] = 51.0
        row["teamWinProbabilityPct"] = 51.0
        row["promoted"] = False
        row["promotionStatus"] = "PASS"
        row["playable"] = False
        row["playabilityStatus"] = "NOT_PLAYABLE"
        row["tags"] = sorted(set(row.get("tags") or []) | {"LOW_CONFIDENCE_PREDICTION", "NOT_PLAYABLE"})

    persist_candidate(module, G1, source, mutate=low_confidence)
    result = module.run_lock(SLATE)

    assert result["locked"] is True
    status = result["perGameLockProgress"]["games"][0]
    assert status["lockedPrediction"] is True
    assert status["officialPrediction"] is True
    assert status["playable"] is False
    assert status["blocked"] is True
    assert status["trainingEligible"] is True


def test_late_playability_check_cannot_rewrite_locked_selection():
    source = pull("2026-07-13T17:15:00+00:00", [G1], "locked-playable")
    module = build_module([source], "2026-07-13T17:15:05+00:00", seed=False)
    persist_candidate(module, G1, source, mutate=_playable)
    locked = module.run_lock(SLATE)
    before = copy.deepcopy(staged_items(module)[0]["data"]["row"])

    late = pull("2026-07-13T17:29:00+00:00", [G1], "late-injury")
    module.history.pulls.append(late)

    def blocked(row):
        _playable(row)
        row["blockedReasons"] = ["CONFIRMED_IMPACT_PLAYER_ABSENCE"]

    persist_candidate(module, G1, late, mutate=blocked)
    module.now = dt("2026-07-13T17:30:05+00:00")
    module.run_lock(SLATE)
    after = staged_items(module)[0]["data"]["row"]
    status = module._status_payload(SLATE)["perGameStatus"][0]

    assert locked["locked"] is True
    assert status["playable"] is False
    assert status["blocked"] is True
    assert "CONFIRMED_IMPACT_PLAYER_ABSENCE" in status["playabilityBlockReasons"]
    assert status["playabilityAssessment"]["selection_rewrite_allowed"] is False
    for field in ("predictedWinner", "predictedSide", "teamWinProbabilityPct", "lastPrelockSelectionFingerprint"):
        assert after[field] == before[field]

    assessment = next(
        item
        for item in module.TABLE.items.values()
        if item.get("record_type") == patch.RELEASE_ASSESSMENT_RECORD_TYPE
    )
    assessment["canonical_selection_fingerprint"] = "tampered-selection"
    assessment["canonical_predicted_winner"] = "Different Team"
    ignored = module._status_payload(SLATE)["perGameStatus"][0]
    assert ignored["predictedWinner"] == before["predictedWinner"]
    assert ignored["selectionFingerprint"] == before["lastPrelockSelectionFingerprint"]
    assert ignored["playabilityAssessment"] is None


def test_doubleheader_game2_rechecks_release_when_game1_final_event_arrives():
    dh1 = game("dh-1", "2026-07-13T20:00:00+00:00")
    dh2 = game("dh-2", "2026-07-13T22:00:00+00:00")
    for row in (dh1, dh2):
        row["home_team"] = "Same Home"
        row["away_team"] = "Same Away"
        row["game_key"] = f"mlb|{SLATE}|same away|same home|{row['game_id']}"

    p1915 = pull("2026-07-13T19:15:00+00:00", [dh1, dh2], "dh-1915")
    module = build_module([p1915], "2026-07-13T19:15:05+00:00", seed=False)
    persist_candidate(module, dh1, p1915, mutate=_playable)
    persist_candidate(module, dh2, p1915, mutate=_playable)
    first = module.run_lock(SLATE)
    assert first["perGameLockProgress"]["canonicalCount"] == 1

    p2115 = pull("2026-07-13T21:15:00+00:00", [dh1, dh2], "dh-2115")
    module.history.pulls.append(p2115)
    persist_candidate(module, dh2, p2115, mutate=_playable)
    module.now = dt("2026-07-13T21:15:05+00:00")
    second = module.run_lock(SLATE)
    assert second["locked"] is True

    module.now = dt("2026-07-13T21:30:05+00:00")
    module.run_lock(SLATE)
    before_final = next(
        row for row in module._status_payload(SLATE)["perGameStatus"] if row["gameId"] == "dh-2"
    )
    assert before_final["blocked"] is True
    assert "DOUBLEHEADER_GAME1_NOT_FINAL" in before_final["playabilityBlockReasons"]

    module.OUTCOMES = FakeTable()
    module.OUTCOMES.put_item(Item={
        "PK": f"OUTCOME#mlb#{SLATE}",
        "SK": f"GAME#{dh1['game_key']}",
        "game_id": dh1["game_id"],
        "completed": True,
    })
    module.now = dt("2026-07-13T21:31:05+00:00")
    module.run_lock(SLATE)
    after_final = next(
        row for row in module._status_payload(SLATE)["perGameStatus"] if row["gameId"] == "dh-2"
    )
    assert after_final["playable"] is True
    assert after_final["playabilityAssessment"]["checkpoint"] == "EVENT_GAME1_FINAL"
    assert after_final["playabilityAssessment"]["game_1_final"] is True
    assert after_final["selectionFingerprint"] == before_final["selectionFingerprint"]
