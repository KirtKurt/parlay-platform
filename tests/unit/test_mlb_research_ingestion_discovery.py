from copy import deepcopy
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'mlb_research'))
sys.path.insert(0,str(ROOT/'scripts'))
from run_mlb_research_ingestion import discovery_summary


class Store:
    def __init__(self,pointer=None,value=None): self.pointer=pointer;self.value=value
    def get(self,name): return deepcopy(self.pointer) if name=='feature-discovery.json' else None
    def load(self,pointer):
        if self.value is Exception: raise ValueError('fixture')
        return deepcopy(self.value)


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
