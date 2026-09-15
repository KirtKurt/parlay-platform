from copy import deepcopy
import hashlib

import pytest

from ks1.historical_feed import (PREFIX, TEAM_CONTEXT_PREFIX, feed_identity,
                                 feed_team_context)
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


def sign(value, prefix=PREFIX):
    value = deepcopy(value)
    value.pop('receipt', None)
    value['payload_sha256'] = hashlib.sha256(encode(value['payload'])).hexdigest()
    value['receipt'] = {'bucket':'retained','key':prefix+'game=99/timecode=20260811_195000.json',
                        'versionId':'v1','sha256':hashlib.sha256(encode(value)).hexdigest()}
    return value


def team_entry(*, missing=None):
    value = entry()
    payload = value['payload']
    payload['gameData']['players'] = {
        'ID104': {'pitchHand': {'code': 'R'}},
        'ID105': {'pitchHand': {'code': 'L'}},
    }
    boxes = {}
    for side, base, team_id in (('home', 100, 1), ('away', 200, 2)):
        lineup = list(range(base+1, base+10))
        bullpen = [base+11, base+12]
        players = {
            'ID'+str(identity): {
                'person': {'id': identity},
                'battingOrder': str(slot*100),
                'gameStatus': {'isSubstitute': False},
            }
            for slot, identity in enumerate(lineup, 1)
        }
        players.update({'ID'+str(identity): {'person': {'id': identity}}
                        for identity in bullpen})
        boxes[side] = {'team': {'id': team_id}, 'battingOrder': lineup,
                       'bullpen': bullpen, 'players': players}
    if missing == 'lineup':
        boxes['home']['battingOrder'] = []
    if missing == 'bullpen':
        boxes['away']['bullpen'] = []
    payload['liveData']['boxscore'] = {'teams': boxes}
    value['payload'] = payload
    return sign(value, TEAM_CONTEXT_PREFIX)


def test_archived_probables_are_admitted_from_their_own_pregame_timestamp():
    value = entry()
    identity = feed_identity(value)
    assert identity['sides']['home']['pitcher_id'] == '104'
    assert identity['sides']['home']['as_of'] == '2026-08-11T19:30:00+00:00'
    assert pregame_identity_index({'historical_pregame_feeds':[value]})[('99','home')][0] == identity['sides']['home']


def test_historical_team_context_binds_lineup_bullpen_and_pitcher_hands():
    stored = team_entry()
    context = feed_team_context(stored)
    assert context['coverage_status'] == 'SUPPORTED_V1_COMPLETE'
    assert context['sides']['home']['lineup_ids'] == [str(value) for value in range(101, 110)]
    assert context['sides']['away']['bullpen_roster_ids'] == ['211', '212']
    assert context['sides']['home']['probable_pitcher_hand'] == 'R'
    assert context['source']['source_type'] == 'mlb_statsapi_timecoded_team_context'
    identities = pregame_identity_index({'historical_team_context': [stored]})
    assert identities[('99', 'home')][0]['pitcher_id'] == '104'


@pytest.mark.parametrize('missing', ['lineup', 'bullpen'])
def test_historical_team_context_preserves_explicit_missing_state(missing):
    context = feed_team_context(team_entry(missing=missing))
    assert context['coverage_status'] == 'SUPPORTED_V1_EXPLICIT_MISSING'
    if missing == 'lineup':
        assert context['sides']['home']['lineup_ids'] is None
        assert context['sides']['home']['lineup_status'] == 'MISSING_FAIL_CLOSED'
    else:
        assert context['sides']['away']['bullpen_roster_ids'] is None
        assert context['sides']['away']['bullpen_status'] == 'MISSING_FAIL_CLOSED'


@pytest.mark.parametrize('mutation', ['wrong_slot', 'substitute', 'unbound_bullpen',
                                      'cross_team', 'wrong_prefix'])
def test_historical_team_context_rejects_malformed_identity_bindings(mutation):
    value = team_entry()
    boxes = value['payload']['liveData']['boxscore']['teams']
    if mutation == 'wrong_slot':
        boxes['home']['players']['ID101']['battingOrder'] = '200'
    elif mutation == 'substitute':
        boxes['home']['players']['ID101']['gameStatus']['isSubstitute'] = True
    elif mutation == 'unbound_bullpen':
        boxes['home']['players']['ID111']['person']['id'] = 999
    elif mutation == 'cross_team':
        boxes['away']['bullpen'][0] = 111
        boxes['away']['players']['ID111'] = {'person': {'id': 111}}
    value = sign(value, TEAM_CONTEXT_PREFIX)
    if mutation == 'wrong_prefix':
        value['receipt']['key'] = PREFIX+'game=99/timecode=20260811_195000.json'
    assert feed_team_context(value) is None


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


def test_historical_team_context_enters_table_with_explicit_missingness():
    games = history()+[full_game(99, '2026-08-11', 999, STATS)]
    schedule = {'gamePk':99,'gameDate':'2026-08-11T20:00:00Z','gameType':'R',
                'teams':{s:{'team':games[-1]['teams'][s]['team']} for s in ('home','away')},
                'status':{'abstractGameState':'Preview'}}
    bundle = {'full':games, 'schedule':[schedule],
              'historical_team_context':[team_entry(missing='lineup')],
              'official_history_source':SOURCE,
              'current30_history_complete':True,
              'current_year_history_complete':True,
              'prior_year_history_complete':True,
              'statcast_coverage_complete':True,
              'current_year_statcast_complete':True,
              'prior_year_statcast_complete':True}
    row = build(bundle)[0].to_pylist()[0]
    assert row['lineup_bullpen_context_evidence'] == 'historical_timecoded_mlb_feed'
    assert row['historical_lineup_bullpen_context_status'] == 'SUPPORTED_V1_EXPLICIT_MISSING'
    assert row['home_starter_id'] == '104'
    assert row['home_starter_status'] == 'observed_archived_pregame'
    assert row['home_lineup_ops_30d'] is None
    assert row['home_lineup_ops_30d_missing'] == 1
    assert row['home_bullpen_context_roster_count'] == 2
    assert row['home_bullpen_context_roster_count_missing'] == 0


def test_archived_context_with_unresolved_team_is_excluded_without_crashing():
    bundle = {'full': history(), 'historical_team_context': [team_entry()],
              'schedule': [{'gamePk': 99, 'gameDate': '2026-08-11T20:00:00Z',
                            'gameType': 'R', 'teams': {},
                            'status': {'abstractGameState': 'Preview'}}],
              'finals': [{'officialGamePk': 12345, 'officialDate': '2025-04-01',
                          'completed': True}], 'official_history_source': SOURCE}
    valid = full_game(999, '2026-08-12', 999, STATS)
    bundle['full'].append(valid)
    bundle['schedule'].append({'gamePk': 999, 'gameDate': '2026-08-12T20:00:00Z',
                              'gameType': 'R', 'teams': {
                                  side: {'team': valid['teams'][side]['team']}
                                  for side in ('home', 'away')},
                              'status': {'abstractGameState': 'Preview'}})
    _, report, *_ = build(bundle)
    assert {'game_id': '99', 'reason': 'missing_official_team_identity'} in report['exclusions']


def test_historical_talent_requires_target_relative_prior_season(monkeypatch):
    games = history()+[full_game(99, '2026-08-11', 999, STATS)]
    bundle = {'full': games, 'historical_team_context': [team_entry()],
              'official_history_source': {**SOURCE, 'complete_years': [2026, 2027]},
              'schedule': [{'gamePk': 99, 'gameDate': '2026-08-11T20:00:00Z',
                            'gameType': 'R', 'teams': {
                                s: {'team': games[-1]['teams'][s]['team']} for s in ('home', 'away')},
                            'status': {'abstractGameState': 'Preview'}}]}
    bundle.update({key: True for key in ('current30_history_complete',
        'current_year_history_complete', 'prior_year_history_complete',
        'statcast_coverage_complete', 'current_year_statcast_complete',
        'prior_year_statcast_complete')})
    monkeypatch.setattr(Features, 'lineup_batters_at', lambda *args, **kwargs:
                        ([], {'lineup_ops_talent': .7, 'lineup_ops_prior_year': .8,
                              'lineup_ops_30d': .9}))
    row = build(bundle)[0].to_pylist()[0]
    assert row['home_lineup_ops_30d'] == .9
    for name in ('home_lineup_ops_talent', 'home_lineup_ops_prior_year'):
        assert row[name] is None
        assert row[name+'_missing'] == 1


@pytest.mark.parametrize('gap_date, gap_team, expected_incomplete', [
    (None, None, True),
    ('2026-04-01', 'home', True),  # outside the old 75-day check
    ('2025-04-01', 'home', True),  # prior-year talent/shrink inputs
    ('2025-04-01', 'Unmapped former team', True),  # traded-player histories
    ('2024-04-01', 'home', False),  # outside consumed seasons
    ('2026-09-01', 'home', False),  # unavailable future gap
])
def test_historical_team_context_fails_closed_on_incomplete_history(
        gap_date, gap_team, expected_incomplete):
    games = history()+[full_game(99, '2026-08-11', 999, STATS)]
    schedule = {'gamePk':99,'gameDate':'2026-08-11T20:00:00Z','gameType':'R',
                'teams':{s:{'team':games[-1]['teams'][s]['team']} for s in ('home','away')},
                'status':{'abstractGameState':'Preview'}}
    bundle = {'full':games, 'schedule':[schedule],
              'historical_team_context':[team_entry()],
              'official_history_source':SOURCE,
              'current30_history_complete':True,
              'current_year_history_complete':True,
              'prior_year_history_complete':gap_date is not None,
              'statcast_coverage_complete':True,
              'current_year_statcast_complete':True,
              'prior_year_statcast_complete':True}
    if gap_date:
        bundle['finals'] = [{'officialGamePk': 12345, 'officialDate': gap_date,
                             'completed': True, 'homeTeam': gap_team,
                             'awayTeam': 'Unmapped away team'}]
    row = build(bundle)[0].to_pylist()[0]
    if not expected_incomplete:
        assert row['lineup_bullpen_context_evidence'] == 'historical_timecoded_mlb_feed'
        return
    assert row['lineup_bullpen_context_evidence'] is None
    assert row['historical_lineup_bullpen_context_status'] == 'HISTORY_INCOMPLETE_FAIL_CLOSED'
    assert row['home_lineup_ops_30d'] is None
    assert row['home_lineup_ops_30d_missing'] == 1


@pytest.mark.parametrize('statcast_flag', ['statcast_coverage_complete',
    'current_year_statcast_complete', 'prior_year_statcast_complete', 'all'])
def test_savant_delay_does_not_discard_verified_official_player_history(statcast_flag):
    games = history()+[full_game(99, '2026-08-11', 999, STATS)]
    for game in games[:-1]:
        for side, base in (('home', 100), ('away', 200)):
            players = game['teams'][side]['players']
            for pid in range(base+1, base+10):
                player = players.setdefault('ID'+str(pid), {'person': {'id': pid}, 'stats': {}})
                player['stats']['batting'] = dict(atBats=4, hits=1, baseOnBalls=1,
                    hitByPitch=0, sacFlies=0, doubles=0, triples=0, homeRuns=0, strikeOuts=1)
            for pid in (base+11, base+12):
                players['ID'+str(pid)] = {'person': {'id': pid},
                    'stats': {'pitching': {**STATS, 'gamesStarted': 0}}}
    stored = team_entry()
    bundle = {'full': games, 'historical_team_context': [stored],
              'official_history_source': SOURCE, 'source_receipts': [stored['receipt']],
              'schedule': [{'gamePk': 99, 'gameDate': '2026-08-11T20:00:00Z',
                  'gameType': 'R', 'teams': {s: {'team': games[-1]['teams'][s]['team']}
                                           for s in ('home', 'away')},
                  'status': {'abstractGameState': 'Preview'}}]}
    flags = ('current30_history_complete', 'current_year_history_complete',
             'prior_year_history_complete', 'statcast_coverage_complete',
             'current_year_statcast_complete', 'prior_year_statcast_complete')
    bundle.update({key: True for key in flags})
    expected = build(bundle)[0].to_pylist()[0]
    for key in flags[3:]:
        if statcast_flag in (key, 'all'):
            bundle[key] = False
    row = build(bundle)[0].to_pylist()[0]
    assert row == expected  # No raw pitches: delayed fetch changes no supported input.
    assert row['historical_lineup_bullpen_context_status'] == 'SUPPORTED_V1_COMPLETE'
    for side in ('home', 'away'):
        for name in ('lineup_ops_30d', 'bullpen_context_era_30d'):
            assert row[side+'_'+name] is not None
            assert row[side+'_'+name+'_missing'] == 0
        for name in ('lineup_pitch_type_matchup_xwoba_30d', 'bullpen_context_velocity_30d'):
            assert row[side+'_'+name] is None
            assert row[side+'_'+name+'_missing'] == 1
    for flag in flags[:3]:
        incomplete = {**bundle, flag: False}
        rejected = build(incomplete)[0].to_pylist()[0]
        assert rejected['historical_lineup_bullpen_context_status'] == 'HISTORY_INCOMPLETE_FAIL_CLOSED'
