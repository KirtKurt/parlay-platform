import json

import pytest

from ks1.daily import mask_starter_sources, starter_profile
from ks1.features import Features
from ks1.prior_pitcher_context import PriorPitcherContext
from tests.ks1.test_pitcher_context_priors import subject
from tests.ks1.test_prior_pitcher_context import SOURCE, STATS, attach, full_game, history


@pytest.mark.parametrize('prior_year', [False, True])
def test_serving_profile_labels_season_debut_prior(prior_year):
    games = history()
    if prior_year:
        games.append(full_game(98, '2025-08-01', 999, {**STATS, 'outs':15}))
    row, identities = subject()
    features = Features(games)
    row = attach(PriorPitcherContext(features.rows, SOURCE, identities), row)
    proof = json.loads(row['reconstructed_pitcher_context_proof'])['home']
    actual = features.at(row['as_of_timestamp'], '1', '999')
    profile_row = {**row, 'starter_source':'verified_archive',
                   'home_starter_name':'debut', 'away_starter_name':None}
    profile = starter_profile(profile_row, {'home_'+k:v for k,v in actual.items()},
                              row['as_of_timestamp'], row['as_of_timestamp'])
    assert profile['sides']['home']['context_basis'] == proof['statistical_basis']


def test_serving_prior_requires_both_current_and_previous_season_coverage():
    values = Features(history()).at('2026-08-11T19:50:00Z', '1', '999')
    for coverage in ({'current_season_context':True}, {'results_prior_year':True}):
        masked = mask_starter_sources(values, coverage)
        assert masked['starter_context_quality'] is None
        assert masked['starter_expected_innings_last5'] is None
    covered = mask_starter_sources(values, {'current_season_context':True, 'results_prior_year':True})
    assert covered['starter_context_quality'] == values['starter_context_quality']
