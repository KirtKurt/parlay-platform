from __future__ import annotations

import os

# Bedrock's current Opus model cards use direct foundation-model IDs for the
# in-region us-east-1 Converse examples. Prepend those IDs ahead of any
# cross-region profiles supplied by infrastructure, while retaining all
# configured fallbacks.
_direct_opus_ids = (
    'anthropic.claude-opus-4-8',
    'anthropic.claude-opus-4-7',
)
_configured_llm_ids = tuple(
    value.strip()
    for value in os.getenv('MLB_AUTO_LLM_MODEL_IDS', '').split(',')
    if value.strip()
)
os.environ['MLB_AUTO_LLM_MODEL_IDS'] = ','.join(
    dict.fromkeys((*_direct_opus_ids, *_configured_llm_ids))
)

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
from .llm_rd import (
    active_program as _active_rd_program,
    apply_program as _apply_rd_program,
    record_training_result as _record_rd_training_result,
    research_discoverer as _research_discoverer,
    run_research as _run_research,
    status_payload as _rd_status,
)
from .ml import promote_challenger
from .model_access import ensure_opus_access as _ensure_opus_access
from .provider_open import OpenEndedOddsApiClient
from .runtime_hardening import install as _install_runtime_hardening
from .storage import Store
from .threshold_policy import qualifies as _qualifies_threshold
from .training_integrity import run as _run_training

base.OddsApiClient = OpenEndedOddsApiClient
base._qualifies_official_pick = lambda champion, win_probability: _qualifies_threshold(
    champion, win_probability, base.MIN_OFFICIAL_PROB,
)

_original_build_feature_vector = base.build_feature_vector


def _build_feature_vector_with_rd(*args, **kwargs):
    features = _original_build_feature_vector(*args, **kwargs)
    try:
        return _apply_rd_program(features, _active_rd_program(Store()))
    except Exception:
        return features


base.build_feature_vector = _build_feature_vector_with_rd
_install_runtime_hardening(base, Store)


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


def _canonical_opus_id(runtime_model_id: str | None) -> str | None:
    value = str(runtime_model_id or '')
    if 'claude-opus-4-8' in value:
        return 'us.anthropic.claude-opus-4-8'
    if 'claude-opus-4-7' in value:
        return 'us.anthropic.claude-opus-4-7'
    return runtime_model_id


def autonomous_research(*, force: bool = False) -> dict:
    result = _run_research(Store=Store, force=force)
    if isinstance(result, dict) and result.get('llm_model_id'):
        runtime_model_id = str(result['llm_model_id'])
        result['llm_runtime_model_id'] = runtime_model_id
        result['llm_model_id'] = _canonical_opus_id(runtime_model_id)
    return result


def autonomous_model_access() -> dict:
    return _ensure_opus_access(Store=Store)


def autonomous_train() -> dict:
    discover = _research_discoverer(Store=Store, discover_challenger=discover_challenger)
    result = _run_training(
        Store=Store, base=base,
        discover_challenger=discover,
        promote_challenger=promote_challenger,
    )
    _record_rd_training_result(Store=Store, result=result)
    return result


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
    result['llm_rd'] = autonomous_research(force=False)
    count = int(result.get('training_examples') or 0)
    result['training'] = autonomous_train() if count >= base.MIN_TRAIN else {
        'trained': False, 'reason': 'INSUFFICIENT_EXAMPLES',
        'count': count, 'minimum': base.MIN_TRAIN,
    }
    return result


def _status_with_rd() -> dict:
    result = base.status()
    if isinstance(result, dict):
        result['llm_rd'] = _rd_status(Store=Store)
        result['llm_model_access'] = Store().get_state('llm_model_access')
    return result


def handler(event, context):
    event = event or {}
    action = str(event.get('action') or event.get('detail-type') or '').upper()
    if action in ('TRAIN', 'MLB_AUTO_TRAIN'):
        return autonomous_train()
    if action in ('LLM_RD', 'MLB_AUTO_LLM_RD'):
        return autonomous_research(force=bool(event.get('force', True)))
    if action in ('ENABLE_OPUS_ACCESS', 'MLB_AUTO_ENABLE_OPUS_ACCESS'):
        return autonomous_model_access()
    if action in ('LIVE_PROVIDER_PROOF', 'MLB_AUTO_LIVE_PROVIDER_PROOF'):
        return live_provider_proof()
    if action in ('MARKET_INVENTORY', 'MLB_AUTO_MARKET_INVENTORY'):
        return discover_market_inventory()
    if action in ('HISTORICAL_BACKFILL', 'MLB_AUTO_HISTORICAL_BACKFILL'):
        return autonomous_backfill(max_games_per_run=event.get('max_games_per_run'))
    if action in ('STATUS', 'MLB_AUTO_STATUS'):
        return _status_with_rd()
    if action in ('REPAIR', 'MLB_AUTO_REPAIR'):
        original_train = base.train
        try:
            base.train = autonomous_train
            return base.repair()
        finally:
            base.train = original_train
    return base.handler(event, context)
