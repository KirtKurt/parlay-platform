import copy
from datetime import date,timedelta
import math
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import benchmark_mlb_reconstructed_development as research
from mlb_historical_development_data import VERSION,digest


def dataset():
    rows=[]
    for i in range(500):
        features={'marketHomeProbability':.5+.1*math.sin(i),'deltaGapHome':.02*math.cos(i)}
        for k in research.FEATURE_SETS['market_movement_prior_baseball'][1:]:features[k]=math.sin(i/7)
        rows.append({'officialGamePk':str(i+1),'slateDateEt':(date(2025,4,1)+timedelta(days=i//10)).isoformat(),
                     'features':features,'featureFingerprint':digest(features),'label':{'homeWon':i%3!=0},
                     'originalObservation':False,'prospectiveQualificationEvidence':False})
    return {'version':VERSION,'rows':rows,'developmentOnly':True,'prospectiveQualificationEvidence':False,'rejections':[]}


def test_later_labels_cannot_change_training_fit():
    data=dataset();first=research.benchmark(data)
    changed=copy.deepcopy(data)
    for row in changed['rows']:
        if row['slateDateEt']>=first['validationFirstSlate']:row['label']['homeWon']=not row['label']['homeWon']
    second=research.benchmark(changed)
    assert [c['model'] for c in first['candidates']]==[c['model'] for c in second['candidates']]
    assert first['trainRows']==350 and first['validationRows']==150
    assert first['trainLastSlate']<first['validationFirstSlate']
    assert first['freshProspectiveQualification'] is False


def test_rejected_game_excludes_its_entire_development_slate():
    data=dataset();day=data['rows'][0]['slateDateEt'];data['rejections']=[{'slateDateEt':day}]
    result=research.benchmark(data)
    assert result['completeSlateRows']==490
    assert result['excludedPartialSlateDates']==[day]


def test_changed_feature_receipt_is_rejected():
    data=dataset();data['rows'][0]['features']['deltaGapHome']=999
    with pytest.raises(ValueError,match='fingerprint'):research.benchmark(data)
