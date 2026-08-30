from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_slate_coverage_patch as coverage

SLATE = "2026-08-04"
START = "2026-08-04T23:05:00+00:00"
OFFICIAL_PK = "824805"


def _game():
    return {
        "officialGamePk": OFFICIAL_PK,
        "game_id": f"mlb_statsapi:{OFFICIAL_PK}",
        "id": f"mlb_statsapi:{OFFICIAL_PK}",
        "commence_time": START,
        "awayTeam": "Example Away",
        "homeTeam": "Example Home",
    }


def _legacy_terminal():
    return {
        "slate_date": SLATE,
        "game_identity": "provider:legacy-provider-event-824805",
        "game_id": "legacy-provider-event-824805",
        "officialGamePk": OFFICIAL_PK,
        "commence_time": START,
        "scheduled_lock_at_utc": "2026-08-04T22:20:00+00:00",
        "data": {
            "row": {
                "awayTeam": "Example Away",
                "homeTeam": "Example Home",
            }
        },
    }


def test_exact_official_pk_accepts_only_legacy_provider_identity_aliases():
    errors = coverage._terminal_outcome_manifest_game_errors(
        _legacy_terminal(), SLATE, _game()
    )
    assert "terminal_manifest_game_identity_mismatch" not in errors
    assert "terminal_manifest_game_id_mismatch" not in errors
    assert "terminal_manifest_official_game_pk_mismatch" not in errors


def test_official_pk_swap_still_fails_closed():
    item = _legacy_terminal()
    item["officialGamePk"] = "824806"
    errors = coverage._terminal_outcome_manifest_game_errors(item, SLATE, _game())
    assert "terminal_manifest_official_game_pk_mismatch" in errors
    assert "terminal_manifest_game_identity_mismatch" in errors
    assert "terminal_manifest_game_id_mismatch" in errors


def test_exact_official_pk_does_not_relax_start_time_guard():
    item = _legacy_terminal()
    item["commence_time"] = "2026-08-04T23:06:00+00:00"
    errors = coverage._terminal_outcome_manifest_game_errors(item, SLATE, _game())
    assert "terminal_manifest_game_identity_mismatch" not in errors
    assert "terminal_manifest_game_id_mismatch" not in errors
    assert "terminal_manifest_start_mismatch" in errors
