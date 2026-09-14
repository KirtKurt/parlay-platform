from copy import deepcopy

from ks1.features import Features


def game(pk, played, completed, *, team=10, hits=1, strikeouts=2):
    batting = dict(atBats=4, hits=hits, baseOnBalls=1, hitByPitch=0,
                   sacFlies=0, doubles=0, triples=0, homeRuns=0, strikeOuts=1)
    pitching = dict(outs=3, earnedRuns=0, runs=0, hits=hits, homeRuns=0,
                    baseOnBalls=1, hitBatsmen=0, strikeOuts=strikeouts,
                    battersFaced=5, wins=0, losses=0, gamesStarted=0,
                    numberOfPitches=20)
    players = {str(pid): {'person': {'id': pid}, 'stats': {'batting': batting}}
               for pid in range(101, 110)}
    players['151'] = {'person': {'id': 151}, 'stats': {'pitching': pitching}}
    return {'officialGamePk': pk, 'startAtUtc': played+'T18:00:00Z',
            'completedAtUtc': completed, 'gameType': 'R',
            'teams': {'home': {'team': {'id': team}, 'teamStats': {}, 'players': players},
                      'away': {'team': {'id': 20}, 'teamStats': {}, 'players': {}}}}


def values(engine, cutoff):
    return (engine.lineup_batters_at(cutoff, list(range(101, 110)),
                                    game_date='2026-09-10'),
            engine.bullpen_roster_at(cutoff, '10', ['151'], game_date='2026-09-10'))


def test_out_of_order_cutoffs_exclude_unfinished_and_same_day_games():
    prior = game(1, '2026-09-08', '2026-09-08T21:00:00Z', team=99)
    resumed = game(2, '2026-09-09', '2026-09-10T16:00:00Z', hits=4, strikeouts=0)
    same_day = game(3, '2026-09-10', '2026-09-10T19:00:00Z', hits=0)
    future = game(4, '2026-09-11', '2026-09-11T21:00:00Z')
    # Nonchronological source order must retain the traded player's history.
    engine = Features([future, resumed, prior, same_day])
    late = values(engine, '2026-09-10T20:00:00Z')
    assert late == values(Features([resumed, prior]), '2026-09-10T20:00:00Z')
    early = values(engine, '2026-09-10T15:00:00Z')
    assert early == values(Features([prior]), '2026-09-10T15:00:00Z')
    # A completion exactly at cutoff is still unavailable.
    assert values(engine, '2026-09-10T16:00:00Z') == early
    assert late != early
    assert values(engine, '2026-09-10T20:00:00Z') == late


def test_roster_order_and_absent_pitch_count_preserve_unknown_fatigue():
    prior = game(1, '2026-09-09', '2026-09-09T21:00:00Z')
    second = deepcopy(prior['teams']['home']['players']['151'])
    second['person']['id'] = 152
    second['stats']['pitching']['numberOfPitches'] = None
    prior['teams']['home']['players']['152'] = second
    engine = Features([prior])
    first = engine.bullpen_roster_at('2026-09-10T17:50:00Z', '10', ['151', '152'])
    reverse = engine.bullpen_roster_at('2026-09-10T17:50:00Z', '10', ['152', '151'])
    assert first == reverse
    assert first['bullpen_context_fatigue_score'] is None
    assert first['bullpen_context_unknown_count'] == 1
