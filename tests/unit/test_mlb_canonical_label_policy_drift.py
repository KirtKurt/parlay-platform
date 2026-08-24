from __future__ import annotations

import os
import sys
from copy import deepcopy
from pathlib import Path

os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
os.environ.setdefault('AWS_EC2_METADATA_DISABLED', 'true')

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / 'hello_world'
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_canonical_final_labels_v1 as subject


def immutable_row():
    return {
        'record_type': subject.RECORD_TYPE,
        'sport': 'mlb',
        'slate_date': '2026-08-03',
        'official_game_pk': '823431',
        'provider_event_id': 'odds-event',
        'provider_identity_match_method': 'exact_official_game_pk_and_ordered_teams',
        'provider_alias_crosswalk': None,
        'away_team': 'Away',
        'home_team': 'Home',
        'game_date_utc': '2026-08-03T23:10:00Z',
        'away_score': 2,
        'home_score': 5,
        'winner': 'Home',
        'home_won': True,
        'predicted_winner': 'Home',
        'predicted_side': 'home',
        'correct': True,
        'canonical_lock_pk': 'GAME_WINNERS#mlb#2026-08-03',
        'canonical_lock_sk': 'LOCKED#GAME#823431',
        'canonical_lock_authority_version': 'v1',
        'canonical_lock_official_audit_eligible': True,
        'canonical_lock_learning_eligible': True,
        'exact_lock_vector_validated': True,
        'canonical_stage_fingerprint': 'stage',
        'canonical_lock_payload_fingerprint': 'lock',
        'frozen_feature_vector_fingerprint': 'vector',
        'fundamentals_snapshot_v2_version': 'v2',
        'fundamentals_snapshot_v2_fingerprint': 'fundamentals',
        'source': subject.SOURCE,
        'source_url': subject.official_finals_url('2026-08-03'),
        'source_payload_fingerprint': 'official-final',
        'official_status': {'abstractGameState': 'Final'},
        'accuracy_eligible': True,
        'training_eligible': True,
        'training_exclusion_reasons': [],
    }


def test_training_policy_drift_is_not_an_official_correction():
    existing = immutable_row()
    proposed = deepcopy(existing)
    proposed['canonical_lock_learning_eligible'] = False
    proposed['training_eligible'] = False
    proposed['training_exclusion_reasons'] = ['new_stricter_policy']
    proposed['accuracy_eligible'] = False
    assert (
        subject._immutable_settlement_facts_fingerprint(existing)
        == subject._immutable_settlement_facts_fingerprint(proposed)
    )


def test_official_score_or_lock_or_prediction_change_remains_conflict():
    existing = immutable_row()
    for key, value in (
        ('home_score', 6),
        ('canonical_lock_payload_fingerprint', 'different-lock'),
        ('predicted_winner', 'Away'),
    ):
        proposed = deepcopy(existing)
        proposed[key] = value
        assert (
            subject._immutable_settlement_facts_fingerprint(existing)
            != subject._immutable_settlement_facts_fingerprint(proposed)
        )


def test_source_retains_write_once_and_policy_drift_status():
    source = (HELLO / 'mlb_canonical_final_labels_v1.py').read_text(encoding='utf-8')
    assert 'IDEMPOTENT_EXISTING_POLICY_DRIFT' in source
    assert 'ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)"' in source
    assert 'outcomes_tbl.update_item' not in source
    assert 'outcomes_tbl.delete_item' not in source
