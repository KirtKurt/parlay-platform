from __future__ import annotations

from . import handler as base
from .autonomous_markets import (
    cached_market_inventory as _cached_inventory,
    discover_market_inventory as _discover_inventory,
    is_period_market as _period_market,
    live_provider_proof as _provider_proof,
    market_categories as _categories,
)
from .evolution import discover_challenger
from .historical_backfill_v2 import run_historical_backfill
from .ml import promote_challenger
from .provider_open import OpenEndedOddsApiClient
from .storage import Store
from .training_integrity import run as _run_training

base.OddsApiClient = OpenEndedOddsApiClient


def _is_period_market(key: str) -> bool:
    return _period_market(key)


def _market_categories(keys: list[str]) -> dict[str, list[str]]:
    return _categories(keys)


def live_provider_proof() -> dict:
    return _provider_proof(OpenEndedOddsApiClient)


def discover_market_inventory() -> dict:
    return _discover_inventory(Store, OpenEndedOddsApiClient, base._iso)


def _cached_market_inventory(max_age_seconds: int = 21600) -> dict | None:
    return _cached_inventory(Store, OpenEndedOddsApiClient, max_age_seconds)


def autonomous_train() -> dict:
    return _run_training(
        Store=Store, base=base,
        discover_challenger=discover_challenger,
        promote_challenger=promote_challenger,
    )


def autonomous_backfill(max_games_per_run: int | None = None) -> dict:
    inventory = _cached_market_inventory() or discover_market_inventory()
    result = run_historical_backfill(max_games_per_run=max_games_per_run)
    result['market_inventory'] = {
        'ok': inventory.get('ok'), 'regions': inventory.get('regions'),
        'event_count': inventory.get('event_count'),
        'market_key_count': inventory.get('market_key_count'),
        'market_keys': inventory.get('market_keys'),
        'categories': inventory.get('categories'),
        'errors': inventory.get('errors'),
        'cached': bool(inventory.get('cached')),
    }
    count = int(result.get('training_examples') or 0)
    result['training'] = autonomous_train() if count >= base.MIN_TRAIN else {
        'trained': False, 'reason': 'INSUFFICIENT_EXAMPLES',
        'count': count, 'minimum': base.MIN_TRAIN,
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
