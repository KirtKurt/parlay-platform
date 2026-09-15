"""Prove one provider taxonomy difference without rewriting PA measurements."""
import math


def needs_force_out_evidence(source, rows):
    observed = {str(r['at_bat_number']): r for r in rows
                if r.get('events') == 'fielders_choice_out'}
    return any(str(p['about']['atBatIndex'] + 1) in observed
               and p['result'].get('eventType') == 'force_out'
               for p in source['data']['liveData']['plays']['allPlays'])


def same_force_out_accounting(row, play):
    """Require a matching grounder, batter to first, and one forced runner out.

This deliberately does not equate arbitrary field outs or change raw outcomes,
wOBA weights, denominators, or contact estimates. Missing evidence rejects it.
"""
    from ks1.features import utc
    from ks1.official_outcomes import positive_id
    if (row.get('events') != 'fielders_choice_out'
            or play['result'].get('eventType') != 'force_out'
            or play['result'].get('isOut') is not True
            or row.get('type') != 'X' or row.get('description') != 'hit_into_play'
            or row.get('bb_type') != 'ground_ball'
            or isinstance(row.get('woba_denom'), bool)
            or row.get('woba_denom') not in (1, '1', '1.0')
            or isinstance(row.get('woba_value'), bool)
            or row.get('woba_value') not in (0, '0', '0.0')):
        return False
    events = play.get('playEvents', [])
    if not events:
        return False
    terminal = events[-1]
    speed = float(row['release_speed'])
    official_speed = float(terminal['pitchData']['startSpeed'])
    if (terminal.get('isPitch') is not True
            or terminal.get('details', {}).get('isInPlay') is not True
            or terminal.get('hitData', {}).get('trajectory') != 'ground_ball'
            or type(terminal.get('pitchNumber')) is not int
            or str(terminal['pitchNumber']) != positive_id(row['pitch_number'])
            or terminal['details'].get('type', {}).get('code') != row.get('pitch_type')
            or not math.isfinite(speed) or not math.isfinite(official_speed)
            or abs(speed - official_speed) > .051
            or utc(terminal['endTime']) != utc(play['about']['endTime'])):
        return False
    batter = positive_id(row['batter'])
    runners = play.get('runners', [])
    outs = [r for r in runners if r.get('movement', {}).get('isOut') is True]
    batter_moves = [r for r in runners if positive_id(r['details']['runner']['id']) == batter]
    return (len(outs) == len(batter_moves) == 1
            and positive_id(outs[0]['details']['runner']['id']) != batter
            and outs[0]['movement'].get('outBase') in ('2B', '3B', 'home')
            and outs[0]['details'].get('movementReason') == 'r_force_out'
            and batter_moves[0]['movement'].get('end') == '1B'
            and batter_moves[0]['movement'].get('isOut') is False)
