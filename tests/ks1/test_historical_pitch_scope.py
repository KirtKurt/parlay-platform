from copy import deepcopy
from datetime import date

import pytest

from ks1.features import Features
from ks1.historical_pitch_scope import matching_scope
from ks1.statcast_history import load_training_statcast
from ks1.table import build
from tests.ks1.test_historical_feed import team_entry
from tests.ks1.test_prior_pitcher_context import history, full_game, STATS
from tests.ks1.test_statcast_history import RetainedS3, fixture


def loaded_scope(*, defect=None):
    bundle, payload, key = fixture()
    source = {'bucket': 'bucket', 'key': 'mlb/development-data/research-v1/prior-games/proven.json',
              'versionId': 'official-v1', 'sha256': 'a'*64, 'complete_years': [2026]}
    bundle['official_history_source'] = source
    bundle['source_receipts'] = [deepcopy(source)]
    bundle['statcast_coverage_complete'] = False
    if defect == 'truncated':
        payload['rows'].pop()
    elif defect == 'outcomes':
        payload['rows'][-1]['woba_denom'] = ''
    elif defect == 'measurement':
        payload['rows'][-1].pop('estimated_woba_using_speedangle')
    elif defect == 'unbound_official':
        bundle['source_receipts'].clear()
    report = load_training_statcast(bundle, RetainedS3({key: (payload, 'raw-v1', None)}), 'bucket')
    return bundle, report


def engine(bundle, scope):
    return Features(bundle['full'], bundle['statcast'], statcast_complete=False,
                    statcast_retained_dates=bundle['statcast_retained_dates'],
                    statcast_physical_dates=bundle['statcast_physical_dates'],
                    historical_pitch_scope=scope)


def test_verified_prior_window_survives_unrelated_incomplete_delivery():
    bundle, report = loaded_scope()
    scope = matching_scope(bundle)
    assert scope is not None and bundle['statcast_coverage_complete'] is False
    assert report['historical_window_scope']['official_receipt']['versionId'] == 'official-v1'
    _, old = engine(bundle, None).lineup_batters_at(
        '2026-09-02T17:50:00Z', list(range(101, 110)), '251', 'R')
    assert old['lineup_platoon_xwoba_7d'] is None
    _, restored = engine(bundle, scope).lineup_batters_at(
        '2026-09-02T17:50:00Z', list(range(101, 110)), '251', 'R')
    assert restored['lineup_platoon_xwoba_7d'] == .5
    assert restored['lineup_pitch_type_matchup_xwoba_30d'] == .5
    # This later window consumes an unverified date and cannot inherit coverage.
    assert not engine(bundle, scope).team_statcast_window_complete(date(2026, 9, 3), 7)


@pytest.mark.parametrize('defect', ['truncated', 'outcomes'])
def test_rejected_historical_date_never_supplies_outcome_values(defect):
    bundle, _ = loaded_scope(defect=defect)
    scope = matching_scope(bundle)
    assert scope is not None
    model_inputs = engine(bundle, scope)
    assert not model_inputs.team_statcast_outcome_window_complete(date(2026, 9, 2), 7)
    assert model_inputs.team_statcast_window_complete(date(2026, 9, 2), 7) is (defect == 'outcomes')
    _, values = model_inputs.lineup_batters_at(
        '2026-09-02T17:50:00Z', list(range(101, 110)), '251', 'R')
    assert values['lineup_platoon_xwoba_7d'] is None


@pytest.mark.parametrize('defect', ['unbound_official', 'inventory_deleted', 'version_changed',
                                   'date_deleted', 'forged_mapping'])
def test_scope_cannot_replace_missing_or_changed_source_proof(defect):
    bundle, _ = loaded_scope(defect=defect)
    if defect == 'inventory_deleted':
        bundle['source_receipts'].pop()
    elif defect == 'version_changed':
        bundle['official_history_source']['versionId'] = 'different-version'
    elif defect == 'date_deleted':
        bundle['statcast_physical_dates'].remove('2026-09-01')
    elif defect == 'forged_mapping':
        bundle['historical_pitch_scope'] = {'verified': True}
    assert matching_scope(bundle) is None


def test_failed_reload_discards_the_previous_scope():
    bundle, _ = loaded_scope()
    assert matching_scope(bundle) is not None
    bundle['official_history_source']['complete_years'] = []
    with pytest.raises(ValueError, match='complete official history'):
        load_training_statcast(bundle, RetainedS3({}), 'bucket')
    assert matching_scope(bundle) is None


def test_missing_daily_source_on_reload_cannot_reuse_old_window_proof():
    bundle, _ = loaded_scope()
    assert matching_scope(bundle).covers(date(2026, 9, 2), 7, outcomes=True)
    load_training_statcast(bundle, RetainedS3({}), 'bucket')
    scope = matching_scope(bundle)
    assert scope is not None
    assert not scope.covers(date(2026, 9, 2), 7, outcomes=True)
    _, values = engine(bundle, scope).lineup_batters_at(
        '2026-09-02T17:50:00Z', list(range(101, 110)), '251', 'R')
    assert values['lineup_platoon_xwoba_7d'] is None


def test_verified_pa_identity_does_not_invent_a_missing_measurement():
    bundle, _ = loaded_scope(defect='measurement')
    scope = matching_scope(bundle)
    model_inputs = engine(bundle, scope)
    assert model_inputs.team_statcast_outcome_window_complete(date(2026, 9, 2), 7)
    _, values = model_inputs.lineup_batters_at(
        '2026-09-02T17:50:00Z', list(range(101, 110)), '251', 'R')
    assert values['lineup_platoon_xwoba_7d'] is None


@pytest.mark.parametrize('official_complete', [True, False])
def test_historical_team_admission_requires_official_history_even_with_pitch_scope(official_complete):
    bundle, _ = loaded_scope()
    games = history()+[full_game(99, '2026-08-11', 999, STATS)]
    bundle.update(full=games, historical_team_context=[team_entry(missing='lineup')],
                  current30_history_complete=official_complete,
                  current_year_history_complete=True, prior_year_history_complete=True,
                  current_year_statcast_complete=False, prior_year_statcast_complete=False,
                  schedule=[{'gamePk': 99, 'gameDate': '2026-08-11T20:00:00Z',
                             'gameType': 'R', 'status': {'abstractGameState': 'Preview'},
                             'teams': {side: {'team': games[-1]['teams'][side]['team']}
                                       for side in ('home', 'away')}}])
    row = build(bundle)[0].to_pylist()[0]
    assert (row['lineup_bullpen_context_evidence'] == 'historical_timecoded_mlb_feed') is official_complete
    assert row['home_lineup_ops_30d'] is None
    assert row['home_lineup_ops_30d_missing'] == 1
    if official_complete:
        assert row['home_bullpen_context_roster_count'] == 2
    else:
        assert row['historical_lineup_bullpen_context_status'] == 'HISTORY_INCOMPLETE_FAIL_CLOSED'
