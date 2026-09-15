"""Prove a runner's third out on a wild pitch without inventing a batter PA."""

EVENTS = frozenset(('wild_pitch', 'passed_ball'))


def needs_inning_evidence(source, rows):
    from ks1.official_outcomes import unfinished_at_bats
    groups = unfinished_at_bats({'rows': rows})
    return any((str(rows[0]['game_pk']), str(p['about']['atBatIndex'] + 1)) in groups
               and p['result'].get('eventType') in EVENTS
               for p in source['data']['liveData']['plays']['allPlays'])


def verified_inning_ending(play, source, rows):
    import math
    from ks1.official_outcomes import positive_id
    from ks1.features import utc
    if play['result'].get('eventType') not in EVENTS or play['result'].get('isOut') is not True:
        return False
    plays = source['data']['liveData']['plays']['allPlays']
    i = plays.index(play)
    if i == 0 or i == len(plays) - 1:
        return False
    prior, following = plays[i - 1], plays[i + 1]
    about = play['about']; count = play.get('count', {})
    if (type(count.get('outs')) is not int or count['outs'] != 3
            or type(prior.get('count', {}).get('outs')) is not int or prior['count']['outs'] != 2
            or prior['about']['inning'] != about['inning']
            or prior['about']['isTopInning'] is not about['isTopInning']
            or type(about.get('inning')) is not int or about['inning'] < 1
            or type(about.get('isTopInning')) is not bool
            or following['about'].get('isComplete') is not True
            or type(following['about'].get('inning')) is not int
            or type(following['about'].get('isTopInning')) is not bool
            or following['about']['inning'] != about['inning'] + (not about['isTopInning'])
            or following['about']['isTopInning'] is about['isTopInning']
            or utc(prior['about']['endTime']) >= utc(about['endTime'])
            or utc(following['about']['endTime']) <= utc(about['endTime'])):
        return False
    events = play.get('playEvents', [])
    if not events:
        return False
    terminal = events[-1]; pitch_count = terminal.get('count', {})
    if (terminal.get('isPitch') is not True
            or terminal.get('details', {}).get('isInPlay') is not False
            or type(pitch_count.get('outs')) is not int or pitch_count['outs'] != 2
            or any(type(count.get(k)) is not int or type(pitch_count.get(k)) is not int
                   or count[k] != pitch_count[k] or not 0 <= count[k] < limit
                   for k, limit in (('balls', 4), ('strikes', 3)))
            or utc(terminal['endTime']) != utc(about['endTime'])):
        return False
    row = max(rows, key=lambda r: int(positive_id(r['pitch_number'])))
    raw_speed = float(row['release_speed']); speed = float(terminal['pitchData']['startSpeed'])
    if (positive_id(row['pitch_number']) != positive_id(terminal['pitchNumber'])
            or row.get('type') not in ('B', 'S')
            or row.get('pitch_type') != terminal['details'].get('type', {}).get('code')
            or not math.isfinite(raw_speed) or not math.isfinite(speed) or abs(raw_speed - speed) > .051):
        return False
    batter = positive_id(play['matchup']['batter']['id'])
    runners = play.get('runners', [])
    outs = [r for r in runners if r.get('movement', {}).get('isOut') is True]
    if len(outs) != 1 or any(positive_id(r['details']['runner']['id']) == batter for r in runners):
        return False
    out = outs[0]
    return (type(out['movement'].get('outNumber')) is int and out['movement']['outNumber'] == 3
            and out['movement'].get('outBase') in ('2B', '3B', '4B', 'home')
            and out['details'].get('eventType') == 'other_out'
            and out['details'].get('movementReason') == 'r_runner_out'
            and type(out['details'].get('playIndex')) is int
            and out['details']['playIndex'] == terminal['index'])
