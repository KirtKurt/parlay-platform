from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest
import mlb_statsapi_team_context as source

NOW = datetime(2026, 9, 9, 14, tzinfo=timezone.utc)

@pytest.fixture(autouse=True)
def cache():
    source._CACHE.clear()
    yield
    source._CACHE.clear()


def fixtures():
    game = {"gamePk": 123, "gameDate": "2026-09-09T20:00:00Z", "status": {"abstractGameState": "Preview"},
            "teams": {s: {"team": {"id": n}} for s,n in (("home",1),("away",2))}}
    teams = {}
    for side, base in (("home",100),("away",200)):
        order = list(range(base+1,base+10))
        players = {"ID"+str(n): {"person": {"id": n}, "battingOrder": str(i*100),
            "gameStatus": {"isSubstitute": False}, "seasonStats": {"batting": {"ops": ".800", "plateAppearances": 100}}}
            for i,n in enumerate(order,1)}
        for n, started in ((base+10,1),(base+11,0)):
            players["ID"+str(n)] = {"person": {"id":n}, "stats": {"pitching": {"gamesStarted": started,
                "numberOfPitches": 30 if started else 18, "outs": 9 if started else 3}}}
        teams[side] = {"team": game["teams"][side]["team"], "battingOrder": order, "players": players,
                       "pitchers": [base+10,base+11]}
    feed = {"gameData": {"game": {"pk":123}, "datetime": {"dateTime":game["gameDate"]}, "status":game["status"]},
            "liveData": {"boxscore": {"teams": teams}}}
    old = deepcopy(game); old.update(gamePk=122,gameDate="2026-09-08T20:00:00Z",status={"abstractGameState":"Final"})
    history = {"ok":True, "historyStartDateEt":"2026-09-04", "historyEndDateEt":"2026-09-09",
        "endpoint":"https://statsapi.mlb.com/api/v1/schedule", "retrievedAtUtc":NOW.isoformat(),
        "payloadFingerprint":"a"*64, "payload":{"totalGames":1,"dates":[{"date":"2026-09-08","games":[old]}]}}
    def get(url, timeout):
        assert timeout == 4
        return feed if url.endswith('/feed/live') else {"teams":teams}
    return game, history, feed, teams, get


def test_named_lineup_and_workload_values_are_preserved_and_cached():
    game,history,feed,teams,get = fixtures(); calls=[]
    def fetch(url,timeout): calls.append(url); return get(url,timeout)
    lineup,bullpen=source.observe('2026-09-09',game,history,fetch,now=lambda:NOW)
    assert lineup['source_status']=='CONNECTED'
    assert lineup['home_lineup_confirmed'] is True
    assert lineup['away_lineup_confirmed'] is True
    assert lineup['home_lineup_mean_ops'] == pytest.approx(.8)
    assert bullpen['home_reliever_usage_1d_3d_5d']['3d'] == {'pitches':18,'outs':3}
    assert bullpen['source_status']=='PARTIAL'
    assert 'home_available_relievers' not in bullpen
    assert source.observe('2026-09-09',game,history,fetch,now=lambda:NOW)==(lineup,bullpen)
    assert len(calls)==2


@pytest.mark.parametrize('mode',['past','locked','live','missing_identity'])
def test_ineligible_requests_never_fetch(mode):
    game,history,*_=fixtures(); moment=NOW; day='2026-09-09'
    if mode=='past': day='2026-09-08'
    if mode=='locked': moment=NOW+timedelta(hours=5,minutes=15)
    if mode=='live': game['status']['abstractGameState']='Live'
    if mode=='missing_identity':game['teams']['home']['team']['id']=None
    def forbidden(*a,**k):pytest.fail('ineligible source request')
    a,b=source.observe(day,game,history,forbidden,now=lambda:moment)
    assert a['source_status']==b['source_status']=='NOT_CONNECTED_SOURCE_REQUIRED'


@pytest.mark.parametrize('mode',['duplicate','substitute','wrong_slot','missing_ops'])
def test_lineup_integrity_and_unknown_strength(mode):
    game,history,feed,teams,get=fixtures(); home=teams['home'];p=home['players']['ID101']
    if mode=='duplicate':home['battingOrder'][1]=101
    if mode=='substitute':p['gameStatus']['isSubstitute']=True
    if mode=='wrong_slot':p['battingOrder']='200'
    if mode=='missing_ops':p['seasonStats']['batting']['ops']=None
    a,_=source.observe('2026-09-09',game,history,get,now=lambda:NOW)
    if mode=='missing_ops':
        assert a['source_status']=='CONNECTED'
        assert a['home_lineup_confirmed'] is True and a['home_lineup_mean_ops'] is None
    else:
        assert a['source_status']=='PARTIAL'
        assert a['home_lineup_confirmed'] is None


def test_snapshot_marks_only_verified_batting_order_group_complete():
    import mlb_fundamentals_snapshot_v2 as snapshots
    game,history,feed,teams,get=fixtures()
    lineup,bullpen=source.observe('2026-09-09',game,history,get,now=lambda:NOW)
    row={'gameId':'mlb_statsapi:123','officialGamePk':123,'slateDateEt':'2026-09-09',
         'predictionSourcePullAt':NOW.isoformat(),'advanced_context':{'confirmed_lineups':lineup,'bullpen_fatigue':bullpen}}
    snap=snapshots.build(row,captured_at_utc=NOW.isoformat())
    group=snap['groups']['confirmed_lineups']
    assert group['status']=='CONNECTED'
    assert group['complete'] is True
    assert group['missingValueKeys']==[]
    assert 'confirmed_lineups' in snap['connectedGroups']
    assert 'bullpen_availability' in snap['missingGroups']


@pytest.mark.parametrize('mode',['wrong_game','wrong_team','in_progress','new_start'])
def test_feed_identity_and_status_must_match_schedule(mode):
    game,history,feed,teams,get=fixtures()
    if mode=='wrong_game':feed['gameData']['game']['pk']=999
    if mode=='wrong_team':teams['home']['team']={'id':999}
    if mode=='in_progress':feed['gameData']['status']={'abstractGameState':'Live'}
    if mode=='new_start':feed['gameData']['datetime']['dateTime']='2026-09-09T21:00:00Z'
    a,_=source.observe('2026-09-09',game,history,get,now=lambda:NOW)
    assert a['source_status']=='NOT_CONNECTED_SOURCE_REQUIRED'


@pytest.mark.parametrize('mode',['unfinished','missing_role','missing_pitches','short_history','provider_error'])
def test_incomplete_workload_never_becomes_zero(mode):
    game,history,feed,teams,get=fixtures()
    if mode=='unfinished':history['payload']['dates'][0]['games'][0]['status']['abstractGameState']='Live'
    if mode=='missing_role':teams['home']['players']['ID111']['stats']['pitching']['gamesStarted']=None
    if mode=='missing_pitches':teams['home']['players']['ID111']['stats']['pitching']['numberOfPitches']=None
    if mode=='short_history':history['historyStartDateEt']='2026-09-08'
    if mode=='provider_error':
        def get(*a,**k):raise TimeoutError()
    _,b=source.observe('2026-09-09',game,history,get,now=lambda:NOW)
    assert b['source_status']=='NOT_CONNECTED_SOURCE_REQUIRED'
    assert 'home_reliever_usage_1d_3d_5d' not in b


def test_response_crossing_lock_discards_both_sources():
    game,history,feed,teams,get=fixtures(); times=[NOW]
    def fetch(url,timeout):
        result=get(url,timeout);times[0]=NOW+timedelta(hours=6);return result
    a,b=source.observe('2026-09-09',game,history,fetch,now=lambda:times[0])
    assert a['source_status']==b['source_status']=='NOT_CONNECTED_SOURCE_REQUIRED'


def test_snapshot_preserves_team_observations_without_inventing_legacy_composites():
    import mlb_fundamentals_snapshot_v2 as snapshots
    import mlb_ml_dual_model_v2 as r8
    game,history,feed,teams,get=fixtures()
    lineup,bullpen=source.observe('2026-09-09',game,history,get,now=lambda:NOW)
    row={'gameId':'mlb_statsapi:123','officialGamePk':123,'slateDateEt':'2026-09-09',
         'predictionSourcePullAt':NOW.isoformat(),'advanced_context':{'confirmed_lineups':lineup,'bullpen_fatigue':bullpen}}
    snap=snapshots.build(row,captured_at_utc=NOW.isoformat())
    assert not snapshots.validate(snap)
    assert snap['groups']['confirmed_lineups']['values']['homeMeanSeasonOps']==pytest.approx(.8)
    assert snap['groups']['bullpen_availability']['values']['homeUsage1d3d5d']['3d']['pitches']==18
    assert snap['groups']['bullpen_availability']['values']['homeComposite'] is None
    assert r8._strict_features({'fundamentalsSnapshotV2':snap},{}) == r8._strict_features({},{})


def test_partial_history_cannot_claim_zero_usage():
    game,history,feed,teams,get=fixtures();history['payload']['totalGames']=2
    _,bullpen=source.observe('2026-09-09',game,history,get,now=lambda:NOW)
    assert bullpen['source_status']=='NOT_CONNECTED_SOURCE_REQUIRED'


# Optional individual batting observations must not alter any existing
# predictor, required feature group, completeness gate, or source boundary.
from mlb_batter_observations_v1 import season_observation, VERSION as BATTING_VERSION


def test_individual_batters_are_bound_to_verified_order_and_existing_feed_receipt():
    game,history,feed,teams,get=fixtures(); original=deepcopy(feed); calls=[]
    teams['home']['players']['ID101']['seasonStats']['batting'].update(obp='.350',slg='.450')
    original=deepcopy(feed)
    def fetch(url,timeout): calls.append(url);return get(url,timeout)
    lineup,_=source.observe('2026-09-09',game,history,fetch,now=lambda:NOW)
    assert lineup['lineupSeasonBattingVersion']==BATTING_VERSION
    for side,base in (('home',100),('away',200)):
        rows=lineup[side+'_lineup_season_batting']
        assert len(rows)==9
        assert [r['playerId'] for r in rows]==list(range(base+1,base+10))
        assert [r['battingSlot'] for r in rows]==list(range(1,10))
        assert all(r['plateAppearances']==100 and r['ops']==.8 for r in rows)
    first=lineup['home_lineup_season_batting'][0]
    assert first['obp']==.35 and first['slg']==.45
    assert lineup['away_lineup_season_batting'][0]['obp'] is None
    assert lineup['sourceProvenance']['retrievedAtUtc']==NOW.isoformat()
    assert len(calls)==2 and feed==original
    assert lineup['home_lineup_mean_ops']==pytest.approx(.8)


@pytest.mark.parametrize('pa',[None,True,False,'',-1,-.5,2.5,float('inf'),float('nan'),[],{}])
def test_passive_batter_unknown_sample_cannot_authorize_rates(pa):
    row=season_observation(101,1,{'plateAppearances':pa,'ops':.8,'obp':.3,'slg':.5})
    assert row['plateAppearances'] is None and row['sampleStatus']=='SAMPLE_UNAVAILABLE'
    assert row['rateObservationCount']==0
    assert all(row[name] is None for name in ('ops','obp','slg'))


@pytest.mark.parametrize('pa',[0,0.0,'0'])
def test_passive_batter_zero_sample_is_not_zero_batting_quality(pa):
    row=season_observation(101,1,{'plateAppearances':pa,'ops':0,'obp':0,'slg':0})
    assert row['plateAppearances']==0 and row['sampleStatus']=='NO_PLATE_APPEARANCES'
    assert row['ops'] is row['obp'] is row['slg'] is None


def test_passive_batter_missing_ops_is_not_synthesized_from_other_rates():
    row=season_observation(101,1,{'plateAppearances':50,'obp':.3,'slg':.5})
    assert row['ops'] is None and row['obp']==.3 and row['slg']==.5
    assert row['rateObservationCount']==2


def test_passive_batter_real_zero_rate_with_sample_remains_observed():
    row=season_observation(101,1,{'plateAppearances':5,'ops':0,'obp':0,'slg':0})
    assert row['ops']==row['obp']==row['slg']==0
    assert row['rateObservationCount']==3


@pytest.mark.parametrize('name,invalid',[('ops',5.1),('obp',1.1),('slg',4.1),('ops',-.1),('obp',True),('slg',float('nan'))])
def test_passive_batter_invalid_rate_does_not_poison_other_fields(name,invalid):
    stats={'plateAppearances':100,'ops':.8,'obp':.3,'slg':.5};stats[name]=invalid
    row=season_observation(101,1,stats)
    assert row[name] is None and row['rateObservationCount']==2


@pytest.mark.parametrize('identity,slot',[(True,1),(0,1),(-1,1),('1',1),(1,True),(1,0),(1,10),(1,'1')])
def test_passive_batter_identity_and_slot_require_explicit_valid_values(identity,slot):
    with pytest.raises(ValueError):season_observation(identity,slot,{})


def test_passive_batting_context_does_not_change_frozen_v2_scoring_inputs():
    import mlb_fundamentals_snapshot_v2 as snapshots
    import mlb_ml_dual_model_v2 as r8
    game,history,feed,teams,get=fixtures()
    lineup,bullpen=source.observe('2026-09-09',game,history,get,now=lambda:NOW)
    row={'gameId':'mlb_statsapi:123','officialGamePk':123,'slateDateEt':'2026-09-09',
         'predictionSourcePullAt':NOW.isoformat(),'advanced_context':{'confirmed_lineups':lineup,'bullpen_fatigue':bullpen}}
    old_row=deepcopy(row)
    for key in ('lineupSeasonBattingVersion','home_lineup_season_batting','away_lineup_season_batting'):
        old_row['advanced_context']['confirmed_lineups'].pop(key)
    current=snapshots.build(row,captured_at_utc=NOW.isoformat())
    prior=snapshots.build(old_row,captured_at_utc=NOW.isoformat())
    assert not snapshots.validate(current) and not snapshots.validate(prior)
    assert current==prior
    assert r8._strict_features({'fundamentalsSnapshotV2':current},{})==r8._strict_features({'fundamentalsSnapshotV2':prior},{})


def test_passive_batting_capture_is_discarded_when_provider_crosses_cutoff():
    game,history,feed,teams,get=fixtures();times=[NOW]
    def fetch(url,timeout):
        result=get(url,timeout);times[0]=NOW+timedelta(hours=6);return result
    lineup,_=source.observe('2026-09-09',game,history,fetch,now=lambda:times[0])
    assert 'home_lineup_season_batting' not in lineup
    assert 'away_lineup_season_batting' not in lineup
    assert lineup['source_status']=='NOT_CONNECTED_SOURCE_REQUIRED'
