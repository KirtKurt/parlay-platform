import json

import pytest

from ks1.features import Features
from ks1.prior_pitcher_context import PriorPitcherContext, verified_reconstruction
from tests.ks1.test_prior_pitcher_context import SOURCE, STATS, attach, full_game, history, target


def subject():
    row = target()
    row.update(home_starter_id='999', home_starter_status='observed_archived_pregame')
    identities = {('99', 'home'): [{'pitcher_id':'999', 'team_id':'1',
        'as_of':'2026-08-11T19:30:00Z', 'commence_time':row['commence_time'],
        'source':{'version_id':'archived', 'sha256':'b'*64}}]}
    return row, identities


@pytest.mark.parametrize('prior_year', [False, True])
def test_season_debut_prior_is_independently_recomputed_and_not_observed_era(prior_year):
    games = history()
    if prior_year:
        games.append(full_game(98, '2025-08-01', 999, {**STATS, 'outs':15}))
    row, identities = subject()
    features = Features(games)
    engine = PriorPitcherContext(features.rows, SOURCE, identities)
    row = attach(engine, row)
    assert verified_reconstruction(row, engine)
    proof = json.loads(row['reconstructed_pitcher_context_proof'])['home']
    assert proof['statistical_basis'] == ('prior_year_pitcher' if prior_year else 'current_season_league_prior')
    assert proof['pitcher_id'] == '999'
    actual = features.at(row['as_of_timestamp'], '1', '999')
    assert {key:actual['pitcher_context_'+key] for key in proof['metrics']} == proof['metrics']
    assert actual['starter_context_basis_code'] == (1.0 if prior_year else 2.0)
    assert actual['starter_era_7d'] is actual['starter_era_30d'] is None
    assert actual['starter_appearances_30d'] == 0
    if prior_year:
        assert {x['game_id'] for x in proof['inputs']} == {'98'}
        assert actual['pitcher_context_expected_innings'] == 5
    else:
        assert len(proof['inputs']) == 10
        assert actual['pitcher_context_expected_innings'] == 6


def test_league_prior_excludes_target_same_day_future_and_late_completed_games():
    row, identities = subject()
    before = PriorPitcherContext(Features(history()).rows, SOURCE, identities).at(row, 'home')
    extras = [full_game(99, '2026-08-11', 999, {**STATS, 'homeRuns':50}),
              full_game(98, '2026-08-11', 500, {**STATS, 'homeRuns':50}),
              full_game(97, '2026-08-12', 500, {**STATS, 'homeRuns':50}),
              full_game(96, '2026-08-10', 500, {**STATS, 'homeRuns':50})]
    extras[-1]['completedAtUtc'] = '2026-08-12T23:00:00Z'
    after = PriorPitcherContext(Features(history()+extras).rows, SOURCE, identities).at(row, 'home')
    assert before == after


def test_priors_cannot_hide_incomplete_observed_counts_or_unproven_previous_season():
    row, identities = subject()
    broken = {k:v for k,v in STATS.items() if k != 'hitBatsmen'}
    games = history()+[full_game(98, '2026-08-05', 999, broken)]
    assert PriorPitcherContext(Features(games).rows, SOURCE, identities).at(row, 'home') is None
    assert PriorPitcherContext(Features(history()).rows,
                               {**SOURCE, 'complete_years':[2026]}, identities).at(row, 'home') is None


def test_opening_day_league_prior_uses_only_previous_season_and_stays_missing_without_history():
    games = [full_game(98, '2025-08-01', 100, STATS)]
    actual = Features(games).at('2026-04-01T19:50:00Z', '1', '999')
    assert actual['starter_context_basis_code'] == 3.0
    assert actual['pitcher_context_expected_innings'] == 6
    assert Features([]).at('2026-04-01T19:50:00Z', '1', '999')['pitcher_context_quality'] is None

