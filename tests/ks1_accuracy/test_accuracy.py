from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ks1.accuracy import CORE, agreement, fit_pair
from ks1.accuracy_features import (ADDONS, build, first_five, lineup_features,
                                    projected_ip, snapshots_for)
from ks1.inventory import encode
import hashlib


def row():
    return {'game_id': '100', 'date': '2026-09-10', 'season': 2026,
            'commence_time': '2026-09-10T20:00:00Z', 'as_of_timestamp': '2026-09-10T19:00:00Z',
            'home_id': '1', 'away_id': '2', 'home_team': 'Home', 'away_team': 'Away',
            'home_starter_id': None, 'away_starter_id': None, 'park_id': None}


def game(pk, date, completed=None, outs=15):
    return {'officialGamePk': pk, 'startAtUtc': date+'T20:00:00Z',
            'completedAtUtc': completed or date+'T23:00:00Z', 'gameType': 'R',
            'teams': {s: {'id': tid, 'name': name, 'priorStarters': {'outs': outs}}
                      for s,tid,name in [('home',1,'Home'),('away',2,'Away')]}}


def snapshot(at='2026-09-10T18:50:00Z'):
    return {'officialGamePk': '100', 'originalObservation': True, 'outcomeKnownAtCapture': False,
            'commenceTime': '2026-09-10T20:00:00Z', 'featureCutoffUtc': '2026-09-10T19:50:00Z',
            'capturedAtUtc': at, 'features': {}, 'featureFingerprint': hashlib.sha256(encode({})).hexdigest(),
            'feedReceipt': {'retrievedAtUtc': at},
            'playerWindows': {'receipts': [], 'teams': {
                s: {'teamId': tid, 'lineupConfirmed': True, 'battingOrder': list(range(tid*10,tid*10+9)),
                    'starterId': tid*100, 'players': [
                        {'id': p, 'batSide': 'S' if i == 0 else 'L', 'pitchHand': None}
                        for i,p in enumerate(range(tid*10,tid*10+9))]+[{'id': tid*100,'pitchHand': hand}]}
                for s,tid,hand in [('home',1,'L'),('away',2,'R')]}},
            'conditions': {'features': {'roofOpenObserved': 1, 'forecastTemperatureF': 80},
                           'receipts': [{'endpoint': 'https://api.weather.gov/gridpoints/X/forecast/hourly', 'retrievedAtUtc': at}]}}


def test_future_current_game_and_late_completions_never_enter_workload():
    bundle = {'compact': [game(1,'2026-09-09')], 'prior': {'games': [], 'schedule': []}}
    before, _ = build(pd.DataFrame([row()]),bundle)
    changed = deepcopy(bundle)
    changed['compact'] += [game(100,'2026-09-10',outs=0), game(2,'2026-09-11',outs=27),
                            game(3,'2026-09-08',completed='2026-09-10T21:00:00Z',outs=0)]
    after, _ = build(pd.DataFrame([row()]),changed)
    pd.testing.assert_frame_equal(before,after)
    assert before.home_starter_expected_ip.iloc[0] == 5
    assert before.home_starter_expected_pitches.isna().all()
    assert before.home_opener.isna().all()


def test_original_snapshot_time_and_forecast_only_outdoor_policy():
    s = snapshot(); assert snapshots_for(row(),[s])
    changed = deepcopy(s); changed['capturedAtUtc'] = '2026-09-10T19:01:00Z'
    assert snapshots_for(row(),[changed]) == []
    assert lineup_features(row(),[s])['outdoor_forecast_temp_f'] == 80
    s['gameData'] = {'weather': {'temp': 120, 'wind': '50 mph'}}
    assert lineup_features(row(),[s])['outdoor_forecast_temp_f'] == 80
    s['conditions']['features']['roofClosedObserved'] = 1
    assert lineup_features(row(),[s])['outdoor_forecast_temp_f'] is None
    s['conditions']['features'].pop('roofClosedObserved')
    s['conditions']['receipts'][0]['retrievedAtUtc'] = '2026-09-10T20:01:00Z'
    assert lineup_features(row(),[s])['outdoor_forecast_temp_f'] is None


def test_switch_hitters_platoon_and_actual_pregame_middle_order_absence():
    early = snapshot('2026-09-10T18:00:00Z'); late = deepcopy(early)
    late['capturedAtUtc'] = '2026-09-10T18:50:00Z'
    assert lineup_features(row(),[early,late])['platoon_advantage_count_diff'] == 8
    assert lineup_features(row(),[late])['middle_order_absent_count_diff'] is None
    assert lineup_features(row(),[early,late])['middle_order_absent_count_diff'] == 0
    home = late['playerWindows']['teams']['home']; home['battingOrder'][1] = 99
    home['players'].append({'id':99,'batSide':'L'})
    result = lineup_features(row(),[early,late])
    assert result['middle_order_absent_count_diff'] == 1
    home['lineupConfirmed'] = False
    assert lineup_features(row(),[early,late])['platoon_advantage_count_diff'] is None


def test_rotation_projection_requires_correct_identity_and_strict_prior_provenance():
    s = {'pointInTimeVerified': True,'sameDayResultsExcluded': True,'targetGameOutcomeUsed': False,
         'selectionUsedOutcomes': False,'postgameFieldsExcluded': True, 'featureEligibility': {'pitchers':True},
         'featureEvidence': {'pitchers': {'availabilityMode':'strict_prior_projection','eligible':True,
                                         'pointInTimeProjectionVerified':True,'sourceEffectiveAtUtc':'2026-09-09T23:00:00Z'}},
         'providerEvidence': {'pitchers': {'pointInTimeProjectionVerified':True,'sourceEffectiveAtUtc':'2026-09-09T23:00:00Z'}},
         'home': {'starterExpectedInnings': 6}}
    s['fingerprint'] = hashlib.sha256(encode(s)).hexdigest()
    record = {'officialGamePk':'100','homeTeam':'Home','awayTeam':'Away','snapshot':s}
    assert projected_ip(row(),record,'home') == 6
    record['officialGamePk'] = '999'; assert projected_ip(row(),record,'home') is None
    record['officialGamePk'] = '100'; s['home']['starterExpectedInnings'] = 9
    with pytest.raises(ValueError,match='fingerprint'): projected_ip(row(),record,'home')


def test_f5_requires_five_complete_innings_and_keeps_ties_separate():
    assert first_five({'homeScore':7,'awayScore':3}) is None
    data = {'linescore': {'innings':[{'num':n,'home':{'runs':1},'away':{'runs':1}} for n in range(1,6)]}}
    assert first_five(data) == [5,5]
    data['linescore']['innings'][-1]['home'].pop('runs')
    assert first_five(data) is None


def test_p60_agreement_includes_confident_away_and_reports_zero_cohort():
    result, mask = agreement([1,0,1,1],[.65,.3,.75,.59],[.61,.4,.4,.61])
    assert mask.tolist() == [True,True,False,False]
    assert result['games'] == 2 and result['accuracy'] == 1
    assert agreement([1],[.55],[.55])[0]['accuracy'] is None


def test_feature_budget_and_unknown_roles_remain_flagged():
    bundle = {'compact':[game(1,'2026-09-09',outs=0)],'prior':{'games':[],'schedule':[]}}
    table, coverage = build(pd.DataFrame([row(),{**row(),'game_id':'101'}]),bundle)
    assert len(CORE) == 35 and len(ADDONS) == 28
    assert set(table.doubleheader_status) == {'flagged'}
    assert set(table.bullpen_game_status) == {'unknown_no_archived_scheduled_role'}
    assert table.home_opener_missing.eq(1).all()  # short realized outing never means scheduled opener


def test_f5_models_reload_and_predict_three_outcomes_on_toy_training_only(tmp_path):
    rng = np.random.default_rng(123)
    train = pd.DataFrame(rng.uniform(.1,1,(150,len(CORE))),columns=CORE)
    train['f5_home_runs'] = rng.poisson(2.1,150); train['f5_away_runs'] = rng.poisson(1.9,150)
    test = pd.DataFrame(rng.uniform(.1,1,(10,len(CORE))),columns=CORE)
    result, metadata = fit_pair(train,test,CORE,tmp_path,target='f5')
    assert metadata['reload_verified']
    np.testing.assert_allclose(result['lightgbm_p_home']+result['lightgbm_p_tie']+result['lightgbm_p_away'],1)
    np.testing.assert_allclose(result['poisson_p_home']+result['poisson_p_tie']+result['poisson_p_away'],1)
    assert (result['lambda_home'] > 0).all()
