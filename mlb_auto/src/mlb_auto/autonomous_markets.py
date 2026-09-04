from __future__ import annotations

from datetime import datetime, timezone


def is_period_market(key: str) -> bool:
    value = str(key or '').lower()
    return value.startswith(('first_', 'innings')) or '_inning' in value or '_innings' in value


def market_categories(keys: list[str]) -> dict[str, list[str]]:
    cats = {
        'featured': [], 'period': [], 'alternate': [], 'team_total': [],
        'pitcher': [], 'batter': [], 'other': [],
    }
    for key in sorted(set(keys)):
        if key in ('h2h', 'spreads', 'totals'):
            cats['featured'].append(key)
        elif is_period_market(key):
            cats['period'].append(key)
        elif key.startswith('pitcher_'):
            cats['pitcher'].append(key)
        elif key.startswith('batter_'):
            cats['batter'].append(key)
        elif 'team_totals' in key:
            cats['team_total'].append(key)
        elif key.startswith('alternate_') or key.endswith('_alternate'):
            cats['alternate'].append(key)
        else:
            cats['other'].append(key)
    return cats


def live_provider_proof(Client) -> dict:
    client = Client()
    response = client.featured_odds()
    events = [x for x in (response.data or []) if x.get('sport_key') == 'baseball_mlb']
    bookmakers = sorted({
        str(book.get('key'))
        for event in events
        for book in (event.get('bookmakers') or [])
        if book.get('key')
    })
    return {
        'ok': bool(events), 'action': 'LIVE_PROVIDER_PROOF',
        'provider_sport_key': 'baseball_mlb', 'regions': client.regions,
        'featured_markets': ['h2h', 'spreads', 'totals'],
        'event_count': len(events), 'bookmaker_count': len(bookmakers),
        'bookmakers': bookmakers,
        'quota_headers_observed': {
            'remaining_present': response.remaining is not None,
            'used_present': response.used is not None,
            'last_cost_present': response.cost is not None,
        },
        'raw_metadata_requested': {
            'links': True, 'source_ids': True, 'bet_limits': True,
            'rotation_numbers': True,
        },
    }


def discover_market_inventory(Store, Client, iso) -> dict:
    store, client = Store(), Client()
    events = client.events().data or []
    event_rows, errors = [], []
    all_keys: set[str] = set()
    for event in events:
        if event.get('sport_key') not in (None, 'baseball_mlb'):
            continue
        event_id = str(event.get('id') or '')
        if not event_id:
            continue
        try:
            keys = client.useful_markets(client.event_markets(event_id).data)
            all_keys.update(keys)
            event_rows.append({
                'event_id': event_id,
                'commence_time': event.get('commence_time'),
                'home_team': event.get('home_team'),
                'away_team': event.get('away_team'),
                'market_keys': keys,
                'market_key_count': len(keys),
            })
        except Exception as exc:
            errors.append(f'{event_id}:{type(exc).__name__}')
    keys = sorted(all_keys)
    categories = market_categories(keys)
    store.put_state('controller', {
        'known_market_keys': keys,
        'known_market_key_count': len(keys),
        'odds_regions': client.regions,
        'last_market_inventory_at': iso(),
        'market_inventory_event_count': len(event_rows),
        'market_inventory_error_count': len(errors),
        'market_category_counts': {k: len(v) for k, v in categories.items()},
    })
    return {
        'ok': bool(event_rows), 'action': 'MARKET_INVENTORY',
        'provider_sport_key': 'baseball_mlb', 'regions': client.regions,
        'event_count': len(event_rows), 'market_key_count': len(keys),
        'market_keys': keys, 'categories': categories,
        'events': event_rows, 'errors': errors,
    }


def cached_market_inventory(Store, Client, max_age_seconds: int = 21600) -> dict | None:
    state = Store().get_state('controller')
    keys = sorted({str(x) for x in (state.get('known_market_keys') or []) if str(x)})
    stamp = state.get('last_market_inventory_at')
    if not keys or not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds()
    except Exception:
        return None
    if age > max_age_seconds:
        return None
    client = Client()
    return {
        'ok': True, 'action': 'MARKET_INVENTORY_CACHE',
        'provider_sport_key': 'baseball_mlb', 'regions': client.regions,
        'event_count': int(state.get('market_inventory_event_count') or 0),
        'market_key_count': len(keys), 'market_keys': keys,
        'categories': market_categories(keys), 'errors': [],
        'cached': True, 'age_seconds': max(0, int(age)),
    }
