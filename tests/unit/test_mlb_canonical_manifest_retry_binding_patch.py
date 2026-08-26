from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_canonical_manifest_retry_binding_patch as patch


def _game(pk: int, away: str, home: str) -> dict:
    return {
        "game_id": f"provider-{pk}",
        "id": f"provider-{pk}",
        "game_key": f"mlb|{pk}",
        "official_game_pk": pk,
        "away_team": away,
        "home_team": home,
        "commence_time": "2026-08-26T23:00:00+00:00",
        "books": {},
    }


def _module(*, canonical_games: list[dict], original_result: dict):
    canonical_authority = {
        "version": "MLB-OFFICIAL-v1",
        "fingerprint": "canonical-authority-fingerprint",
        "officialGameCount": len(canonical_games),
    }
    canonical_manifest = {
        "version": "INQSI-PROVIDER-SCHEDULE-MANIFEST-v1",
        "fingerprint": "canonical-manifest-fingerprint",
        "scheduleAuthority": canonical_authority,
        "games": copy.deepcopy(canonical_games),
    }
    canonical_binding = {
        "pk": "PROVIDER_MANIFEST#mlb#2026-08-26",
        "sk": "OBSERVED#canonical",
    }
    stored_pull = {
        "pull_id": "canonical-pull",
        "sport": "mlb",
        "slate_date": "2026-08-26",
        "pulled_at": "2026-08-26T17:15:26+00:00",
        "games": copy.deepcopy(canonical_games),
        "provider_schedule_manifest": canonical_manifest,
        "provider_manifest_binding": canonical_binding,
    }
    calls: list[dict] = []

    class PullHistory:
        @staticmethod
        def store_pull(body):
            calls.append(copy.deepcopy(body))
            return {
                "ok": True,
                "deduped": True,
                "stored": {
                    "pk": "PULLS#mlb#2026-08-26",
                    "sk": "PULL#SLOT#2026-08-26T17:15:00+00:00",
                    "pull_id": "canonical-pull",
                    "provider_manifest": {
                        "version": canonical_manifest["version"],
                        "fingerprint": canonical_manifest["fingerprint"],
                        "game_count": len(canonical_games),
                        "pk": canonical_binding["pk"],
                        "sk": canonical_binding["sk"],
                        "immutable": True,
                        "full_provider_schedule": True,
                        "official_schedule_backed": True,
                        "official_schedule_authority_version": canonical_authority[
                            "version"
                        ],
                        "official_schedule_authority_fingerprint": canonical_authority[
                            "fingerprint"
                        ],
                        "official_schedule_game_count": len(canonical_games),
                    },
                },
                "pull": copy.deepcopy(stored_pull),
                "canonicalSlot": {
                    "slotStartUtc": "2026-08-26T17:15:00+00:00",
                    "canonicalPullId": "canonical-pull",
                    "canonicalPulledAtUtc": "2026-08-26T17:15:26+00:00",
                    "retryReturnedExistingCanonicalPull": True,
                },
            }

        @staticmethod
        def validate_provider_schedule_manifest(
            pull,
            expected_slate,
            *,
            verify_immutable_storage=False,
        ):
            assert pull["pull_id"] == "canonical-pull"
            assert expected_slate == "2026-08-26"
            assert verify_immutable_storage is True
            return []

        @staticmethod
        def pull_payload_fingerprint(pull):
            assert pull["pull_id"] == "canonical-pull"
            return "canonical-pull-fingerprint"

    def original(**_kwargs):
        return copy.deepcopy(original_result)

    module = SimpleNamespace(
        _store_canonical_pull_history=original,
        _canonical_games=lambda compact: copy.deepcopy(compact.get("games") or []),
        _safe_pull_id=lambda game_date, asof: f"candidate-{game_date}-{asof}",
        MLB_SCHED_INTERVAL_MINUTES=15,
        PLATFORM_VERSION="MLB_PREDICTIVE_PLATFORM_V1",
        SPORT_KEY="baseball_mlb",
        pull_history=PullHistory,
        official_schedule=SimpleNamespace(
            normalize_team=lambda value: " ".join(
                str(value or "").lower().strip().split()
            )
        ),
    )
    return module, calls


def _failed_retry() -> dict:
    return {
        "ok": False,
        "games": 2,
        "error": None,
        "retryReturnedExistingCanonicalPull": True,
        "providerManifestImmutable": True,
        "providerManifestFullSchedule": True,
        "providerManifestBound": False,
        "officialScheduleAuthorityBound": False,
    }


def _compact(games: list[dict]) -> dict:
    return {
        "games": copy.deepcopy(games),
        "provider_roster": {
            "source": "mlb_stats_api_exact_date_with_the_odds_api_event_crosswalk",
            "exactProviderIdMerge": True,
        },
        # This is deliberately a later observation with a different fingerprint.
        # It is independently validated by store_pull before the canonical slot
        # is returned, but it must not be compared byte-for-byte with the first
        # immutable slot authority.
        "official_schedule_authority": {
            "version": "MLB-OFFICIAL-v1",
            "fingerprint": "later-observation-fingerprint",
            "officialGameCount": len(games),
        },
    }


def test_same_membership_retry_binds_to_first_immutable_authority():
    games = [_game(824234, "Away One", "Home One"), _game(825039, "Away Two", "Home Two")]
    module, calls = _module(canonical_games=games, original_result=_failed_retry())

    installed = patch.install(module)
    result = module._store_canonical_pull_history(
        game_date="2026-08-26",
        asof="2026-08-26T17:22:23+00:00",
        run="manifest_binding_retry",
        compact=_compact(games),
    )

    assert installed["ok"] is True
    assert result["ok"] is True
    assert result["providerManifestBound"] is True
    assert result["officialScheduleAuthorityBound"] is True
    assert result["canonicalMembershipCompatible"] is True
    assert result["manifestBindingRepairApplied"] is True
    assert result["manifestBindingRepairVersion"] == patch.VERSION
    assert result["officialScheduleAuthorityFingerprint"] == (
        "canonical-authority-fingerprint"
    )
    assert result["canonicalPullId"] == "canonical-pull"
    assert result["retryReturnedExistingCanonicalPull"] is True
    assert result["immutablePredictionHistoryRewritten"] is False
    assert result["postStartPredictionCreated"] is False
    assert result["productionAuthorityChanged"] is False
    assert calls[0]["meta"]["official_schedule_authority"]["fingerprint"] == (
        "later-observation-fingerprint"
    )


def test_retry_fails_closed_when_official_game_membership_changes():
    canonical = [_game(824234, "Away One", "Home One"), _game(825039, "Away Two", "Home Two")]
    current = [_game(824234, "Away One", "Home One"), _game(999999, "Away Three", "Home Three")]
    module, _calls = _module(
        canonical_games=canonical,
        original_result=_failed_retry(),
    )
    patch.install(module)

    result = module._store_canonical_pull_history(
        game_date="2026-08-26",
        asof="2026-08-26T17:22:23+00:00",
        run="manifest_binding_retry",
        compact=_compact(current),
    )

    assert result["ok"] is False
    assert result["providerManifestBound"] is False
    assert result["manifestBindingRepairApplied"] is False
    assert "canonical_game_membership_changed" in result[
        "manifestBindingRepairError"
    ]
    assert result["immutablePredictionHistoryRewritten"] is False
    assert result["postStartPredictionCreated"] is False
    assert result["productionAuthorityChanged"] is False


def test_non_retry_failure_is_unchanged_and_install_is_idempotent():
    original = {"ok": False, "error": "unrelated", "games": 2}
    games = [_game(824234, "Away One", "Home One"), _game(825039, "Away Two", "Home Two")]
    module, calls = _module(canonical_games=games, original_result=original)

    first = patch.install(module)
    second = patch.install(module)
    result = module._store_canonical_pull_history(
        game_date="2026-08-26",
        asof="2026-08-26T17:22:23+00:00",
        run="unrelated",
        compact=_compact(games),
    )

    assert first["ok"] is True
    assert second["alreadyApplied"] is True
    assert result == original
    assert calls == []


def test_protected_entrypoint_installs_retry_patch():
    source = (HELLO_WORLD / "mlb_manual_pull_protected.py").read_text(
        encoding="utf-8"
    )
    assert "import mlb_canonical_manifest_retry_binding_patch" in source
    assert "canonicalManifestRetryBinding" in source
    assert "mlb_canonical_manifest_retry_binding_patch.install" in source
