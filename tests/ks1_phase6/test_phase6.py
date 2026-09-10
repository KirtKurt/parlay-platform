from copy import deepcopy
import hashlib

import numpy as np
import pandas as pd
import pytest

from ks1.inventory import encode
from ks1.phase6 import select_additions
from ks1.phase6_features import (FEATURES, VALUES, build_phase6, bullpen_history,
                                  context_park, environment_at)
from tests.ks1_accuracy.test_accuracy import row, snapshot


def target(**changes):
    return {**row(),'home_missing_history_boxes_75d':0,'away_missing_history_boxes_75d':0,**changes}


def game(pk,date,outs=6,bb=2,pitches=30):
    return {'officialGamePk':pk,'gameType':'R','startAtUtc':date+'T20:00:00Z',
        'completedAtUtc':date+'T23:00:00Z','teams':{s:{'id':tid,'name':name,
            'batting':{'baseOnBalls':bb+1},'priorStarters':{'baseOnBalls':1},
            'relief':{'outs':outs,'pitches':pitches}} for s,tid,name in [('home',1,'Home'),('away',2,'Away')]}}


def bundle(games):
    return {'compact':games,'prior':{'games':[]},'snapshots':[],'contexts':[]}


def test_walk_decomposition_shrinkage_and_zero_out_relief():
    b=bundle([game(1,'2026-09-08'),game(2,'2026-09-09',outs=0,bb=1,pitches=5)])
    f,proof=build_phase6(pd.DataFrame([target()]),b)
    assert f.home_phase6_bullpen_rest_days.iloc[0]==0 # zero outs still counts as use
    assert f.home_phase6_bullpen_bb_per_9_30d.iloc[0]==pytest.approx(27*3/6)
    assert f.home_phase6_bullpen_k_bb_pct_30d.isna().all()
    assert f.home_phase6_bullpen_relievers_used_2d.isna().all()
    assert proof['invalid_walk_decompositions']==0


def test_future_same_day_late_final_and_season_stats_do_not_enter_features():
    b=bundle([game(1,'2026-09-09')]);r=target()
    before,_=build_phase6(pd.DataFrame([r]),b)
    changed=deepcopy(b);late=game(2,'2026-09-08',bb=99)
    late['completedAtUtc']='2026-09-10T21:00:00Z'
    changed['compact'] += [late,game(100,'2026-09-10',bb=99),game(3,'2026-09-11',bb=99)]
    after,_=build_phase6(pd.DataFrame([r]),changed)
    pd.testing.assert_frame_equal(before,after)
    gap,_=build_phase6(pd.DataFrame([target(home_missing_history_boxes_75d=1)]),b)
    assert gap.home_phase6_bullpen_rest_days.isna().all()
    assert gap.home_phase6_bullpen_bb_per_9_30d.isna().all()


def full_game():
    g=game(1,'2026-09-09')
    for side,tid in [('home',1),('away',2)]:
        g['teams'][side]={'team':{'id':tid,'name':side.title()},'teamStats':{'batting':{'baseOnBalls':3}},
            'players':{'s':{'person':{'id':tid*10},'stats':{'pitching':{'gamesStarted':1,'outs':21,'numberOfPitches':90,'battersFaced':26,'baseOnBalls':1}}},
                       'r':{'person':{'id':tid*10+1},'seasonStats':{'pitching':{'baseOnBalls':999,'strikeOuts':999}},
                            'stats':{'pitching':{'gamesStarted':0,'outs':6,'numberOfPitches':30,'battersFaced':9,'baseOnBalls':2,'strikeOuts':3}}}}}
    return g


def test_full_box_relief_quality_matches_compact_walk_identity():
    b=bundle([game(1,'2026-09-09')]);b['prior']['games']=[full_game()]
    f,proof=build_phase6(pd.DataFrame([target()]),b)
    assert proof['full_team_walk_identities_verified']==2 and proof['compact_full_walk_checks']==2
    assert proof['compact_full_walk_differences']==0
    assert f.home_phase6_bullpen_k_bb_pct_30d.iloc[0]==pytest.approx(100/9)
    assert f.home_phase6_bullpen_relievers_used_2d.iloc[0]==1
    changed=deepcopy(b)
    changed['prior']['games'][0]['teams']['home']['players']['r']['seasonStats']['pitching']={'baseOnBalls':0,'strikeOuts':0}
    pd.testing.assert_frame_equal(f,build_phase6(pd.DataFrame([target()]),changed)[0])
    bad=game(2,'2026-09-08');bad['teams']['away']['batting']['baseOnBalls']=0
    h,_,p=bullpen_history(bundle([bad]))
    assert h.loc[h.team_id=='1','bb'].isna().all() and p['invalid_walk_decompositions']==1


def test_weather_forecast_outdoor_guard_and_static_fallback():
    s=snapshot();s['conditions']['features'].update(recentVenueScoringRatio=1.2,observedVenueRunSample=20)
    e=environment_at(target(),[s],None)
    assert e['phase6_static_park_run_factor']==1.1
    assert e['phase6_park_run_factor']==pytest.approx(1.1*1.02)
    original=deepcopy(s);s['gameData']={'weather':{'temp':999,'wind':'99 mph'}}
    assert environment_at(target(),[s],None)==e
    s['conditions']['features']['roofClosedObserved']=1
    closed=environment_at(target(),[s],None)
    assert closed['phase6_park_run_factor']==1.1
    assert closed['phase6_environment_status']=='static_only_weather_unavailable'
    original['conditions']['receipts'][0]['retrievedAtUtc']='2026-09-10T21:00:00Z'
    assert environment_at(target(),[original],None)['phase6_park_run_factor']==1.1


def test_context_static_park_requires_point_in_time_evidence_and_hash():
    evidence={'eligible':True,'pointInTimeProjectionVerified':True,'sourceEffectiveAtUtc':'2026-09-09T23:00:00Z'}
    s={'pointInTimeVerified':True,'sameDayResultsExcluded':True,'postgameFieldsExcluded':True,
       'targetGameOutcomeUsed':False,'selectionUsedOutcomes':False,'featureEligibility':{'park':True},
       'featureEvidence':{'park':evidence},'providerEvidence':{'park':evidence},'parkRunFactor':1.3}
    s['fingerprint']=hashlib.sha256(encode(s)).hexdigest()
    record={'officialGamePk':'100','homeTeam':'Home','awayTeam':'Away','snapshot':s}
    assert context_park(target(),record)==1.1
    record['officialGamePk']='101';assert context_park(target(),record) is None
    record['officialGamePk']='100';s['parkRunFactor']=1.0
    with pytest.raises(ValueError,match='fingerprint'):context_park(target(),record)


def test_sparse_features_do_not_enter_model_and_new_contract_is_compact():
    train=pd.DataFrame({c:np.nan for c in FEATURES},index=range(10))
    for c in VALUES:train[c+'_missing']=1.
    good='home_phase6_bullpen_bb_per_9_30d';train[good]=np.arange(10,dtype=float);train[good+'_missing']=0.
    test=train.copy();added,coverage=select_additions(train,test)
    assert added==[good] and len(FEATURES)==20
    test.loc[:4,good]=np.nan
    assert select_additions(train,test)[0]==[]
