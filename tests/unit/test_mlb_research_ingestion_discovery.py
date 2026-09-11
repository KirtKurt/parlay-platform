from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'mlb_research'))
sys.path.insert(0,str(ROOT/'scripts'))
from run_mlb_research_ingestion import discovery_summary,_snapshot_problem
from mlb_research_store_v1 import digest,utc


class Store:
    def __init__(self,pointer=None,value=None): self.pointer=pointer;self.value=value
    def get(self,name): return deepcopy(self.pointer) if name=='feature-discovery.json' else None
    def load(self,pointer):
        if self.value is Exception: raise ValueError('fixture')
        return deepcopy(self.value)


def game():
    return {'gamePk':1,'gameDate':'2026-09-10T20:00:00Z','teams':{
        'home':{'team':{'id':10}},'away':{'team':{'id':20}}}}


def snapshot():
    g=game(); cutoff=utc(g['gameDate'])-timedelta(minutes=10)
    features={'marketHomeProbability':.55,'signal':1.0}
    return {'officialGamePk':'1','slateDateEt':'2026-09-10','commenceTime':g['gameDate'],
            'checkpoint':'T10','featureCutoffUtc':cutoff.isoformat(),
            'capturedAtUtc':(cutoff-timedelta(minutes=1)).isoformat(),
            'features':features,'featureFingerprint':digest(features),
            'originalObservation':True,'outcomeKnownAtCapture':False,'productionAuthority':False}


def test_discovery_summary_reports_research_evidence_without_authority():
    value={'status':'CANDIDATES_FOUND','candidates':[{'feature':'starterGap'},{'feature':'bullpenGap'}],
           'evaluatedFeatures':42,'developmentRows':900,'holdoutRows':220,
           'holdoutLabelsInspectedByScreen':False,'datasetRowsHash':'data','implementationSha256':'impl'}
    result=discovery_summary(Store({'artifact':{'name':'x','versionId':'1','sha256':'x'}},value))
    assert result=={'status':'CANDIDATES_FOUND','candidateCount':2,
        'candidateFeatures':['starterGap','bullpenGap'],'evaluatedFeatures':42,'developmentRows':900,
        'holdoutRows':220,'holdoutLabelsInspectedByScreen':False,'datasetRowsHash':'data',
        'implementationSha256':'impl','productionAuthorityChanged':False}


def test_discovery_summary_handles_missing_and_corrupt_evidence_without_raising():
    assert discovery_summary(Store())=={'status':'NOT_OBSERVED','candidateCount':0,'productionAuthorityChanged':False}
    bad=discovery_summary(Store({'artifact':{'name':'x','versionId':'1','sha256':'x'}},Exception))
    assert bad['status']=='EVIDENCE_READ_FAILED'
    assert bad['candidateCount']==0 and bad['productionAuthorityChanged'] is False
    assert bad['error']=='ValueError'


def test_snapshot_problem_is_empty_for_valid_original_evidence():
    assert _snapshot_problem(snapshot(),game()) is None


def test_snapshot_problem_preserves_timing_evidence_for_postgame_start_change():
    value=snapshot(); current=game(); current['gameDate']='2026-09-10T20:05:00Z'
    problem=_snapshot_problem(value,current)
    assert problem['gamePk']=='1'
    assert problem['error']=='ValueError'
    assert problem['code']=='invalid original T10 observation'
    assert problem['snapshotCommenceTime']=='2026-09-10T20:00:00Z'
    assert problem['currentOfficialStart']=='2026-09-10T20:05:00Z'
    assert problem['snapshotFingerprint']==digest(value)
    assert 'features' not in problem


def test_snapshot_problem_never_invents_validity_for_corrupt_fingerprint():
    value=snapshot(); value['features']['signal']=2.0
    problem=_snapshot_problem(value,game())
    assert problem['code']=='invalid original T10 observation'
    assert problem['capturedAtUtc']==value['capturedAtUtc']
