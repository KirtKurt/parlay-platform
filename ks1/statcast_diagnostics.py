"""Read-only replay material for bounded retained physical-pitch failures."""
import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
from pathlib import Path

from ks1.features import day
from ks1.inventory import RESEARCH, Reader, encode
from ks1.statcast_events import credited_at_bat_ids, is_thrown_pitch
from ks1.statcast_history import official_physical_pitch_counts, physical_validation_reason


def retained_official_diagnostics(payload, s3, bucket):
    """Summarize already-retained official evidence without provider calls."""
    from ks1.official_outcomes import (event_name, needed_games, source_name,
                                       unfinished_at_bats)
    from ks1.statcast_events import PLATE_APPEARANCE_EVENTS, is_plate_appearance

    results = []
    for game_id in needed_games(payload):
        rows = [row for row in payload['rows'] if str(row.get('game_pk')) == game_id]
        unfinished = {ab for (pk, ab) in unfinished_at_bats({'rows': rows}) if pk == game_id}
        for mode, kwargs in (
                ('pitch', {'pitch_evidence': True}),
                ('accounting', {'accounting_evidence': True}),
                ('inning', {'inning_evidence': True})):
            name = source_name(game_id, rows, **kwargs)
            try:
                reader = Reader(s3, bucket)
                evidence = reader.read(RESEARCH + name)
                official = {}
                unfinished_plays = []
                for play in evidence['data']['liveData']['plays']['allPlays']:
                    at_bat = str(play['about']['atBatIndex'] + 1)
                    event = event_name(play['result'].get('eventType'))
                    identity = [str(play['matchup']['batter']['id']),
                                str(play['matchup']['pitcher']['id']), event]
                    if event in PLATE_APPEARANCE_EVENTS:
                        official[at_bat] = identity
                    if at_bat in unfinished:
                        unfinished_plays.append({
                            'at_bat_number': at_bat,
                            'identity': identity,
                            'events': [{
                                'index': item.get('index'),
                                'type': item.get('type'),
                                'isPitch': item.get('isPitch'),
                                'isSubstitution': item.get('isSubstitution'),
                                'description': item.get('details', {}).get('description'),
                                'eventType': item.get('details', {}).get('eventType'),
                                'code': (item.get('details', {}).get('code') or
                                         item.get('details', {}).get('call', {}).get('code')),
                                'count': item.get('count'),
                            } for item in play.get('playEvents', [])],
                            'runners': play.get('runners', []),
                        })
                observed = {str(row['at_bat_number']): [str(row['batter']),
                            str(row['pitcher']), event_name(row['events'])]
                            for row in rows if is_plate_appearance(row)}
                results.append({
                    'game_id': game_id, 'mode': mode, 'name': name,
                    'retained_receipt': reader.receipts[-1],
                    'official_only': [{'at_bat_number': key, 'identity': official[key]}
                                      for key in sorted(set(official) - set(observed), key=int)],
                    'statcast_only': [{'at_bat_number': key, 'identity': observed[key]}
                                      for key in sorted(set(observed) - set(official), key=int)],
                    'identity_mismatches': [{'at_bat_number': key,
                        'official': official[key], 'statcast': observed[key]}
                        for key in sorted(set(official) & set(observed), key=int)
                        if official[key] != observed[key]],
                    'unfinished_plays': unfinished_plays,
                })
            except Exception as exc:
                results.append({'game_id': game_id, 'mode': mode, 'name': name,
                                'error': type(exc).__name__})
    return results


def differences(expected, actual):
    return [{'game_pk': key[0], 'player_id': key[1],
             'expected': expected.get(key, 0), 'observed': actual.get(key, 0)}
            for key in sorted(set(expected) | set(actual))
            if expected.get(key, 0) != actual.get(key, 0)]


def count_diagnostics(payload, value, games, expected, batters, invalid):
    rows = payload.get('rows', [])
    wanted = {key: count for pk in map(str, games)
              for key, count in expected.get(pk, {}).items() if count}
    wanted_batters = {key: count for pk in map(str, games)
                      for key, count in batters.get(pk, {}).items() if count}
    actual = Counter((str(row.get('game_pk')), str(row.get('pitcher')))
                     for row in rows if is_thrown_pitch(row))
    at_bats = defaultdict(set)
    identities = Counter()
    profiles = Counter()
    for row in rows:
        at_bats[str(row.get('game_pk')), str(row.get('at_bat_number'))].add(str(row.get('batter')))
        identities[tuple(str(row.get(key)) for key in ('game_pk', 'at_bat_number', 'pitch_number'))] += 1
        if row.get('pitch_type') in (None, '') or row.get('release_speed') in (None, ''):
            profiles[tuple(str(row.get(key)) for key in ('description', 'pitch_type', 'type', 'events'))
                     + (is_thrown_pitch(row),)] += 1
    credited = credited_at_bat_ids(rows)
    actual_batters = Counter((pk, next(iter(ids))) for (pk, ab), ids in at_bats.items()
                             if len(ids) == 1 and (pk, ab) in credited)
    return {
        'reason': physical_validation_reason(payload, value, games, expected, batters, invalid),
        'invalid_player_rows': [
            {'row_index': i, **{key: row.get(key) for key in
                ('game_pk', 'at_bat_number', 'pitch_number', 'pitcher', 'batter', 'events', 'description')}}
            for i, row in enumerate(rows)
            if (str(row.get('game_pk')), str(row.get('pitcher')))
               not in expected.get(str(row.get('game_pk')), {})
            or (str(row.get('game_pk')), str(row.get('batter')))
               not in batters.get(str(row.get('game_pk')), {})],
        'rows': len(rows), 'invalid_official_games': sorted(set(map(str, games)) & invalid),
        'pitcher_count_differences': differences(wanted, actual),
        'batter_pa_differences': differences(wanted_batters, actual_batters),
        'ambiguous_at_bats': [{'game_pk': key[0], 'at_bat_number': key[1], 'batters': sorted(ids)}
                             for key, ids in sorted(at_bats.items()) if len(ids) != 1],
        'duplicate_identities': [{'identity': list(key), 'count': count}
                                 for key, count in sorted(identities.items()) if count != 1],
        'untracked_row_profiles': [{'description': key[0], 'pitch_type': key[1],
                                   'type': key[2], 'events': key[3],
                                   'counted_as_thrown': key[4], 'rows': count}
                                  for key, count in sorted(profiles.items())],
    }


def diagnose_date(bundle, s3, bucket, value):
    value = date.fromisoformat(value).isoformat()
    schedule = [g for g in bundle['schedule'] if day(g['gameDate']).isoformat() == value
                and g.get('gameType') in ('R', 'F', 'D', 'L', 'W')
                and g.get('status', {}).get('abstractGameState') == 'Final'
                and g.get('status', {}).get('detailedState') not in ('Postponed', 'Cancelled')
                and not g.get('resumedFrom')]
    games = {int(g['gamePk']) for g in schedule}
    official = [g for g in bundle['full'] if int(g['officialGamePk']) in games]
    expected, batters, invalid = official_physical_pitch_counts(official)
    revision = hashlib.sha256(encode(sorted(games))).hexdigest()
    keys = [RESEARCH + f'sources/statcast-v2/{value}.json',
            RESEARCH + f'sources/statcast-v2-revisions/{value}/{revision}.json']
    sources, errors = [], []
    for key in keys:
        try:
            reader = Reader(s3, bucket)
            payload = reader.read(key)
            receipt = reader.receipts[-1]
            if receipt.get('versionId') in (None, '', 'null'):
                raise ValueError('unversioned diagnostic source')
            encoded = encode(payload)
            if len(encoded) > 20_000_000:
                raise ValueError('diagnostic source exceeds 20MB bound')
            if hashlib.sha256(encoded).hexdigest() != receipt['sha256']:
                raise ValueError('diagnostic serialization differs from retained bytes')
            sources.append({'payload': payload, 'retained_receipt': receipt,
                            'counts': count_diagnostics(payload, value, games, expected, batters, invalid),
                            'retained_official': retained_official_diagnostics(payload, s3, bucket)})
        except Exception as exc:
            errors.append({'key': key, 'error': type(exc).__name__,
                           'reason': str(exc)[:240] if isinstance(exc, ValueError) else
                           str(getattr(exc, 'response', {}).get('Error', {}).get('Code',
                               type(exc).__name__))[:80]})
    return {'method': 'retained_physical_count_diagnostic_v1', 'date': value,
            'schedule': schedule, 'official_games': official, 'sources': sources,
            'errors': errors, 'source_writes': 0, 'provider_requests': 0,
            'diagnostic_only': True, 'admission_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, action='append')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    dates = sorted({date.fromisoformat(value).isoformat() for value in args.date})
    if len(dates) > 2:
        raise ValueError('diagnostic is bounded to two dates')
    from ks1.sources import aws_clients
    cf, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
    reader = Reader(s3, bucket)
    prior = reader.pointer(reader.read(RESEARCH + 'prior-games.json')['artifact'])
    bundle = {'full': prior['games'], 'schedule': prior['schedule']}
    args.output.mkdir(parents=True, exist_ok=True)
    for value in dates:
        report = diagnose_date(bundle, s3, bucket, value)
        (args.output / f'{value}.json').write_bytes(encode(report))
        print(encode({'date': value, 'sources': len(report['sources']),
                      'counts': [source['counts'] for source in report['sources']],
                      'errors': report['errors'], 'source_writes': 0}).decode())


if __name__ == '__main__':
    main()
