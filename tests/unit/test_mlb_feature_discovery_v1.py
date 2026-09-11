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


class Store:
    def __init__(self): self.latest_value=None; self.artifacts={}; self.writes=0
    def get(self,name): return deepcopy(self.latest_value) if name=='feature-discovery.json' else None
    def load(self,pointer): return deepcopy(self.artifacts[pointer['name']])
    def artifact(self,kind,value):
        self.writes+=1; name=f'{kind}/{self.writes}.json'; self.artifacts[name]=deepcopy(value)
        return {'name':name,'versionId':str(self.writes),'sha256':'x'}
    def latest(self,name,value): self.writes+=1; self.latest_value=deepcopy(value)


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
    store=Store(); dataset={'rows':data,'rowsHash':'fixture-hash','originalRows':0}
    report=runner.publish(store,dataset)
    assert captured['ids']=={r['officialGamePk'] for r in development}
    assert captured['ids'].isdisjoint({r['officialGamePk'] for r in holdout})
    assert report['holdoutRows']==len(holdout)
    assert report['holdoutLabelsInspectedByScreen'] is False
    assert report['productionAuthorityChanged'] is False
    assert store.latest_value['candidateCount']==0


def test_runner_reuses_identical_dataset_and_implementation_without_new_writes(monkeypatch):
    calls=[]
    monkeypatch.setattr(runner.discovery,'screen',lambda value:(calls.append(len(value)) or {'status':'NO_REPEATABLE_CANDIDATES','candidates':[]}))
    store=Store(); dataset={'rows':rows(),'rowsHash':'fixture-hash','originalRows':0}
    first=runner.publish(store,dataset); writes=store.writes
    second=runner.publish(store,dataset)
    assert second==first
    assert store.writes==writes
    assert calls==[len(runner.development_slice(dataset['rows'])[0])]
    changed={**dataset,'rowsHash':'new-hash'}
    runner.publish(store,changed)
    assert len(calls)==2 and store.writes>writes


def test_runner_failure_is_persisted_without_raising_or_gaining_authority(monkeypatch):
    def fail(_): raise ValueError('fixture failure')
    monkeypatch.setattr(runner.discovery,'screen',fail)
    store=Store()
    report=runner.publish(store,{'rows':rows(),'rowsHash':'fixture-hash','originalRows':0})
    assert report['status']=='FAILED'
    assert report['error']=='ValueError'
    assert report['productionAuthorityChanged'] is False
    assert report['holdoutLabelsInspectedByScreen'] is False
    assert store.latest_value['candidateCount']==0


def test_small_dataset_returns_insufficient_without_candidate_invention():
    report=discovery.screen(rows(days=5,games_per_day=5))
    assert report['status']=='INSUFFICIENT_DEVELOPMENT_DATA'
    assert report['candidates']==[]
    assert report['evaluatedFeatures']==0
