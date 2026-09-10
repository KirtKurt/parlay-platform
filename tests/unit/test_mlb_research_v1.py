import copy
import io
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
import pytest
from botocore.exceptions import ClientError

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'mlb_research'))
sys.path.insert(0,str(ROOT/'scripts'))
import mlb_research_store_v1 as storage
import mlb_research_sources_v1 as source
import mlb_player_windows_v1 as players
import mlb_research_signals_v1 as signals
import mlb_research_models_v1 as models
import mlb_research_runtime_v1 as runtime
from mlb_research_dataset_v1 import publish_dataset

AT=datetime(2026,9,9,18,tzinfo=timezone.utc)


class MemoryS3:
    def __init__(self): self.items={};self.sequence=0
    def get_object(self,Bucket,Key,VersionId=None):
        versions=self.items.get((Bucket,Key),[])
        chosen=next((v for v in versions if v['VersionId']==VersionId),None) if VersionId else (versions[-1] if versions else None)
        if chosen is None: raise ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
        return {**chosen,'Body':io.BytesIO(chosen['Body'])}
    def put_object(self,**kwargs):
        key=(kwargs['Bucket'],kwargs['Key']);existing=self.items.get(key,[])
        if (kwargs.get('IfNoneMatch')=='*' and existing) or ('IfMatch' in kwargs and (not existing or existing[-1]['ETag']!=kwargs['IfMatch'])):
            raise ClientError({'Error':{'Code':'PreconditionFailed'}},'PutObject')
        self.sequence+=1
        value={'Body':kwargs['Body'],'Metadata':kwargs['Metadata'],'VersionId':str(self.sequence),'ETag':str(self.sequence)}
        self.items.setdefault(key,[]).append(value)
        return {'VersionId':value['VersionId'],'ETag':value['ETag']}
    def get_paginator(self,name):
        return self
    def paginate(self,Bucket,Prefix):
        yield {'Contents':[{'Key':k} for b,k in self.items if b==Bucket and k.startswith(Prefix)]}


@pytest.fixture
def store(): return storage.Store('test',MemoryS3())


def game(pk=1,day='2026-09-09',state='Preview',start=None):
    return {'gamePk':pk,'gameType':'R','gameDate':start or day+'T18:18:00+00:00',
        'status':{'abstractGameState':state},'teams':{
            'home':{'team':{'id':10,'name':'Home'},'isWinner':True,'score':5},
            'away':{'team':{'id':20,'name':'Away'},'isWinner':False,'score':3}}}


def snap(g=None):
    g=g or game()
    cutoff=storage.utc(g['gameDate'])-timedelta(minutes=10)
    f={'marketHomeProbability':.55,'signal':1.}
    return {'officialGamePk':str(g['gamePk']),'slateDateEt':g['gameDate'][:10], 'commenceTime':g['gameDate'],
            'checkpoint':'T10','featureCutoffUtc':cutoff.isoformat(),'capturedAtUtc':(cutoff-timedelta(minutes=1)).isoformat(),
            'features':f,'featureFingerprint':storage.digest(f),'originalObservation':True,
            'outcomeKnownAtCapture':False,'productionAuthority':False}


def frozen(first='2026-09-09'):
    return {'id':'frozen-one','firstProspectiveSlateDate':first,'frozenAtUtc':'2026-09-08T10:00:00+00:00',
            'developmentRows':600,'model':{'kind':'linear','features':[],'weights':[0.],'means':{},'scales':{}}}


def activate(store,value):
    store.latest('active-frozen.json',{'artifact':store.artifact('frozen',value)})


def row(pk=1,day='2026-01-01',original=False):
    f={'marketHomeProbability':.55,'signal':float(pk%3)}
    return {'officialGamePk':str(pk),'slateDateEt':day,'features':f,'featureFingerprint':storage.digest(f),
            'slateComplete':True,'originalObservation':original,'homeWon':pk%2,'homeRuns':5 if pk%2 else 2,'awayRuns':2 if pk%2 else 5}


def test_versioned_readback_checksum_and_conditional_history(store):
    pointer=store.artifact('input',{'value':1})
    assert store.load(pointer)=={'value':1}
    store.once('snapshot',{'value':1});assert store.once('snapshot',{'value':2})=={'value':1}
    old=store.read('snapshot')
    store.put('snapshot',{'value':3},etag=old[1])
    with pytest.raises(ClientError): store.put('snapshot',{'value':4},etag=old[1])
    altered={**pointer,'sha256':'0'*64}
    with pytest.raises(ValueError,match='binding'):store.load(altered)


def test_storage_lease_prevents_overlap_and_releases(store):
    a=store.acquire('capture');assert a and not store.acquire('capture')
    store.release('capture','someone-else');assert not store.acquire('capture')
    store.release('capture',a);assert store.acquire('capture')


@pytest.mark.parametrize('bad',[float('inf'),float('-inf'),float('nan'),-0.1,1.1,[.5]])
def test_invalid_probability_rejected_before_clipping(bad):
    with pytest.raises((ValueError,TypeError)):models.metrics([{'homeWon':1}],[bad])


@pytest.mark.parametrize('bad',[2,-1,float('nan'),.5])
def test_invalid_labels_rejected(bad):
    with pytest.raises(ValueError):models.metrics([{'homeWon':bad}],[.5])


def test_player_era_uses_outs_and_zero_exposure_stays_missing():
    values=dict.fromkeys(players.PITCHING,0);values.update(outs=19,earnedRuns=2,hits=4,baseOnBalls=1,battersFaced=24,strikeOuts=5)
    r=players.rates(values,'pitching')
    assert r['era']==pytest.approx(54/19) and r['whip']==pytest.approx(15/19)
    assert r['kMinusBbPct']==pytest.approx(100*4/24)
    assert players.rates(dict.fromkeys(players.PITCHING,0),'pitching')['era'] is None


def test_calendar_windows_include_completed_today_and_exclude_eighth_date():
    stats={k:1 for k in players.PITCHING}
    entries=[{'date':(AT.date()-timedelta(days=n)).isoformat(),'gamePk':n+1,'stats':stats} for n in (0,6,7,14,29,30)]
    assert players.aggregate(entries,'pitching',AT,7)['gameIds']==[1,7]
    assert players.aggregate(entries,'pitching',AT,15)['appearances']==4
    assert players.aggregate(entries,'pitching',AT,30)['appearances']==5
    assert players.aggregate(None,'pitching',AT,7)['stats'] is None
    assert players.aggregate([],'pitching',AT,7)['status']=='NO_APPEARANCES'


def test_game_log_missing_final_source_cannot_look_like_zero_usage():
    split={'date':'2026-09-08','gameType':'R','game':{'gamePk':10},'stat':dict.fromkeys(players.PITCHING,1)}
    person={'stats':[{'type':{'displayName':'gameLog'},'group':{'displayName':'pitching'},'splits':[split]}]}
    with pytest.raises(ValueError,match='independently'):players.logs(person,'pitching',{},AT)
    prior={10:{'completedAtUtc':'2026-09-08T23:00:00Z','startAtUtc':'2026-09-08T18:00:00Z'}}
    assert len(players.logs(person,'pitching',prior,AT))==1
    person['stats'][0]['splits'].append(split)
    with pytest.raises(ValueError,match='duplicate'):players.logs(person,'pitching',prior,AT)


def test_same_day_game_cannot_contribute_before_completion():
    split={'date':'2026-09-09','gameType':'R','game':{'gamePk':10},'stat':dict.fromkeys(players.PITCHING,1)}
    person={'stats':[{'type':{'displayName':'gameLog'},'group':{'displayName':'pitching'},'splits':[split]}]}
    assert players.logs(person,'pitching',{10:{'startAtUtc':'2026-09-09T16:00:00Z','completedAtUtc':'2026-09-09T19:00:00Z'}},AT)==[]


def test_prior_team_history_missing_or_partial_suppresses_workload():
    g=game();old=game(2,'2026-09-08','Final')
    for history in (None,[old]):
        value=signals.prior_features(g,[],history,AT)
        assert value['homePriorHistoryComplete']==0 and value['homePriorBullpenPitches3d'] is None


def test_freshness_checks_include_cross_bucket_identity(store):
    value={'coverageComplete':True,'games':[1]}
    pointer=store.artifact('bundle',value)
    store.latest('prior-games.json',{'artifact':pointer,'updatedAtUtc':AT.isoformat()})
    assert runtime.bundle(store,'prior-games',AT)[1]=='COMPLETE'
    assert runtime.bundle(store,'prior-games',AT+timedelta(hours=4))[1]=='STALE'
    assert runtime.bundle(store,'prior-games',AT-timedelta(minutes=1))[1]=='INVALID_FUTURE_TIME'
    other=storage.Store('other',store.s3)
    assert runtime.bundle(other,'prior-games',AT)[1]=='NOT_OBSERVED'


def test_statcast_only_fair_contact_and_missing_player_coverage():
    rows=[{'pitcher':'10','batter':'20','type':t,'launch_speed':v,'release_speed':'95','pitch_type':'FF','estimated_woba_using_speedangle':'.4'} for t,v in [('X','96'),('S','110')]]
    value=source.statcast_player(rows,10,'pitcher')
    assert value['fairContacts']==1 and value['hardHitRate']==1 and value['xwobaOnContact']==.4
    assert source.statcast_player(rows,99,'pitcher')['hardHitRate'] is None


@pytest.mark.parametrize('field,value',[('originalObservation',False),('outcomeKnownAtCapture',True),('officialGamePk','2'),
    ('slateDateEt','2026-09-08'),('checkpoint','T30'),('featureCutoffUtc','2026-09-09T18:10:00Z'),('capturedAtUtc','2026-09-09T18:10:00Z')])
def test_original_snapshot_binding_rejects_invalid_evidence(field,value):
    s=snap();s[field]=value
    with pytest.raises(ValueError):runtime.validate_snapshot(s,game())


def test_slow_prediction_cannot_write_after_t10(store,monkeypatch):
    s=snap();times=iter([storage.utc(s['capturedAtUtc']),storage.utc(s['featureCutoffUtc'])+timedelta(seconds=1)])
    with pytest.raises(ValueError,match='deadline'):runtime.save_prediction(store,s,frozen(),clock=lambda:next(times))
    assert list(store.keys('predictions/'))==[]


def test_capture_retries_missing_prediction_from_existing_immutable_snapshot(store,monkeypatch):
    g=game();s=snap();s['capturedAtUtc']=AT.isoformat()
    activate(store,frozen());monkeypatch.setattr(runtime,'now',lambda:AT)
    monkeypatch.setattr(source,'schedule',lambda *args:([g],{}));monkeypatch.setattr(source,'markets',lambda games:{})
    monkeypatch.setattr(runtime,'snapshot',lambda *args:s)
    original=runtime.save_prediction;calls=[]
    def unreliable(*args,**kwargs):
        calls.append(1)
        if len(calls)==1: raise OSError('transient prediction write')
        return original(*args,**kwargs,clock=lambda:AT)
    monkeypatch.setattr(runtime,'save_prediction',unreliable)
    assert runtime.capture(store)['failures']
    saved=store.get('snapshots/2026-09-09/1/T10.json')
    assert saved==s
    report=runtime.capture(store)
    assert report['predictionWrites']==1
    assert store.get('snapshots/2026-09-09/1/T10.json')==saved


def test_missing_first_final_slate_seals_failure_without_skipping(store,monkeypatch):
    monkeypatch.setattr(runtime,'now',lambda:AT+timedelta(days=2))
    monkeypatch.setattr(source,'schedule',lambda *args:([game(state='Final')],{}))
    result=runtime.evaluate_fresh(store,frozen())
    assert result['status']=='FRESH_TEST_SEALED_INCOMPLETE' and result['testCanBeReopened'] is False
    before=copy.deepcopy(result)
    monkeypatch.setattr(source,'schedule',lambda *args:pytest.fail('sealed test cannot be reopened'))
    assert runtime.evaluate_fresh(store,frozen())==before


def test_pending_first_slate_waits_without_skipping(store,monkeypatch):
    monkeypatch.setattr(runtime,'now',lambda:AT+timedelta(days=2))
    monkeypatch.setattr(source,'schedule',lambda *args:([game(state='Live')],{}))
    result=runtime.evaluate_fresh(store,frozen())
    assert result['status']=='WAITING_FOR_COMPLETE_FRESH_SLATES' and not result['sealed']


def test_failed_test_starts_new_candidate_only_after_new_rows(store,monkeypatch):
    old=frozen();activate(store,old)
    seal={'sealed':True,'passed':False,'frozenId':old['id'],'status':'FAILED'}
    store.once('sealed/frozen-one.json',seal)
    data={'rows':[row(pk=i) for i in range(700)],'originalRows':0}
    store.latest('dataset.json',{'artifact':store.artifact('datasets',data),'rowsHash':storage.digest(data['rows'])})
    monkeypatch.setattr(models,'research',lambda rows:{'status':'READY_FOR_NEW_FUTURE_TEST','model':old['model']})
    result=runtime.train(store)
    new=runtime.active_frozen(store)
    assert new['id']!=old['id'] and new['predecessorFrozenId']==old['id']
    assert new['firstProspectiveSlateDate']>storage.now().astimezone(source.ET).date().isoformat()
    assert store.get('sealed/frozen-one.json')==seal


def test_dataset_publication_preserves_original_and_historical_shards(store):
    historical={'rows':[row(1),row(2,'2026-01-02')]}
    store.latest('historical-input.json',{'artifact':store.artifact('historical',historical)})
    original={'rows':[row(1,original=True)]}
    store.latest('original-index.json',{'slates':{'2026-01-01':store.artifact('original',original)}})
    data=publish_dataset(store)
    assert len(data['rows'])==2 and data['originalRows']==1
    assert data['rows'][0]['originalObservation'] is True
    assert store.load(store.get('dataset.json')['artifact'])['rows']==data['rows']


def test_invalid_dataset_fingerprint_is_not_published(store):
    r=row();r['features']['marketHomeProbability']=.8
    store.latest('historical-input.json',{'artifact':store.artifact('historical',{'rows':[r]})})
    with pytest.raises(ValueError,match='invalid'):publish_dataset(store)
    assert store.get('dataset.json') is None


def test_training_only_feature_selection_and_chronological_holdout(monkeypatch):
    from datetime import date
    rows=[row(i,(date(2025,1,1)+timedelta(days=i//20)).isoformat()) for i in range(1000)]
    for r in rows:
        r['features']['signal']=float(r['homeWon'])
        if r['slateDateEt']>=rows[800]['slateDateEt']:r['features']['holdoutOnly']=100.
    result=models.research(rows)
    assert set(result['partitionDates']['development']).isdisjoint(result['partitionDates']['holdout'])
    assert max(result['partitionDates']['development'])<min(result['partitionDates']['holdout'])
    assert 'holdoutOnly' not in result['model']['features']
    assert result['holdout']['model']['count']==200
    assert result['prospectiveQualificationEvidence'] is False


def test_health_never_hides_stale_or_failed_sources(store,monkeypatch):
    monkeypatch.setattr(runtime,'now',lambda:AT)
    store.latest('capture.json',{'ok':True,'status':'CAPTURE_COMPLETE','updatedAtUtc':(AT-timedelta(hours=1)).isoformat()})
    store.latest('training.json',{'ok':False,'status':'FAILED','updatedAtUtc':AT.isoformat()})
    store.latest('ingestion.json',{'ok':True,'status':'PARTIAL','updatedAtUtc':AT.isoformat()})
    status=runtime.health(store)
    assert [status[k]['health'] for k in ('capture','training','ingestion')]==['STALE','FAILED','PARTIAL']


def test_research_deployment_has_single_owner_and_scoped_storage():
    text=(ROOT/'template.yaml').read_text()
    block=text.split('\n  MLBResearchFunction:\n')[1].split('\n  SoccerSchedulerFunction:')[0]
    assert 'Events:' not in block and 'ReservedConcurrentExecutions' not in block and 'DynamoDB' not in block
    assert 'mlb/development-data/research-v1/*' in block
    dispatch=(ROOT/'hello_world/mlb_research_dispatch_v1.py').read_text()
    assert "('training','selection_capture')" in dispatch
    ingestion=(ROOT/'scripts/run_mlb_research_ingestion.py').read_text()
    assert '.invoke(' not in ingestion and 'lambda_handler(' not in ingestion


def test_closed_roof_suppresses_outdoor_forecast_features(monkeypatch):
    g=game();payload={'gameData':{'venue':{'fieldInfo':{'roofType':'Dome'},'location':{'defaultCoordinates':{'latitude':1,'longitude':2}}},'weather':{'condition':'Roof Closed'}}}
    def fetch(url):
        if '/points/' in url:return {'properties':{'forecastHourly':'https://forecast'}},{}
        if url=='https://forecast':return {'properties':{'periods':[{'startTime':'2026-09-09T18:00:00Z','endTime':'2026-09-09T19:00:00Z','temperature':90,'temperatureUnit':'F'}]}},{}
        return {'transactions':[]},{}
    monkeypatch.setattr(source,'fetch',fetch)
    result=signals.conditions(g,payload,[],AT)
    assert result['features']['roofClosedObserved']==1
    assert 'forecastTemperatureF' not in result['features']


def test_original_cohort_can_enable_new_player_features_without_old_history_dilution():
    from datetime import date
    rows=[row(i,(date(2025,1,1)+timedelta(days=i//20)).isoformat()) for i in range(1000)]
    rows += [row(1000+i,(date(2026,1,1)+timedelta(days=i//20)).isoformat(),True) for i in range(800)]
    for r in rows:
        if r['originalObservation']:r['features']['starterEra7d']=float(r['homeWon'])
    result=models.research(rows)
    assert result['sourceCohort']=='ORIGINAL_OBSERVATIONS' and result['rows']==800
    assert 'starterEra7d' in result['model']['features']


def test_rolling_dataset_detects_new_game_identities_after_failed_test(store,monkeypatch):
    previous={'rows':[row(i) for i in range(600)],'originalRows':0}
    pointer={'artifact':store.artifact('datasets',previous),'rowsHash':storage.digest(previous['rows'])}
    old={**frozen(),'dataset':pointer};activate(store,old)
    store.once('sealed/frozen-one.json',{'sealed':True,'passed':False,'frozenId':old['id'],'status':'FAILED'})
    current={'rows':[row(i) for i in range(100,700)],'originalRows':0}
    store.latest('dataset.json',{'artifact':store.artifact('datasets',current),'rowsHash':storage.digest(current['rows'])})
    monkeypatch.setattr(models,'research',lambda rows:{'status':'READY_FOR_NEW_FUTURE_TEST','model':old['model']})
    result=runtime.train(store)
    assert result['newGamesSincePreviousFreeze']==100
    assert runtime.active_frozen(store)['id']!=old['id']


def test_research_report_exposes_missing_sources_and_deadline_misses():
    import report_mlb_30m_progress as report
    lines='\n'.join(report._research_lines({'research':{
        'capture':{'health':'PARTIAL','evidence':{'missedT10':['123']}},
        'training':{'health':'STALE','evidence':{}},
        'ingestion':{'health':'PARTIAL','evidence':{'statcastDays':9,'errors':[{}]}}}}))
    assert 'PARTIAL' in lines and 'STALE' in lines and '123' in lines and '9/30' in lines


def test_schedule_queries_each_season_and_counts_makeup_once(monkeypatch):
    from urllib.parse import parse_qs,urlsplit
    calls=[]
    postponed=game(1,day='2025-09-09',state='Final')
    postponed['status']['detailedState']='Postponed'
    makeup=game(1,day='2025-09-10',state='Final')
    resumed=game(2,day='2025-09-11',state='Final')
    resumed['resumedFrom']='2025-09-10T18:18:00+00:00'
    original=game(2,day='2025-09-10',state='Final')
    def fetch(url):
        q=parse_qs(urlsplit(url).query);calls.append(q)
        entries=[postponed,makeup,original,resumed] if q['season']==['2025'] else [game(3)]
        return {'totalGames':len(entries),'dates':[{'games':entries}]},{'sha256':'receipt'}
    monkeypatch.setattr(source,'fetch',fetch)
    games,receipt=source.schedule('2025-09-01','2026-09-09')
    assert [g['gamePk'] for g in games]==[1,2,3]
    assert [q['season'] for q in calls]==[['2025'],['2026']]
    assert calls[0]['endDate']==['2025-12-31'] and calls[1]['startDate']==['2026-01-01']
    assert len(receipt['pages'])==2 and len(receipt['excludedNonPlayableEntries'])==2
    assert not source.final(postponed) and not source.playable(resumed)


def test_schedule_still_rejects_duplicate_playable_games(monkeypatch):
    monkeypatch.setattr(source,'fetch',lambda url:({'totalGames':2,'dates':[{'games':[game(),game()]}]},{}))
    with pytest.raises(ValueError,match='duplicate or ambiguous'):
        source.schedule('2026-09-09')


def test_historical_failure_does_not_stop_current_source_ingestion(monkeypatch,store):
    import run_mlb_research_ingestion as ingestion
    monkeypatch.setattr(ingestion,'now',lambda:AT)
    monkeypatch.setattr(ingestion,'publish_historical',lambda store:(_ for _ in ()).throw(ValueError('bad historical source')))
    monkeypatch.setattr(source,'schedule',lambda *args:([],{}))
    result=ingestion.ingest(store,seconds=60)
    assert result['status']=='PARTIAL' and result['statcastDays']==30
    assert result['errors']==[{'source':'historical','error':'ValueError'}]
    assert store.get('dataset.json')['rows']==0


def test_new_deployment_refreshes_training_from_existing_capture_owner(monkeypatch,store):
    monkeypatch.setenv('INQSI_DEPLOY_GIT_SHA','old-release')
    store.latest('dataset.json',{'rowsHash':'unchanged-data'})
    store.latest('training.json',{'ok':True,'datasetRowsHash':'unchanged-data',
                                 'updatedAtUtc':(AT-timedelta(minutes=20)).isoformat()})
    monkeypatch.setenv('INQSI_DEPLOY_GIT_SHA','new-release')
    monkeypatch.setattr(runtime,'Store',lambda:store)
    monkeypatch.setattr(runtime,'now',lambda:AT)
    monkeypatch.setattr(runtime,'capture',lambda store:{'ok':True})
    calls=[]
    def train(store):
        calls.append('train')
        store.latest('training.json',{'ok':True,'status':'REFRESHED','datasetRowsHash':'unchanged-data',
                                     'updatedAtUtc':AT.isoformat()})
        return {'status':'REFRESHED'}
    monkeypatch.setattr(runtime,'train',train)
    class Context:
        def get_remaining_time_in_millis(self):return 400000
    assert runtime.lambda_handler({'mode':'capture'},Context())['newDataTraining']=='REFRESHED'
    assert store.get('training.json')['deploymentGitSha']=='new-release'
    runtime.lambda_handler({'mode':'capture'},Context())
    assert calls==['train']


def market_event(g,offset=60,event_id='provider-one',age=30):
    return {'id':event_id,'home_team':'Home','away_team':'Away',
        'commence_time':(storage.utc(g['gameDate'])+timedelta(seconds=offset)).isoformat(),
        'bookmakers':[{'key':'book','last_update':(AT-timedelta(seconds=age)).isoformat(),
            'markets':[{'key':'h2h','outcomes':[{'name':'Home','price':1.8},{'name':'Away','price':2.1}]}]}]}


@pytest.mark.parametrize('offset,accepted',[(-91,False),(-90,True),(-60,True),(0,True),(60,True),(90,True),(91,False)])
def test_provider_minute_rounding_keeps_official_identity_and_deadline(monkeypatch,offset,accepted):
    g=game();event=market_event(g,offset)
    monkeypatch.setenv('ODDS_API_KEY','test-only')
    monkeypatch.setattr(source,'fetch',lambda url:([event],{'retrievedAtUtc':AT.isoformat()}))
    result=source.markets([g])
    assert bool(result)==accepted
    if accepted:
        assert result['1']['officialCommenceTime']==g['gameDate']
        assert result['1']['officialGamePk']=='1'
        assert result['1']['providerEventId']=='provider-one'
        assert result['1']['providerStartOffsetSeconds']==offset
        assert 0<result['1']['marketHomeProbability']<1


@pytest.mark.parametrize('case',['two_events','two_games','reused_event_id','missing_event_id','stale_quotes','reversed_teams'])
def test_provider_alignment_rejects_ambiguous_identity_and_stale_prices(monkeypatch,case):
    g=game();events=[market_event(g)];games=[g]
    if case=='two_events':events.append(market_event(g,-30,'provider-two'))
    if case=='two_games':games.append(game(2,start=(storage.utc(g['gameDate'])+timedelta(seconds=60)).isoformat()))
    if case=='reused_event_id':events.append(market_event(g,1800))
    if case=='missing_event_id':events[0]['id']=''
    if case=='stale_quotes':events=[market_event(g,age=901)]
    if case=='reversed_teams':events[0].update(home_team='Away',away_team='Home')
    monkeypatch.setenv('ODDS_API_KEY','test-only')
    monkeypatch.setattr(source,'fetch',lambda url:(events,{'retrievedAtUtc':AT.isoformat()}))
    assert source.markets(games)=={}
