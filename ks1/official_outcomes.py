"""Reconcile deterministic wOBA accounting against independent official PAs.

Raw Statcast observations remain intact. No contact estimate or seasonal
positive-event weight is inferred. Every derived field is reproducible from
two matching, complete game records and retained official source evidence.
"""
from copy import deepcopy
import hashlib
from urllib.parse import urlencode

from ks1.features import day, utc
from ks1.inventory import encode
from ks1.statcast_events import (PLATE_APPEARANCE_EVENTS, WOBA_EXCLUDED_EVENTS,
                                complete_pa_outcome, is_plate_appearance)

METHOD = 'official_pa_woba_accounting_v1'
FIELDS = ('gameData,game,pk,datetime,dateTime,status,abstractGameState,liveData,'
          'plays,allPlays,result,eventType,about,atBatIndex,isComplete,endTime,'
          'matchup,batter,pitcher,id')
POSITIVE_EVENTS = frozenset(('single', 'double', 'triple', 'home_run', 'walk', 'hit_by_pitch'))
ZERO_EVENTS = PLATE_APPEARANCE_EVENTS - POSITIVE_EVENTS - WOBA_EXCLUDED_EVENTS - {'os_ruling_pending_primary'}


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def endpoint(game_id):
    return f'https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live?' + urlencode({'fields': FIELDS})


def event_name(value):
    value = str(value or '').lower()
    return 'strikeout' if value == 'strike_out' else value


def positive_id(value):
    if isinstance(value, bool) or int(value) <= 0 or str(int(value)) != str(value):
        raise ValueError('invalid PA identity')
    return str(value)


def missing(value):
    return value is None or value == ''


def needed_games(raw):
    return sorted({positive_id(row['game_pk']) for row in raw['rows']
                   if is_plate_appearance(row) and not complete_pa_outcome(row)
                   and (missing(row.get('woba_denom'))
                        or (event_name(row.get('events')) in ZERO_EVENTS
                            and missing(row.get('woba_value'))))})


def schedule_times(schedule):
    """Keep original/resume times from the independently retained schedule."""
    return {str(game['gamePk']): [game['gameDate']] +
            ([game['resumeDate']] if game.get('resumeDate') else [])
            for game in schedule if not game.get('resumedFrom')}


def official_index(evidence, game_id, value, raw_rows, *, require_retained=True,
                   scheduled_times=None):
    body, receipt = evidence['data'], evidence['receipt']
    if (receipt['endpoint'] != endpoint(game_id)
            or receipt['sha256'] != digest(body)):
        raise ValueError('official PA source receipt mismatch')
    if require_retained:
        pointer = evidence['retained_receipt']
        if (set(pointer) != {'name', 'versionId', 'sha256'}
                or pointer['name'] != f'sources/official-pa-accounting-v1/{game_id}/{digest(raw_rows)}.json'
                or pointer['sha256'] != digest({'data': body, 'receipt': receipt})
                or pointer['versionId'] in (None, '', 'null')):
            raise ValueError('retained official PA receipt mismatch')
    utc(receipt['retrievedAtUtc'])
    identity = body['gameData']
    feed_start = utc(identity['datetime']['dateTime'])
    allowed = [utc(t) for t in scheduled_times] if scheduled_times is not None else [feed_start]
    original_start = allowed[0]
    if (positive_id(identity['game']['pk']) != game_id
            or day(original_start.isoformat()).isoformat() != value
            or feed_start not in allowed
            or identity['status']['abstractGameState'] != 'Final'):
        raise ValueError('official PA game/date/finality mismatch')
    result = {}
    for play in body['liveData']['plays']['allPlays']:
        event = event_name(play['result'].get('eventType'))
        if event not in PLATE_APPEARANCE_EVENTS:
            continue  # Baserunning outs are not completed plate appearances.
        about = play['about']
        index = about['atBatIndex']
        if (isinstance(index, bool) or not isinstance(index, int) or index < 0
                or play['atBatIndex'] != index or about['isComplete'] is not True
                or event == 'os_ruling_pending_primary'
                or utc(about['endTime']) < original_start):
            raise ValueError('official PA incomplete or ambiguous')
        key = str(index + 1)
        if key in result:
            raise ValueError('duplicate official PA')
        result[key] = (positive_id(play['matchup']['batter']['id']),
                       positive_id(play['matchup']['pitcher']['id']), event)
    # Cross-check every PA in the referenced game, not only the missing field.
    observed = {}
    for row in raw_rows:
        if is_plate_appearance(row):
            key = positive_id(row['at_bat_number'])
            if key in observed:
                raise ValueError('duplicate Statcast PA')
            observed[key] = (positive_id(row['batter']), positive_id(row['pitcher']),
                             event_name(row['events']))
    if not observed or observed != result:
        raise ValueError('official and Statcast PA identities/outcomes disagree')
    return result


def reconciled_rows(raw, evidence, scheduled_by_game=None):
    if set(evidence) != set(needed_games(raw)):
        raise ValueError('official PA evidence set mismatch')
    for pk, source in evidence.items():
        official_index(source, pk, raw['date'],
                       [row for row in raw['rows'] if str(row['game_pk']) == pk],
                       scheduled_times=scheduled_by_game[pk] if scheduled_by_game is not None else None)
    rows, changes = deepcopy(raw['rows']), []
    for index, row in enumerate(rows):
        pk = str(row['game_pk'])
        if pk not in evidence or not is_plate_appearance(row):
            continue
        event = event_name(row['events'])
        fields = {}
        if missing(row.get('woba_denom')):
            fields['woba_denom'] = 0 if event in WOBA_EXCLUDED_EVENTS else 1
        if event in ZERO_EVENTS:
            if missing(row.get('woba_value')):
                fields['woba_value'] = 0
            elif float(row['woba_value']) != 0:
                raise ValueError('observed wOBA value contradicts official zero outcome')
        if fields:
            row.update(fields)
            changes.append({'row_index': index, 'game_pk': pk,
                            'at_bat_number': str(row['at_bat_number']),
                            'fields': fields, 'official_source_sha256': evidence[pk]['receipt']['sha256']})
    return rows, changes


def reconcile(raw, get_official, raw_receipt, scheduled_by_game=None):
    evidence = {pk: get_official(pk, [row for row in raw['rows'] if str(row['game_pk']) == pk])
                for pk in needed_games(raw)}
    rows, changes = reconciled_rows(raw, evidence, scheduled_by_game)
    if not changes:
        return raw
    return {'date': raw['date'], 'rows': rows, 'raw_statcast': raw,
            'outcome_reconciliation': {'method': METHOD, 'official_sources': evidence,
                                       'raw_receipt': raw_receipt,
                                       'derivations': changes}}


def verify_official_time(evidence, completed_at):
    latest = max(utc(play['about']['endTime'])
                 for play in evidence['data']['liveData']['plays']['allPlays'])
    if latest > utc(completed_at) or latest > utc(evidence['receipt']['retrievedAtUtc']):
        raise ValueError('official PA exceeds independently retained completion boundary')


def verify_reconciliation(payload, completed_by_game, scheduled_by_game=None):
    proof = payload['outcome_reconciliation']
    raw = payload['raw_statcast']
    if (proof['method'] != METHOD or raw['date'] != payload['date']
            or 'outcome_reconciliation' in raw or 'raw_statcast' in raw):
        raise ValueError('invalid outcome reconciliation envelope')
    pointer = proof['raw_receipt']
    if (set(pointer) != {'name', 'versionId', 'sha256'}
            or pointer['sha256'] != digest(raw)
            or pointer['name'] != f"sources/statcast-recovery-v1/{raw['date']}/raw/{digest(raw)}.json"
            or pointer['versionId'] in (None, '', 'null')):
        raise ValueError('retained raw Statcast receipt mismatch')
    rows, changes = reconciled_rows(raw, proof['official_sources'], scheduled_by_game)
    if not changes or rows != payload['rows'] or changes != proof['derivations']:
        raise ValueError('derived PA accounting cannot be reproduced')
    for pk, evidence in proof['official_sources'].items():
        verify_official_time(evidence, completed_by_game[pk])


def outcome_diagnostics(payload):
    from collections import Counter
    invalid = [row for row in payload['rows']
               if is_plate_appearance(row) and not complete_pa_outcome(row)]
    return {'incomplete_pas': len(invalid),
            'events': dict(Counter(str(row.get('events')) for row in invalid)),
            'examples': [{key: row.get(key) for key in (
                'game_pk', 'at_bat_number', 'pitch_number', 'batter', 'pitcher',
                'events', 'type', 'woba_value', 'woba_denom',
                'estimated_woba_using_speedangle')} for row in invalid[:8]]}
