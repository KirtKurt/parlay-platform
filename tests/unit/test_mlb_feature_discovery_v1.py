from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'mlb_research'))
import mlb_feature_discovery_v1 as discovery
import mlb_feature_discovery_runner_v1 as runner


def rows(days=30, games_per_day=10):
    result=[]
    start=date(2026,1,1)
    pk=1
    for d in range(days):
        day=(start+timedelta(days=d)).isoformat()
        for i in range(games_per_day):
            y=(i+d)%2
            result.append({'officialGamePk':str(pk),'slateDateEt':day,'homeWon':y,
                'features':{'marketHomeProbability':.5,
                            'repeatableSignal':1. if y else -1.,
                            'constantSignal':3.,
                            'sparseSignal':1. if pk%5==0 else None,
                            'noiseSignal':1. if pk%4<2 else -1.}})
            pk+=1
    return result


def test_chronological_screen_finds_repeatable_signal_without_mutating_rows():
    data=rows(); original=deepcopy(data)
    report=discovery.screen(data)
    assert data==original
    assert report['status']=='CANDIDATES_FOUND'
    names=[r['feature'] for r in report['candidates']]
    assert 'repeatableSignal' in names
    candidate=next(r for r in report['candidates'] if r['feature']=='repeatableSignal')
    assert candidate['positiveFolds']==3
    assert candidate['stableDirection'] is True
    assert candidate['direction']=='HOME_POSITIVE'
    assert candidate['meanBrierImprovement']>0
    rejected={r['feature']:r['reason'] for r in report['rejected']}
    assert rejected['constantSignal']=='LOW_COVERAGE_OR_VARIANCE'
    assert rejected['sparseSignal']=='LOW_COVERAGE_OR_VARIANCE'
    assert report['promotionAuthority'] is False


def test_runner_reserves_newest_whole_slates_and_never_passes_them_to_screen(monkeypatch):
    data=rows(days=25,games_per_day=8)
    development, holdout, dates=runner.development_slice(data)
    assert len(dates)==25
    assert {r['slateDateEt'] for r in development}.isdisjoint({r['slateDateEt'] for r in holdout})
    assert max(r['slateDateEt'] for r in development)<min(r['slateDateEt'] for r in holdout)
    captured={}
    def fake_screen(value):
        captured['ids']={r['officialGamePk'] for r in value}
        return {'status':'NO_REPEATABLE_CANDIDATES','candidates':[]}
    monkeypatch.setattr(runner.discovery,'screen',fake_screen)
    class Store:
        def artifact(self,kind,value): return {'name':kind+'/x.json','versionId':'1','sha256':'x'}
        def latest(self,name,value): captured['latest']=(name,value)
    dataset={'rows':data,'rowsHash':'fixture-hash','originalRows':0}
    report=runner.publish(Store(),dataset)
    assert captured['ids']=={r['officialGamePk'] for r in development}
    assert captured['ids'].isdisjoint({r['officialGamePk'] for r in holdout})
    assert report['holdoutRows']==len(holdout)
    assert report['holdoutLabelsInspectedByScreen'] is False
    assert report['productionAuthorityChanged'] is False
    assert captured['latest'][0]=='feature-discovery.json'


def test_runner_failure_is_persisted_without_raising_or_gaining_authority(monkeypatch):
    def fail(_): raise ValueError('fixture failure')
    monkeypatch.setattr(runner.discovery,'screen',fail)
    captured={}
    class Store:
        def artifact(self,kind,value): captured['artifact']=(kind,deepcopy(value));return {'name':kind+'/x','versionId':'1','sha256':'x'}
        def latest(self,name,value): captured['latest']=(name,deepcopy(value))
    report=runner.publish(Store(),{'rows':rows(),'rowsHash':'fixture-hash','originalRows':0})
    assert report['status']=='FAILED'
    assert report['error']=='ValueError'
    assert report['productionAuthorityChanged'] is False
    assert report['holdoutLabelsInspectedByScreen'] is False
    assert captured['artifact'][0]=='feature-discovery'
    assert captured['latest'][1]['candidateCount']==0


def test_small_dataset_returns_insufficient_without_candidate_invention():
    report=discovery.screen(rows(days=5,games_per_day=5))
    assert report['status']=='INSUFFICIENT_DEVELOPMENT_DATA'
    assert report['candidates']==[]
    assert report['evaluatedFeatures']==0
