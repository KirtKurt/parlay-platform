"""BBS isolation must not change trusted matches, inference or stored rows."""
from copy import deepcopy
import json

import pytest

from ks1 import daily
from ks1.inventory import encode
from tests.ks1_phase4.test_daily import adjusted_start_fixture
from tests.ks1_phase5.test_refresh import AT, DATE, advance, capture, envelope, seal


def replace_bbs(folder, events, at=AT):
    (folder/'bbs.json').write_bytes(encode(envelope({'data': events}, at)))
    seal(folder, at)


def fixture():
    game, event = adjusted_start_fixture()
    event['kickoff_utc'] = game['gameDate']
    return game, event


def assign(events, games):
    cw = daily.Crosswalk([], games)
    result = daily.bbs_assignments({'data': events}, games, cw, '2026-09-11', isolate_unmatched=True)
    return result, cw


def test_extra_unmatched_event_leaves_matched_predictions_byte_identical(capture):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output)
    body = (out/'predictions.parquet').read_bytes()
    events = json.loads((folder/'bbs.json').read_bytes())['payload']['data']
    extra = deepcopy(events[0])
    extra.update(id='unmatched-extra')
    extra['home'] = {'id': 'unknown-home', 'name': 'Unknown Team'}
    replace_bbs(folder, events+[extra])
    second, report, out = daily.predict(folder, output)
    assert first.equals(second)
    assert body == (out/'predictions.parquet').read_bytes()
    assert calls == [2, 2]
    assert report['bbs_matched_games'] == 2 and report['exclusions'] == []
    assert report['bbs_identity_exclusions'] == [{
        'bbs_game_id': 'unmatched-extra', 'bbs_start': events[0]['kickoff_utc'],
        'reason': 'unmatched_bbs_identity', 'official_candidate_game_ids': []}]
    crosswalk = json.loads((out/'crosswalk.json').read_bytes())
    assert crosswalk['bbs_identity_exclusions'] == report['bbs_identity_exclusions']
    assert not any(row['bbs_id'] == 'unknown-home' for row in crosswalk['teams'])


@pytest.mark.parametrize('mode', ['absent', 'unknown_team', 'time_mismatch'])
def test_unverified_new_game_is_explicitly_excluded_not_invented(capture, mode):
    folder, output, calls, _ = capture
    events = json.loads((folder/'bbs.json').read_bytes())['payload']['data']
    if mode == 'absent':
        events.pop(0)
    elif mode == 'unknown_team':
        events[0]['home']['name'] = 'Unknown Team'
    else:
        events[0]['kickoff_utc'] = DATE+'T19:50:00Z'
    replace_bbs(folder, events)
    table, report, _ = daily.predict(folder, output)
    assert [row['game_id'] for row in table.to_pylist()] == ['2']
    assert report['official_games'] == 2 and report['bbs_matched_games'] == 1
    assert report['exclusions'] == [{'game_id': '1', 'reason': 'missing_bbs_identity',
                                    'retained_previous': False}]
    assert calls == [1]


@pytest.mark.parametrize('at', [DATE+'T10:01:00+00:00', DATE+'T19:50:01+00:00'])
def test_missing_bbs_retains_prior_rows_before_and_after_t10(capture, at):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output)
    body = (out/'predictions.parquet').read_bytes()
    advance(folder, out, at=at)
    events = json.loads((folder/'bbs.json').read_bytes())['payload']['data']
    replace_bbs(folder, events[1:], at)
    second, report, out = daily.predict(folder, output)
    assert first.equals(second) and body == (out/'predictions.parquet').read_bytes()
    assert calls == [2] and report['newly_scored'] == 0
    assert report['removed_game_ids'] == []
    if at == DATE+'T10:01:00+00:00':
        assert report['exclusions'] == [{'game_id': '1', 'reason': 'missing_bbs_identity',
                                        'retained_previous': True}]
    else:
        assert report['preserved_pregame_rows'] == 2


def test_ambiguous_event_is_not_assigned_or_bound():
    game, event = fixture()
    games = [game, dict(deepcopy(game), gamePk=999)]
    original = deepcopy((games, event))
    result, cw = assign([event], games)
    assert result == {} and cw.bbs_ids == {} and cw.rows == []
    assert cw.bbs_identity_exclusions[0]['reason'] == 'ambiguous_bbs_identity'
    assert set(cw.bbs_identity_exclusions[0]['official_candidate_game_ids']) == {'823736', '999'}
    assert (games, event) == original


@pytest.mark.parametrize('fault, message', [
    ('duplicate_id', 'duplicate BBS match ID'),
    ('duplicate_mapping', 'multiple BBS IDs'),
    ('non_mlb_sport', 'non-MLB'), ('non_mlb_league', 'non-MLB'),
    ('missing_team_id', 'schema'), ('missing_team_name', 'schema'),
    ('missing_status', 'schema'), ('malformed_team', 'schema'),
    ('malformed_event', 'schema'), ('truncation', 'truncated'),
])
def test_corrupt_catalogue_still_hard_fails(fault, message):
    game, event = fixture()
    other = deepcopy(event)
    other['id'] = 'extra'
    events = [event, other]
    if fault == 'duplicate_id': other['id'] = event['id']
    elif fault == 'non_mlb_sport': other['sport'] = 'soccer'
    elif fault == 'non_mlb_league': other['league'] = 'MiLB'
    elif fault == 'missing_team_id': del other['home']['id']
    elif fault == 'missing_team_name': del other['away']['name']
    elif fault == 'missing_status': del other['status']
    elif fault == 'malformed_team': other['home'] = 'not a team object'
    elif fault == 'malformed_event': events[1] = None
    elif fault == 'truncation': events = [event]*200
    with pytest.raises(ValueError, match=message):
        assign(events, [game])


def test_duplicate_unmatched_ids_are_not_silently_skipped():
    game, event = fixture()
    event['home']['name'] = 'Unknown Team'
    with pytest.raises(ValueError, match='duplicate BBS match ID'):
        assign([event, deepcopy(event)], [game])


def test_conflicting_team_crosswalk_remains_a_hard_failure():
    game, event = fixture()
    other_game = deepcopy(game)
    other_game['gamePk'] = 999
    other_game['teams']['home']['team'] = {'id': 999, 'name': 'Other Home'}
    other = deepcopy(event)
    other['id'] = 'other'
    other['home']['name'] = 'Other Home'  # Reuses the first team's BBS ID.
    with pytest.raises(ValueError, match='conflicting BBS/MLB team crosswalk'):
        assign([event, other], [game, other_game])


@pytest.mark.parametrize('case', ['over_five_minutes', 'doubleheader', 'two_official_games',
                                  'two_provider_games', 'unknown_team', 'reversed_teams',
                                  'time_tbd', 'missing_dh_flag'])
def test_isolation_does_not_relax_existing_identity_boundaries(case):
    game, event = adjusted_start_fixture()
    games, events = [game], [event]
    if case == 'over_five_minutes': event['kickoff_utc'] = '2026-09-11T23:39:59Z'
    elif case == 'doubleheader': game['doubleHeader'] = 'Y'
    elif case == 'two_official_games': games.append(dict(deepcopy(game), gamePk=999, gameDate='2026-09-11T20:00:00Z'))
    elif case == 'two_provider_games': events.append(dict(deepcopy(event), id='second', kickoff_utc='2026-09-11T20:00:00Z'))
    elif case == 'unknown_team': event['home']['name'] = 'Unknown Team'
    elif case == 'reversed_teams': event['home'], event['away'] = event['away'], event['home']
    elif case == 'time_tbd': game['status']['startTimeTBD'] = True
    elif case == 'missing_dh_flag': del game['doubleHeader']
    result, cw = assign(events, games)
    assert result == {} and cw.bbs_ids == {} and cw.game_time_adjustments == []
    assert len(cw.bbs_identity_exclusions) == len(events)


def test_verified_five_minute_adjustment_and_default_strict_mode_are_unchanged():
    game, event = adjusted_start_fixture()
    original = deepcopy((game, event))
    strict = daily.Crosswalk([], [game])
    isolated, cw = assign([event], [game])
    assert isolated == daily.bbs_assignments({'data': [event]}, [game], strict, '2026-09-11')
    assert cw.rows == strict.rows and cw.game_time_adjustments == strict.game_time_adjustments
    assert cw.bbs_identity_exclusions == [] and (game, event) == original


def test_observed_september16_ten_minute_mismatch_is_not_time_rewritten():
    # Retained run 35091046705, artifact 10444855169. ZIP SHA256:
    # 89335ad4e3d5fd6da3eebe4fc233d4d5b8dc8683cc1544177e7cdb715929359b.
    game = {'gamePk': 823576, 'gameDate': '2026-09-16T23:10:00Z', 'doubleHeader': 'N',
            'status': {'startTimeTBD': False},
            'teams': {'home': {'team': {'id': 121, 'name': 'New York Mets'}},
                      'away': {'team': {'id': 110, 'name': 'Baltimore Orioles'}}}}
    event = {'id': '4aa9a275-f30b-44fe-92dd-d57d90997abc',
             'kickoff_utc': '2026-09-16T23:00:00.000Z', 'sport': 'baseball', 'league': 'MLB',
             'status': 'scheduled',
             'home': {'id': '589750f3-3a2e-4bf1-8e39-8ecc2e92fae1', 'name': 'New York Mets'},
             'away': {'id': '9c9cdc34-d351-4f73-8f60-e9ecf8d38861', 'name': 'Baltimore Orioles'}}
    original = deepcopy((game, event))
    cw = daily.Crosswalk([], [game])
    result = daily.bbs_assignments({'data': [event]}, [game], cw, '2026-09-16', isolate_unmatched=True)
    assert result == {} and cw.bbs_ids == {} and cw.game_time_adjustments == []
    assert cw.bbs_identity_exclusions[0]['official_candidate_game_ids'] == ['823576']
    assert (game, event) == original
