from copy import deepcopy
import hashlib
import io

import pytest

from ks1.features import Features
from ks1.inventory import RESEARCH, encode
from ks1.statcast_history import load_training_statcast
from tests.ks1.test_indexed_history import game


class RetainedS3:
    def __init__(self, objects):
        self.objects = objects
        self.reads = []

    def get_object(self, Bucket, Key):
        self.reads.append(Key)
        payload, version, checksum = self.objects[Key]
        body = encode(payload)
        return {'Body': io.BytesIO(body), 'VersionId': version,
                'Metadata': {'sha256': checksum or hashlib.sha256(body).hexdigest()}}


def fixture():
    source = game(1, '2026-09-01', '2026-09-01T21:00:00Z')
    for player in source['teams']['home']['players'].values():
        if player.get('stats', {}).get('batting'):
            player['stats']['batting']['plateAppearances'] = 1
    source['teams']['home']['players']['151']['stats']['pitching']['numberOfPitches'] = 18
    source['teams']['home']['players']['151']['stats']['pitching']['battersFaced'] = 9
    source['teams']['away']['players'] = deepcopy(source['teams']['home']['players'])
    for player in source['teams']['away']['players'].values():
        player['person']['id'] += 100
    rows = []
    for pitcher, base in ((151, 200), (251, 100)):
        for batter in range(base+1, base+10):
            for pitch in (1, 2):
                rows.append({'game_pk': '1', 'game_date': '2026-09-01',
                             'pitcher': str(pitcher), 'batter': str(batter),
                             'at_bat_number': str(batter), 'pitch_number': str(pitch),
                             'pitch_type': 'FF', 'p_throws': 'R', 'type': 'X' if pitch == 2 else 'S',
                             'description': 'hit_into_play' if pitch == 2 else 'called_strike',
                             'events': 'single' if pitch == 2 else '',
                             'woba_denom': '1' if pitch == 2 else '0',
                             'woba_value': '.9' if pitch == 2 else '',
                             'estimated_woba_using_speedangle': '.5'})
    bundle = {'full': [source], 'official_history_source': {'complete_years': [2026]},
              'schedule': [{'gamePk': 1, 'gameDate': source['startAtUtc'], 'gameType': 'R',
                            'status': {'abstractGameState': 'Final'}}],
              'source_receipts': [], 'statcast': [], 'statcast_coverage_complete': True}
    payload = {'date': '2026-09-01', 'rows': rows}
    key = RESEARCH+'sources/statcast-v2/2026-09-01.json'
    return bundle, payload, key


def test_historical_archive_restores_matchup_values_with_prior_only_boundaries():
    bundle, payload, key = fixture()
    report = load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'bucket')
    assert report['provider_requests'] == 0 and report['verified_pitch_objects'] == 1
    assert not report['errors']
    assert bundle['source_receipts'][0]['versionId'] == 'v1'
    engine = Features(bundle['full'], bundle['statcast'],
                      statcast_retained_dates=bundle['statcast_retained_dates'])
    _, values = engine.lineup_batters_at('2026-09-02T17:50:00Z', list(range(101, 110)), '251', 'R')
    assert values['lineup_platoon_xwoba_7d'] == .5
    assert values['lineup_pitch_type_matchup_xwoba_30d'] == .5
    _, earlier = engine.lineup_batters_at('2026-09-01T17:50:00Z', list(range(101, 110)), '251', 'R')
    assert earlier['lineup_platoon_xwoba_7d'] is None


@pytest.mark.parametrize('defect', ['truncated', 'duplicate', 'missing_count',
                                   'foreign_pitcher', 'wrong_date', 'unversioned', 'checksum'])
def test_unverified_daily_pitches_never_create_window_coverage(defect):
    bundle, payload, key = fixture()
    bundle['statcast_retained_dates'] = ['2026-09-01']
    bundle['statcast_verified_games'] = ['1']
    bundle['statcast'] = deepcopy(payload['rows'])
    version, checksum = 'v1', None
    if defect == 'truncated':
        payload['rows'].pop()
    elif defect == 'duplicate':
        payload['rows'][-1] = payload['rows'][-2]
    elif defect == 'missing_count':
        bundle['full'][0]['teams']['home']['players']['151']['stats']['pitching'].pop('numberOfPitches')
    elif defect == 'foreign_pitcher':
        payload['rows'][-1]['pitcher'] = '999'
    elif defect == 'wrong_date':
        payload['rows'][-1]['game_date'] = '2026-09-02'
    elif defect == 'unversioned':
        version = None
    elif defect == 'checksum':
        checksum = '0'*64
    report = load_training_statcast(bundle, RetainedS3({key: (payload, version, checksum)}), 'bucket')
    assert '2026-09-01' not in bundle['statcast_retained_dates']
    assert '1' not in bundle['statcast_verified_games']
    assert report['verified_pitch_objects'] == 0 and len(report['errors']) == 1
    assert bundle['source_receipts'] == []


def test_valid_game_set_revision_and_global_gate_are_preserved():
    bundle, payload, key = fixture()
    bundle['statcast_coverage_complete'] = False
    revision = RESEARCH+'sources/statcast-v2-revisions/2026-09-01/'+hashlib.sha256(encode([1])).hexdigest()+'.json'
    s3 = RetainedS3({key: ({**payload, 'rows': payload['rows'][:-1]}, 'v1', None),
                    revision: (payload, 'v2', None)})
    report = load_training_statcast(bundle, s3, 'bucket')
    assert report['verified_pitch_objects'] == 1
    assert bundle['source_receipts'][0]['key'] == revision
    assert bundle['source_receipts'][0]['versionId'] == 'v2'
    assert bundle['statcast_coverage_complete'] is False


def test_existing_verified_compact_dates_survive_partial_historical_load():
    bundle, payload, key = fixture()
    bundle['statcast_retained_dates'] = ['2025-08-31']
    bundle['statcast_verified_games'] = ['2']
    bundle['statcast'].append({'game_pk': '2', 'game_date': '2025-08-31',
                               'pitcher': '351', 'batter': '401',
                               'at_bat_number': '1', 'pitch_number': '1'})
    load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'bucket')
    assert '2025-08-31' in bundle['statcast_retained_dates']
    assert '2026-09-01' in bundle['statcast_retained_dates']
    assert bundle['statcast_verified_games'] == ['1', '2']
    assert bundle['statcast_retained_dates'] == sorted(bundle['statcast_retained_dates'])
    assert any(row.get('game_pk') == '2' for row in bundle['statcast'])


def test_unfinished_scheduled_game_is_not_an_empty_verified_day():
    bundle, payload, key = fixture()
    bundle['schedule'].append({'gamePk': 2, 'gameDate': '2026-08-31T18:00:00Z',
                               'gameType': 'R', 'status': {'abstractGameState': 'Live'}})
    report = load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'bucket')
    assert '2026-08-31' not in bundle['statcast_retained_dates']
    assert report['errors'] == [{'date': '2026-08-31', 'reason': 'unfinished_scheduled_game'}]
    bundle['official_history_source']['complete_years'] = []
    with pytest.raises(ValueError, match='complete official history'):
        load_training_statcast(bundle, RetainedS3({}), 'bucket')
