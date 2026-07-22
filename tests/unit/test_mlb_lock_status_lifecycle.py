from __future__ import annotations

import copy

from tests.unit.test_mlb_daily_per_game_lock import (
    FakeTable,
    G1,
    SLATE,
    _fallback_to_provider_transition,
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


def _playable_with_confirmed_late_sources(row):
    _playable(row)
    snapshot = row.setdefault("fundamentalsSnapshot", {})
    snapshot.setdefault("sourceStatuses", {}).update({
        "confirmed_lineups": "CONNECTED",
        "injuries_late_scratches_news": "CONNECTED",
    })
    context = row.setdefault("advanced_context", {})
    context["confirmed_lineups"] = {
        "source_status": "CONNECTED",
        "home_lineup_confirmed": True,
        "away_lineup_confirmed": True,
    }
    context["injuries_late_scratches_news"] = {
        "source_status": "CONNECTED",
        "home_key_injuries": [],
        "away_key_injuries": [],
        "late_scratch_flags": [],
        "pitcher_change_flag": None,
    }


def _put_provider_outcome(module, game_row, provider_id, *, reverse_teams=False):
    module.OUTCOMES = FakeTable()
    module.OUTCOMES.put_item(Item={
        "PK": f"OUTCOME#mlb#{SLATE}",
        "SK": f"GAME_ID#{provider_id}",
        "game_id": provider_id,
        "official_game_pk": game_row["official_game_pk"],
        "home_team": (
            game_row["away_team"] if reverse_teams else game_row["home_team"]
        ),
        "away_team": (
            game_row["home_team"] if reverse_teams else game_row["away_team"]
        ),
        "completed": True,
    })


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


def test_missed_readiness_checkpoint_never_backfills_later_evidence():
    source = pull("2026-07-13T17:05:00+00:00", [G1], "late-for-t60")
    module = build_module([source], "2026-07-13T17:05:05+00:00")

    result = module.run_lock(SLATE)
    status = result["perGameLockProgress"]["games"][0]
    record = next(
        item
        for item in module.TABLE.items.values()
        if item.get("record_type") == patch.READINESS_RECORD_TYPE
        and item.get("checkpoint") == "T_MINUS_60"
    )

    assert status["readiness"]["tMinus60"]["status"] == "MISSED"
    assert status["readiness"]["tMinus60"]["candidateReady"] is False
    assert record["evidence_cutoff_at_utc"] == "2026-07-13T17:00:00+00:00"
    assert record["candidate_source_at_utc"] is None


def test_checkpoint_rejects_candidate_persisted_after_scheduled_cutoff():
    source = pull("2026-07-13T16:59:00+00:00", [G1], "pre-t60-source")
    module = build_module(
        [source],
        "2026-07-13T17:00:45+00:00",
        seed=False,
    )
    persist_candidate(
        module,
        G1,
        source,
        persisted_at="2026-07-13T17:00:30+00:00",
    )

    result = module.run_lock(SLATE)
    status = result["perGameLockProgress"]["games"][0]

    assert status["readiness"]["tMinus60"]["status"] == "AT_RISK"
    assert status["readiness"]["tMinus60"]["candidateReady"] is False
    assert (
        "no_persisted_user_visible_platform_prelock_prediction_at_or_before_cutoff"
        in status["readiness"]["tMinus60"]["blockingReasons"]
    )


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
    assert ignored["playable"] is False
    assert ignored["blocked"] is True
    assert ignored["wagerReleaseBlocked"] is True
    assert "T_MINUS_30:selection_fingerprint_mismatch" in ignored[
        "playabilityAssessmentValidationErrors"
    ]


def test_missing_due_tminus15_assessment_fails_status_release_closed():
    source = pull("2026-07-13T17:15:00+00:00", [G1], "locked-playable")
    module = build_module([source], "2026-07-13T17:15:05+00:00", seed=False)
    persist_candidate(module, G1, source, mutate=_playable)
    module.run_lock(SLATE)
    locked_row = copy.deepcopy(staged_items(module)[0]["data"]["row"])

    late = pull("2026-07-13T17:29:00+00:00", [G1], "late-playability")
    module.history.pulls.append(late)
    persist_candidate(module, G1, late, mutate=_playable)
    module.now = dt("2026-07-13T17:30:05+00:00")
    module.run_lock(SLATE)

    # Do not run the writer at T-15. The read endpoint must detect that the
    # newest due checkpoint is absent and block release without changing the
    # already-immutable selection.
    module.now = dt("2026-07-13T17:46:00+00:00")
    payload = module._status_payload(SLATE)
    status = payload["perGameStatus"][0]

    assert status["predictedWinner"] == locked_row["predictedWinner"]
    assert status["predictedSide"] == locked_row["predictedSide"]
    assert status["selectionFingerprint"] == locked_row["lastPrelockSelectionFingerprint"]
    assert status["lockedPrediction"] is True
    assert status["playable"] is False
    assert status["blocked"] is True
    assert status["wagerReleaseBlocked"] is True
    assert status["requiredPlayabilityCheckpoint"] == "T_MINUS_15"
    assert status["playabilityAssessment"] is None
    assert status["playabilityAssessmentValidationErrors"] == [
        "T_MINUS_15:required_assessment_missing"
    ]
    assert payload["lockedPredictionCount"] == 1
    assert payload["lockedStatusCount"] == 1
    assert payload["noPredictionDataCount"] == 0
    assert payload["canonicalPredictionComplete"] is True
    assert payload["lockStatusComplete"] is True
    assert payload["playabilityValidationErrorCount"] == 1
    assert payload["operationalDefect"] is True


def test_tminus30_playability_rejects_evidence_persisted_after_checkpoint():
    source = pull("2026-07-13T17:15:00+00:00", [G1], "locked-t30-boundary")
    module = build_module([source], "2026-07-13T17:15:05+00:00", seed=False)
    persist_candidate(module, G1, source, mutate=_playable)
    assert module.run_lock(SLATE)["locked"] is True

    late = pull("2026-07-13T17:29:00+00:00", [G1], "persisted-after-t30")
    module.history.pulls.append(late)
    persist_candidate(
        module,
        G1,
        late,
        mutate=_playable,
        persisted_at="2026-07-13T17:30:30+00:00",
    )
    module.now = dt("2026-07-13T17:30:45+00:00")

    module.run_lock(SLATE)
    assessment = next(
        item
        for item in module.TABLE.items.values()
        if item.get("record_type") == patch.RELEASE_ASSESSMENT_RECORD_TYPE
        and item.get("checkpoint") == "T_MINUS_30"
    )

    assert assessment["evidence_cutoff_at_utc"] == "2026-07-13T17:30:00+00:00"
    assert assessment["blocked"] is True
    assert "NO_POST_LOCK_PLAYABILITY_EVIDENCE" in assessment["reasons"]


def test_readiness_write_failure_cannot_block_a_due_game_lock():
    later = game("readiness-later", "2026-07-13T18:15:00+00:00")
    source = pull("2026-07-13T17:15:00+00:00", [G1, later], "readiness-failure")
    module = build_module([source], "2026-07-13T17:15:05+00:00")
    original_put = module.TABLE.put_item

    def put_with_readiness_failure(Item, **kwargs):
        if (
            Item.get("record_type") == patch.READINESS_RECORD_TYPE
            and Item.get("game_id") == later["game_id"]
        ):
            raise RuntimeError("injected readiness write failure")
        return original_put(Item=Item, **kwargs)

    module.TABLE.put_item = put_with_readiness_failure
    result = module.run_lock(SLATE)

    g1_status = next(
        row
        for row in result["perGameLockProgress"]["games"]
        if row["gameId"] == G1["game_id"]
    )
    assert g1_status["state"] == "LOCKED_CANONICAL"
    assert any(
        error.get("gameIdentity") == patch.game_identity(later)
        for error in result["lifecycleDiagnosticErrors"]
    )


def test_stage_failure_for_one_game_does_not_block_another_due_game():
    same_start = game("stage-failure-peer", G1["commence_time"])
    source = pull("2026-07-13T17:15:00+00:00", [G1, same_start], "stage-isolation")
    module = build_module([source], "2026-07-13T17:15:05+00:00")
    original_put = module.TABLE.put_item

    def put_with_stage_failure(Item, **kwargs):
        if (
            Item.get("record_type") == patch.STAGE_RECORD_TYPE
            and Item.get("game_id") == G1["game_id"]
        ):
            raise RuntimeError("injected stage write failure")
        return original_put(Item=Item, **kwargs)

    module.TABLE.put_item = put_with_stage_failure
    result = module.run_lock(SLATE)

    peer = next(
        row
        for row in result["perGameLockProgress"]["games"]
        if row["gameId"] == same_start["game_id"]
    )
    assert result["ok"] is False
    assert peer["state"] == "LOCKED_CANONICAL"
    assert any(
        item.get("game_id") == same_start["game_id"]
        for item in staged_items(module)
    )


def test_terminal_outcome_failure_for_one_game_does_not_block_valid_peer():
    missing = game("missing-candidate", G1["commence_time"])
    valid = game("valid-peer", G1["commence_time"])
    source = pull("2026-07-13T17:15:00+00:00", [missing, valid], "outcome-isolation")
    module = build_module([source], "2026-07-13T17:15:05+00:00", seed=False)
    persist_candidate(module, valid, source)
    original_put = module.TABLE.put_item

    def put_with_outcome_failure(Item, **kwargs):
        if (
            Item.get("record_type") == patch.LOCK_OUTCOME_RECORD_TYPE
            and Item.get("game_id") == missing["game_id"]
        ):
            raise RuntimeError("injected terminal outcome write failure")
        return original_put(Item=Item, **kwargs)

    module.TABLE.put_item = put_with_outcome_failure
    result = module.run_lock(SLATE)

    valid_status = next(
        row
        for row in result["perGameLockProgress"]["games"]
        if row["gameId"] == valid["game_id"]
    )
    assert result["ok"] is False
    assert valid_status["state"] == "LOCKED_CANONICAL"
    assert any(
        failure.get("reason") == "TERMINAL_LOCK_OUTCOME_WRITE_FAILED"
        for failure in result["failures"]
    )


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
    immediately_blocked = next(
        row for row in module._status_payload(SLATE)["perGameStatus"] if row["gameId"] == "dh-2"
    )
    assert immediately_blocked["blocked"] is True
    assert immediately_blocked["playabilityAssessment"]["checkpoint"] == "EVENT_GAME1_PENDING"
    assert "DOUBLEHEADER_GAME1_NOT_FINAL" in immediately_blocked["playabilityBlockReasons"]

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
        "SK": f"GAME_ID#{dh1['game_id']}",
        "game_id": dh1["game_id"],
        "completed": True,
    })
    final_refresh = pull("2026-07-13T21:31:00+00:00", [dh1, dh2], "dh-final-refresh")
    module.history.pulls.append(final_refresh)
    persist_candidate(
        module,
        dh2,
        final_refresh,
        mutate=_playable_with_confirmed_late_sources,
    )
    module.now = dt("2026-07-13T21:31:05+00:00")
    module.run_lock(SLATE)
    after_final = next(
        row for row in module._status_payload(SLATE)["perGameStatus"] if row["gameId"] == "dh-2"
    )
    assert after_final["playable"] is True
    assert after_final["playabilityAssessment"]["checkpoint"] == "EVENT_GAME1_FINAL"
    assert after_final["playabilityAssessment"]["game_1_final"] is True
    assert after_final["selectionFingerprint"] == before_final["selectionFingerprint"]


def test_stats_fallback_game1_provider_outcome_releases_game2_without_selection_change():
    def official_game(game_pk, start):
        row = game(f"mlb_statsapi:{game_pk}", start)
        row.update({
            "game_key": f"mlb|{SLATE}|same away|same home|statsapi:{game_pk}",
            "official_game_pk": str(game_pk),
            "official_game_id": f"mlb_statsapi:{game_pk}",
            "official_commence_time": start,
            "official_game_number": 1 if str(game_pk).endswith("1") else 2,
            "official_double_header": "Y",
            "canonical_start_time_source": "MLB_STATS_API_EXACT_DATE",
            "home_team": "Same Home",
            "away_team": "Same Away",
            "books": {},
        })
        return row

    def provider_game(canonical, provider_id):
        row = copy.deepcopy(canonical)
        row.update({
            "game_id": provider_id,
            "game_key": f"mlb|{SLATE}|same away|same home|{provider_id}",
            "provider_event_id": provider_id,
            "provider_commence_time": canonical["commence_time"],
            "provider_start_drift_seconds": 0,
            "books": {"fanduel": {"ml": {"home": -125, "away": 115}}},
        })
        return row

    game_one = official_game("991101", "2026-07-13T20:00:00+00:00")
    game_two = official_game("991102", "2026-07-13T22:00:00+00:00")
    provider_one = provider_game(game_one, "provider-dh-1")
    provider_two = provider_game(game_two, "provider-dh-2")
    official_pull = pull(
        "2026-07-13T19:00:00+00:00",
        [game_one, game_two],
        "official-doubleheader",
    )
    provider_pull = pull(
        "2026-07-13T19:15:00+00:00",
        [provider_one, provider_two],
        "provider-doubleheader",
    )
    module = build_module(
        [official_pull, provider_pull],
        "2026-07-13T19:15:05+00:00",
        seed=False,
    )
    module._latest_games_for_date = lambda slate, pulls: copy.deepcopy(
        [game_one, game_two]
    )

    def bind_identity(game_pk, provider_id, *, confirmed=False):
        def mutate(row):
            if confirmed:
                _playable_with_confirmed_late_sources(row)
            else:
                _playable(row)
            row["officialGamePk"] = str(game_pk)
            row["officialGameId"] = f"mlb_statsapi:{game_pk}"
            row["providerEventId"] = provider_id

        return mutate

    persist_candidate(
        module,
        provider_one,
        provider_pull,
        mutate=bind_identity("991101", "provider-dh-1"),
    )
    module.run_lock(SLATE)

    game_two_lock_pull = pull(
        "2026-07-13T21:15:00+00:00",
        [provider_one, provider_two],
        "provider-doubleheader-game2-lock",
    )
    module.history.pulls.append(game_two_lock_pull)
    persist_candidate(
        module,
        provider_two,
        game_two_lock_pull,
        mutate=bind_identity("991102", "provider-dh-2"),
    )
    module.now = dt("2026-07-13T21:15:05+00:00")
    module.run_lock(SLATE)
    pending = next(
        row
        for row in module._status_payload(SLATE)["perGameStatus"]
        if row["gameId"] == game_two["game_id"]
    )
    immutable_selection = (
        pending["predictedWinner"],
        pending["predictedSide"],
        pending["selectionFingerprint"],
    )
    assert pending["playabilityAssessment"]["checkpoint"] == "EVENT_GAME1_PENDING"
    assert pending["blocked"] is True

    module.now = dt("2026-07-13T21:30:05+00:00")
    module.run_lock(SLATE)

    module.OUTCOMES = FakeTable()
    module.OUTCOMES.put_item(Item={
        "PK": f"OUTCOME#mlb#{SLATE}",
        "SK": "GAME_ID#provider-dh-1",
        "game_id": "provider-dh-1",
        "official_game_pk": "991101",
        "home_team": "Same Home",
        "away_team": "Same Away",
        "completed": True,
    })
    final_refresh = pull(
        "2026-07-13T21:31:00+00:00",
        [provider_one, provider_two],
        "provider-doubleheader-final-refresh",
    )
    module.history.pulls.append(final_refresh)
    persist_candidate(
        module,
        provider_two,
        final_refresh,
        mutate=bind_identity("991102", "provider-dh-2", confirmed=True),
    )
    assert patch._game_outcome_aliases(module, SLATE, game_one) == [
        game_one["game_id"],
        "provider-dh-1",
    ]
    assert patch._game_final(module, SLATE, game_one) is True
    module.now = dt("2026-07-13T21:31:05+00:00")
    module.run_lock(SLATE)
    checkpoints = [
        item.get("checkpoint")
        for item in module.TABLE.items.values()
        if item.get("record_type") == patch.RELEASE_ASSESSMENT_RECORD_TYPE
        and item.get("game_id") == game_two["game_id"]
    ]
    assert "EVENT_GAME1_FINAL" in checkpoints, checkpoints
    released = next(
        row
        for row in module._status_payload(SLATE)["perGameStatus"]
        if row["gameId"] == game_two["game_id"]
    )

    assert released["playabilityAssessment"]["checkpoint"] == "EVENT_GAME1_FINAL"
    assert released["playabilityAssessment"]["game_1_final"] is True
    assert released["playable"] is True
    assert released["blocked"] is False
    assert (
        released["predictedWinner"],
        released["predictedSide"],
        released["selectionFingerprint"],
    ) == immutable_selection


def test_official_crosswalk_retains_provider_alias_through_later_contraction():
    module, canonical, provider = _fallback_to_provider_transition(
        candidate_has_official_identity=True,
    )
    module.history.pulls.append(
        pull(
            "2026-07-13T17:16:00+00:00",
            [canonical],
            "provider-missing-contraction",
        )
    )
    _put_provider_outcome(module, canonical, provider["provider_event_id"])

    assert patch._game_outcome_aliases(module, SLATE, canonical) == [
        canonical["game_id"],
        provider["provider_event_id"],
    ]
    assert patch._game_final(module, SLATE, canonical) is True


def test_official_crosswalk_fails_closed_when_one_game_changes_provider_alias():
    module, canonical, provider = _fallback_to_provider_transition(
        candidate_has_official_identity=True,
    )
    changed = copy.deepcopy(provider)
    changed.update({
        "game_id": "conflicting-provider-alias",
        "game_key": "mlb|provider|conflicting-alias",
        "provider_event_id": "conflicting-provider-alias",
    })
    module.history.pulls.append(
        pull(
            "2026-07-13T17:16:00+00:00",
            [changed],
            "conflicting-provider-alias",
        )
    )
    _put_provider_outcome(module, canonical, provider["provider_event_id"])

    assert patch._game_outcome_aliases(module, SLATE, canonical) is None
    assert patch._game_final(module, SLATE, canonical) is False


def test_official_crosswalk_fails_closed_when_provider_alias_changes_game_pk():
    module, canonical, provider = _fallback_to_provider_transition(
        candidate_has_official_identity=True,
    )
    other_game = copy.deepcopy(provider)
    other_game.update({
        "official_game_pk": "991099",
        "official_game_id": "mlb_statsapi:991099",
        "home_team": "Other Home",
        "away_team": "Other Away",
    })
    module.history.pulls.append(
        pull(
            "2026-07-13T17:16:00+00:00",
            [other_game],
            "provider-alias-reused-for-other-game-pk",
        )
    )
    _put_provider_outcome(module, canonical, provider["provider_event_id"])

    assert patch._game_outcome_aliases(module, SLATE, canonical) is None
    assert patch._game_final(module, SLATE, canonical) is False


def test_official_crosswalk_fails_closed_when_immutable_manifest_readback_fails():
    module, canonical, provider = _fallback_to_provider_transition(
        candidate_has_official_identity=True,
    )
    module._pulls_for_date(SLATE)
    provider_pull = module.history.pulls[-1]
    key = patch.history_contract._provider_manifest_key(
        provider_pull["provider_schedule_manifest"]
    )
    module.TABLE.delete(key["PK"], key["SK"])
    _put_provider_outcome(module, canonical, provider["provider_event_id"])

    assert patch._game_outcome_aliases(module, SLATE, canonical) is None
    assert patch._game_final(module, SLATE, canonical) is False


def test_caller_injected_provider_alias_without_stage_or_manifest_fails_closed():
    canonical = game("mlb_statsapi:991777", "2026-07-13T18:00:00+00:00")
    canonical.update({
        "game_key": "mlb|statsapi|991777",
        "official_game_pk": "991777",
        "official_game_id": "mlb_statsapi:991777",
        "provider_event_id": "unproved-provider-alias",
        "home_team": "Injected Home",
        "away_team": "Injected Away",
    })
    module = build_module([], "2026-07-13T17:16:00+00:00", seed=False)
    _put_provider_outcome(module, canonical, "unproved-provider-alias")

    assert patch._game_outcome_aliases(module, SLATE, canonical) is None
    assert patch._game_final(module, SLATE, canonical) is False


def test_provider_alias_fails_closed_when_persisted_stage_authority_is_tampered():
    module, canonical, provider = _fallback_to_provider_transition(
        candidate_has_official_identity=True,
    )
    assert module.run_lock(SLATE)["locked"] is True
    stage = staged_items(module)[0]
    stage["source_pull_id"] = "tampered-source-pull"
    # Recomputing the shallow stage fingerprint must not bypass the persisted
    # candidate/manifest/source-window authority-chain validation.
    stage["stage_fingerprint"] = patch._stage_fingerprint(stage)
    _put_provider_outcome(module, canonical, provider["provider_event_id"])

    assert patch._game_outcome_aliases(module, SLATE, canonical) is None
    assert patch._game_final(module, SLATE, canonical) is False


def test_official_crosswalk_fails_closed_on_reversed_ordered_teams():
    module, canonical, provider = _fallback_to_provider_transition(
        candidate_has_official_identity=True,
    )
    reversed_teams = copy.deepcopy(provider)
    reversed_teams["home_team"], reversed_teams["away_team"] = (
        provider["away_team"],
        provider["home_team"],
    )
    module.history.pulls.append(
        pull(
            "2026-07-13T17:16:00+00:00",
            [reversed_teams],
            "reversed-ordered-teams",
        )
    )
    _put_provider_outcome(module, canonical, provider["provider_event_id"])

    assert patch._game_outcome_aliases(module, SLATE, canonical) is None
    assert patch._game_final(module, SLATE, canonical) is False
