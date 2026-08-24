from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_canonical_final_labels_v1 as labels


def _facts() -> dict:
    return {
        "record_type": labels.RECORD_TYPE,
        "sport": "mlb",
        "slate_date": "2026-08-03",
        "official_game_pk": "824160",
        "provider_event_id": "odds-event",
        "provider_identity_match_method": "exact_official_game_pk_and_ordered_teams",
        "provider_alias_crosswalk": None,
        "away_team": "Away",
        "home_team": "Home",
        "game_date_utc": "2026-08-03T23:00:00Z",
        "away_score": 2,
        "home_score": 5,
        "winner": "Home",
        "home_won": True,
        "predicted_winner": "Home",
        "predicted_side": "home",
        "correct": True,
        "canonical_lock_pk": "LOCKS#2026-08-03",
        "canonical_lock_sk": "GAME#824160",
        "canonical_lock_authority_version": "v1",
        "canonical_lock_official_audit_eligible": True,
        "exact_lock_vector_validated": True,
        "canonical_stage_fingerprint": "stage",
        "canonical_lock_payload_fingerprint": "lock",
        "frozen_feature_vector_fingerprint": "vector",
        "fundamentals_snapshot_v2_version": "v2",
        "fundamentals_snapshot_v2_fingerprint": "fundamentals",
        "source": labels.SOURCE,
        "source_url": "https://statsapi.mlb.com/example",
        "source_payload_fingerprint": "raw-game-over",
        "official_status": {
            "abstractGameState": "Final",
            "codedGameState": "O",
            "statusCode": "O",
            "detailedState": "Game Over",
        },
    }


def test_terminal_status_and_raw_payload_drift_are_not_correction_facts() -> None:
    stored = _facts()
    proposed = copy.deepcopy(stored)
    proposed["source_payload_fingerprint"] = "raw-final"
    proposed["official_status"] = {
        "abstractGameState": "Final",
        "codedGameState": "F",
        "statusCode": "F",
        "detailedState": "Final",
    }

    assert labels._immutable_settlement_facts_fingerprint(stored) == (
        labels._immutable_settlement_facts_fingerprint(proposed)
    )


def test_score_change_remains_an_official_correction_conflict() -> None:
    stored = _facts()
    proposed = copy.deepcopy(stored)
    proposed["away_score"] = 6
    proposed["home_score"] = 5
    proposed["winner"] = "Away"
    proposed["home_won"] = False

    assert labels._immutable_settlement_facts_fingerprint(stored) != (
        labels._immutable_settlement_facts_fingerprint(proposed)
    )


def test_prediction_identity_change_remains_a_conflict() -> None:
    stored = _facts()
    proposed = copy.deepcopy(stored)
    proposed["predicted_winner"] = "Away"
    proposed["predicted_side"] = "away"
    proposed["correct"] = False

    assert labels._immutable_settlement_facts_fingerprint(stored) != (
        labels._immutable_settlement_facts_fingerprint(proposed)
    )
