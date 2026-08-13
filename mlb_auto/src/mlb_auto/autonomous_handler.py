from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from . import handler as base
from .evolution import discover_challenger
from .historical_backfill_v2 import run_historical_backfill
from .ml import chronological_split, promote_challenger
from .provider_open import OpenEndedOddsApiClient
from .storage import Store

# Force the production entrypoint to use open-ended baseball_mlb market discovery.
base.OddsApiClient = OpenEndedOddsApiClient


def _market_categories(keys: list[str]) -> dict[str, list[str]]:
    cats = {
        'featured': [], 'period': [], 'alternate': [], 'team_total': [],
        'pitcher': [], 'batter': [], 'other': [],
    }
    for key in sorted(set(keys)):
        if key in ('h2h', 'spreads', 'totals'):
            cats['featured'].append(key)
        elif key.startswith(('first_', 'innings')):
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


def live_provider_proof() -> dict:
    client = OpenEndedOddsApiClient()
    response = client.featured_odds()
    events = [x for x in (response.data or []) if x.get('sport_key') == 'baseball_mlb']
    bookmakers = sorted({str(b.get('key')) for e in events for b in (e.get('bookmakers') or []) if b.get('key')})
    return {
        'ok': bool(events),
        'action': 'LIVE_PROVIDER_PROOF',
        'provider_sport_key': 'baseball_mlb',
        'regions': client.regions,
        'featured_markets': ['h2h', 'spreads', 'totals'],
        'event_count': len(events),
        'bookmaker_count': len(bookmakers),
        'bookmakers': bookmakers,
        'quota_headers_observed': {
            'remaining_present': response.remaining is not None,
            'used_present': response.used is not None,
            'last_cost_present': response.cost is not None,
        },
        'raw_metadata_requested': {
            'links': True,
            'source_ids': True,
            'bet_limits': True,
            'rotation_numbers': True,
        },
    }


def discover_market_inventory() -> dict:
    store = Store()
    client = OpenEndedOddsApiClient()
    events = client.events().data or []
    event_rows = []
    all_keys: set[str] = set()
    errors: list[str] = []
    for event in events:
        if event.get('sport_key') not in (None, 'baseball_mlb'):
            continue
        event_id = str(event.get('id') or '')
        if not event_id:
            continue
        try:
            payload = client.event_markets(event_id).data
            keys = client.useful_markets(payload)
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
    categories = _market_categories(keys)
    state = store.get_state('controller')
    store.put_state('controller', {
        **state,
        'known_market_keys': keys,
        'known_market_key_count': len(keys),
        'odds_regions': client.regions,
        'last_market_inventory_at': base._iso(),
        'market_inventory_event_count': len(event_rows),
        'market_inventory_error_count': len(errors),
        'market_category_counts': {k: len(v) for k, v in categories.items()},
    })
    return {
        'ok': len(event_rows) > 0,
        'action': 'MARKET_INVENTORY',
        'provider_sport_key': 'baseball_mlb',
        'regions': client.regions,
        'event_count': len(event_rows),
        'market_key_count': len(keys),
        'market_keys': keys,
        'categories': categories,
        'events': event_rows,
        'errors': errors,
    }


def _cached_market_inventory(max_age_seconds: int = 21600) -> dict | None:
    store = Store()
    state = store.get_state('controller')
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
    client = OpenEndedOddsApiClient()
    return {
        'ok': True,
        'action': 'MARKET_INVENTORY_CACHE',
        'provider_sport_key': 'baseball_mlb',
        'regions': client.regions,
        'event_count': int(state.get('market_inventory_event_count') or 0),
        'market_key_count': len(keys),
        'market_keys': keys,
        'categories': _market_categories(keys),
        'errors': [],
        'cached': True,
        'age_seconds': max(0, int(age)),
    }


def autonomous_train() -> dict:
    store = Store()
    examples = store.query_training_examples(limit=5000)
    examples.sort(key=lambda x: (str(x.get('commence_time') or ''), str(x.get('SK') or '')))
    if len(examples) < base.MIN_TRAIN:
        return {'ok': True, 'trained': False, 'reason': 'INSUFFICIENT_EXAMPLES', 'count': len(examples), 'minimum': base.MIN_TRAIN}

    rows = [dict(x.get('features') or {}) for x in examples]
    labels = [int(x.get('label_home_win')) for x in examples]
    tr, yl, vr, vl = chronological_split(rows, labels, .2)
    if len(vr) < base.MIN_VALID:
        return {'ok': True, 'trained': False, 'reason': 'INSUFFICIENT_VALIDATION', 'count': len(examples), 'validation_count': len(vr), 'minimum_validation': base.MIN_VALID}

    discovered = discover_challenger(rows, labels, min_train=max(50, base.MIN_TRAIN - base.MIN_VALID), min_validation=base.MIN_VALID)
    challenger = discovered.model
    incumbent_item = store.get_model('CHAMPION')
    incumbent = base._model_from_item(incumbent_item)
    gate = promote_challenger(challenger=challenger, incumbent=incumbent, validation_rows=vr, validation_labels=vl)
    model_id = f'MLB_AUTO_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{hashlib.sha256(challenger.dumps().encode()).hexdigest()[:12]}'
    artifact = {
        'model_id': model_id,
        'created_at': base._iso(),
        'model_json': challenger.dumps(),
        'training_count': len(tr),
        'validation_count': len(vr),
        'gate': gate,
        'sport': 'mlb_auto',
        'autonomous_evolution': True,
        'search_manifest': discovered.search_manifest,
        'selected_features': list(discovered.feature_names),
        'discovery_metrics': discovered.metrics,
    }
    store.put_model(f'CHALLENGER#{model_id}', artifact)
    promoted = bool(gate.get('promote'))
    if promoted:
        store.put_model('CHAMPION', artifact)
    state = store.get_state('controller')
    store.put_state('controller', {
        **state,
        'last_training_at': base._iso(),
        'last_training_count': len(examples),
        'champion_model_id': model_id if promoted else incumbent_item.get('model_id'),
        'last_training_gate': gate,
        'last_search_manifest': discovered.search_manifest,
    })
    return {
        'ok': True,
        'trained': True,
        'model_id': model_id,
        'promoted': promoted,
        'gate': gate,
        'examples': len(examples),
        'search_manifest': discovered.search_manifest,
    }


def autonomous_backfill(max_games_per_run: int | None = None) -> dict:
    inventory = _cached_market_inventory() or discover_market_inventory()
    result = run_historical_backfill(max_games_per_run=max_games_per_run)
    result['market_inventory'] = {
        'ok': inventory.get('ok'),
        'regions': inventory.get('regions'),
        'event_count': inventory.get('event_count'),
        'market_key_count': inventory.get('market_key_count'),
        'market_keys': inventory.get('market_keys'),
        'categories': inventory.get('categories'),
        'errors': inventory.get('errors'),
        'cached': bool(inventory.get('cached')),
    }
    if int(result.get('training_examples') or 0) >= base.MIN_TRAIN:
        result['training'] = autonomous_train()
    else:
        result['training'] = {
            'trained': False,
            'reason': 'INSUFFICIENT_EXAMPLES',
            'count': int(result.get('training_examples') or 0),
            'minimum': base.MIN_TRAIN,
        }
    return result


def handler(event, context):
    event = event or {}
    action = str(event.get('action') or event.get('detail-type') or '').upper()
    if action in ('TRAIN', 'MLB_AUTO_TRAIN'):
        return autonomous_train()
    if action in ('LIVE_PROVIDER_PROOF', 'MLB_AUTO_LIVE_PROVIDER_PROOF'):
        return live_provider_proof()
    if action in ('MARKET_INVENTORY', 'MLB_AUTO_MARKET_INVENTORY'):
        return discover_market_inventory()
    if action in ('HISTORICAL_BACKFILL', 'MLB_AUTO_HISTORICAL_BACKFILL'):
        return autonomous_backfill(max_games_per_run=event.get('max_games_per_run'))
    if action in ('REPAIR', 'MLB_AUTO_REPAIR'):
        original_train = base.train
        try:
            base.train = autonomous_train
            return base.repair()
        finally:
            base.train = original_train
    return base.handler(event, context)
