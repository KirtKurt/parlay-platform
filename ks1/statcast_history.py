"""Read retained daily pitches for training with official pitch-count evidence."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import hashlib

from ks1.features import day, normalize, number
from ks1.inventory import Reader, RESEARCH, encode


def official_pitch_counts(sources):
    expected, invalid = {}, set()
    for game in normalize(sources):
        game_id = str(game['game_id'])
        counts = expected.setdefault(game_id, {})
        if not game['context_players']:
            invalid.add(game_id)
        for player in game['context_players']:
            count = number(player['stats'].get('numberOfPitches'))
            if count is None or count < 0 or int(count) != count:
                invalid.add(game_id)
            else:
                counts[(game_id, str(player['id']))] = int(count)
    return expected, invalid


def pitches_complete(rows, games, expected, invalid):
    games = {str(pk) for pk in games}
    if games & invalid or not games.issubset(expected):
        return False
    wanted = {key: count for pk in games for key, count in expected[pk].items() if count}
    actual = Counter((str(row.get('game_pk')), str(row.get('pitcher'))) for row in rows)
    identities = {tuple(str(row.get(key)) for key in (
        'game_pk', 'at_bat_number', 'pitch_number')) for row in rows}
    return len(identities) == len(rows) and dict(actual) == wanted


def pitch_complete_dates(sources, statcast_by_date, expected_by_date):
    """Preserve the ingestion gate: every pitch must reconcile to final boxes."""
    expected, invalid = official_pitch_counts(sources)
    return sorted(value for value, rows in statcast_by_date.items()
                  if pitches_complete(rows, expected_by_date[value], expected, invalid))


def load_training_statcast(bundle, s3, bucket):
    """Restore prior pitch windows from retained objects; never call a provider.

    Read at most the two complete official-history seasons already loaded by
    KS1. A missing/truncated daily object stays unqualified. Empty dates require
    the same complete schedule evidence; an unfinished game is not an off day.
    """
    years = sorted(bundle.get('official_history_source', {}).get('complete_years', []))[-2:]
    if not years or not bundle.get('full'):
        raise ValueError('historical Statcast requires complete official history')
    schedule = bundle.get('schedule', [])
    if not schedule:
        raise ValueError('historical Statcast requires retained official schedule')
    last_day = max(day(game['startAtUtc']) for game in bundle['full'])
    expected_dates = {}
    for year in years:
        current = date(year, 1, 1)
        while current.year == year and current <= last_day:
            expected_dates[current.isoformat()] = set()
            current += timedelta(days=1)
    unfinished = set()
    for game in schedule:
        value = day(game['gameDate']).isoformat()
        if value not in expected_dates:
            continue
        if (game.get('gameType') not in ('R', 'F', 'D', 'L', 'W')
                or game.get('status', {}).get('detailedState') in ('Postponed', 'Cancelled')
                or game.get('resumedFrom')):
            continue
        if game.get('status', {}).get('abstractGameState') == 'Final':
            expected_dates[value].add(int(game['gamePk']))
        else:
            unfinished.add(value)
    expected, invalid = official_pitch_counts(bundle['full'])

    def read_date(value):
        games = expected_dates[value]
        if value in unfinished:
            return value, [], [], 'unfinished_scheduled_game'
        if not games:
            return value, [], [], None
        reader = Reader(s3, bucket)
        revision = hashlib.sha256(encode(sorted(games))).hexdigest()
        keys = [RESEARCH+f'sources/statcast-v2/{value}.json',
                RESEARCH+f'sources/statcast-v2-revisions/{value}/{revision}.json']
        for key in keys:
            try:
                payload = reader.read(key)
                receipt = reader.receipts[-1]
                rows = payload['rows']
                if (receipt.get('versionId') in (None, '', 'null')
                        or payload.get('date') != value
                        or any(row.get('game_date') != value for row in rows)
                        or {str(row.get('game_pk')) for row in rows} != {str(pk) for pk in games}
                        or not pitches_complete(rows, games, expected, invalid)):
                    continue
                return value, rows, [receipt], None
            except Exception:
                # Missing or invalid immutable objects do not create coverage.
                continue
        return value, [], [], 'retained_pitches_unavailable_or_unverified'

    rows, receipts, verified, errors = [], [], [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for value, daily_rows, daily_receipts, error in pool.map(read_date, sorted(expected_dates)):
            if error:
                errors.append({'date': value, 'reason': error})
            else:
                verified.append(value)
                rows.extend(daily_rows)
                receipts.extend(daily_receipts)
    loaded_games = {str(row['game_pk']) for row in rows}
    # Keep compact starter-only rows for other games, but give them no new
    # whole-date coverage. Verified daily objects supply all rows for their games.
    rows.extend(row for row in bundle.get('statcast', [])
                if str(row.get('game_pk')) not in loaded_games)
    bundle['statcast'] = rows
    bundle['statcast_retained_dates'] = verified
    # Preserve the existing global source-completeness gates. Individual
    # windows additionally require every date in statcast_retained_dates.
    bundle['source_receipts'].extend(receipts)
    return {'source': RESEARCH+'sources/statcast-v2/', 'provider_requests': 0,
            'complete_official_years': years, 'expected_dates': len(expected_dates),
            'verified_dates': len(verified), 'verified_pitch_objects': len(receipts),
            'retained_pitch_rows': len(rows), 'errors': errors,
            'pitch_coverage_method': 'official_box_pitcher_counts_v1',
            'original_prospective_storage_claimed': False}
