from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import mlb_recovery_shadow_v1 as recovery

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path('/tmp/mlb_recovery_fundamentals/mlb_recovery_fundamentals_shadow_seed.json')


def _artifact():
    if FIXTURE.exists():
        return json.loads(FIXTURE.read_text(encoding='utf-8'))
    artifact = {
        'ok': True,
        'version': 'test',
        'shadowOnly': True,
        'productionAuthority': False,
        'officialPickOverrideAllowed': False,
        'prospectivePromotionRequired': True,
        'model': {
            'features': ['marketHomeProbability'],
            'bias': 0.0,
            'weights': {'marketHomeProbability': 1.0},
            'means': {'marketHomeProbability': 0.5},
            'scales': {'marketHomeProbability': 0.1},
            'impute': {'marketHomeProbability': 0.5},
        },
        'noPlayGate': {'threshold': 0.63},
    }
    artifact['artifactDigest'] = recovery._artifact_digest(artifact)
    return artifact


def test_candidate_artifact_is_shadow_only_and_digest_bound():
    artifact = _artifact()
    assert recovery.validate_candidate_artifact(artifact)['productionAuthority'] is False

    for field, value in (
        ('shadowOnly', False),
        ('productionAuthority', True),
        ('officialPickOverrideAllowed', True),
        ('prospectivePromotionRequired', False),
    ):
        invalid = copy.deepcopy(artifact)
        invalid[field] = value
        with pytest.raises(recovery.RecoveryShadowError):
            recovery.validate_candidate_artifact(invalid)

    invalid = copy.deepcopy(artifact)
    invalid['artifactDigest'] = '0' * 64
    with pytest.raises(recovery.RecoveryShadowError, match='digest'):
        recovery.validate_candidate_artifact(invalid)


def test_candidate_scoring_applies_no_play_threshold_without_authority():
    artifact = _artifact()
    signals = {
        'marketHomeProbability': 0.62,
        'currentHomeProbability': 0.63,
        'v10HomeProbability': 0.61,
        'v11HomeProbability': 0.60,
        'lineMovementHomeProbability': 0.59,
        'homeVoteFraction': 1.0,
        'pullDepthLog': 3.0,
    }
    result = recovery.score_candidate(
        artifact,
        signals,
        {'features': {}, 'missingMasks': {}},
    )
    assert 0.0 < result['homeWinProbability'] < 1.0
    assert result['selectedSide'] in {'home', 'away'}
    assert result['threshold'] >= 0.5
    assert 'productionAuthority' not in result


def test_capture_disposition_is_strictly_pregame_and_tminus45_sourced():
    now = datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)
    start = now + timedelta(minutes=30)
    source = now - timedelta(minutes=20)
    eligible = recovery.capture_disposition(start, now, source, 5)
    assert eligible['eligible'] is True

    assert recovery.capture_disposition(now + timedelta(hours=2), now, source, 5)['reason'] == 'BEFORE_TMINUS45'
    assert recovery.capture_disposition(now, now, source, 5)['reason'] == 'GAME_STARTED_NO_BACKFILL'
    assert recovery.capture_disposition(start, now, now, 5)['reason'] == 'PRELOCK_SOURCE_PULL_MISSING'
    assert recovery.capture_disposition(start, now, now - timedelta(hours=2), 5)['reason'] == 'TMINUS45_SOURCE_STALE'
    assert recovery.capture_disposition(start, now, source, 3)['reason'] == 'INSUFFICIENT_PULL_DEPTH'


def test_selection_and_grade_keys_are_isolated_from_official_prediction_partitions():
    selection = recovery._selection_key('2026-07-23', 'mlb_statsapi:123')
    grade = recovery._grade_key('2026-07-23', 'mlb_statsapi:123')
    assert selection['PK'] == 'MLB_RECOVERY_SHADOW#2026-07-23'
    assert selection['SK'].startswith('SELECTION#')
    assert grade['PK'] == selection['PK']
    assert grade['SK'].startswith('GRADE#')
    assert 'GAME_WINNERS' not in json.dumps(selection)
    assert 'PREDICTIONS' not in json.dumps(selection)


def test_sam_resource_has_no_prediction_table_write_authority():
    template = (ROOT / 'template.yaml').read_text(encoding='utf-8')
    if 'MLBRecoveryShadowFunction:' not in template:
        pytest.skip('SAM resource is applied by the branch migration step')
    block = template.split('  MLBRecoveryShadowFunction:', 1)[1].split('\n  ', 1)[0]
    assert 'Handler: mlb_recovery_shadow_v1.lambda_handler' in block
    assert 'MLB_ML_ARTIFACTS_BUCKET' in block
    assert 'SignalLedgerTable' not in block
    assert 'PredictionsTable' not in block
    assert 'MLBRecoveryShadowCaptureEvery15Minutes' in block
    assert "'productionAuthority': false" not in block
