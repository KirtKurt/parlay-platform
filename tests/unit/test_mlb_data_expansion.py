import copy
import importlib.util
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import mlb_historical_development_data as historical
import mlb_data_admission as admission


def game(pk=1, start='2025-06-01T17:00:00Z', end='2025-06-01T20:00:00Z'):
    return {'officialGamePk':pk,'startAtUtc':start,'completedAtUtc':end,'teams':{
        'home':{'id':10,'batting':{'atBats':30,'hits':10,'baseOnBalls':3,'hitByPitch':1,'sacFlies':1,'doubles':2,'triples':0,'homeRuns':1},
                'relief':{'pitches':40,'outs':9},'priorStarters':{'outs':18,'earnedRuns':2,'strikeOuts':6,'baseOnBalls':2,'battersFaced':25}},
        'away':{'id':20}}}


def test_prior_day_statistics_exclude_target_and_future_game():
    old=game();target=game(2,'2025-06-02T17:00:00Z','2025-06-02T20:00:00Z')
    future=game(3,'2025-06-03T17:00:00Z','2025-06-03T20:00:00Z')
    a=historical.team_features(10,'2025-06-02',[old,target,future])
    assert a['priorCompletedGameIds']==[1]
    assert a['bullpenUsage1d3d5d']['1d']['pitches']==40
    assert a['priorStartingPitchersEra14d']==3
    assert a['priorStartingPitchersKMinusBbPct14d']==16
    assert a['teamBattingOps14d']==pytest.approx(14/35+15/30)
    target['teams']['home']['batting']['hits']=100
    assert historical.team_features(10,'2025-06-02',[old,target,future])==a


def test_suspended_game_late_completion_is_not_pregame_information():
    old=game(end='2025-06-02T18:00:00Z')
    with pytest.raises(ValueError,match='after feature cutoff'):
        historical.team_features(10,'2025-06-02',[old])


def test_workload_windows_and_empty_batting_are_distinct():
    result=historical.team_features(10,'2025-06-04',[game()])
    assert result['bullpenUsage1d3d5d']['1d']['pitches']==0
    assert result['bullpenUsage1d3d5d']['3d']['pitches']==40
    empty=historical.team_features(10,'2025-07-01',[game()])
    assert empty['teamBattingOps14d'] is None


def test_rejection_audit_preserves_missing_original_and_historical_sources():
    rows=[{'gameId':'1','slateDateEt':'2025-06-01','historicalTrainingOnly':True},
          {'gameId':'2','slateDateEt':'2026-09-01'}]
    before=copy.deepcopy(rows)
    result=admission.audit_rows(rows)
    assert rows==before
    assert result['admittedRows']==0
    assert result['classificationCounts']=={'HISTORICAL_MISSINGNESS_SEPARATE_DATASET':1,'MISSING_ORIGINAL_SNAPSHOT':1}
    assert result['rows'][0]['snapshotErrors']


def test_timezone_and_integer_integrity():
    with pytest.raises(ValueError):historical.utc('2025-06-01T12:00:00')
    for value in (True,-1,1.5,float('inf')):
        with pytest.raises((ValueError,OverflowError)):historical.count(value)


def test_historical_features_are_label_blind_and_never_live_evidence():
    spec=importlib.util.spec_from_file_location('bridge_fixtures',ROOT/'tests/unit/test_mlb_r7_historical_walkforward_bridge.py')
    fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
    record=fixtures._record();dataset=fixtures._dataset(record)
    target={'gamePk':123,'gameDate':record['commenceTime'],'teams':{'home':{'team':{'id':10,'name':'Home'}},'away':{'team':{'id':20,'name':'Away'}}}}
    a=historical.materialize(record,dataset,fixtures._artifact(),target,[],'2026-09-09T17:00:00Z')
    record['winner']='Away'
    b=historical.materialize(record,dataset,fixtures._artifact(),target,[],'2026-09-09T17:00:00Z')
    assert a['features']==b['features']
    assert a['featureFingerprint']==b['featureFingerprint']
    assert a['label']!=b['label']
    assert a['originalObservation'] is False
    assert a['prospectiveQualificationEvidence'] is False
    assert a['missingFeatures']==['archivedPregameCurrentStarterIdentity','archivedPregameBattingOrder']
    target['teams']['home']['team']['name']='Other'
    with pytest.raises(ValueError,match='name or side'):historical.materialize(record,dataset,fixtures._artifact(),target,[],'2026-09-09T17:00:00Z')


def test_writer_uses_separate_namespace_and_checks_exact_readback():
    class S3:
        def get_bucket_versioning(self,**kwargs):return {'Status':'Enabled'}
        def put_object(self,**kwargs):
            self.write=kwargs;return {'VersionId':'v1'}
        def get_object(self,**kwargs):
            from io import BytesIO
            assert kwargs['VersionId']=='v1'
            return {'Body':BytesIO(self.write['Body'])}
    s3=S3();p=historical.write_verified(s3,'bucket',{'rows':[]})
    assert p['key'].startswith('mlb/development-data/reconstructed-v1/')
    assert p['sha256']==historical.digest({'rows':[]})


def test_preparation_workflow_cannot_invoke_learning_or_write_canonical_state():
    script=(ROOT/'scripts/run_mlb_data_expansion.py').read_text()
    assert '"mode":"status"' in script
    for forbidden in ('.update_item(','.delete_item(','.put_item(', '"mode":"train"', '"mode":"selection_capture"'):
        assert forbidden not in script
    assert 'PREFIX = \'mlb/development-data/reconstructed-v1/\'' in (ROOT/'scripts/mlb_historical_development_data.py').read_text()


def test_daily_report_freshness_and_separate_historical_counts(tmp_path):
    import report_mlb_30m_progress as reporter
    from datetime import datetime,timezone
    import json
    p=tmp_path/'report.json'
    data={'ok':True,'createdAtUtc':'2026-09-09T10:00:00+00:00',
          'historicalDevelopment':{'preparedGames':1000,'rejectedGames':2},'daily':[]}
    p.write_text(json.dumps(data))
    result=reporter._data_admission_summary(p,datetime(2026,9,9,12,tzinfo=timezone.utc))
    assert result['status']=='CURRENT'
    assert 'not original live observations' in '\n'.join(reporter._data_admission_lines({'dataAdmission':result}))
    assert reporter._data_admission_summary(p,datetime(2026,9,11,12,tzinfo=timezone.utc))['status']=='STALE'
    data['ok']=False;p.write_text(json.dumps(data))
    assert reporter._data_admission_summary(p,datetime(2026,9,9,12,tzinfo=timezone.utc))['status']=='INCOMPLETE'


def test_publisher_rejects_mixed_run_bundles(tmp_path):
    import publish_mlb_data_admission as publisher
    import json
    for path,at in zip(publisher.FILES,('2026-09-09T10:00:00Z','2026-09-09T11:00:00Z')):
        target=tmp_path/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps({'createdAtUtc':at}))
    with pytest.raises(ValueError,match='different runs'):publisher.publish(tmp_path)


def test_postponed_schedule_occurrence_does_not_duplicate_actual_game():
    from run_mlb_data_expansion import normalize_schedule
    original={'gamePk':1,'gameType':'R','gameDate':'2025-04-05T20:10:00Z',
              'status':{'abstractGameState':'Final','detailedState':'Postponed'},
              'teams':{'home':{'team':{'id':10}},'away':{'team':{'id':20}}}}
    played=copy.deepcopy(original);played['gameDate']='2025-04-06T17:35:00Z'
    played['status']['detailedState']='Final';played['teams']['home']['isWinner']=True
    p={'totalGames':2,'dates':[{'games':[original]},{'games':[played]}]}
    games=normalize_schedule(p)
    assert len(games)==1 and games[0]['gameDate']==played['gameDate']
    assert historical.is_final(original) is False
    assert historical.is_final(games[0]) is True
    played['teams']['home']['team']['id']=99
    with pytest.raises(ValueError,match='conflicting official'):normalize_schedule(p)


def test_incomplete_official_schedule_is_still_rejected():
    from run_mlb_data_expansion import normalize_schedule
    with pytest.raises(ValueError,match='incomplete'):normalize_schedule({'totalGames':1,'dates':[]})


def test_source_cache_conflict_reads_the_first_verified_receipt(monkeypatch):
    import run_mlb_data_expansion as runner
    from botocore.exceptions import ClientError
    from io import BytesIO
    import hashlib
    value={'officialGamePk':1};value['fingerprint']=historical.digest(value)
    body=historical.encoded(value)
    class S3:
        def __init__(self):self.reads=0
        def get_object(self,**kwargs):
            self.reads+=1
            if self.reads==1:raise ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
            return {'Body':BytesIO(body),'Metadata':{'sha256':hashlib.sha256(body).hexdigest()}}
        def put_object(self,**kwargs):
            assert kwargs['IfNoneMatch']=='*'
            raise ClientError({'Error':{'Code':'PreconditionFailed'}},'PutObject')
    monkeypatch.setattr(runner.advanced,'_http_get_json',lambda *a,**k:{})
    monkeypatch.setattr(runner.historical,'compact_game',lambda *a:value)
    s3=S3();assert runner.source_game({'gamePk':1},s3,'bucket',True)==value
    assert s3.reads==2


def test_source_cache_permission_failure_is_not_retried_as_missing():
    import run_mlb_data_expansion as runner
    from botocore.exceptions import ClientError
    class S3:
        def get_object(self,**kwargs):raise ClientError({'Error':{'Code':'AccessDenied'}},'GetObject')
    with pytest.raises(ClientError):runner.source_game({'gamePk':1},S3(),'bucket',True)


def test_one_final_game_does_not_finalize_a_partially_settled_slate():
    final={'status':{'abstractGameState':'Final','detailedState':'Final'},'teams':{'home':{'isWinner':True},'away':{'isWinner':False}}}
    preview={'status':{'abstractGameState':'Preview','detailedState':'Scheduled'},'teams':{}}
    assert historical.slate_complete([final,preview]) is False
    assert historical.slate_complete([final]) is True
    report=admission.audit_rows([{'gameId':'1','slateFinalized':False}])
    assert report['admittedRows']==0
    assert report['rows'][0]['reason']=='waiting for complete slate settlement'


def test_cross_year_schedule_requests_do_not_silently_drop_a_season(monkeypatch):
    import run_mlb_data_expansion as runner
    from urllib.parse import parse_qs,urlparse
    calls=[]
    def fetch(url,timeout):
        query=parse_qs(urlparse(url).query);calls.append(query)
        year=int(query['startDate'][0][:4]);assert query['endDate'][0][:4]==str(year)
        game={'gamePk':year,'gameType':'R','gameDate':str(year)+'-06-01T17:00:00Z','status':{'detailedState':'Final'},
              'teams':{'home':{'team':{'id':1}},'away':{'team':{'id':2}}}}
        return {'totalGames':1,'dates':[{'games':[game]}]}
    monkeypatch.setattr(runner.advanced,'_http_get_json',fetch)
    games,receipt=runner.schedule('2025-04-01','2026-09-08')
    assert [g['gamePk'] for g in games]==[2025,2026]
    assert len(calls)==2 and len(receipt['requests'])==2


def test_prior_game_finishing_after_midnight_is_usable_only_after_completion():
    old=game(end='2025-06-02T06:00:00Z')
    result=historical.team_features(10,'2025-06-02',[old],'2025-06-02T20:00:00Z')
    assert result['priorCompletedGameIds']==[1]
    with pytest.raises(ValueError,match='after feature cutoff'):
        historical.team_features(10,'2025-06-02',[old],'2025-06-02T05:00:00Z')
