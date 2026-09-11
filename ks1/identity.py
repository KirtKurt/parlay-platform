"""Auditable BBS/MLB identity reconciliation; never invent a game or start time.

The 90-second exact-start path is retained. A separate, bounded recovery path
requires a unique exact home/away pairing in BOTH catalogues, an explicitly
non-doubleheader official fixture, and agreement that it remains scheduled.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
EXACT_START_SECONDS = 90
SINGLE_FIXTURE_DRIFT_SECONDS = 300


def timestamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp must have timezone')
    return result


def event_day(value):
    return timestamp(value).astimezone(ET).date().isoformat()


def reconcile_bbs(payload, schedule, resolve, target_date):
    """Return (official-ID -> unmodified BBS event, per-game identity evidence).

    ``resolve`` must resolve only unique exact team aliases. No odds, model
    probabilities, results, or future observations influence identity matching.
    Missing/ambiguous identities still raise; this is not a partial-slate mode.
    """
    if date.fromisoformat(target_date).isoformat() != target_date:
        raise ValueError('invalid date')
    data = payload.get('data')
    if not isinstance(data, list):
        raise ValueError('BBS matches require data array; scores are not match identities')
    if len(data) >= 200:
        raise ValueError('BBS result may be truncated; refuse partial catalogue')
    ids, events = set(), []
    for event in data:
        if not all(event.get(k) for k in ('id', 'kickoff_utc', 'home', 'away')):
            raise ValueError('BBS match identity schema changed')
        if event_day(event['kickoff_utc']) != target_date:
            continue
        if str(event.get('sport', '')).lower() != 'baseball' or str(event.get('league', '')).lower() != 'mlb':
            raise ValueError('non-MLB BBS event')
        if event['id'] in ids:
            raise ValueError('duplicate BBS match ID')
        ids.add(event['id'])
        pair = tuple(resolve(event[s]['name']) for s in ('home', 'away'))
        events.append((event, pair))
    games = [g for g in schedule if event_day(g['gameDate']) == target_date]
    assigned, evidence = {}, []
    for event, pair in events:
        same_pair = [g for g in games if pair == tuple(str(g['teams'][s]['team']['id']) for s in ('home', 'away'))]
        exact = [g for g in same_pair if abs((timestamp(event['kickoff_utc']) - timestamp(g['gameDate'])).total_seconds()) <= EXACT_START_SECONDS]
        method = 'unique_exact_alias_and_game_start'
        match = exact[0] if len(exact) == 1 else None
        if not exact and len(same_pair) == 1 and sum(other_pair == pair for _, other_pair in events) == 1:
            game = same_pair[0]
            status = game.get('status', {})
            drift = abs((timestamp(event['kickoff_utc']) - timestamp(game['gameDate'])).total_seconds())
            if (game.get('doubleHeader') == 'N' and str(game.get('gameNumber')) == '1'
                    and status.get('abstractGameState') == 'Preview'
                    and status.get('detailedState') == 'Scheduled'
                    and status.get('startTimeTBD') is False
                    and str(event.get('status', '')).lower() == 'scheduled'
                    and str(event.get('gameNumber', 1)) == '1'
                    and drift <= SINGLE_FIXTURE_DRIFT_SECONDS):
                match = game
                method = 'unique_scheduled_single_fixture_official_start'
        if match is None:
            raise ValueError('ambiguous or unmatched BBS game identity: ' + str(event['id']))
        pk = str(match['gamePk'])
        if pk in assigned:
            raise ValueError('multiple BBS IDs map to one official game')
        assigned[pk] = event
        evidence.append({'game_id': pk, 'bbs_game_id': str(event['id']), 'method': method,
                         'official_start': match['gameDate'], 'provider_start': event['kickoff_utc'],
                         'provider_minus_official_seconds': (timestamp(event['kickoff_utc']) - timestamp(match['gameDate'])).total_seconds(),
                         'start_time_authority': 'MLB', 'home_id': pair[0], 'away_id': pair[1]})
    return assigned, evidence
