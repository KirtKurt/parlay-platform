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
