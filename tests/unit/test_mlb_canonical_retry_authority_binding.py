from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_manual_pull


GAME_DATE = "2026-08-26"
CANONICAL_AT = "2026-08-26T17:15:27.196414+00:00"
RETRY_AT = "2026-08-26T17:17:43.767563+00:00"
SLOT_AT = "2026-08-26T17:15:00+00:00"


def _game():
    return {
        "game_id": "provider-game-1",
        "id": "provider-game-1",
        "game_key": "mlb|2026-08-26|away club|home club",
        "home_team": "Home Club",
        "away_team": "Away Club",
        "commence_time": "2026-08-26T23:05:00+00:00",
        "provider_sport_key": "baseball_mlb",
        "books": {"fanduel": {"ml": {"home": -120, "away": 110}}},
    }


def _persisted_pull():
    authority = {
        "version": "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date",
        "source": "MLB Stats API exact-date schedule",
        "slateDate": GAME_DATE,
        "observedAtUtc": CANONICAL_AT,
        "officialGameCount": 1,
        "authoritativeRoster": True,
        "authoritativeStartTimes": True,
        "verified": True,
        "fingerprint": "persisted-authority-fingerprint",
    }
    return {
        "pull_id": "first-slot-pull",
        "sport": "mlb",
        "slate_date": GAME_DATE,
        "pulled_at": CANONICAL_AT,
        "source": "the_odds_api",
        "games": [_game()],
        "provider_schedule_manifest": {
            "scheduleAuthority": authority,
        },
    }


def _stored_result(persisted_pull):
    authority = persisted_pull["provider_schedule_manifest"][
        "scheduleAuthority"
    ]
    return {
        "ok": True,
        "stored": {
            "pk": f"PULLS#mlb#{GAME_DATE}",
            "sk": f"PULL#SLOT#{SLOT_AT}",
            "pull_id": "first-slot-pull",
            "provider_manifest": {
                "version": "INQSI-PROVIDER-SCHEDULE-MANIFEST-v1",
                "fingerprint": "persisted-manifest-fingerprint",
                "game_count": 1,
                "pk": f"PROVIDER_MANIFEST#mlb#{GAME_DATE}",
                "sk": (
                    f"OBSERVED#{CANONICAL_AT}#PULL#first-slot-pull"
                ),
                "immutable": True,
                "full_provider_schedule": True,
                "official_schedule_backed": True,
                "official_schedule_authority_version": authority[
                    "version"
                ],
                "official_schedule_authority_fingerprint": authority[
                    "fingerprint"
                ],
                "official_schedule_game_count": 1,
            },
        },
        "pull": copy.deepcopy(persisted_pull),
        "deduped": True,
        "canonicalSlot": {
            "slotStartUtc": SLOT_AT,
            "canonicalPullId": "first-slot-pull",
            "canonicalPulledAtUtc": CANONICAL_AT,
            "retryReturnedExistingCanonicalPull": True,
        },
    }


def _retry_compact():
    return {
        "games": [_game()],
        "official_schedule_authority": {
            "version": (
                "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date"
            ),
            "slateDate": GAME_DATE,
            "observedAtUtc": RETRY_AT,
            "officialGameCount": 1,
            "authoritativeRoster": True,
            "authoritativeStartTimes": True,
            "verified": True,
            # A fresh observation legitimately has a different proof
            # fingerprint; it must not replace the slot authority.
            "fingerprint": "retry-observation-fingerprint",
        },
    }


def test_same_slot_retry_binds_to_persisted_manifest_not_fresh_fingerprint(
    monkeypatch,
):
    persisted_pull = _persisted_pull()
    calls = []

    monkeypatch.setattr(
        mlb_manual_pull.pull_history,
        "store_pull",
        lambda _body: _stored_result(persisted_pull),
    )

    def validate(pull, slate, verify_immutable_storage=False):
        calls.append((pull, slate, verify_immutable_storage))
        return []

    monkeypatch.setattr(
        mlb_manual_pull.pull_history,
        "validate_provider_schedule_manifest",
        validate,
    )

    result = mlb_manual_pull._store_canonical_pull_history(
        game_date=GAME_DATE,
        asof=RETRY_AT,
        run="same_slot_retry",
        compact=_retry_compact(),
    )

    assert result["ok"] is True
    assert result["providerManifestBound"] is True
    assert result["officialScheduleAuthorityBound"] is True
    assert result["sameSlotRetryAuthorityRebound"] is True
    assert result["providerManifestValidationErrors"] == []
    assert result["canonicalPullId"] == "first-slot-pull"
    assert calls == [(persisted_pull, GAME_DATE, True)]


def test_same_slot_retry_fails_closed_on_immutable_manifest_readback_error(
    monkeypatch,
):
    persisted_pull = _persisted_pull()
    monkeypatch.setattr(
        mlb_manual_pull.pull_history,
        "store_pull",
        lambda _body: _stored_result(persisted_pull),
    )
    monkeypatch.setattr(
        mlb_manual_pull.pull_history,
        "validate_provider_schedule_manifest",
        lambda *_args, **_kwargs: [
            "immutable_provider_manifest_readback_mismatch"
        ],
    )

    result = mlb_manual_pull._store_canonical_pull_history(
        game_date=GAME_DATE,
        asof=RETRY_AT,
        run="same_slot_retry",
        compact=_retry_compact(),
    )

    assert result["ok"] is False
    assert result["providerManifestBound"] is False
    assert result["officialScheduleAuthorityBound"] is False
    assert result["providerManifestValidationErrors"] == [
        "immutable_provider_manifest_readback_mismatch"
    ]
