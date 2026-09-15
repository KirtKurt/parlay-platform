"""Isolate unmatched BBS identities without failing the slate."""
import pytest

from ks1.daily import bbs_assignments


class _Walk:
    def resolve(self, name):
        return {'Home': '1', 'Away': '2'}.get(name)

    def bind(self, *args, **kwargs):
        pass

    game_time_adjustments = []


def _event(eid, home='Home', away='Away', kick='2026-09-15T17:00:00Z'):
    return {
        'id': eid, 'kickoff_utc': kick, 'home': {'id': 'h', 'name': home},
        'away': {'id': 'a', 'name': away}, 'sport': 'baseball', 'league': 'mlb',
        'status': 'scheduled',
    }


def _game(pk='100', home='1', away='2', when='2026-09-15T17:00:00Z'):
    return {
        'gamePk': pk, 'gameDate': when, 'doubleHeader': 'N',
        'status': {'startTimeTBD': False},
        'teams': {'home': {'team': {'id': home}}, 'away': {'team': {'id': away}}},
    }


def test_extra_bbs_event_is_skipped():
    schedule = [_game()]
    payload = {'data': [_event('match'), _event('orphan', home='X', away='Y')]}
    assigned = bbs_assignments(payload, schedule, _Walk(), '2026-09-15')
    assert list(assigned) == ['100']
    assert assigned['100']['id'] == 'match'


def test_duplicate_bbs_to_one_game_still_hard_error():
    schedule = [_game()]
    payload = {'data': [_event('a'), _event('b')]}
    with pytest.raises(ValueError, match='multiple BBS IDs map to one official game'):
        bbs_assignments(payload, schedule, _Walk(), '2026-09-15')


def test_truncated_catalogue_still_hard_error():
    payload = {'data': [_event(str(i)) for i in range(200)]}
    with pytest.raises(ValueError, match='truncated'):
        bbs_assignments(payload, [], _Walk(), '2026-09-15')
