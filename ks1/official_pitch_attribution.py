"""Prove automatic count events and mid-at-bat substitutions from play events.

This module only proposes reproducible derivations. Independent final box-score
counts and the retained-source/time checks remain mandatory admission gates.
"""
from collections import defaultdict
import math


AUTOMATIC_CODES = {'automatic_ball': {'AB', 'VP'},
                   'automatic_strike': {'AS', 'VB', 'VC'}}


def candidate_groups(rows):
    grouped = defaultdict(list)
    for i, row in enumerate(rows):
        grouped[str(row['game_pk']), str(row['at_bat_number'])].append(i)
    return {key: indices for key, indices in grouped.items()
            if len({str(rows[i]['batter']) for i in indices}) > 1
            or any(str(rows[i].get('description')) in AUTOMATIC_CODES
                   and (rows[i].get('pitch_type') not in (None, '')
                        or rows[i].get('release_speed') not in (None, ''))
                   for i in indices)}


def derive(rows, evidence, scheduled_by_game):
    from ks1.official_outcomes import positive_id, event_name
    from ks1.statcast_events import is_plate_appearance
    from ks1.features import utc
    changes = []
    for (pk, ab), indices in candidate_groups(rows).items():
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
        mixed = len({str(rows[i]['batter']) for i in indices}) > 1
        terminal_batter = positive_id(play['matchup']['batter']['id'])
        pitcher = positive_id(play['matchup']['pitcher']['id'])
        if mixed:
            # Two-strike substitutions can charge the predecessor a strikeout;
            # leave those cases unqualified until their credit is separately proven.
            if (len(substitutions) != 1
                    or substitutions[0]['details'].get('eventType') != 'offensive_substitution'
                    or substitutions[0].get('position', {}).get('abbreviation') != 'PH'
                    or type(substitutions[0]['count'].get('strikes')) is not int
                    or not 0 <= substitutions[0]['count']['strikes'] < 2
                    or positive_id(substitutions[0]['player']['id']) != terminal_batter):
                raise ValueError('unsupported official mid-at-bat substitution')
            current_batter = positive_id(substitutions[0]['replacedPlayer']['id'])
        else:
            if substitutions:
                raise ValueError('unexpected substitution in automatic count-event proof')
            current_batter = terminal_batter
        sequence = []
        previous_end = utc(start)
        physical_number = 0
        for event in events:
            if utc(event['startTime']) < previous_end or utc(event['endTime']) < utc(event['startTime']):
                raise ValueError('official pitch-event chronology invalid')
            previous_end = utc(event['endTime'])
            if previous_end > utc(about['endTime']):
                raise ValueError('official pitch exceeds completed at-bat')
            if event in substitutions:
                current_batter = terminal_batter
            code = event.get('details', {}).get('call', {}).get('code')
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
            if positive_id(row['batter']) != batter or positive_id(row['pitcher']) != pitcher:
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
            if fields:
                changes.append({'row_index': i, 'game_pk': pk, 'at_bat_number': ab,
                                'fields': fields, 'original_fields': {k: row.get(k) for k in fields},
                                'derivation_kind': 'official_automatic_count_event',
                                'official_source_sha256': source['receipt']['sha256']})
                row.update(fields)
        if mixed:
            terminal = rows[ordered[-1]]
            if (not is_plate_appearance(terminal)
                    or event_name(terminal['events']) != event_name(play['result']['eventType'])
                    or positive_id(terminal['batter']) != terminal_batter):
                raise ValueError('substitution lacks a matching terminal plate appearance')
            changes.append({'row_index': ordered[-1], 'game_pk': pk, 'at_bat_number': ab,
                            'fields': {}, 'original_fields': {}, 'credited_batter': terminal_batter,
                            'derivation_kind': 'official_mid_at_bat_credit',
                            'official_source_sha256': source['receipt']['sha256']})
    return changes
