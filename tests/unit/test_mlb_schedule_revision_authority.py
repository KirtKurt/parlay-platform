from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
UNIT_TESTS = ROOT / "tests" / "unit"
for path in (HELLO_WORLD, UNIT_TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import inqsi_pull_history as history
import mlb_daily_per_game_lock_patch as lock_patch
import test_mlb_daily_per_game_lock as lock_fixtures


SLATE = lock_fixtures.SLATE


def _official_game(
    official_pk: str,
    provider_id: str,
    start: str,
    *,
    home: str = "Schedule Home",
    away: str = "Schedule Away",
):
    return {
        "game_id": provider_id,
        "game_key": f"mlb|{SLATE}|{away.lower()}|{home.lower()}|{provider_id}",
        "official_game_pk": official_pk,
        "official_game_id": f"mlb_statsapi:{official_pk}",
        "official_commence_time": start,
        "official_game_type": "R",
        "official_game_number": 1,
        "official_double_header": "N",
        "official_status": {"abstractGameState": "Preview"},
        "provider_event_id": provider_id,
        "provider_commence_time": start,
        "provider_start_drift_seconds": 0,
        "canonical_start_time_source": "MLB_STATS_API_EXACT_DATE",
        "commence_time": start,
        "home_team": home,
        "away_team": away,
        "books": {"fanduel": {"ml": {"home": -125, "away": 115}}},
    }


def _official_pull(at: str, games, suffix: str):
    pull = lock_fixtures.pull(at, games, suffix)
    pull["source"] = "the_odds_api"
    manifest = pull["provider_schedule_manifest"]
    manifest["source"] = "the_odds_api"
    manifest["fingerprint"] = history.provider_manifest_fingerprint(manifest)
    key = history._provider_manifest_key(manifest)
    binding = pull["provider_manifest_binding"]
    binding.update({
        "fingerprint": manifest["fingerprint"],
        "pk": key["PK"],
        "sk": key["SK"],
    })
    return pull


def _install_resolver(module):
    def resolve(pulls, slate):
        original_table = history.PULLS
        history.PULLS = module.TABLE
        try:
            return history.verified_full_slate_manifest(pulls, slate)
        finally:
            history.PULLS = original_table

    module.history.verified_full_slate_manifest = resolve
    module._latest_games_for_date = (
        lambda slate, pulls: copy.deepcopy(resolve(pulls, slate)["games"])
    )
    return resolve


def test_later_official_delay_moves_tminus45_but_preserves_roster_identity():
    early_game = _official_game(
        "992001",
        "provider-original",
        "2026-07-13T18:00:00+00:00",
    )
    delayed_game = _official_game(
        "992001",
        "provider-later-alias",
        "2026-07-13T19:00:00+00:00",
    )
    early = _official_pull("2026-07-13T16:00:00+00:00", [early_game], "schedule-early")
    delayed = _official_pull("2026-07-13T16:30:00+00:00", [delayed_game], "schedule-delay")
    module = lock_fixtures.build_module(
        [early, delayed],
        "2026-07-13T17:15:05+00:00",
    )
    resolve = _install_resolver(module)

    pending = module.run_lock(SLATE)
    resolved = resolve(module._pulls_for_date(SLATE), SLATE)

    assert pending["perGameLockProgress"]["stagedCount"] == 0
    assert resolved["membershipAuthorityPullId"] == "pull-schedule-early"
    assert resolved["scheduleAuthorityPullId"] == "pull-schedule-delay"
    assert resolved["games"][0]["game_id"] == "provider-original"
    assert resolved["games"][0]["provider_event_id"] == "provider-original"
    assert resolved["games"][0]["commence_time"] == "2026-07-13T19:00:00+00:00"

    module.now = lock_fixtures.dt("2026-07-13T18:15:05+00:00")
    locked = module.run_lock(SLATE)
    stage = lock_fixtures.staged_items(module)[0]

    assert locked["perGameLockProgress"]["canonicalCount"] == 1
    assert stage["game_id"] == "provider-original"
    assert stage["commence_time"] == "2026-07-13T19:00:00+00:00"
    assert stage["scheduled_lock_at_utc"] == "2026-07-13T18:15:00+00:00"
    assert stage["provider_manifest_authority"]["scheduleRevisionApplied"] is True
    assert stage["provider_manifest_authority"]["scheduleRevisionAuthority"]["pullId"] == "pull-schedule-delay"


def test_earlier_official_move_uses_revised_cutoff_and_never_stages_after_start():
    early_game = _official_game(
        "992002",
        "provider-earlier",
        "2026-07-13T19:00:00+00:00",
    )
    moved_game = copy.deepcopy(early_game)
    moved_game.update({
        "official_commence_time": "2026-07-13T18:00:00+00:00",
        "provider_commence_time": "2026-07-13T18:00:00+00:00",
        "commence_time": "2026-07-13T18:00:00+00:00",
    })
    early = _official_pull("2026-07-13T16:00:00+00:00", [early_game], "move-early")
    moved = _official_pull("2026-07-13T16:30:00+00:00", [moved_game], "move-revised")
    module = lock_fixtures.build_module(
        [early, moved],
        "2026-07-13T17:15:05+00:00",
    )
    _install_resolver(module)

    result = module.run_lock(SLATE)
    stage = lock_fixtures.staged_items(module)[0]

    assert result["perGameLockProgress"]["canonicalCount"] == 1
    assert stage["commence_time"] == "2026-07-13T18:00:00+00:00"
    assert stage["scheduled_lock_at_utc"] == "2026-07-13T17:15:00+00:00"
    assert stage["staged_at_utc"] < stage["commence_time"]
    assert stage["source_pull_at_utc"] <= stage["scheduled_lock_at_utc"]


def test_post_lock_schedule_revision_does_not_invalidate_or_rewrite_stage():
    original_game = _official_game(
        "992003",
        "provider-post-lock",
        "2026-07-13T18:00:00+00:00",
    )
    early = _official_pull("2026-07-13T16:00:00+00:00", [original_game], "post-lock-early")
    module = lock_fixtures.build_module(
        [early],
        "2026-07-13T17:15:05+00:00",
    )
    _install_resolver(module)
    module.run_lock(SLATE)
    before = copy.deepcopy(lock_fixtures.staged_items(module)[0])

    revised_game = copy.deepcopy(original_game)
    revised_game.update({
        "official_commence_time": "2026-07-13T19:00:00+00:00",
        "provider_commence_time": "2026-07-13T19:00:00+00:00",
        "commence_time": "2026-07-13T19:00:00+00:00",
    })
    revised = _official_pull("2026-07-13T17:30:00+00:00", [revised_game], "post-lock-revised")
    module.history.pulls.append(revised)
    module.now = lock_fixtures.dt("2026-07-13T17:31:00+00:00")

    result = module.run_lock(SLATE)
    after = lock_fixtures.staged_items(module)[0]
    status = result["perGameLockProgress"]["games"][0]

    assert status["state"] == "LOCKED_CANONICAL"
    assert status["commenceTime"] == "2026-07-13T19:00:00+00:00"
    assert after["stage_fingerprint"] == before["stage_fingerprint"]
    assert after["scheduled_lock_at_utc"] == "2026-07-13T17:15:00+00:00"
    assert after["data"]["row"]["predictedWinner"] == before["data"]["row"]["predictedWinner"]
    assert lock_patch._validate_stage(
        module,
        after,
        SLATE,
        revised_game,
        [revised_game],
        lock_patch._scoring_pulls(module, module._pulls_for_date(SLATE), revised_game),
    ) == []


def test_later_official_revision_with_changed_membership_is_rejected():
    first = [
        _official_game("992011", "provider-11", "2026-07-13T18:00:00+00:00", home="Home 11", away="Away 11"),
        _official_game("992012", "provider-12", "2026-07-13T19:00:00+00:00", home="Home 12", away="Away 12"),
    ]
    ambiguous = [
        _official_game("992011", "provider-11", "2026-07-13T20:00:00+00:00", home="Home 12", away="Away 12"),
        _official_game("992012", "provider-12", "2026-07-13T21:00:00+00:00", home="Home 11", away="Away 11"),
    ]
    early = _official_pull("2026-07-13T16:00:00+00:00", first, "membership-early")
    later = _official_pull("2026-07-13T16:30:00+00:00", ambiguous, "membership-ambiguous")
    module = lock_fixtures.build_module(
        [early, later],
        "2026-07-13T16:31:00+00:00",
        seed=False,
    )
    resolve = _install_resolver(module)

    resolved = resolve(module._pulls_for_date(SLATE), SLATE)

    assert resolved["scheduleAuthorityPullId"] == "pull-membership-early"
    assert [game["commence_time"] for game in resolved["games"]] == [
        "2026-07-13T18:00:00+00:00",
        "2026-07-13T19:00:00+00:00",
    ]
    rejected = [
        anomaly
        for anomaly in resolved["latestFeedAnomalies"]
        if anomaly.get("type") == "OFFICIAL_SCHEDULE_REVISION_MEMBERSHIP_REJECTED"
    ]
    assert rejected and rejected[0]["pullId"] == "pull-membership-ambiguous"
    assert rejected[0]["errors"] == [
        "ordered_teams_changed:992011",
        "ordered_teams_changed:992012",
    ]
