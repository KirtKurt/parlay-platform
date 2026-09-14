"""Read retained daily pitches for training with official pitch-count evidence."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import hashlib

from ks1.features import day, normalize, number
from ks1.inventory import Reader, RESEARCH, encode
from ks1.statcast_events import complete_pa_outcome, is_plate_appearance, is_thrown_pitch


def official_pitch_counts(sources):
    """Bind physical pitches and batters faced to the same official boxes."""
    expected, invalid = {}, set()
    for game in normalize(sources):
        game_id = str(game['game_id'])
        counts = expected.setdefault(game_id, {})
        if not game['context_players']:
            invalid.add(game_id)
        for player in game['context_players']:
            count = number(player['stats'].get('numberOfPitches'))
            faced = number(player['stats'].get('battersFaced'))
            if any(value is None or value < 0 or int(value) != value
                   for value in (count, faced)):
                invalid.add(game_id)
            else:
                counts[(game_id, str(player['id']))] = (int(count), int(faced))
    return expected, invalid


def pitches_complete(rows, games, expected, invalid):
    games = {str(pk) for pk in games}
    if games & invalid or not games.issubset(expected):
        return False
    wanted = {key: count[0] for pk in games for key, count in expected[pk].items() if count[0]}
    # An automatic ball/strike is a count event, not an official thrown pitch.
    # Validate identities for every event, including zero-pitch appearances.
    if any((str(row.get('game_pk')), str(row.get('pitcher')))
           not in expected.get(str(row.get('game_pk')), {})
           or str(row.get('game_pk')) not in games for row in rows):
        return False
    actual = Counter((str(row.get('game_pk')), str(row.get('pitcher')))
                     for row in rows if is_thrown_pitch(row))
    identities = {tuple(str(row.get(key)) for key in (
        'game_pk', 'at_bat_number', 'pitch_number')) for row in rows}
    # Exact physical counts alone cannot detect a lost automatic terminal
    # event. Reconcile every game's PA outcomes independently to batters faced.
    # Aggregate across pitchers because inherited counts can attribute a walk
    # to a different pitcher from the one throwing the terminal pitch.
    pas = [row for row in rows if is_plate_appearance(row)]
    pa_ids = {(str(row.get('game_pk')), str(row.get('at_bat_number'))) for row in pas}
    actual_pas = Counter(str(row.get('game_pk')) for row in pas)
    wanted_pas = {pk: sum(count[1] for count in expected[pk].values()) for pk in games}
    return (len(identities) == len(rows) and dict(actual) == wanted
            and len(pa_ids) == len(pas)
            and all(complete_pa_outcome(row) for row in pas)
            and all(actual_pas[pk] == count for pk, count in wanted_pas.items()))


def pitch_complete_dates(sources, statcast_by_date, expected_by_date):
    """Preserve the ingestion gate: every pitch must reconcile to final boxes."""
    expected, invalid = official_pitch_counts(sources)
    return sorted(value for value, rows in statcast_by_date.items()
                  if pitches_complete(rows, expected_by_date[value], expected, invalid))


def validation_reason(payload, value, games, expected, invalid):
    """Explain rejection without changing the physical/PA/outcome predicate."""
    rows = payload.get('rows', [])
    if payload.get('date') != value or any(row.get('game_date') != value for row in rows):
        return 'date_mismatch'
    if {str(row.get('game_pk')) for row in rows} != {str(pk) for pk in games}:
        return 'game_set_mismatch'
    if {str(pk) for pk in games} & invalid or not {str(pk) for pk in games}.issubset(expected):
        return 'official_evidence_incomplete'
    if any(not complete_pa_outcome(row) for row in rows if is_plate_appearance(row)):
        return 'incomplete_pa_outcome_fields'
    if not pitches_complete(rows, games, expected, invalid):
        return 'pitch_or_pa_identity_count_mismatch'
    return None


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
        reasons = []
        for key in keys:
            try:
                payload = reader.read(key)
                receipt = reader.receipts[-1]
                reason = validation_reason(payload, value, games, expected, invalid)
                if receipt.get('versionId') in (None, '', 'null'):
                    reason = 'unversioned_source'
                if reason:
                    reasons.append(reason)
                    continue
                return value, payload['rows'], [receipt], None
            except Exception as exc:
                # Missing or invalid immutable objects do not create coverage.
                reasons.append('source_read:' + type(exc).__name__)
        try:
            from ks1.statcast_recovery import read_recovery
            payload, recovery_receipts = read_recovery(s3, bucket, value)
            if payload is not None:
                reason = validation_reason(payload, value, games, expected, invalid)
                if reason is None:
                    return value, payload['rows'], recovery_receipts, None
                reasons.append('recovered_' + reason)
        except Exception as exc:
            reasons.append('recovery_read:' + type(exc).__name__)
        return value, [], [], {'reason': 'retained_pitches_unavailable_or_unverified',
                              'details': sorted(set(reasons))}

    rows, receipts, verified, errors = [], [], [], []
    verified_pitch_objects = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for value, daily_rows, daily_receipts, error in pool.map(read_date, sorted(expected_dates)):
            if error:
                errors.append({'date': value, **(error if isinstance(error, dict) else {'reason': error})})
            else:
                verified.append(value)
                rows.extend(daily_rows)
                receipts.extend(daily_receipts)
                verified_pitch_objects += bool(daily_rows)
    loaded_games = {str(row['game_pk']) for row in rows}
    # Keep compact starter-only rows for other games, but give them no new
    # whole-date coverage. Verified daily objects supply all rows for their games.
    rows.extend(row for row in bundle.get('statcast', [])
                if str(row.get('game_pk')) not in loaded_games)
    bundle['statcast'] = rows
    # The compact source can already contain recent dates whose complete pitch
    # rows were reconciled by ingestion.  A historical load may cover only a
    # subset of seasons (for example, when the current official season is not
    # globally complete), so replacing this set would discard valid coverage
    # for rows that remain in ``bundle['statcast']`` above.
    # For dates in the historical range, this load is authoritative: do not
    # restore a compact date that was rejected against the current schedule or
    # official pitch counts. Dates outside the range were not examined here and
    # retain their ingestion-time verification.
    existing_outside_range = (
        set(bundle.get('statcast_retained_dates', ())) - set(expected_dates))
    bundle['statcast_retained_dates'] = sorted(
        existing_outside_range | set(verified))
    existing_game_dates = {str(row.get('game_pk')): row.get('game_date')
                           for row in bundle['statcast']}
    outside_games = {str(pk) for pk in bundle.get('statcast_verified_games', [])
                     if existing_game_dates.get(str(pk))
                     and existing_game_dates[str(pk)] not in expected_dates}
    bundle['statcast_verified_games'] = sorted(outside_games | loaded_games)
    # Preserve the existing global source-completeness gates. Individual
    # windows additionally require every date in statcast_retained_dates.
    bundle['source_receipts'].extend(receipts)
    return {'source': RESEARCH+'sources/statcast-v2/', 'provider_requests': 0,
            'complete_official_years': years, 'expected_dates': len(expected_dates),
            'verified_dates': len(verified), 'verified_pitch_objects': verified_pitch_objects,
            'retained_pitch_rows': len(rows), 'errors': errors,
            'pitch_coverage_method': 'official_box_thrown_pitches_and_pa_v3',
            'original_prospective_storage_claimed': False}
