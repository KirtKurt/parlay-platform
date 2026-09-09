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
    assert lineup['home_lineup_confirmed'] is True
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
    if mode=='missing_ops':assert a['home_lineup_confirmed'] is True and a['home_lineup_mean_ops'] is None
    else:assert a['home_lineup_confirmed'] is None


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
