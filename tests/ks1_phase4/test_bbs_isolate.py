from copy import deepcopy
import pytest
from ks1.daily import Crosswalk, bbs_assignments


def adjusted_start_fixture():
    game = {'gamePk': 823736, 'gameDate': '2026-09-11T23:45:00Z', 'doubleHeader': 'N',
            'status': {'abstractGameState': 'Preview', 'detailedState': 'Scheduled', 'startTimeTBD': False},
            'teams': {'home': {'team': {'id': 158, 'name': 'Milwaukee Brewers'}},
                      'away': {'team': {'id': 113, 'name': 'Cincinnati Reds'}}}}
    event = {'id': '9ba968c3-513f-4e43-81eb-34e1244b07a0', 'kickoff_utc': '2026-09-11T23:40:00Z',
             'sport': 'baseball', 'league': 'MLB', 'status': 'scheduled',
             'home': {'id': '3848ebb8-eb47-4135-b1ba-7354f1fb5b02', 'name': 'Milwaukee Brewers'},
             'away': {'id': '49dfd770-26ca-4b8c-8eb9-68ab2dce1bb2', 'name': 'Cincinnati Reds'}}
    return game, event


def test_unmatched_bbs_event_is_skipped_and_does_not_fail():
    game, event = adjusted_start_fixture()
    extra = dict(event, id='4aa9a275-f30b-44fe-92dd-d57d90997abc',
                 kickoff_utc='2026-09-11T18:00:00Z',
                 home={'id': 'x', 'name': 'Unmatched Extra'},
                 away={'id': 'y', 'name': 'Also Unmatched'})
    assigned = bbs_assignments({'data': [event, extra]}, [game], Crosswalk([], [game]), '2026-09-11')
    assert assigned == {'823736': event}


def test_duplicate_bbs_to_one_game_still_hard_error():
    game, event = adjusted_start_fixture()
    event2 = dict(event, id='dup-bbs')
    with pytest.raises(ValueError, match='multiple BBS IDs map to one official game'):
        bbs_assignments({'data': [event, event2]}, [game], Crosswalk([], [game]), '2026-09-11')


def test_truncated_bbs_catalogue_still_hard_error():
    with pytest.raises(ValueError, match='truncated'):
        bbs_assignments({'data': [{'id': str(i)} for i in range(200)]}, [], Crosswalk([], []), '2026-09-11')


def test_bbs_schema_change_still_hard_error():
    with pytest.raises(ValueError, match='schema changed'):
        bbs_assignments({'data': [{'id': '1'}]}, [], Crosswalk([], []), '2026-09-11')
