"""Prove automatic count events and mid-at-bat substitutions from play events.

This module only proposes reproducible derivations. Independent final box-score
counts and the retained-source/time checks remain mandatory admission gates.
"""
from collections import defaultdict
import math


# MLB's /api/v1/pitchCodes classification: each code has pitchStatus:false.
AUTOMATIC_CODES = {'automatic_ball': {'V', 'VB', 'VC', 'VP', 'VS'},
                   'automatic_strike': {'A', 'AB', 'AC'}}


def candidate_groups(rows, evidence=None):
    grouped = defaultdict(list)
    for i, row in enumerate(rows):
        grouped[str(row['game_pk']), str(row['at_bat_number'])].append(i)
    candidates = {key: indices for key, indices in grouped.items()
            if len({str(rows[i]['batter']) for i in indices}) > 1
            or any(str(rows[i].get('description')) in AUTOMATIC_CODES
                   and (rows[i].get('pitch_type') not in (None, '')
                        or rows[i].get('release_speed') not in (None, ''))
                   for i in indices)}
    for game_id, source in (evidence or {}).items():
        for play in source['data']['liveData']['plays']['allPlays']:
            key = (str(game_id), str(play['about']['atBatIndex'] + 1))
            indices = grouped.get(key, [])
            substitutions = [event for event in play.get('playEvents', [])
                             if event.get('isSubstitution') is True
                             and event.get('details', {}).get('eventType')
                                 == 'offensive_substitution']
            if (indices and substitutions
                    and len({str(rows[i]['batter']) for i in indices}) == 1
                    and str(rows[indices[-1]]['batter'])
                        != str(play['matchup']['batter']['id'])):
                candidates[key] = indices
    return candidates


def derive(rows, evidence, scheduled_by_game, *, extended_substitutions=False,
           two_strike_strikeouts=False, prefix_mound_visits=False,
           prefix_game_advisories=False, raw_rows=None):
    from ks1.official_outcomes import positive_id, event_name
    from ks1.statcast_events import is_plate_appearance
    from ks1.features import utc
    changes = []
    for (pk, ab), indices in candidate_groups(rows, evidence).items():
        source = evidence[pk]
        plays = [p for p in source['data']['liveData']['plays']['allPlays']
                 if str(p['about']['atBatIndex'] + 1) == ab]
        if len(plays) != 1:
            raise ValueError('pitch attribution play missing or ambiguous')
        play = plays[0]
        about = play['about']
        start = (scheduled_by_game[pk][0] if scheduled_by_game is not None
                 else source['data']['gameData']['datetime']['dateTime'])
        if (type(about['atBatIndex']) is not int or about['atBatIndex'] < 0
                or play['atBatIndex'] != about['atBatIndex']
                or about['isComplete'] is not True or utc(about['endTime']) < utc(start)):
            raise ValueError('pitch attribution play incomplete')
        ordered = sorted(indices, key=lambda i: int(positive_id(rows[i]['pitch_number'])))
        if [int(rows[i]['pitch_number']) for i in ordered] != list(range(1, len(indices) + 1)):
            raise ValueError('pitch attribution requires a complete ordered count-event sequence')
        events = play['playEvents']
        if (any(type(e['index']) is not int for e in events)
                or [e['index'] for e in events] != list(range(len(events)))):
            raise ValueError('official play-event indices incomplete')
        substitutions = [e for e in events if e.get('isSubstitution') is True]
        mixed = (len({str(rows[i]['batter']) for i in indices}) > 1
                 or any(event.get('details', {}).get('eventType')
                        == 'offensive_substitution' for event in substitutions))
        terminal_batter = positive_id(play['matchup']['batter']['id'])
        credited_batter = terminal_batter
        pitcher = positive_id(play['matchup']['pitcher']['id'])
        administrative_prelude_ids = set()
        first_count_event = next((event for event in events
            if event.get('isPitch') is True or any(
                (event.get('details', {}).get('code')
                 or event.get('details', {}).get('call', {}).get('code')) in codes
                for codes in AUTOMATIC_CODES.values())), None)
        if prefix_game_advisories and first_count_event is not None:
            count_index = events.index(first_count_event)
            prelude = events[:count_index]
            # MLB can attach its pregame status transition chain to play zero.
            # It is administrative evidence only when the complete prefix is a
            # contiguous, zero-count sequence that brackets the scheduled start
            # and ends exactly when the first count event begins.
            advisory_prelude = (about['atBatIndex'] == 0 and len(prelude) == 3
                and [event.get('details', {}).get('description') for event in prelude] == [
                    'Status Change - Pre-Game', 'Status Change - Warmup',
                    'Status Change - In Progress']
                and all(
                    event.get('isPitch') is False
                    and event.get('isSubstitution') is not True
                    and event.get('type') == 'action'
                    and event.get('details', {}).get('eventType') == 'game_advisory'
                    and not event.get('pitchData')
                    and event.get('pitchNumber') in (None, '')
                    and all(type(event.get('count', {}).get(k)) is int
                            and event['count'][k] == 0 for k in ('balls', 'strikes', 'outs'))
                    and utc(event['startTime']) <= utc(event['endTime'])
                    for event in prelude)
                and all(utc(left['endTime']) == utc(right['startTime'])
                        for left, right in zip(prelude, prelude[1:]))
                and utc(prelude[0]['startTime']) <= utc(start)
                <= utc(prelude[-1]['endTime'])
                and utc(prelude[-1]['endTime']) == utc(first_count_event['startTime'])
                and utc(first_count_event['startTime']) <= utc(about['endTime']))
            if advisory_prelude:
                administrative_prelude_ids.update(id(event) for event in prelude)
        if mixed:
            # Rule 9.15(b) charges a two-strike substitute's strikeout to the
            # predecessor. Other completed outcomes belong to the substitute,
            # but only accept them with the inherited count independently shown.
            two_strike_credit = False
            predecessor_strikeout = False
            if extended_substitutions and len(substitutions) == 1:
                sub = substitutions[0]
                prior = [e for e in events[:events.index(sub)] if e.get('isPitch') is True]
                count = sub.get('count', {})
                two_strike_credit = (type(count.get('strikes')) is int and count['strikes'] == 2
                    and type(count.get('balls')) is int and 0 <= count['balls'] < 4
                    and bool(prior) and prior[-1].get('count', {}).get('strikes') == 2
                    and prior[-1].get('count', {}).get('balls') == count['balls']
                    and not event_name(play['result']['eventType']).startswith('strikeout'))
                if two_strike_strikeouts:
                    from ks1.two_strike_credit import proven_strikeout
                    predecessor_strikeout = (raw_rows is not None and
                        proven_strikeout(play, sub, prior, raw_rows[ordered[-1]]))
            if (len(substitutions) != 1
                    or substitutions[0]['details'].get('eventType') != 'offensive_substitution'
                    or substitutions[0].get('position', {}).get('abbreviation') != 'PH'
                    or type(substitutions[0]['count'].get('strikes')) is not int
                    or not (0 <= substitutions[0]['count']['strikes'] < 2
                            or two_strike_credit or predecessor_strikeout)
                    or positive_id(substitutions[0]['player']['id']) != terminal_batter):
                raise ValueError('unsupported official mid-at-bat substitution')
            current_batter = positive_id(substitutions[0]['replacedPlayer']['id'])
            if predecessor_strikeout:
                credited_batter = current_batter
        else:
            # A zero-count pitching change before every count event leaves all
            # rows attributed to the incoming pitcher. An official mound visit
            # may precede that change, but it must be a zero-count,
            # non-substitution action. Mid-count changes and batter/runner/
            # defensive substitutions remain unsupported here.
            prefix_pitching_change = False
            if extended_substitutions and len(substitutions) == 1:
                sub = substitutions[0]
                sub_index = events.index(sub)
                prelude = events[:sub_index]
                following = events[sub_index + 1:]
                first_count_event = next((event for event in following
                    if event.get('isPitch') is True or any(
                        (event.get('details', {}).get('code')
                         or event.get('details', {}).get('call', {}).get('code')) in codes
                        for codes in AUTOMATIC_CODES.values())), None)
                proven_prelude = (not prelude or prefix_mound_visits
                    and first_count_event is not None and all(
                    event.get('isPitch') is False
                    and event.get('isSubstitution') is not True
                    and event.get('type') == 'action'
                    and event.get('details', {}).get('eventType') == 'mound_visit'
                    and not event.get('pitchData')
                    and event.get('pitchNumber') in (None, '')
                    and all(type(event.get('count', {}).get(k)) is int
                            and event['count'][k] == 0 for k in ('balls', 'strikes'))
                    and utc(start) <= utc(event['startTime']) <= utc(sub['startTime'])
                    and utc(event['startTime']) <= utc(event['endTime'])
                    <= utc(first_count_event['endTime']) <= utc(about['endTime'])
                    for event in prelude))
                prefix_pitching_change = (proven_prelude and sub.get('isPitch') is False
                    and sub.get('type') == 'action'
                    and sub.get('details', {}).get('eventType') == 'pitching_substitution'
                    and sub.get('position', {}).get('abbreviation') == 'P'
                    and positive_id(sub['player']['id']) == pitcher
                    and all(type(sub.get('count', {}).get(k)) is int and sub['count'][k] == 0
                            for k in ('balls', 'strikes')))
                if prefix_pitching_change:
                    administrative_prelude_ids = {id(event) for event in prelude}
            if substitutions and not prefix_pitching_change:
                raise ValueError('unexpected substitution in automatic count-event proof')
            current_batter = terminal_batter
        sequence = []
        previous_end = utc(start)
        physical_number = 0
        for event in events:
            event_start, event_end = utc(event['startTime']), utc(event['endTime'])
            if (event_end < event_start
                    or (id(event) not in administrative_prelude_ids
                        and (event_start < utc(start) or event_start < previous_end))):
                raise ValueError('official pitch-event chronology invalid')
            if id(event) not in administrative_prelude_ids:
                previous_end = event_end
            if event_end > utc(about['endTime']):
                raise ValueError('official pitch exceeds completed at-bat')
            if event in substitutions:
                current_batter = terminal_batter
            details = event.get('details', {})
            code, call_code = details.get('code'), details.get('call', {}).get('code')
            if code is not None and call_code is not None and code != call_code:
                raise ValueError('official event code fields contradict each other')
            code = code if code is not None else call_code
            automatic = next((name for name, codes in AUTOMATIC_CODES.items() if code in codes), None)
            if automatic:
                if (event.get('isPitch') is not False or event.get('type') != 'no_pitch'
                        or event.get('pitchData')):
                    raise ValueError('official automatic event contains physical-pitch evidence')
            elif event.get('isPitch') is True:
                physical_number += 1
                if (event.get('type') != 'pitch' or type(event.get('pitchNumber')) is not int
                        or event['pitchNumber'] != physical_number):
                    raise ValueError('official physical pitch sequence invalid')
            else:
                continue
            sequence.append((event, current_batter, automatic))
        if len(sequence) != len(ordered):
            raise ValueError('official and Statcast count-event sequences differ')
        for i, (event, batter, automatic) in zip(ordered, sequence):
            row = rows[i]
            if positive_id(row['pitcher']) != pitcher:
                raise ValueError('official per-pitch player attribution differs')
            fields = {}
            if automatic:
                if row.get('description') != automatic:
                    raise ValueError('automatic count-event identity differs')
                for field in ('pitch_type', 'release_speed'):
                    if row.get(field) not in (None, ''):
                        fields[field] = ''
            else:
                if row.get('description') in AUTOMATIC_CODES:
                    raise ValueError('Statcast automatic event is an official thrown pitch')
                # Matching tracked type and rounded speed binds the complete
                # sequence despite automatic events shifting provider numbering.
                data = event.get('pitchData', {})
                speed, official_speed = float(row['release_speed']), float(data['startSpeed'])
                if (not math.isfinite(speed) or not math.isfinite(official_speed)
                        or row.get('pitch_type') != event['details'].get('type', {}).get('code')
                        or abs(speed - official_speed) > .051):
                    raise ValueError('official and Statcast physical pitch tracking differs')
            if positive_id(row['batter']) != batter:
                if (not predecessor_strikeout
                        or positive_id(row['batter']) != credited_batter
                        or batter != terminal_batter):
                    raise ValueError('official per-pitch player attribution differs')
                fields['batter'] = batter
            if fields:
                changes.append({'row_index': i, 'game_pk': pk, 'at_bat_number': ab,
                                'fields': fields, 'original_fields': {k: row.get(k) for k in fields},
                                'derivation_kind': ('official_automatic_count_event' if automatic
                                    else 'official_substitution_pitch_attribution'),
                                'official_source_sha256': source['receipt']['sha256']})
                row.update(fields)
        if mixed:
            terminal = rows[ordered[-1]]
            if (not is_plate_appearance(terminal)
                    or event_name(terminal['events']) != event_name(play['result']['eventType'])
                    or positive_id(terminal['batter']) != terminal_batter):
                raise ValueError('substitution lacks a matching terminal plate appearance')
            changes.append({'row_index': ordered[-1], 'game_pk': pk, 'at_bat_number': ab,
                            'fields': {}, 'original_fields': {}, 'credited_batter': credited_batter,
                            'derivation_kind': 'official_mid_at_bat_credit',
                            'official_source_sha256': source['receipt']['sha256']})
    return changes


WALKOFF_RUNNER_EVENTS = frozenset(('stolen_base_2b', 'stolen_base_3b', 'stolen_base_home',
                                    'wild_pitch', 'passed_ball', 'balk', 'error'))


def needs_walkoff_evidence(source, raw_rows):
    from ks1.official_outcomes import unfinished_at_bats
    groups = unfinished_at_bats({'rows': raw_rows})
    return any((str(raw_rows[0]['game_pk']), str(p['about']['atBatIndex'] + 1)) in groups
               and p['result'].get('eventType') in WALKOFF_RUNNER_EVENTS
               for p in source['data']['liveData']['plays']['allPlays'])


def verified_walkoff_ending(play, source):
    """A successful baserunning event ends a PA only when it ends the game."""
    from ks1.official_outcomes import positive_id
    from ks1.features import utc
    plays = source['data']['liveData']['plays']['allPlays']
    about, result = play['about'], play['result']
    if (result.get('eventType') not in WALKOFF_RUNNER_EVENTS or len(plays) < 2
            or play != plays[-1] or about.get('isScoringPlay') is not True
            or about.get('isTopInning') is not False
            or type(about.get('inning')) is not int or about['inning'] < 9):
        return False
    prior = plays[-2]
    scores = [prior['result'].get('homeScore'), prior['result'].get('awayScore'),
              result.get('homeScore'), result.get('awayScore')]
    if (any(type(score) is not int or score < 0 for score in scores)
            or scores[0] > scores[1] or scores[2] <= scores[3]
            or scores[2] <= scores[0] or scores[1] != scores[3]
            or utc(prior['about']['endTime']) >= utc(about['endTime'])):
        return False
    events = play.get('playEvents', [])
    if not events:
        return False
    count = events[-1].get('count', {})
    if (type(count.get('balls')) is not int or not 0 <= count['balls'] < 4
            or type(count.get('strikes')) is not int or not 0 <= count['strikes'] < 3
            or utc(events[-1]['endTime']) != utc(about['endTime'])):
        return False
    batter = positive_id(play['matchup']['batter']['id'])
    scorers = [r for r in play.get('runners', [])
               if r.get('movement', {}).get('end') == 'score'
               and r['movement'].get('isOut') is False
               and r.get('details', {}).get('isScoringEvent') is True]
    return (len(scorers) == scores[2] - scores[0]
            and len({positive_id(r['details']['runner']['id']) for r in scorers}) == len(scorers)
            and all(positive_id(r['details']['runner']['id']) != batter for r in scorers))
