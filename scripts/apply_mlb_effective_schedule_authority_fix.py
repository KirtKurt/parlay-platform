#!/usr/bin/env python3
"""Apply the MLB effective-schedule authority and lifecycle-storage repair.

The migration is intentionally idempotent. It keeps immutable roster membership
strict while allowing a verified compatible official-schedule revision to
supply the effective per-game rows. It also classifies
``MISSED_NOT_BACKFILLED`` as lifecycle evidence rather than a pre-lock write
candidate.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COVERAGE = ROOT / "hello_world" / "mlb_slate_coverage_patch.py"
FINALIZER = ROOT / "hello_world" / "mlb_locked_prediction_storage_finalizer_v1.py"
PUBLIC_TEST = ROOT / "tests" / "unit" / "test_mlb_public_per_game_authority.py"
POSTCUTOFF_TEST = ROOT / "tests" / "unit" / "test_mlb_postcutoff_storage_contract.py"

OLD_COVERAGE = '''    games = reader(full_pull, slate)
    if not isinstance(games, list):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:games_not_list")
    resolved_games = list(resolved.get("games") or [])
    if games != resolved_games:
        raise RuntimeError("MLB_VERIFIED_FULL_SLATE_MANIFEST_INVALID:resolved_games_mismatch")
'''

NEW_COVERAGE = '''    membership_games = reader(full_pull, slate)
    if not isinstance(membership_games, list):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:games_not_list")
    resolved_games = list(resolved.get("games") or [])
    membership_ids = [game_identity(game) for game in membership_games]
    resolved_ids = [game_identity(game) for game in resolved_games]
    # ``verified_full_slate_manifest`` deliberately separates immutable roster
    # membership from later, compatible official-schedule revisions. The
    # effective rows may therefore differ in start time or other schedule
    # metadata while retaining the exact same game identities. Comparing the
    # entire dictionaries falsely rejects that valid overlay after schedule
    # revisions or completed-game status hydration. Membership remains strict.
    if membership_ids != resolved_ids:
        raise RuntimeError(
            "MLB_VERIFIED_FULL_SLATE_MANIFEST_INVALID:"
            "resolved_games_membership_mismatch"
        )
    games = resolved_games
'''

OLD_COUNT = '''    if declared_count != len(games) or len(declared_games) != len(games):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:game_count_mismatch")
'''

NEW_COUNT = '''    if (
        declared_count != len(membership_games)
        or len(declared_games) != len(membership_games)
        or len(games) != len(membership_games)
    ):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:game_count_mismatch")
'''

OLD_VERSION = 'VERSION = "MLB-LOCKED-PREDICTION-STORAGE-FINALIZER-v5-lifecycle-aware"'
NEW_VERSION = 'VERSION = "MLB-LOCKED-PREDICTION-STORAGE-FINALIZER-v6-effective-schedule-lifecycle"'
OLD_STATUS = '''    "MISSED_LOCK",
    "POSTPONED",'''
NEW_STATUS = '''    "MISSED_LOCK",
    "MISSED_NOT_BACKFILLED",
    "POSTPONED",'''

PUBLIC_TEST_MARKER = "def test_verified_effective_schedule_revision_preserves_roster_membership"
PUBLIC_TESTS = r'''


def test_verified_effective_schedule_revision_preserves_roster_membership(monkeypatch):
    """Compatible official start-time updates must not invalidate roster authority."""

    engine = _engine([])
    inqsi_pull_history.PULLS = engine.history.PULLS
    membership_games = inqsi_pull_history.provider_manifest_games_for_lock(
        PULLS[0],
        SLATE,
    )
    effective_games = copy.deepcopy(membership_games)
    effective_games[0]["commence_time"] = "2026-07-17T23:30:00Z"
    effective_games[0]["canonical_start_time_source"] = "MLB_STATS_API_EXACT_DATE"

    def resolved_manifest(pulls, slate):
        assert pulls == PULLS
        assert slate == SLATE
        return {
            "version": "MLB-VERIFIED-FULL-SLATE-ROSTER-v-test",
            "fullAuthorityPull": PULLS[0],
            "games": copy.deepcopy(effective_games),
            "fullSlateGameCount": len(effective_games),
            "latestFeedGameCount": len(effective_games),
            "latestFeedContracted": False,
            "scheduleRevisionApplied": True,
            "immutableReadbackVerified": True,
        }

    monkeypatch.setattr(
        engine.history,
        "verified_full_slate_manifest",
        staticmethod(resolved_manifest),
        raising=False,
    )
    inqsi_pull_history.PULLS = engine.history.PULLS

    games, authority = coverage._provider_manifest_for_public(
        engine,
        slate_lock,
        PULLS,
        SLATE,
    )

    assert [coverage.game_identity(game) for game in games] == [
        coverage.game_identity(game) for game in membership_games
    ]
    assert games[0]["commence_time"] == "2026-07-17T23:30:00Z"
    assert games[0]["canonical_start_time_source"] == "MLB_STATS_API_EXACT_DATE"
    assert authority["providerManifestValidated"] is True
    assert authority["verifiedFullSlateGameCount"] == 2
    assert authority["durableRosterImmutableReadbackVerified"] is True


def test_verified_effective_schedule_revision_rejects_membership_change(monkeypatch):
    engine = _engine([])
    inqsi_pull_history.PULLS = engine.history.PULLS
    effective_games = inqsi_pull_history.provider_manifest_games_for_lock(
        PULLS[0],
        SLATE,
    )
    effective_games = copy.deepcopy(effective_games)
    effective_games[1]["game_id"] = "unexpected-game"

    monkeypatch.setattr(
        engine.history,
        "verified_full_slate_manifest",
        staticmethod(
            lambda pulls, slate: {
                "version": "MLB-VERIFIED-FULL-SLATE-ROSTER-v-test",
                "fullAuthorityPull": PULLS[0],
                "games": copy.deepcopy(effective_games),
                "fullSlateGameCount": len(effective_games),
                "latestFeedGameCount": len(effective_games),
                "immutableReadbackVerified": True,
            }
        ),
        raising=False,
    )
    inqsi_pull_history.PULLS = engine.history.PULLS

    try:
        coverage._provider_manifest_for_public(
            engine,
            slate_lock,
            PULLS,
            SLATE,
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected effective-schedule membership mismatch")

    assert "resolved_games_membership_mismatch" in message
'''


def _replace_once(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return False
    if old not in text:
        raise RuntimeError(f"{label} migration anchor missing in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def _append_public_tests() -> bool:
    text = PUBLIC_TEST.read_text(encoding="utf-8")
    if PUBLIC_TEST_MARKER in text:
        return False
    PUBLIC_TEST.write_text(text.rstrip() + PUBLIC_TESTS + "\n", encoding="utf-8")
    return True


def _patch_postcutoff_test() -> bool:
    text = POSTCUTOFF_TEST.read_text(encoding="utf-8")
    if '"gameId": "missed-not-backfilled"' in text:
        return False
    replacements = (
        (
            '        "gameCount": 2,\n        "allGamesPredicted": False,',
            '        "gameCount": 3,\n        "allGamesPredicted": False,',
        ),
        (
            '            {\n                "gameId": "terminal-no-data",',
            '            {\n'
            '                "gameId": "missed-not-backfilled",\n'
            '                "predictedWinner": None,\n'
            '                "predictedSide": None,\n'
            '                "lockStatus": "MISSED_NOT_BACKFILLED",\n'
            '                "officialPredictionStatus": "MISSED_NOT_BACKFILLED",\n'
            '                "recommendationStatus": "MISSED_NOT_BACKFILLED",\n'
            '                "displayGroup": "lock_failure",\n'
            '                "perGameCanonicalLock": {"status": "MISSED_NOT_BACKFILLED"},\n'
            '            },\n'
            '            {\n'
            '                "gameId": "terminal-no-data",',
        ),
        (
            '    assert result["preLockStorageLifecycleSkippedCount"] == 2\n'
            '    assert result["preLockStorageDispositionCount"] == 2',
            '    assert result["preLockStorageLifecycleSkippedCount"] == 3\n'
            '    assert result["preLockStorageDispositionCount"] == 3',
        ),
        (
            '        "LOCKED_NO_PREDICTION_DATA",\n        "MISSED_LOCK",',
            '        "LOCKED_NO_PREDICTION_DATA",\n'
            '        "MISSED_LOCK",\n'
            '        "MISSED_NOT_BACKFILLED",',
        ),
    )
    for old, new in replacements:
        if old not in text:
            raise RuntimeError(f"post-cutoff test migration anchor missing: {old!r}")
        text = text.replace(old, new, 1)
    POSTCUTOFF_TEST.write_text(text, encoding="utf-8")
    return True


def apply() -> dict[str, bool]:
    return {
        "coverageAuthorityUpdated": _replace_once(
            COVERAGE,
            OLD_COVERAGE,
            NEW_COVERAGE,
            "effective schedule authority",
        ),
        "coverageCountUpdated": _replace_once(
            COVERAGE,
            OLD_COUNT,
            NEW_COUNT,
            "effective schedule count",
        ),
        "finalizerVersionUpdated": _replace_once(
            FINALIZER,
            OLD_VERSION,
            NEW_VERSION,
            "lifecycle finalizer version",
        ),
        "finalizerStatusUpdated": _replace_once(
            FINALIZER,
            OLD_STATUS,
            NEW_STATUS,
            "MISSED_NOT_BACKFILLED lifecycle status",
        ),
        "publicAuthorityTestsAdded": _append_public_tests(),
        "postcutoffTestUpdated": _patch_postcutoff_test(),
    }


def main() -> None:
    result = apply()
    print(
        "MLB effective schedule authority fix applied: "
        + ", ".join(f"{key}={value}" for key, value in result.items())
    )


if __name__ == "__main__":
    main()
