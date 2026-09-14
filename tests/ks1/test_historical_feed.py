from copy import deepcopy
import hashlib

import pytest

from ks1.historical_feed import PREFIX, feed_identity
from ks1.inventory import encode
from ks1.prior_pitcher_context import PriorPitcherContext, pregame_identity_index, verified_reconstruction
from ks1.features import Features
from ks1.table import build
from tests.ks1.test_prior_pitcher_context import history, full_game, STATS, SOURCE


def entry():
    payload = {'gamePk':99,'metaData':{'timeStamp':'20260811_193000'},
               'gameData':{'datetime':{'dateTime':'2026-08-11T20:00:00Z'},
                           'status':{'codedGameState':'P'},
                           'teams':{'home':{'id':1},'away':{'id':2}},
                           'probablePitchers':{'home':{'id':104},'away':{'id':105}}},
               # MLB initializes inning one during warmup; no pitch has occurred.
               'liveData':{'linescore':{'currentInning':1},
                           'plays':{'allPlays':[{'playEvents':[{'isPitch':False}]}]}}}
    return sign({'game_id':'99','commence_time':'2026-08-11T20:00:00Z','timecode':'20260811_195000',
                 'provider':'MLB Stats API','endpoint':'https://statsapi.mlb.com/api/v1.1/game/99/feed/live',
                 'retrieved_at':'2026-09-14T04:00:00Z','payload':payload})


def sign(value):
    value = deepcopy(value)
    value.pop('receipt', None)
    value['payload_sha256'] = hashlib.sha256(encode(value['payload'])).hexdigest()
    value['receipt'] = {'bucket':'retained','key':PREFIX+'game=99/timecode=20260811_195000.json',
                        'versionId':'v1','sha256':hashlib.sha256(encode(value)).hexdigest()}
    return value


def test_archived_probables_are_admitted_from_their_own_pregame_timestamp():
    value = entry()
    identity = feed_identity(value)
    assert identity['sides']['home']['pitcher_id'] == '104'
    assert identity['sides']['home']['as_of'] == '2026-08-11T19:30:00+00:00'
    assert pregame_identity_index({'historical_pregame_feeds':[value]})[('99','home')][0] == identity['sides']['home']


@pytest.mark.parametrize('kind', ['late','final','pitch','wrong_game','wrong_start','missing_pitcher','missing_version','wrong_hash'])
def test_archive_rejects_late_or_unbound_identity(kind):
    value = entry()
    if kind == 'late': value['payload']['metaData']['timeStamp'] = '20260811_195001'
    if kind == 'final': value['payload']['gameData']['status']['codedGameState'] = 'F'
    if kind == 'pitch': value['payload']['liveData']['plays']['allPlays'][0]['playEvents'][0]['isPitch'] = True
    if kind == 'wrong_game': value['payload']['gamePk'] = 100
    if kind == 'wrong_start': value['payload']['gameData']['datetime']['dateTime'] = '2026-08-12T20:00:00Z'
    if kind == 'missing_pitcher': value['payload']['gameData']['probablePitchers'].pop('home')
    value = sign(value)
    if kind == 'missing_version': value['receipt']['versionId'] = 'null'
    if kind == 'wrong_hash': value['receipt']['sha256'] = 'f'*64
    assert feed_identity(value) is None


def test_original_snapshot_needs_actual_pregame_storage_time():
    features = {}
    snapshot = {'officialGamePk':99,'originalObservation':True,'outcomeKnownAtCapture':False,
                'capturedAtUtc':'2026-08-11T19:30:00Z','featureCutoffUtc':'2026-08-11T19:50:00Z',
                'commenceTime':'2026-08-11T20:00:00Z','source_key':'snapshot.json',
                'features':features,'featureFingerprint':hashlib.sha256(encode(features)).hexdigest(),
                'playerWindows':{'teams':{'home':{'teamId':1,'starterId':104,'players':[{'id':104}]}}}}
    source = {'bucket':'retained','key':'snapshot.json','versionId':'v1','sha256':'a'*64}
    bundle = {'snapshots':[snapshot],'source_receipts':[source]}
    assert not pregame_identity_index(bundle)
    source['stored_at'] = '2026-08-12T00:00:00Z'
    assert not pregame_identity_index(bundle)
    source['stored_at'] = '2026-08-11T19:31:00Z'
    assert pregame_identity_index(bundle)[('99','home')][0]['pitcher_id'] == '104'


def test_latest_archive_replaces_earlier_snapshot_before_both_sides_are_computed():
    games = history()+[full_game(99, '2026-08-11', 999, STATS)]
    features = {'home_starter_era_30d':999., 'away_starter_era_30d':888.}
    snapshot = {'officialGamePk':99,'originalObservation':True,'outcomeKnownAtCapture':False,
                'capturedAtUtc':'2026-08-11T19:00:00Z','featureCutoffUtc':'2026-08-11T19:50:00Z',
                'commenceTime':'2026-08-11T20:00:00Z','source_key':'snapshot.json',
                'features':features,'featureFingerprint':hashlib.sha256(encode(features)).hexdigest(),
                'playerWindows':{'teams':{s:{'teamId':tid,'starterId':pid,'players':[{'id':pid}]}
                                           for s,tid,pid in [('home',1,100),('away',2,101)]}}}
    schedule = {'gamePk':99,'gameDate':'2026-08-11T20:00:00Z','gameType':'R',
                'teams':{s:{'team':games[-1]['teams'][s]['team']} for s in ('home','away')},
                'status':{'abstractGameState':'Preview'}}
    bundle = {'full':games,'schedule':[schedule],'snapshots':[snapshot],
              'historical_pregame_feeds':[entry()], 'official_history_source':SOURCE,
              'source_receipts':[{'key':'snapshot.json','bucket':'retained','versionId':'v1',
                                  'sha256':'a'*64,'stored_at':'2026-08-11T19:01:00Z'}]}
    row = build(bundle)[0].to_pylist()[0]
    assert row['as_of_timestamp'] == '2026-08-11T19:30:00+00:00'
    assert row['home_starter_id'] == '104' and row['away_starter_id'] == '105'
    assert row['home_starter_status'] == row['away_starter_status'] == 'observed_archived_pregame'
    assert row['home_starter_era_30d'] == row['away_starter_era_30d'] == 3
    engine = PriorPitcherContext(Features(games).rows, SOURCE, pregame_identity_index(bundle))
    assert verified_reconstruction(row, engine)
