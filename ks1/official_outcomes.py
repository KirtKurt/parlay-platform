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
PITCH_METHOD = 'official_pitch_attribution_and_pa_credit_v4'
ACCOUNTING_METHOD = 'official_pitch_attribution_and_pa_accounting_v5'
SUBSTITUTION_METHOD = 'official_pitch_attribution_and_substitution_v6'
INNING_METHOD = 'official_pitch_attribution_and_inning_ending_v7'
TWO_STRIKE_METHOD = 'official_pitch_attribution_and_two_strike_credit_v8'
MOUND_VISIT_METHOD = 'official_pitch_attribution_and_mound_visit_v9'
GAME_ADVISORY_METHOD = 'official_pitch_attribution_and_game_advisory_v10'
METHOD = 'official_pitch_attribution_and_game_advisory_count_v11'
FIELDS = ('gameData,game,pk,datetime,dateTime,status,abstractGameState,liveData,'
          'plays,allPlays,result,eventType,about,atBatIndex,isComplete,endTime,'
          'matchup,batter,pitcher,id')
POSITIVE_EVENTS = frozenset(('single', 'double', 'triple', 'home_run', 'walk', 'hit_by_pitch'))
ZERO_EVENTS = PLATE_APPEARANCE_EVENTS - POSITIVE_EVENTS - WOBA_EXCLUDED_EVENTS - {'os_ruling_pending_primary'}


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def endpoint(game_id, *, pitch_evidence=False, accounting_evidence=False,
             inning_evidence=False, game_advisories=True, advisory_outs=True):
    fields = FIELDS
    if pitch_evidence or accounting_evidence or inning_evidence:
        fields += (',playEvents,index,type,isPitch,pitchNumber,details,code,call,pitchData,'
                   'startSpeed,count,balls,strikes,player,replacedPlayer,isSubstitution,'
                   'position,abbreviation,startTime,runners,movement,end,isOut,runner,'
                   'isScoringEvent,homeScore,awayScore,inning,isTopInning,isScoringPlay')
        if game_advisories:
            fields += ',description'
            if advisory_outs and not inning_evidence:
                fields += ',outs'
    if accounting_evidence or inning_evidence:
        fields += ',movementReason,outBase,hitData,trajectory,isInPlay'
    if inning_evidence:
        fields += ',outs,outNumber,playIndex'
    return f'https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live?' + urlencode({'fields': fields})


def needs_pitch_evidence(rows):
    from ks1.official_pitch_attribution import candidate_groups
    return bool(candidate_groups(rows))


def source_name(game_id, rows, *, pitch_evidence=False, accounting_evidence=False,
                inning_evidence=False, game_advisories=True, advisory_outs=True):
    prefix = 'official-pitch-attribution-v1' if pitch_evidence else 'official-pa-accounting-v1'
    if accounting_evidence:
        prefix = 'official-pa-taxonomy-v1'
    if inning_evidence:
        prefix = 'official-inning-ending-v1'
    if game_advisories and (pitch_evidence or accounting_evidence or inning_evidence):
        # The v11 global advisory-count contract is stronger even where the
        # endpoint projection already contained ``outs``. Keep every v11
        # object separate so a v10 cache entry can never block a fresh fetch.
        version = 'count-' if advisory_outs else ''
        prefix = (f'official-game-advisory-{version}inning-v1' if inning_evidence
                  else f'official-game-advisory-{version}taxonomy-v1' if accounting_evidence
                  else f'official-game-advisory-{version}pitch-v1')
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
    credit_games = {pk for pk, ab in unfinished_at_bats(raw)} if method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD, ACCOUNTING_METHOD, PITCH_METHOD, CREDIT_METHOD) else set()
    if method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD, ACCOUNTING_METHOD, PITCH_METHOD):
        from ks1.official_pitch_attribution import candidate_groups
        credit_games.update(pk for pk, ab in candidate_groups(raw['rows']))
    return sorted(credit_games | {positive_id(row['game_pk']) for row in raw['rows']
                   if is_plate_appearance(row) and not complete_pa_outcome(row)
                   and ((method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD, ACCOUNTING_METHOD, PITCH_METHOD, CREDIT_METHOD, DENOMINATOR_METHOD) and denominator_change(row) is not None)
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
                   scheduled_times=None, pitch_evidence=False, accounting_evidence=False,
                   inning_evidence=False, game_advisories=True, advisory_outs=True,
                   two_strike_substitution=False):
    body, receipt = evidence['data'], evidence['receipt']
    if (receipt['endpoint'] != endpoint(game_id, pitch_evidence=pitch_evidence, accounting_evidence=accounting_evidence, inning_evidence=inning_evidence, game_advisories=game_advisories, advisory_outs=advisory_outs)
            or receipt['sha256'] != digest(body)):
        raise ValueError('official PA source receipt mismatch')
    if require_retained:
        pointer = evidence['retained_receipt']
        if (set(pointer) != {'name', 'versionId', 'sha256'}
                or pointer['name'] != source_name(game_id, raw_rows, pitch_evidence=pitch_evidence, accounting_evidence=accounting_evidence, inning_evidence=inning_evidence, game_advisories=game_advisories, advisory_outs=advisory_outs)
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
    if game_advisories and advisory_outs:
        advisories = [event for play in body['liveData']['plays']['allPlays']
                      for event in play.get('playEvents', [])
                      if event.get('details', {}).get('eventType') == 'game_advisory']
        if any(any(type(event.get('count', {}).get(key)) is not int
                   for key in ('balls', 'strikes', 'outs')) for event in advisories):
            raise ValueError('game advisory count evidence incomplete')
    result, plays_by_ab = {}, {}
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
        plays_by_ab[key] = play
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
    if not observed or set(observed) != set(result):
        raise ValueError('official and Statcast PA identities/outcomes disagree')
    rows_by_ab = {positive_id(row['at_bat_number']): row for row in raw_rows if is_plate_appearance(row)}
    from ks1.pa_accounting import same_force_out_accounting
    from ks1.two_strike_credit import same_substitution_strikeout_credit
    for ab, expected in result.items():
        if observed[ab] == expected:
            continue
        if (two_strike_substitution
                and observed[ab][1:] == expected[1:]
                and same_substitution_strikeout_credit(rows_by_ab[ab], plays_by_ab[ab])):
            continue
        if (not (accounting_evidence or inning_evidence) or observed[ab][:2] != expected[:2]
                or not same_force_out_accounting(rows_by_ab[ab], plays_by_ab[ab])):
            raise ValueError('official and Statcast PA identities/outcomes disagree')
    return result


def reconciled_rows(raw, evidence, scheduled_by_game=None, method=METHOD):
    if method not in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD, ACCOUNTING_METHOD, PITCH_METHOD, CREDIT_METHOD, DENOMINATOR_METHOD, LEGACY_METHOD):
        raise ValueError('unsupported outcome reconciliation method')
    required_evidence = set(needed_games(raw, method))
    if not required_evidence.issubset(evidence):
        raise ValueError('official PA evidence set mismatch')
    for pk, source in evidence.items():
        source_endpoint = source['receipt']['endpoint']
        current_endpoints = {
            endpoint(pk, pitch_evidence=True),
            endpoint(pk, accounting_evidence=True),
            endpoint(pk, inning_evidence=True)}
        v10_endpoints = {
            endpoint(pk, pitch_evidence=True, advisory_outs=False),
            endpoint(pk, accounting_evidence=True, advisory_outs=False),
            endpoint(pk, inning_evidence=True, advisory_outs=False)}
        legacy_endpoints = {
            endpoint(pk, pitch_evidence=True, game_advisories=False),
            endpoint(pk, accounting_evidence=True, game_advisories=False),
            endpoint(pk, inning_evidence=True, game_advisories=False)}
        if (source_endpoint in current_endpoints | v10_endpoints | legacy_endpoints
                and ((method == METHOD and source_endpoint not in current_endpoints)
                     or (method == GAME_ADVISORY_METHOD and source_endpoint not in v10_endpoints)
                     or (method not in (METHOD, GAME_ADVISORY_METHOD)
                         and source_endpoint not in legacy_endpoints))):
            raise ValueError('official PA source endpoint does not match reconciliation method')
        pitch_evidence = source_endpoint in {
            endpoint(pk, pitch_evidence=True),
            endpoint(pk, pitch_evidence=True, advisory_outs=False),
            endpoint(pk, pitch_evidence=True, game_advisories=False)}
        accounting_evidence = source_endpoint in {
            endpoint(pk, accounting_evidence=True),
            endpoint(pk, accounting_evidence=True, advisory_outs=False),
            endpoint(pk, accounting_evidence=True, game_advisories=False)}
        inning_evidence = source_endpoint in {
            endpoint(pk, inning_evidence=True),
            endpoint(pk, inning_evidence=True, advisory_outs=False),
            endpoint(pk, inning_evidence=True, game_advisories=False)}
        game_advisories = source_endpoint in {
            endpoint(pk, pitch_evidence=True), endpoint(pk, accounting_evidence=True),
            endpoint(pk, inning_evidence=True),
            endpoint(pk, pitch_evidence=True, advisory_outs=False),
            endpoint(pk, accounting_evidence=True, advisory_outs=False),
            endpoint(pk, inning_evidence=True, advisory_outs=False)}
        # The v10/v11 inning endpoints intentionally collide because inning
        # evidence already requested ``outs``. Only the reconciliation method
        # can say whether every advisory must satisfy the v11 count contract.
        advisory_outs = method == METHOD and source_endpoint in current_endpoints
        official_index(source, pk, raw['date'],
                       [row for row in raw['rows'] if str(row['game_pk']) == pk],
                       scheduled_times=scheduled_by_game[pk] if scheduled_by_game is not None else None,
                       pitch_evidence=pitch_evidence,
                       accounting_evidence=accounting_evidence,
                       inning_evidence=inning_evidence,
                       game_advisories=game_advisories,
                       advisory_outs=advisory_outs,
                       two_strike_substitution=method in (
                           METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD,
                           TWO_STRIKE_METHOD))
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
            if method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD, ACCOUNTING_METHOD, PITCH_METHOD, CREDIT_METHOD, DENOMINATOR_METHOD):
                change.update(original_fields=prior, derivation_kind='official_pa_denominator')
            changes.append(change)
    if method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD, ACCOUNTING_METHOD, PITCH_METHOD, CREDIT_METHOD):
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
            from ks1.inning_ending import verified_inning_ending
            rich_endpoints = {endpoint(pk, pitch_evidence=True),
                endpoint(pk, pitch_evidence=True, advisory_outs=False),
                endpoint(pk, accounting_evidence=True),
                endpoint(pk, accounting_evidence=True, advisory_outs=False),
                endpoint(pk, inning_evidence=True),
                endpoint(pk, inning_evidence=True, advisory_outs=False),
                endpoint(pk, pitch_evidence=True, game_advisories=False),
                endpoint(pk, accounting_evidence=True, game_advisories=False),
                endpoint(pk, inning_evidence=True, game_advisories=False)}
            inning_endpoints = {endpoint(pk, inning_evidence=True),
                endpoint(pk, inning_evidence=True, advisory_outs=False),
                endpoint(pk, inning_evidence=True, game_advisories=False)}
            inning_ending = (method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD) and source['receipt']['endpoint'] in inning_endpoints
                             and verified_inning_ending(play, source, [raw['rows'][i] for i in indices]))
            walkoff = (method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD, ACCOUNTING_METHOD, PITCH_METHOD) and event not in NON_PA_AT_BAT_END_EVENTS
                       and source['receipt']['endpoint'] in rich_endpoints
                       and verified_walkoff_ending(play, source))
            if (event not in NON_PA_AT_BAT_END_EVENTS and not (walkoff or inning_ending)
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
                fields = {'events': event} if i == index and not (walkoff or inning_ending) else {}
                if denom in (None, '', 1, '1', '1.0'):
                    fields['woba_denom'] = 0
                if not fields and not ((walkoff or inning_ending) and i == index):
                    continue
                changes.append({'row_index': i, 'game_pk': pk, 'at_bat_number': ab,
                                'fields': fields,
                                'original_fields': {key: current.get(key) for key in fields},
                                'derivation_kind': ('official_non_pa_inning_ending' if inning_ending else
                                                    'official_non_pa_walkoff' if walkoff else 'official_non_pa_ending'),
                                **({'credited_batter': None} if (walkoff or inning_ending) and i == index else {}),
                                'official_source_sha256': source['receipt']['sha256']})
                current.update(fields)
    if method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD, ACCOUNTING_METHOD, PITCH_METHOD):
        from ks1.official_pitch_attribution import derive
        changes.extend(derive(rows, evidence, scheduled_by_game, extended_substitutions=method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD),
                              two_strike_strikeouts=method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD),
                              prefix_mound_visits=method in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD),
                              prefix_game_advisories=method in (METHOD, GAME_ADVISORY_METHOD), raw_rows=raw['rows']))
    changed_games = {item['game_pk'] for item in changes}
    if set(evidence) - required_evidence - changed_games:
        raise ValueError('unneeded official PA evidence')
    return rows, changes


def reconcile(raw, get_official, raw_receipt, scheduled_by_game=None,
              additional_games=None, inspection_games=None):
    additional_games = {positive_id(pk) for pk in (additional_games or [])}
    inspection_games = {positive_id(pk) for pk in (inspection_games or [])}
    raw_games = {positive_id(row['game_pk']) for row in raw['rows']}
    if not (additional_games | inspection_games).issubset(raw_games):
        raise ValueError('additional official evidence game missing from Statcast')
    games = sorted(set(needed_games(raw)) | additional_games)
    def official_for(pk):
        rows = [row for row in raw['rows'] if str(row['game_pk']) == pk]
        if pk in additional_games or pk in inspection_games:
            return get_official(pk, rows, force_pitch_evidence=True)
        return get_official(pk, rows)
    evidence = {pk: official_for(pk) for pk in games}
    from ks1.two_strike_credit import needs_substitution_pitch_attribution
    for pk in sorted(inspection_games - set(games)):
        rows = [row for row in raw['rows'] if str(row['game_pk']) == pk]
        source = get_official(pk, rows, force_pitch_evidence=True)
        if needs_substitution_pitch_attribution(rows, source):
            evidence[pk] = source
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
    if (proof['method'] not in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD, ACCOUNTING_METHOD, PITCH_METHOD, CREDIT_METHOD, DENOMINATOR_METHOD, LEGACY_METHOD) or raw['date'] != payload['date']
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
                                                       'sources/official-pitch-attribution-v1/',
                                                       'sources/official-pa-taxonomy-v1/',
                                                       'sources/official-inning-ending-v1/',
                                                       'sources/official-game-advisory-pitch-v1/',
                                                       'sources/official-game-advisory-taxonomy-v1/',
                                                       'sources/official-game-advisory-inning-v1/',
                                                       'sources/official-game-advisory-count-pitch-v1/',
                                                       'sources/official-game-advisory-count-taxonomy-v1/',
                                                       'sources/official-game-advisory-count-inning-v1/'))):
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
    if proof.get('method') not in (METHOD, GAME_ADVISORY_METHOD, MOUND_VISIT_METHOD, TWO_STRIKE_METHOD, INNING_METHOD, SUBSTITUTION_METHOD, ACCOUNTING_METHOD, PITCH_METHOD) or not any(
            item.get('derivation_kind') in ('official_mid_at_bat_credit', 'official_non_pa_walkoff', 'official_non_pa_inning_ending')
            for item in proof.get('derivations', [])):
        return {}
    raw = payload['raw_statcast']
    rows, changes = reconciled_rows(raw, proof['official_sources'], scheduled_by_game, method=proof['method'])
    if rows != payload['rows'] or changes != proof['derivations']:
        raise ValueError('official batter credits cannot be reproduced')
    return {(item['game_pk'], item['at_bat_number']): item['credited_batter']
            for item in changes if item['derivation_kind'] in
            ('official_mid_at_bat_credit', 'official_non_pa_walkoff', 'official_non_pa_inning_ending') and 'credited_batter' in item}
