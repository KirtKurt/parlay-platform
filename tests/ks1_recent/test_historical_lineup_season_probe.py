from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from ks1.features import utc
from ks1.historical_feed import TEAM_CONTEXT_PREFIX
from ks1.historical_lineup_season_probe import extract
from ks1.inventory import encode


def fixture():
    game_id = '99'
    start = '2026-08-11T20:00:00Z'
    code = '20260811_195000'
    boxes = {}
    row = {'game_id': game_id, 'commence_time': start, 'home_id': '1', 'away_id': '2'}
    for side, base, team_id in (('home', 100, 1), ('away', 200, 2)):
        order = list(range(base + 1, base + 10))
        row[side + '_lineup_ids'] = json.dumps(order)
        players = {}
        for slot, identity in enumerate(order, 1):
            players['ID' + str(identity)] = {
                'person': {'id': identity},
                'battingOrder': str(slot * 100),
                'gameStatus': {'isSubstitute': False},
                'seasonStats': {'batting': {
                    'plateAppearances': str(100 + slot),
                    'ops': str(.700 + slot / 100),
                    'obp': str(.300 + slot / 1000),
                    'slg': str(.400 + slot / 1000),
                }},
            }
        boxes[side] = {'team': {'id': team_id}, 'battingOrder': order, 'players': players}
    payload = {
        'gamePk': 99,
        'metaData': {'timeStamp': '20260811_193000'},
        'gameData': {
            'datetime': {'dateTime': start},
            'status': {'codedGameState': 'P'},
            'teams': {'home': {'id': 1}, 'away': {'id': 2}},
        },
        'liveData': {
            'boxscore': {'teams': boxes},
            'plays': {'allPlays': [{'playEvents': [{'isPitch': False}]}]},
        },
    }
    entry = {
        'game_id': game_id,
        'commence_time': start,
        'provider': 'MLB Stats API',
        'endpoint': 'https://statsapi.mlb.com/api/v1.1/game/99/feed/live',
        'timecode': code,
        'retrieved_at': '2026-09-14T04:00:00Z',
        'payload': payload,
        'payload_sha256': hashlib.sha256(encode(payload)).hexdigest(),
    }
    body = encode(entry)
    receipt = {
        'bucket': 'retained',
        'key': TEAM_CONTEXT_PREFIX + 'game=99/timecode=' + code + '.json',
        'versionId': 'v1',
        'sha256': hashlib.sha256(body).hexdigest(),
        'source_type': 'mlb_statsapi_timecoded_team_context',
        'provider': 'MLB Stats API',
        'endpoint': entry['endpoint'],
        'timecode': code,
        'retrieved_at': entry['retrieved_at'],
        'payload_sha256': entry['payload_sha256'],
        'historical_retrieval_is_original_storage': False,
    }
    return body, row, receipt


def test_extracts_live_equivalent_top_four_ops_from_exact_timecoded_feed():
    body, row, receipt = fixture()
    values = extract(body, row, receipt)
    assert values['home_lineup_top4_ops'] is not None
    assert values['away_lineup_top4_ops'] is not None
    assert values['home_lineup_observed_batters'] == 9
    assert values['home_lineup_total_pa'] == sum(range(101, 110))
    assert values['home_lineup_quality_ops'] > .70


@pytest.mark.parametrize('mutation', ['sha', 'pitch', 'late', 'lineup', 'team', 'stats'])
def test_probe_fails_closed_on_unproven_or_incomplete_source(mutation):
    body, row, receipt = fixture()
    entry = json.loads(body)
    if mutation == 'sha':
        receipt['sha256'] = 'f' * 64
    elif mutation == 'pitch':
        entry['payload']['liveData']['plays']['allPlays'][0]['playEvents'][0]['isPitch'] = True
    elif mutation == 'late':
        entry['payload']['metaData']['timeStamp'] = '20260811_195001'
    elif mutation == 'lineup':
        row['home_lineup_ids'] = json.dumps(list(range(102, 111)))
    elif mutation == 'team':
        entry['payload']['liveData']['boxscore']['teams']['home']['team']['id'] = 3
    elif mutation == 'stats':
        entry['payload']['liveData']['boxscore']['teams']['home']['players']['ID101'][
            'seasonStats']['batting']['plateAppearances'] = None
    if mutation not in ('sha', 'lineup'):
        entry['payload_sha256'] = hashlib.sha256(encode(entry['payload'])).hexdigest()
        body = encode(entry)
        receipt['sha256'] = hashlib.sha256(body).hexdigest()
        receipt['payload_sha256'] = entry['payload_sha256']
    if mutation == 'stats':
        values = extract(body, row, receipt)
        assert values['home_lineup_top4_ops'] is None
        return
    with pytest.raises(ValueError):
        extract(body, row, receipt)


def test_probe_rejects_wrong_timecode_even_when_payload_is_pregame():
    body, row, receipt = fixture()
    start = utc(row['commence_time'])
    receipt['timecode'] = (start - timedelta(minutes=11)).strftime('%Y%m%d_%H%M%S')
    with pytest.raises(ValueError, match='source_identity_mismatch'):
        extract(body, row, receipt)
