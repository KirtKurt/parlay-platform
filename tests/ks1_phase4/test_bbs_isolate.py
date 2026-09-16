"""Isolate-skip: unmatched BBS events continue; missing official BBS is an exclusion."""
import pytest
from copy import deepcopy
from ks1.daily import Crosswalk, bbs_assignments


def fixture():
    game = {'gamePk': 823736, 'gameDate': '2026-09-11T23:45:00Z', 'doubleHeader': 'N',
            'status': {'abstractGameState': 'Preview', 'detailedState': 'Scheduled', 'startTimeTBD': False},
            'teams': {'home': {'team': {'id': 158, 'name': 'Milwaukee Brewers'}},
                      'away': {'team': {'id': 113, 'name': 'Cincinnati Reds'}}}}
    event = {'id': '9ba968c3-513f-4e43-81eb-34e1244b07a0', 'kickoff_utc': '2026-09-11T23:45:00Z',
             'sport': 'baseball', 'league': 'MLB', 'status': 'scheduled',
             'home': {'id': 'h', 'name': 'Milwaukee Brewers'},
             'away': {'id': 'a', 'name': 'Cincinnati Reds'}}
    return game, event


def test_unmatched_bbs_event_is_skipped():
    game, event = fixture()
    extra = dict(event, id='unmatched-extra',
                 home={'id': 'x', 'name': 'Unknown Club'}, away={'id': 'y', 'name': 'Other Club'})
    assigned = bbs_assignments({'data': [event, extra]}, [game], Crosswalk([], [game]), '2026-09-11')
    assert assigned == {'823736': event}


def test_duplicate_bbs_to_one_game_is_hard_error():
    game, event = fixture()
    twin = dict(event, id='second-same-game')
    with pytest.raises(ValueError, match='multiple BBS'):
        bbs_assignments({'data': [event, twin]}, [game], Crosswalk([], [game]), '2026-09-11')


def test_truncated_catalogue_is_hard_error():
    game, event = fixture()
    with pytest.raises(ValueError, match='truncated'):
        bbs_assignments({'data': [event] * 200}, [game], Crosswalk([], [game]), '2026-09-11')
