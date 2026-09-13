from pathlib import Path

import pandas as pd
import pytest
from ks1.features import Features, pitching
from ks1.retrain_recent import accepted, choose_features, completion_times, split_recent
from ks1.train import artifact_write_authorized
from tests.ks1.test_game_table import game


def test_recent_validation_credentials_are_restricted_to_trusted_branch():
    path = Path(__file__).resolve().parents[2]/'.github/workflows/ks1-retrain-recent.yml'
    workflow_text = path.read_text()
    assert 'github.event.pull_request.head.repo.full_name == github.repository' in workflow_text
    assert "github.head_ref == 'codex/ks1-starter-postmerge-repairs-20260913'" in workflow_text
    assert "github.event_name == 'schedule'" in workflow_text
    assert "github.event_name == 'workflow_dispatch'" in workflow_text
    assert "github.ref == 'refs/heads/main'" in workflow_text
    assert 'cancel-in-progress: true' in workflow_text


@pytest.mark.parametrize(('event', 'ref', 'head', 'authorized'), [
    ('schedule', 'refs/heads/main', '', True),
    ('workflow_dispatch', 'refs/heads/main', '', True),
    ('push', 'refs/heads/main', '', False),
    ('pull_request', 'refs/pull/1/merge',
     'codex/ks1-starter-postmerge-repairs-20260913', True),
    ('pull_request', 'refs/pull/2/merge', 'untrusted', False),
])
def test_challenger_artifact_write_authority(monkeypatch, event, ref, head, authorized):
    values = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'KirtKurt/parlay-platform',
              'GITHUB_WORKFLOW_REF': ('KirtKurt/parlay-platform/.github/workflows/'
                                      'ks1-retrain-recent.yml@refs/heads/main'),
              'GITHUB_EVENT_NAME': event, 'GITHUB_REF': ref, 'GITHUB_HEAD_REF': head}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    assert artifact_write_authorized() is authorized
    monkeypatch.setenv('GITHUB_WORKFLOW_REF',
                       'KirtKurt/parlay-platform/.github/workflows/other.yml@refs/heads/main')
    assert artifact_write_authorized() is False


def test_seven_day_calendar_boundary_and_future_exclusion():
    games = [game(1, '2026-08-01'), game(2, '2026-08-02'), game(3, '2026-08-08'),
             game(4, '2026-08-09', hits=25), game(5, '2026-08-07', completed='2026-08-10T23:00:00Z')]
    row = Features(games).at('2026-08-09T19:00:00Z', '1')
    assert row['offense_games_7d'] == 2
    assert row['offense_games_10d'] == 3
    assert row['offense_pa_7d'] == 66
    assert row['team_starter_bf_7d'] == 48


def test_history_games_keeps_incumbent_target_season_semantics():
    games = [game(1, '2025-09-01'), game(2, '2026-04-01')]
    row = Features(games).at('2026-04-03T19:00:00Z', '1')
    assert row['history_games'] == 1


def test_individual_starter_profile_keeps_results_and_skill_separate():
    stats = {'outs': 18, 'earnedRuns': 2, 'runs': 3, 'hits': 4, 'homeRuns': 1,
             'baseOnBalls': 2, 'hitBatsmen': 1, 'strikeOuts': 7, 'battersFaced': 25,
             'wins': 1, 'losses': 0, 'gamesStarted': 1}
    league = {'kbb': {'strikeOuts': 20, 'baseOnBalls': 8, 'battersFaced': 100},
              'whip': {'outs': 75, 'hits': 20, 'baseOnBalls': 8}}
    row = pitching([stats], league)
    assert row['era'] == 3 and row['ra9'] == 4.5
    assert row['wins'] == 1 and row['losses'] == 0
    assert row['k_pct'] == pytest.approx(28) and row['bb_pct'] == pytest.approx(8) and row['k_bb_pct'] < 20
    assert row['fip'] == pytest.approx(3.1 + 8*3/18)
    incomplete = pitching([{k:v for k,v in stats.items() if k != 'runs'}], league)
    assert incomplete['era'] is None and incomplete['ra9'] is None
    assert incomplete['result_games'] == 0


def test_savant_profile_requires_exact_pitch_coverage_and_keeps_arsenal():
    rows = [
        {'game_pk':'1','pitcher':'99','type':'X','pitch_type':'FF','description':'hit_into_play',
         'launch_speed':'101','launch_speed_angle':'6','estimated_woba_using_speedangle':'.51',
         'release_speed':'96','release_spin_rate':'2400','release_extension':'6.5','pfx_x':'-.7','pfx_z':'1.3'},
        {'game_pk':'1','pitcher':'99','type':'S','pitch_type':'SL','description':'swinging_strike',
         'launch_speed':'','launch_speed_angle':'','estimated_woba_using_speedangle':'',
         'release_speed':'86','release_spin_rate':'2500','release_extension':'6.3','pfx_x':'.3','pfx_z':'.2'},
    ]
    engine = Features([], rows)
    engine.statcast_rows = None  # aggregation must use the constructor-built index
    profile = engine.statcast('99', {'1'}, 2)
    assert profile['complete'] == 1 and profile['hard_hit_pct'] == profile['barrel_pct'] == 100
    assert profile['csw_pct'] == profile['swstr_pct'] == 50
    assert profile['ff_mix_pct'] == profile['sl_mix_pct'] == 50
    assert profile['ff_velocity'] == 96 and profile['sl_velocity'] == 86
    partial = Features([], rows[:-1]).statcast('99', {'1'}, 2)
    assert partial['complete'] == 0 and partial['velocity'] is None and partial['ff_mix_pct'] is None


def test_savant_full_xwoba_and_whiff_denominators_are_not_mislabeled():
    rows = [
        {'game_pk':'1','pitcher':'99','type':'X','pitch_type':'FF','description':'hit_into_play',
         'estimated_woba_using_speedangle':'.50','woba_value':'.9','woba_denom':'1',
         'launch_speed':'100','launch_speed_angle':'6','bb_type':'fly_ball','events':'single',
         'release_speed':'96','release_spin_rate':'2400','release_extension':'6.5','pfx_x':'-.7','pfx_z':'1.3'},
        {'game_pk':'1','pitcher':'99','type':'B','pitch_type':'FF','description':'ball',
         'estimated_woba_using_speedangle':'','woba_value':'.70','woba_denom':'1','events':'walk',
         'release_speed':'95','release_spin_rate':'2380','release_extension':'6.4','pfx_x':'-.6','pfx_z':'1.2'},
        {'game_pk':'1','pitcher':'99','type':'S','pitch_type':'FF','description':'swinging_strike',
         'estimated_woba_using_speedangle':'','woba_value':'','woba_denom':'','events':'',
         'release_speed':'95','release_spin_rate':'2380','release_extension':'6.4','pfx_x':'-.6','pfx_z':'1.2'},
        {'game_pk':'1','pitcher':'99','type':'S','pitch_type':'FF','description':'foul',
         'estimated_woba_using_speedangle':'','woba_value':'','woba_denom':'','events':'',
         'release_speed':'95','release_spin_rate':'2380','release_extension':'6.4','pfx_x':'-.6','pfx_z':'1.2'},
    ]
    profile = Features([], rows).statcast('99', {'1'}, 4)
    assert profile['xwoba'] == pytest.approx(.60) and profile['xwoba_pa'] == 2
    assert profile['ff_whiff_per_pitch_pct'] == 25
    assert profile['ff_whiff_pct'] == pytest.approx(100/3)
    assert profile['ff_spin'] == 2385 and profile['ff_horizontal_break_in'] == pytest.approx(-7.5)
    assert profile['xera'] is profile['siera'] is profile['active_spin_pct'] is None


def test_uncommon_pitch_types_are_retained_in_other_bucket():
    rows = [{'game_pk':'1','pitcher':'99','type':'S','pitch_type':pitch,
             'description':'called_strike','release_speed':'70','release_spin_rate':'1000',
             'release_extension':'6','pfx_x':'0','pfx_z':'0'}
            for pitch in ('FF', 'KN')]
    profile = Features([], rows).statcast('99', {'1'}, 2)
    assert profile['ff_mix_pct'] == profile['other_mix_pct'] == 50
    assert profile['other_velocity'] == 70
    assert sum(profile[name] for name in ('ff_mix_pct', 'other_mix_pct')) == 100


def test_partial_statcast_source_suppresses_league_xfip_input():
    rows = [{'game_pk':'1','pitcher':'99','type':'X','pitch_type':'FF',
             'bb_type':'fly_ball','events':'home_run'}]
    engine = Features([], rows, statcast_complete=False)
    assert engine.league_hr_fb([{'game_id':'1'}]) is None


def full_game(pk, date, pitcher_id, stats):
    batting = {'atBats':30,'hits':8,'baseOnBalls':3,'hitByPitch':0,'sacFlies':0,
               'doubles':1,'triples':0,'homeRuns':1}
    teams = {}
    for side, tid in (('home', 1), ('away', 2)):
        player_stats = stats if side == 'home' else {**stats, 'numberOfPitches': 1}
        pid = pitcher_id if side == 'home' else pitcher_id+1
        teams[side] = {'team': {'id': tid, 'name': side}, 'teamStats': {'batting': batting},
                       'players': {'ID'+str(pid): {'person': {'id': pid},
                                   'stats': {'pitching': player_stats}}}}
    return {'officialGamePk': pk, 'startAtUtc': date+'T20:00:00Z',
            'completedAtUtc': date+'T23:00:00Z', 'gameType': 'R', 'teams': teams}


def test_xfip_uses_past_only_league_hr_fb_and_prior_year_talent_is_explicit():
    stats = {'outs':18,'earnedRuns':2,'runs':3,'hits':4,'homeRuns':1,'baseOnBalls':2,
             'hitBatsmen':1,'strikeOuts':7,'battersFaced':25,'wins':1,'losses':0,
             'gamesStarted':1,'numberOfPitches':2}
    prior = {**stats, 'numberOfPitches': 2}
    rows = [
        {'game_pk':'10','pitcher':'99','type':'X','pitch_type':'FF','description':'hit_into_play',
         'bb_type':'fly_ball','events':'home_run','woba_denom':'1','woba_value':'2',
         'estimated_woba_using_speedangle':'2','release_speed':'94','release_spin_rate':'2300',
         'release_extension':'6','pfx_x':'-.5','pfx_z':'1.2','launch_speed':'101','launch_speed_angle':'6'},
        {'game_pk':'10','pitcher':'99','type':'S','pitch_type':'FF','description':'called_strike',
         'release_speed':'96','release_spin_rate':'2400','release_extension':'6','pfx_x':'-.5','pfx_z':'1.2'},
        {'game_pk':'10','pitcher':'100','type':'X','pitch_type':'FF','description':'hit_into_play',
         'bb_type':'fly_ball','events':'field_out','woba_denom':'1','woba_value':'0',
         'estimated_woba_using_speedangle':'.1','release_speed':'90'},
        {'game_pk':'9','pitcher':'99','type':'S','pitch_type':'FF','description':'called_strike',
         'release_speed':'90','release_spin_rate':'2200','release_extension':'6','pfx_x':'-.5','pfx_z':'1.2'},
        {'game_pk':'9','pitcher':'99','type':'S','pitch_type':'FF','description':'called_strike',
         'release_speed':'92','release_spin_rate':'2200','release_extension':'6','pfx_x':'-.5','pfx_z':'1.2'},
    ]
    feature = Features([full_game(9, '2025-09-01', 99, prior),
                        full_game(10, '2026-08-01', 99, stats)], rows).at(
                            '2026-08-10T19:50:00Z', '1', '99', game_date='2026-08-10')
    expected = (13*.5 + 3*(2+1)-2*7)*3/18 + 3.10
    assert feature['starter_xfip_30d'] == pytest.approx(expected)
    assert feature['starter_velocity_prior_year'] == 91
    assert feature['starter_velocity_talent'] == pytest.approx(93)
    assert feature['starter_talent_prior_weight_cap_pitches'] == 300


def test_last_three_is_fail_closed_when_only_two_starts_are_retained():
    stats = {'outs':18,'earnedRuns':2,'runs':3,'hits':4,'homeRuns':1,'baseOnBalls':2,
             'hitBatsmen':1,'strikeOuts':7,'battersFaced':25,'wins':1,'losses':0,
             'gamesStarted':1,'numberOfPitches':90}
    games = [full_game(1, '2026-07-01', 99, stats), full_game(2, '2026-08-01', 99, stats)]
    feature = Features(games).at('2026-08-10T19:50:00Z', '1', '99', game_date='2026-08-10')
    assert feature['starter_starts_observed_last3'] == 2
    assert feature['starter_era_last3'] is None


def test_opening_day_last_three_uses_prior_year_league_baseline():
    stats = {'outs':18,'earnedRuns':2,'runs':3,'hits':4,'homeRuns':1,'baseOnBalls':2,
             'hitBatsmen':1,'strikeOuts':7,'battersFaced':25,'wins':1,'losses':0,
             'gamesStarted':1,'numberOfPitches':90}
    games = [full_game(pk, date, 99, stats) for pk, date in (
        (1, '2025-09-01'), (2, '2025-09-08'), (3, '2025-09-15'))]

    feature = Features(games).at(
        '2026-04-01T19:50:00Z', '1', '99', game_date='2026-04-01')
    unrelated = {**stats, 'hits': 15, 'baseOnBalls': 8, 'strikeOuts': 1,
                 'battersFaced': 30}
    after_other_teams_play = Features([
        *games, full_game(4, '2026-03-20', 199, unrelated)
    ]).at('2026-04-01T19:50:00Z', '1', '99', game_date='2026-04-01')

    assert feature['starter_starts_observed_last3'] == 3
    assert feature['starter_whip_last3'] is not None
    assert feature['starter_k_bb_pct_last3'] is not None
    assert after_other_teams_play['starter_whip_last3'] == feature['starter_whip_last3']
    assert after_other_teams_play['starter_k_bb_pct_last3'] == feature['starter_k_bb_pct_last3']


def test_calendar_windows_cross_new_year_without_using_same_day_results():
    stats = {'outs':18,'earnedRuns':2,'runs':3,'hits':4,'homeRuns':1,'baseOnBalls':2,
             'hitBatsmen':1,'strikeOuts':7,'battersFaced':25,'wins':1,'losses':0,
             'gamesStarted':1,'numberOfPitches':90}
    feature = Features([full_game(1, '2025-12-25', 99, stats)]).at(
        '2026-01-05T19:50:00Z', '1', '99', game_date='2026-01-05')
    assert feature['starter_era_30d'] == 3
    assert feature['starter_appearances_30d'] == 1


def test_split_has_no_overlap_and_requires_labels_and_counts():
    rows = [{'game_id': str(i), 'date': '2026-08-31' if i < 500 else '2026-09-01',
             'home_win': i % 2, 'home_score': 3, 'away_score': 2,
             'as_of_timestamp': '2026-08-31T20:00:00Z' if i < 500 else '2026-09-01T19:50:00Z',
             'label_completed_at': '2026-08-31T23:00:00Z' if i < 500 else '2026-09-02T02:00:00Z'} for i in range(800)]
    frame = pd.DataFrame(rows)
    frame.loc[0, 'label_completed_at'] = '2026-08-31T23:00:00.123456+00:00'
    train, test = split_recent(frame)
    assert len(train) == 500 and len(test) == 300
    assert train.date.max() < test.date.min()
    with pytest.raises(ValueError, match='duplicate'):
        split_recent(pd.concat([frame, frame.iloc[:1]]))
    frame.loc[599, 'home_score'] = None
    with pytest.raises(ValueError, match='insufficient'):
        split_recent(frame)


def test_completion_times_accept_mixed_fractional_iso8601_forms():
    parsed = completion_times(pd.Series([
        '2026-08-31T23:00:00.123456+00:00',
        '2026-09-02T02:00:00Z',
    ]))
    assert parsed.notna().all()
    assert min(parsed).isoformat() == '2026-08-31T23:00:00.123456+00:00'


def test_no_individual_starter_learning_from_unobserved_ids_or_prior_only():
    frame = pd.DataFrame({'home_offense_ops_7d': [0.5, 0.6],
                          'home_starter_id': [None, None], 'away_starter_id': [None, None],
                          'home_starter_bf_30d': [0., 1.], 'away_starter_bf_30d': [0., 1.],
                          'home_actual_starter_id': ['1', '2'], 'home_score': [1, 2]})
    features, _, coverage = choose_features(frame)
    assert features == ['home_offense_ops_7d']
    assert coverage == {'home': 0, 'away': 0}


def test_sparse_advanced_starter_feature_is_not_learned_from_too_few_rows():
    n = 300
    frame = pd.DataFrame({'home_offense_ops_7d': [0.5+i/1000 for i in range(n)],
                          'home_starter_id': [str(i) for i in range(n)],
                          'away_starter_id': [str(i+n) for i in range(n)],
                          'home_starter_bf_30d': [25.]*n, 'away_starter_bf_30d': [25.]*n,
                          'home_starter_xwoba_30d': [None]+[.3+i/10000 for i in range(n-1)]})
    features, omitted, coverage = choose_features(frame)
    assert coverage == {'home': n, 'away': n}
    assert 'home_starter_xwoba_30d' not in features
    assert 'home_starter_xwoba_30d' in omitted


def test_promotion_requires_both_probability_metrics_and_same_sufficient_cohort():
    old = {'games': 300, 'brier': .24, 'logloss': .68}
    assert accepted({'games': 300, 'brier': .23, 'logloss': .67}, old)
    assert not accepted({'games': 300, 'brier': .23, 'logloss': .69}, old)
    assert not accepted(old, old)
    assert not accepted({'games': 299, 'brier': .23, 'logloss': .67}, old)


def test_evaluation_uses_only_the_latest_300_eligible_games():
    rows = [{'game_id': str(i), 'date': '2026-08-31' if i < 500 else '2026-09-01',
             'home_win': i % 2, 'home_score': 3, 'away_score': 2,
             'as_of_timestamp': '2026-08-31T20:00:00Z' if i < 500 else '2026-09-01T19:50:00Z',
             'label_completed_at': '2026-08-31T23:00:00Z' if i < 500 else '2026-09-02T02:00:00Z'}
            for i in range(850)]
    _, test = split_recent(pd.DataFrame(rows))
    assert len(test) == 300
    assert set(test.game_id) == {str(i) for i in range(550, 850)}


def test_latest_300_are_ordered_by_completion_not_game_id():
    training = [{'game_id': str(i), 'date': '2026-08-31', 'home_win': i % 2,
                 'home_score': 3, 'away_score': 2,
                 'as_of_timestamp': '2026-08-31T20:00:00Z',
                 'label_completed_at': '2026-08-31T23:00:00Z'} for i in range(500)]
    holdout = [{'game_id': f'test-{300-i:03d}', 'date': '2026-09-01', 'home_win': i % 2,
                'home_score': 3, 'away_score': 2,
                'as_of_timestamp': '2026-09-01T19:50:00Z',
                'label_completed_at': (pd.Timestamp('2026-09-02T00:00:00Z')+
                                       pd.Timedelta(minutes=i)).isoformat()}
               for i in range(301)]

    _, test = split_recent(pd.DataFrame([*training, *holdout]))

    assert 'test-300' not in set(test.game_id)
    assert 'test-000' in set(test.game_id)


def test_rolling_split_allows_mature_prospective_rows_into_training():
    rows = []
    start = pd.Timestamp('2026-08-01T00:00:00Z')
    for i in range(900):
        rows.append({'game_id': str(i),
                     'date': '2026-08-31' if i < 500 else '2026-09-01',
                     'home_win': i % 2, 'home_score': 3, 'away_score': 2,
                     'as_of_timestamp': (start+pd.Timedelta(minutes=i)-
                                         pd.Timedelta(seconds=30)).isoformat(),
                     'label_completed_at': (start+pd.Timedelta(minutes=i)).isoformat()})

    train, test = split_recent(pd.DataFrame(rows))

    assert len(train) == 600 and len(test) == 300
    assert (train.date >= '2026-09-01').sum() == 100


def test_training_labels_stop_before_first_holdout_prediction():
    early = [{'game_id': str(i), 'date': '2026-08-31', 'home_win': i % 2,
              'home_score': 3, 'away_score': 2,
              'as_of_timestamp': '2026-08-31T17:50:00Z',
              'label_completed_at': '2026-08-31T18:00:00Z'} for i in range(500)]
    overlapping = {'game_id': 'overlapping-result', 'date': '2026-09-01',
                   'home_win': 1, 'home_score': 3, 'away_score': 2,
                   'as_of_timestamp': '2026-09-01T18:00:00Z',
                   'label_completed_at': '2026-09-01T20:30:00Z'}
    holdout = [{'game_id': f'holdout-{i:03d}', 'date': '2026-09-01',
                'home_win': i % 2, 'home_score': 3, 'away_score': 2,
                'as_of_timestamp': '2026-09-01T20:00:00Z',
                'label_completed_at': '2026-09-01T23:00:00Z'} for i in range(300)]

    train, test = split_recent(pd.DataFrame([*early, overlapping, *holdout]))

    assert len(train) == 500 and len(test) == 300
    assert 'overlapping-result' not in set(train.game_id)


def test_evaluation_excludes_labels_without_official_completion_time():
    training = [{'game_id': str(i), 'date': '2026-08-31', 'home_win': i % 2,
                 'home_score': 3, 'away_score': 2,
                 'as_of_timestamp': '2026-08-31T20:00:00Z',
                 'label_completed_at': '2026-08-31T23:00:00Z'} for i in range(500)]
    holdout = [{'game_id': f'test-{i:03d}', 'date': '2026-09-01', 'home_win': i % 2,
                'home_score': 3, 'away_score': 2,
                'as_of_timestamp': '2026-09-01T19:50:00Z',
                'label_completed_at': '2026-09-02T02:00:00Z'} for i in range(300)]
    missing = {'game_id': 'missing-completion', 'date': '2026-09-01', 'home_win': 1,
               'home_score': 3, 'away_score': 2,
               'as_of_timestamp': '2026-09-01T19:50:00Z', 'label_completed_at': None}

    _, test = split_recent(pd.DataFrame([*training, *holdout, missing]))

    assert len(test) == 300
    assert 'missing-completion' not in set(test.game_id)


def test_august_game_completed_after_holdout_start_cannot_train():
    rows = [{'game_id': str(i), 'date': '2026-08-31' if i < 501 else '2026-09-01',
             'home_win': i % 2, 'home_score': 3, 'away_score': 2,
             'as_of_timestamp': '2026-08-31T20:00:00Z' if i < 501 else '2026-09-01T19:50:00Z',
             'label_completed_at': '2026-08-31T23:00:00Z' if i < 500 else '2026-09-02T02:00:00Z'} for i in range(801)]
    train, test = split_recent(pd.DataFrame(rows))
    assert len(train) == 500 and len(test) == 300
    assert '500' not in set(train.game_id)


def test_training_rejects_features_unavailable_at_serving():
    frame = pd.DataFrame({'home_offense_ops_7d': [0.5, 0.6], 'temp': [65., 75.],
                          'home_starter_id': [None, None], 'away_starter_id': [None, None],
                          'home_starter_bf_30d': [0., 0.], 'away_starter_bf_30d': [0., 0.]})
    with pytest.raises(ValueError, match='missing from daily inference: temp'):
        choose_features(frame)
