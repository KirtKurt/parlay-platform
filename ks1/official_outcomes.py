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
                                complete_pa_outcome, is_plate_appearance, NON_PA_AT_BAT_END_EVENTS)

LEGACY_METHOD = 'official_pa_woba_accounting_v1'
DENOMINATOR_METHOD = 'official_pa_woba_denominator_v2'
CREDIT_METHOD = 'official_pa_credit_and_denominator_v3'
METHOD = 'official_pitch_attribution_and_pa_credit_v4'
FIELDS = ('gameData,game,pk,datetime,dateTime,status,abstractGameState,liveData,'
          'plays,allPlays,result,eventType,about,atBatIndex,isComplete,endTime,'
          'matchup,batter,pitcher,id')
POSITIVE_EVENTS = frozenset(('single', 'double', 'triple', 'home_run', 'walk', 'hit_by_pitch'))
ZERO_EVENTS = PLATE_APPEARANCE_EVENTS - POSITIVE_EVENTS - WOBA_EXCLUDED_EVENTS - {'os_ruling_pending_primary'}


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def endpoint(game_id, *, pitch_evidence=False):
    fields = FIELDS
    if pitch_evidence:
        fields += (',playEvents,index,type,isPitch,pitchNumber,details,code,call,pitchData,'
                   'startSpeed,count,balls,strikes,player,replacedPlayer,isSubstitution,'
                   'position,abbreviation,startTime,runners,movement,end,isOut,runner,'
                   'isScoringEvent,homeScore,awayScore,inning,isTopInning,isScoringPlay')
    return f'https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live?' + urlencode({'fields': fields})


def needs_pitch_evidence(rows):
    from ks1.official_pitch_attribution import candidate_groups
    return bool(candidate_groups(rows))


def source_name(game_id, rows, *, pitch_evidence=False):
    prefix = 'official-pitch-attribution-v1' if pitch_evidence else 'official-pa-accounting-v1'
    return f'sources/{prefix}/{game_id}/{digest(rows)}.json'


def event_name(value):
    value = str(value or '').lower()
    return 'strikeout' if value == 'strike_out' else value


def positive_id(value):
    if isinstance(value, bool) or int(value) <= 0 or str(int(value)) != str(value):
        raise ValueError('invalid PA identity')
    return str(value)


def missing(value):
    return value is None or value == ''


def denominator_change(row):
    event = event_name(row.get('events'))
    if missing(row.get('woba_denom')):
        return 0 if event in WOBA_EXCLUDED_EVENTS else 1
    # This is a canonical denominator convention, not a replacement raw
    # observation. Only an explicit counted PA can be normalized to exclusion.
    if (event in WOBA_EXCLUDED_EVENTS
            and not isinstance(row.get('woba_denom'), bool)
            and row.get('woba_denom') in (1, '1', '1.0')):
        return 0
    return None


def unfinished_at_bats(raw):
    """Only blank/truncated groups are candidates, never presumed non-PAs."""
    groups = {}
    for index, row in enumerate(raw['rows']):
        key = (positive_id(row['game_pk']), positive_id(row['at_bat_number']))
        groups.setdefault(key, []).append(index)
    return {key: indices for key, indices in groups.items()
            if all(event_name(raw['rows'][i].get('events')) in ('', 'truncated_pa')
                   for i in indices)}


def needed_games(raw, method=METHOD):
    credit_games = {pk for pk, ab in unfinished_at_bats(raw)} if method in (METHOD, CREDIT_METHOD) else set()
    if method == METHOD:
        from ks1.official_pitch_attribution import candidate_groups
        credit_games.update(pk for pk, ab in candidate_groups(raw['rows']))
    return sorted(credit_games | {positive_id(row['game_pk']) for row in raw['rows']
                   if is_plate_appearance(row) and not complete_pa_outcome(row)
                   and ((method in (METHOD, CREDIT_METHOD, DENOMINATOR_METHOD) and denominator_change(row) is not None)
                        or (method == LEGACY_METHOD and
                            (missing(row.get('woba_denom'))
                             or (event_name(row.get('events')) in ZERO_EVENTS
                                 and missing(row.get('woba_value'))))))})


def schedule_times(schedule):
    """Keep original/resume times from the independently retained schedule."""
    return {str(game['gamePk']): [game['gameDate']] +
            ([game['resumeDate']] if game.get('resumeDate') else [])
            for game in schedule if not game.get('resumedFrom')}


def official_index(evidence, game_id, value, raw_rows, *, require_retained=True,
                   scheduled_times=None, pitch_evidence=False):
    body, receipt = evidence['data'], evidence['receipt']
    if (receipt['endpoint'] != endpoint(game_id, pitch_evidence=pitch_evidence)
            or receipt['sha256'] != digest(body)):
        raise ValueError('official PA source receipt mismatch')
    if require_retained:
        pointer = evidence['retained_receipt']
        if (set(pointer) != {'name', 'versionId', 'sha256'}
                or pointer['name'] != source_name(game_id, raw_rows, pitch_evidence=pitch_evidence)
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


def reconciled_rows(raw, evidence, scheduled_by_game=None, method=METHOD):
    if method not in (METHOD, CREDIT_METHOD, DENOMINATOR_METHOD, LEGACY_METHOD):
        raise ValueError('unsupported outcome reconciliation method')
    if set(evidence) != set(needed_games(raw, method)):
        raise ValueError('official PA evidence set mismatch')
    for pk, source in evidence.items():
        official_index(source, pk, raw['date'],
                       [row for row in raw['rows'] if str(row['game_pk']) == pk],
                       scheduled_times=scheduled_by_game[pk] if scheduled_by_game is not None else None,
                       pitch_evidence=method == METHOD and source['receipt']['endpoint'] ==
                       endpoint(pk, pitch_evidence=True))
    rows, changes = deepcopy(raw['rows']), []
    for index, row in enumerate(rows):
        pk = str(row['game_pk'])
        if pk not in evidence or not is_plate_appearance(row):
            continue
        event = event_name(row['events'])
        fields = {}
        if method == LEGACY_METHOD:
            # Reproduce previously retained v1 objects exactly. New payloads
            # never derive or second-guess a provider's wOBA event weight.
            if missing(row.get('woba_denom')):
                fields['woba_denom'] = 0 if event in WOBA_EXCLUDED_EVENTS else 1
            if event in ZERO_EVENTS:
                if missing(row.get('woba_value')):
                    fields['woba_value'] = 0
                elif float(row['woba_value']) != 0:
                    raise ValueError('observed wOBA value contradicts official zero outcome')
        else:
            denom = denominator_change(row)
            if denom is not None:
                fields['woba_denom'] = denom
        if fields:
            prior = {key: row.get(key) for key in fields}
            row.update(fields)
            change = {'row_index': index, 'game_pk': pk,
                      'at_bat_number': str(row['at_bat_number']),
                      'fields': fields, 'official_source_sha256': evidence[pk]['receipt']['sha256']}
            if method in (METHOD, CREDIT_METHOD, DENOMINATOR_METHOD):
                change.update(original_fields=prior, derivation_kind='official_pa_denominator')
            changes.append(change)
    if method in (METHOD, CREDIT_METHOD):
        for (pk, ab), indices in unfinished_at_bats(raw).items():
            source = evidence[pk]
            plays = [p for p in source['data']['liveData']['plays']['allPlays']
                     if str(p['about']['atBatIndex'] + 1) == ab]
            if len(plays) != 1:
                raise ValueError('unfinished at-bat official identity is missing or ambiguous')
            play = plays[0]
            about = play['about']
            event = event_name(play['result'].get('eventType'))
            start = (scheduled_by_game[pk][0] if scheduled_by_game is not None
                     else source['data']['gameData']['datetime']['dateTime'])
            from ks1.official_pitch_attribution import verified_walkoff_ending
            walkoff = (method == METHOD and event not in NON_PA_AT_BAT_END_EVENTS
                       and source['receipt']['endpoint'] == endpoint(pk, pitch_evidence=True)
                       and verified_walkoff_ending(play, source))
            if (event not in NON_PA_AT_BAT_END_EVENTS and not walkoff
                    or isinstance(about['atBatIndex'], bool)
                    or not isinstance(about['atBatIndex'], int)
                    or about['atBatIndex'] < 0
                    or play['atBatIndex'] != about['atBatIndex']
                    or about['isComplete'] is not True or utc(about['endTime']) < utc(start)):
                raise ValueError('unfinished at-bat lacks a completed official non-PA ending')
            ordered = sorted(indices, key=lambda i: int(positive_id(rows[i]['pitch_number'])))
            if len({positive_id(rows[i]['pitch_number']) for i in indices}) != len(indices):
                raise ValueError('unfinished at-bat has duplicate pitch identities')
            index = ordered[-1]
            row = rows[index]
            if (any(positive_id(rows[i]['batter']) != positive_id(play['matchup']['batter']['id'])
                    for i in indices)
                    or positive_id(row['pitcher']) != positive_id(play['matchup']['pitcher']['id'])):
                raise ValueError('unfinished at-bat official player attribution differs')
            # Every row in this proven non-PA group has zero wOBA exposure.
            # Feature consumers use the denominator, so deriving only the
            # terminal event would leave a contradictory raw 1 in the sample.
            for i in ordered:
                current = rows[i]
                denom = current.get('woba_denom')
                if isinstance(denom, bool) or denom not in (None, '', 0, '0', '0.0', 1, '1', '1.0'):
                    raise ValueError('unfinished at-bat has an invalid wOBA denominator')
                fields = {'events': event} if i == index and not walkoff else {}
                if denom in (None, '', 1, '1', '1.0'):
                    fields['woba_denom'] = 0
                if not fields and not (walkoff and i == index):
                    continue
                changes.append({'row_index': i, 'game_pk': pk, 'at_bat_number': ab,
                                'fields': fields,
                                'original_fields': {key: current.get(key) for key in fields},
                                'derivation_kind': 'official_non_pa_walkoff' if walkoff else 'official_non_pa_ending',
                                **({'credited_batter': None} if walkoff and i == index else {}),
                                'official_source_sha256': source['receipt']['sha256']})
                current.update(fields)
    if method == METHOD:
        from ks1.official_pitch_attribution import derive
        changes.extend(derive(rows, evidence, scheduled_by_game))
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
    if (proof['method'] not in (METHOD, CREDIT_METHOD, DENOMINATOR_METHOD, LEGACY_METHOD) or raw['date'] != payload['date']
            or 'outcome_reconciliation' in raw or 'raw_statcast' in raw):
        raise ValueError('invalid outcome reconciliation envelope')
    pointer = proof['raw_receipt']
    if (set(pointer) != {'name', 'versionId', 'sha256'}
            or pointer['sha256'] != digest(raw)
            or pointer['name'] != f"sources/statcast-recovery-v1/{raw['date']}/raw/{digest(raw)}.json"
            or pointer['versionId'] in (None, '', 'null')):
        raise ValueError('retained raw Statcast receipt mismatch')
    rows, changes = reconciled_rows(raw, proof['official_sources'], scheduled_by_game, proof['method'])
    if not changes or rows != payload['rows'] or changes != proof['derivations']:
        raise ValueError('derived PA accounting cannot be reproduced')
    for pk, evidence in proof['official_sources'].items():
        verify_official_time(evidence, completed_by_game[pk])


def read_retained_evidence(payload, reader):
    """Require each claimed exact source version, not just embedded copies."""
    if 'outcome_reconciliation' not in payload:
        return
    proof = payload['outcome_reconciliation']
    sources = [(proof['raw_receipt'], payload['raw_statcast'])]
    sources.extend((source['retained_receipt'],
                    {key: source[key] for key in ('data', 'receipt')})
                   for source in proof['official_sources'].values())
    for pointer, expected in sources:
        if (set(pointer) != {'name', 'versionId', 'sha256'}
                or pointer['versionId'] in (None, '', 'null')
                or not pointer['name'].startswith(('sources/statcast-recovery-v1/',
                                                   'sources/official-pa-accounting-v1/',
                                                   'sources/official-pitch-attribution-v1/'))):
            raise ValueError('invalid retained outcome source pointer')
        if (reader.pointer(pointer) != expected
                or reader.receipts[-1]['versionId'] != pointer['versionId']):
            raise ValueError('retained outcome source version mismatch')


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


def verified_batter_credits(payload, scheduled_by_game=None):
    proof = payload.get('outcome_reconciliation', {})
    if proof.get('method') != METHOD or not any(
            item.get('derivation_kind') in ('official_mid_at_bat_credit', 'official_non_pa_walkoff')
            for item in proof.get('derivations', [])):
        return {}
    raw = payload['raw_statcast']
    rows, changes = reconciled_rows(raw, proof['official_sources'], scheduled_by_game, method=METHOD)
    if rows != payload['rows'] or changes != proof['derivations']:
        raise ValueError('official batter credits cannot be reproduced')
    return {(item['game_pk'], item['at_bat_number']): item['credited_batter']
            for item in changes if item['derivation_kind'] in
            ('official_mid_at_bat_credit', 'official_non_pa_walkoff') and 'credited_batter' in item}
