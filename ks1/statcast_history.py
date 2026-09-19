"""Read retained daily pitches for training with official pitch-count evidence."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import hashlib

from ks1.features import day, normalize, number
from ks1.inventory import Reader, RESEARCH, encode
from ks1.statcast_events import (complete_pa_outcome, credited_at_bat_ids,
                                 is_plate_appearance, is_thrown_pitch)


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


def official_physical_pitch_counts(sources):
    """Bind thrown pitches and batter attribution without outcome fields."""
    expected, batters, invalid = {}, {}, set()
    for game in normalize(sources):
        game_id = str(game['game_id'])
        counts = expected.setdefault(game_id, {})
        batter_counts = batters.setdefault(game_id, {})
        if not game['context_players']:
            invalid.add(game_id)
        for player in game['context_players']:
            count = number(player['stats'].get('numberOfPitches'))
            if count is None or count < 0 or int(count) != count:
                invalid.add(game_id)
            else:
                counts[(game_id, str(player['id']))] = int(count)
        for player in game['batters']:
            appearances = number(player['stats'].get('plateAppearances'))
            if (appearances is None or appearances < 0
                    or int(appearances) != appearances):
                invalid.add(game_id)
            else:
                # Keep zero-PA batting participants as valid identities. They
                # may have genuine pitches in an at-bat explicitly ended by a
                # baserunning out, but must never contribute a credited PA.
                batter_counts[(game_id, str(player['id']))] = int(appearances)
    for game_id in expected:
        if not batters.get(game_id):
            invalid.add(game_id)
    return expected, batters, invalid


def physical_pitch_inventory_complete(rows, games, expected, batters, invalid, *, batter_credits=None):
    """Exact thrown-pitch inventory and row identities, before PA credit.

    This is only a recovery precondition. It cannot admit a date without the
    separate individual batter PA counts in physical_pitches_complete.
    """
    games = {str(pk) for pk in games}
    if games & invalid or not games.issubset(expected):
        return False
    wanted = {key: count for pk in games for key, count in expected[pk].items() if count}
    if any((str(row.get('game_pk')), str(row.get('pitcher')))
           not in expected.get(str(row.get('game_pk')), {})
           or (str(row.get('game_pk')), str(row.get('batter')))
           not in batters.get(str(row.get('game_pk')), {})
           or str(row.get('game_pk')) not in games for row in rows):
        return False
    actual = Counter((str(row.get('game_pk')), str(row.get('pitcher')))
                     for row in rows if is_thrown_pitch(row))
    identities = {tuple(str(row.get(key)) for key in (
        'game_pk', 'at_bat_number', 'pitch_number')) for row in rows}
    at_bat_batters = {}
    for row in rows:
        at_bat_batters.setdefault(
            (str(row.get('game_pk')), str(row.get('at_bat_number'))),
            set()).add(str(row.get('batter')))
    return (len(identities) == len(rows) and dict(actual) == wanted
            and all(len(values) == 1 or (batter_credits or {}).get(key) in values
                    for key, values in at_bat_batters.items()))


def physical_pitches_complete(rows, games, expected, batters, invalid, *, batter_credits=None):
    """Require the exact pitch inventory and each official batter PA total."""
    games = {str(pk) for pk in games}
    if not physical_pitch_inventory_complete(rows, games, expected, batters, invalid,
                                             batter_credits=batter_credits):
        return False
    at_bat_batters = {}
    for row in rows:
        at_bat_batters.setdefault(
            (str(row.get('game_pk')), str(row.get('at_bat_number'))),
            set()).add(str(row.get('batter')))
    credited = credited_at_bat_ids(rows)
    actual_batters = Counter(
        (game_id, (batter_credits or {}).get((game_id, at_bat), next(iter(values))))
        for (game_id, at_bat), values in at_bat_batters.items()
        if (game_id, at_bat) in credited
        and ((game_id, at_bat) not in (batter_credits or {})
             or batter_credits[game_id, at_bat] is not None))
    wanted_batters = {key: count for pk in games
                      for key, count in batters[pk].items() if count}
    return dict(actual_batters) == wanted_batters


def physical_batter_mismatch_games(rows, games, expected, batters, invalid):
    """Games with exact pitch inventory but unresolved official PA credit."""
    result = set()
    for game_id in map(str, games):
        game_rows = [row for row in rows if str(row.get('game_pk')) == game_id]
        if (physical_pitch_inventory_complete(
                game_rows, {game_id}, expected, batters, invalid)
                and not physical_pitches_complete(
                    game_rows, {game_id}, expected, batters, invalid)):
            result.add(game_id)
    return result


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


def physical_pitch_complete_dates(sources, statcast_by_date, expected_by_date):
    """Return dates whose physical pitches reconcile to official boxes."""
    expected, batters, invalid = official_physical_pitch_counts(sources)
    return sorted(value for value, rows in statcast_by_date.items()
                  if physical_pitches_complete(
                      rows, expected_by_date[value], expected, batters, invalid))


def physical_validation_reason(payload, value, games, expected, batters, invalid, *, scheduled_by_game=None):
    rows = payload.get('rows', [])
    if payload.get('date') != value or any(row.get('game_date') != value for row in rows):
        return 'date_mismatch'
    if {str(row.get('game_pk')) for row in rows} != {str(pk) for pk in games}:
        return 'game_set_mismatch'
    from ks1.official_outcomes import verified_batter_credits
    try:
        credits = verified_batter_credits(payload, scheduled_by_game)
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'official_batter_credit_unverified'
    if not physical_pitches_complete(rows, games, expected, batters, invalid, batter_credits=credits):
        return 'physical_pitch_or_batter_attribution_mismatch'
    return None


def validation_reason(payload, value, games, expected, invalid,
                      completed_by_game=None, scheduled_by_game=None):
    """Explain rejection without changing the physical/PA/outcome predicate."""
    if 'outcome_reconciliation' in payload or 'raw_statcast' in payload:
        from ks1.official_outcomes import verify_reconciliation
        try:
            verify_reconciliation(payload, completed_by_game or {}, scheduled_by_game)
        except (KeyError, TypeError, ValueError, OverflowError):
            return 'official_outcome_reconciliation_unverified'
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


def verified_raw_games(payload, value, games, expected, invalid,
                       physical_expected, physical_batters, physical_invalid):
    """Retain independently complete games, never certify a partial date.

    Only an unmodified daily payload with the exact scheduled game set can
    enter this fallback. Reconciled envelopes require their whole-proof path;
    projecting them would discard the binding to raw/official evidence.
    """
    if 'outcome_reconciliation' in payload or 'raw_statcast' in payload:
        return {}
    rows = payload.get('rows', [])
    games = {str(pk) for pk in games}
    if (payload.get('date') != value
            or any(row.get('game_date') != value for row in rows)
            or {str(row.get('game_pk')) for row in rows} != games):
        return {}
    grouped = {pk: [] for pk in games}
    for row in rows:
        grouped[str(row['game_pk'])].append(row)
    verified = {}
    for pk, game_rows in grouped.items():
        if not physical_pitches_complete(
                game_rows, {pk}, physical_expected, physical_batters, physical_invalid):
            continue
        verified[pk] = (game_rows, pitches_complete(game_rows, {pk}, expected, invalid))
    return verified


def load_training_statcast(bundle, s3, bucket, *, requested_dates=None):
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
    if requested_dates is not None:
        requested = {date.fromisoformat(value).isoformat() for value in requested_dates}
        if len(requested) > 30:
            raise ValueError('bounded retained restore permits at most 30 dates')
        expected_dates = {value: games for value, games in expected_dates.items()
                          if value in requested}
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
    completed_by_game = {str(g['officialGamePk']): g.get('completedAtUtc') for g in bundle['full']}
    from ks1.official_outcomes import schedule_times
    scheduled_by_game = schedule_times(schedule)
    physical_expected, physical_batters, physical_invalid = official_physical_pitch_counts(
        bundle['full'])

    def read_date(value):
        games = expected_dates[value]
        if value in unfinished:
            return value, [], [], 'unfinished_scheduled_game', False, None, set()
        if not games:
            return value, [], [], None, True, None, set()
        reader = Reader(s3, bucket)
        revision = hashlib.sha256(encode(sorted(games))).hexdigest()
        keys = [RESEARCH+f'sources/statcast-v2/{value}.json',
                RESEARCH+f'sources/statcast-v2-revisions/{value}/{revision}.json']
        reasons = []
        physical_candidate = None
        partial_games = {}
        for key in keys:
            receipt_start = len(reader.receipts)
            try:
                payload = reader.read(key)
                receipt = reader.receipts[-1]
                physical_reason = physical_validation_reason(
                    payload, value, games, physical_expected, physical_batters,
                    physical_invalid, scheduled_by_game=scheduled_by_game)
                reason = validation_reason(payload, value, games, expected, invalid,
                                           completed_by_game, scheduled_by_game)
                if receipt.get('versionId') in (None, '', 'null'):
                    physical_reason = reason = 'unversioned_source'
                elif physical_reason or reason:
                    for pk, (game_rows, outcomes) in verified_raw_games(
                            payload, value, games, expected, invalid,
                            physical_expected, physical_batters, physical_invalid).items():
                        # Keep every game's rows and proof from one exact
                        # version. Prefer outcome-complete evidence, but never
                        # splice pitches across source revisions.
                        if pk not in partial_games or (outcomes and not partial_games[pk][1]):
                            partial_games[pk] = (game_rows, outcomes, receipt)
                if 'outcome_reconciliation' in payload or 'raw_statcast' in payload:
                    from ks1.official_outcomes import read_retained_evidence
                    read_retained_evidence(payload, reader)
                    if reason is not None:
                        physical_reason = reason
                if physical_reason:
                    reasons.append(physical_reason)
                    continue
                if reason is None:
                    return value, payload['rows'], list(reader.receipts[receipt_start:]), None, True, None, set()
                reasons.append(reason)
                if physical_candidate is None:
                    physical_candidate = (payload['rows'], [receipt], reason)
            except Exception as exc:
                # Missing or invalid immutable objects do not create coverage.
                reasons.append('source_read:' + type(exc).__name__)
        try:
            from ks1.statcast_recovery import read_recovery
            payload, recovery_receipts = read_recovery(s3, bucket, value)
            if payload is not None:
                reason = physical_validation_reason(
                    payload, value, games, physical_expected, physical_batters,
                    physical_invalid, scheduled_by_game=scheduled_by_game)
                if reason is None:
                    reason = validation_reason(payload, value, games, expected, invalid,
                                               completed_by_game, scheduled_by_game)
                if reason is None:
                    return value, payload['rows'], recovery_receipts, None, True, None, set()
                reasons.append('recovered_' + reason)
        except Exception as exc:
            reasons.append('recovery_read:' + type(exc).__name__)
        if physical_candidate is not None:
            daily_rows, daily_receipts, outcome_reason = physical_candidate
            # A complete physical date can still contain games with complete
            # outcomes. Use only the same source version as the chosen rows.
            outcomes = {pk for pk, (_, ok, receipt) in partial_games.items()
                        if ok and receipt == daily_receipts[0]}
            return value, daily_rows, daily_receipts, None, False, outcome_reason, outcomes
        if partial_games:
            daily_rows, daily_receipts, outcomes = [], [], set()
            for pk in sorted(partial_games):
                game_rows, ok, receipt = partial_games[pk]
                daily_rows.extend(game_rows)
                if receipt not in daily_receipts:
                    daily_receipts.append(receipt)
                if ok:
                    outcomes.add(pk)
            error = {'reason': 'retained_pitches_unavailable_or_unverified',
                     'details': sorted(set(reasons)),
                     'independently_verified_physical_games': sorted(partial_games),
                     'independently_verified_outcome_games': sorted(outcomes)}
            return value, daily_rows, daily_receipts, error, False, None, outcomes
        return value, [], [], {'reason': 'retained_pitches_unavailable_or_unverified',
                              'details': sorted(set(reasons))}, False, None, set()

    rows, receipts, verified_physical, verified_outcomes, errors = [], [], [], [], []
    verified_physical_pitch_objects = 0
    verified_outcome_pitch_objects = 0
    independently_verified_outcomes = set()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for value, daily_rows, daily_receipts, error, outcomes_complete, outcome_reason, game_outcomes in pool.map(
                read_date, sorted(expected_dates)):
            rows.extend(daily_rows)
            receipts.extend(daily_receipts)
            independently_verified_outcomes.update(game_outcomes)
            if error:
                errors.append({'date': value, **(error if isinstance(error, dict) else {'reason': error})})
            else:
                verified_physical.append(value)
                if outcomes_complete:
                    verified_outcomes.append(value)
                    verified_outcome_pitch_objects += bool(daily_rows)
                elif outcome_reason:
                    errors.append({
                        'date': value,
                        'reason': 'retained_pa_outcomes_unavailable_or_unverified',
                        'details': [outcome_reason],
                        'physical_pitch_coverage_retained': True,
                    })
                verified_physical_pitch_objects += bool(daily_rows)
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
    existing_physical_outside_range = (
        set(bundle.get('statcast_physical_dates',
                       bundle.get('statcast_retained_dates', ()))) - set(expected_dates))
    bundle['statcast_retained_dates'] = sorted(
        existing_outside_range | set(verified_outcomes))
    bundle['statcast_physical_dates'] = sorted(
        existing_physical_outside_range | set(verified_physical))
    existing_game_dates = {str(row.get('game_pk')): row.get('game_date')
                           for row in bundle['statcast']}
    prior_physical_games = set(bundle.get(
        'statcast_physical_games', bundle.get('statcast_verified_games', [])))
    outside_games = {str(pk) for pk in bundle.get('statcast_verified_games', [])
                     if existing_game_dates.get(str(pk))
                     and existing_game_dates[str(pk)] not in expected_dates}
    outcome_games = {str(row['game_pk']) for row in rows
                     if row.get('game_date') in set(verified_outcomes)}
    bundle['statcast_verified_games'] = sorted(
        outside_games | outcome_games | independently_verified_outcomes)
    existing_physical_games = {str(pk) for pk in prior_physical_games
        if existing_game_dates.get(str(pk))
        and existing_game_dates[str(pk)] not in expected_dates}
    bundle['statcast_physical_games'] = sorted(existing_physical_games | loaded_games)
    # Preserve the existing global source-completeness gates. Individual
    # windows additionally require every date in statcast_retained_dates.
    bundle['source_receipts'].extend(receipts)
    return {'source': RESEARCH+'sources/statcast-v2/', 'provider_requests': 0,
            'complete_official_years': years, 'expected_dates': len(expected_dates),
            'verified_physical_dates': len(verified_physical),
            'verified_outcome_dates': len(verified_outcomes),
            'verified_dates': len(verified_outcomes),
            'verified_pitch_objects': verified_outcome_pitch_objects,
            'verified_physical_pitch_objects': verified_physical_pitch_objects,
            'verified_physical_games': len(loaded_games),
            'verified_outcome_games': len(outcome_games | independently_verified_outcomes),
            'retained_pitch_rows': len(rows), 'errors': errors,
            'pitch_coverage_method': 'official_box_physical_v1_plus_pa_outcomes_v3',
            'original_prospective_storage_claimed': False}
