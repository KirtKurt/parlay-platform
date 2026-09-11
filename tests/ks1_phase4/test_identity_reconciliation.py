"""Regression coverage for the retained 2026-09-11 ingestion failure."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest

from ks1.identity import reconcile_bbs, timestamp


def fixture():
    source = json.loads(Path(__file__).with_name('identity_20260911.json').read_text())
    games, events = [], []
    for pk, official, hid, home, aid, away, bid, provider in source['rows']:
        games.append({'gamePk': pk, 'gameDate': official, 'doubleHeader': 'N', 'gameNumber': 1,
                      'status': {'abstractGameState': 'Preview', 'detailedState': 'Scheduled', 'startTimeTBD': False},
                      'teams': {'home': {'team': {'id': hid, 'name': home}}, 'away': {'team': {'id': aid, 'name': away}}}})
        events.append({'id': bid, 'kickoff_utc': provider, 'sport': 'baseball', 'league': 'MLB', 'status': 'scheduled',
                       'home': {'id': 'bbs-'+str(hid), 'name': home}, 'away': {'id': 'bbs-'+str(aid), 'name': away}})
    return {'official_games': games, 'bbs': {'data': events}}


def resolver(games):
    aliases = {}
    for game in games:
        for side in ('home', 'away'):
            team = game['teams'][side]['team']
            aliases.setdefault(team['name'], set()).add(str(team['id']))
    def resolve(name):
        ids = aliases.get(name, set())
        return next(iter(ids)) if len(ids) == 1 else None
    return resolve


def single():
    f = fixture()
    games = [g for g in f['official_games'] if str(g['gamePk']) == '823736']
    payload = {'data': [e for e in f['bbs']['data'] if e['id'] == '9ba968c3-513f-4e43-81eb-34e1244b07a0']}
    return payload, games


def match(payload, games):
    return reconcile_bbs(payload, games, resolver(games), '2026-09-11')


def test_retained_failure_recovers_all_fifteen_official_games_without_mutation():
    f = fixture(); original = deepcopy(f)
    assigned, audit = match(f['bbs'], f['official_games'])
    assert set(assigned) == {str(g['gamePk']) for g in f['official_games']}
    assert len(assigned) == len(audit) == 15
    corrected = [r for r in audit if r['method'] != 'unique_exact_alias_and_game_start']
    assert len(corrected) == 1
    assert corrected[0]['game_id'] == '823736'
    assert corrected[0]['provider_minus_official_seconds'] == -300
    assert corrected[0]['official_start'] == '2026-09-11T23:45:00Z'
    assert corrected[0]['start_time_authority'] == 'MLB'
    assert f == original


@pytest.mark.parametrize('seconds', [-301, 301, 900, 3600])
def test_larger_start_disagreements_remain_blocked(seconds):
    payload, games = single()
    payload['data'][0]['kickoff_utc'] = (timestamp(games[0]['gameDate']) + timedelta(seconds=seconds)).isoformat()
    with pytest.raises(ValueError, match='ambiguous or unmatched'):
        match(payload, games)


@pytest.mark.parametrize('field,value', [('doubleHeader', 'Y'), ('doubleHeader', 'S'), ('doubleHeader', None), ('gameNumber', 2)])
def test_drift_recovery_requires_explicit_single_game(field, value):
    payload, games = single(); games[0][field] = value
    with pytest.raises(ValueError, match='ambiguous or unmatched'):
        match(payload, games)


@pytest.mark.parametrize('field,value', [('abstractGameState','Final'), ('detailedState','Postponed'), ('detailedState','Suspended'), ('startTimeTBD',True), ('startTimeTBD',None)])
def test_official_status_uncertainty_blocks_drift_recovery(field, value):
    payload, games = single(); games[0]['status'][field] = value
    with pytest.raises(ValueError, match='ambiguous or unmatched'):
        match(payload, games)


def test_provider_status_conflict_is_not_reconciled():
    payload, games = single(); payload['data'][0]['status'] = 'live'
    with pytest.raises(ValueError, match='ambiguous or unmatched'):
        match(payload, games)


def test_two_official_games_for_same_pair_never_use_drift_recovery():
    payload, games = single()
    games.append({**deepcopy(games[0]), 'gamePk': 999, 'gameDate': '2026-09-11T20:00:00Z'})
    with pytest.raises(ValueError, match='ambiguous or unmatched'):
        match(payload, games)


def test_two_provider_games_for_same_pair_never_use_drift_recovery():
    payload, games = single()
    payload['data'].append({**deepcopy(payload['data'][0]), 'id': 'competing-id', 'kickoff_utc': '2026-09-11T20:00:00Z'})
    with pytest.raises(ValueError, match='ambiguous or unmatched'):
        match(payload, games)


def test_duplicate_provider_id_rejected_before_recovery():
    payload, games = single();payload['data'].append(deepcopy(payload['data'][0]))
    with pytest.raises(ValueError, match='duplicate BBS match ID'):
        match(payload, games)


def test_unknown_alias_and_swapped_sides_still_fail():
    for mode in ('unknown', 'swapped'):
        payload, games = single(); e = payload['data'][0]
        if mode == 'unknown':
            e['home']['name'] = 'Milwaukee Brewerz'
        else:
            e['home'], e['away'] = e['away'], e['home']
        with pytest.raises(ValueError, match='ambiguous or unmatched'):
            match(payload, games)


def test_exact_doubleheader_identity_path_is_preserved():
    payload, games = single(); games[0].update(doubleHeader='Y', gameNumber=2)
    payload['data'][0]['kickoff_utc'] = games[0]['gameDate']
    assigned, audit = match(payload, games)
    assert list(assigned) == ['823736']
    assert audit[0]['method'] == 'unique_exact_alias_and_game_start'


def test_duplicate_exact_provider_identity_still_fails():
    payload, games = single();payload['data'][0]['kickoff_utc'] = games[0]['gameDate']
    payload['data'].append({**deepcopy(payload['data'][0]), 'id': 'other'})
    with pytest.raises(ValueError, match='multiple BBS'):
        match(payload, games)


def test_exact_start_ambiguity_is_not_resolved_by_fallback():
    payload, games = single();payload['data'][0]['kickoff_utc'] = games[0]['gameDate']
    games.append({**deepcopy(games[0]), 'gamePk': 999})
    with pytest.raises(ValueError, match='ambiguous or unmatched'):
        match(payload, games)


def test_unknown_timezone_is_rejected():
    payload, games = single();payload['data'][0]['kickoff_utc'] = '2026-09-11T23:40:00'
    with pytest.raises(ValueError, match='timezone'):
        match(payload, games)


def test_duplicate_or_wrong_day_official_games_cannot_enable_fallback():
    payload, games = single(); games[0]['gameDate'] = '2026-09-12T23:45:00Z'
    with pytest.raises(ValueError, match='ambiguous or unmatched'):
        match(payload, games)


def test_recovery_report_and_crosswalk_are_wired_without_changing_prediction_time():
    source = (Path(__file__).resolve().parents[2]/'ks1/daily.py').read_text()
    assert "'games': crosswalk.game_rows" in source
    assert "'schedule_time_reconciliations':" in source
    assert "'commence_time': start.isoformat()" in source
    assert 'start-timedelta(minutes=10)' in source


def test_other_eastern_date_is_excluded_while_after_midnight_utc_games_remain():
    f = fixture()
    other = {**deepcopy(f['bbs']['data'][0]), 'id': 'next-day', 'kickoff_utc': '2026-09-12T18:20:00Z'}
    f['bbs']['data'].append(other)
    assigned, audit = match(f['bbs'], f['official_games'])
    assert len(assigned) == len(audit) == 15
    assert 'next-day' not in {e['id'] for e in assigned.values()}
    assert '823173' in assigned  # Giants/Padres start after midnight UTC.
