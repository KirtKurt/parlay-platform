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
from .llm_rd import (
    MODEL_IDS as _configured_rd_models,
    active_program as _active_rd_program,
    apply_program as _apply_rd_program,
    record_training_result as _record_rd_training_result,
    research_discoverer as _research_discoverer,
    run_research as _run_research,
    status_payload as _rd_status,
)
from .ml import promote_challenger
from .model_guard import policy_payload as _model_guard_policy
from .model_guard_runtime import (
    OFFICIAL_PICK_POLICY as _guard_official_pick_policy,
    PLATFORM_VERSION as _guard_platform_version,
    install as _install_model_guard,
)
from .provider_open import OpenEndedOddsApiClient
from .runtime_hardening import install as _install_runtime_hardening
from .storage import Store
from .team_form import (
    rematerialize_training_examples as _rematerialize_team_form,
    status_payload as _team_form_status,
)
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
_install_model_guard(base, Store)


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


def autonomous_research(*, force: bool = False) -> dict:
    result = _run_research(Store=Store, force=force)
    if isinstance(result, dict) and result.get('llm_model_id'):
        result['llm_runtime_model_id'] = str(result['llm_model_id'])
    return result


def autonomous_train() -> dict:
    discover = _research_discoverer(Store=Store, discover_challenger=discover_challenger)
    result = _run_training(
        Store=Store, base=base,
        discover_challenger=discover,
        promote_challenger=promote_challenger,
    )
    _record_rd_training_result(Store=Store, result=result)
    return result


def autonomous_team_form_backfill(max_rows: int | None = None) -> dict:
    return _rematerialize_team_form(Store=Store, limit=max_rows)


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
    team_form = autonomous_team_form_backfill()
    result['team_form_backfill'] = team_form
    result['llm_rd'] = autonomous_research(
        force=bool(team_form.get('feature_family_became_ready')),
    )
    count = int(result.get('training_examples') or 0)
    result['training'] = autonomous_train() if count >= base.MIN_TRAIN else {
        'trained': False, 'reason': 'INSUFFICIENT_EXAMPLES',
        'count': count, 'minimum': base.MIN_TRAIN,
    }
    return result


def _model_access_status(rd: dict) -> dict:
    configured = list(_configured_rd_models)
    selected = rd.get('model_id')
    if selected not in configured:
        selected = None
    provider_available = rd.get('provider_available')
    provider_degraded = bool(rd.get('degraded'))
    verified_by_last_run = bool(
        selected
        and rd.get('last_run_ok') is True
        and rd.get('last_invocation_ok') is True
        and rd.get('last_result') == 'CANDIDATE_GENERATED'
    )
    verification_state = (
        'VERIFIED_BY_RESEARCH'
        if verified_by_last_run
        else 'PROVIDER_UNAVAILABLE_RETRYABLE'
        if provider_available is False and provider_degraded
        else 'PREVIOUSLY_SELECTED'
        if selected
        else 'NOT_YET_INVOKED'
    )
    return {
        'scope': 'mlb_auto_only',
        'mode': 'CONFIGURATION_DRIVEN_FALLBACK',
        'configured_model_ids': configured,
        'account_model_policy': rd.get('account_model_policy'),
        'account_excluded_model_ids': rd.get('account_excluded_model_ids') or [],
        'selected_model_id': selected,
        'selection_policy': 'FIRST_INVOKABLE_CONFIGURED_MODEL',
        'verification_state': verification_state,
        'model_access_verified_by_last_research_run': verified_by_last_run,
        'provider_available': provider_available,
        'provider_degraded': provider_degraded,
        'last_invocation_ok': rd.get('last_invocation_ok'),
        'retryable': bool(rd.get('retryable')),
        'exact_model_requirement': False,
        'required_exact_model_ids': [],
        'runtime_account_enrollment_managed': False,
        'account_mutation_attempted': False,
    }


def _status_with_rd() -> dict:
    result = base.status()
    if isinstance(result, dict):
        rd = _rd_status(Store=Store)
        result['llm_rd'] = rd
        result['llm_model_access'] = _model_access_status(rd)
        result['team_form'] = _team_form_status(Store=Store)
        result['platform_version'] = _guard_platform_version
        result['official_pick_policy'] = _guard_official_pick_policy
        result['model_input_guard'] = _model_guard_policy()
        controller = result.get('controller') or {}
        if controller.get('prediction_mode'):
            result['prediction_mode'] = controller['prediction_mode']
    return result


def handler(event, context):
    event = event or {}
    action = str(event.get('action') or event.get('detail-type') or '').upper()
    if event.get('requestContext'):
        path = str(event.get('rawPath') or '')
        if path.endswith('/status'):
            return base._response(_status_with_rd())
        return base.handler(event, context)
    if action in ('TRAIN', 'MLB_AUTO_TRAIN'):
        return autonomous_train()
    if action in ('LLM_RD', 'MLB_AUTO_LLM_RD'):
        return autonomous_research(force=bool(event.get('force', True)))
    if action in ('TEAM_FORM_BACKFILL', 'MLB_AUTO_TEAM_FORM_BACKFILL'):
        return autonomous_team_form_backfill(max_rows=event.get('max_rows'))
    if action in ('LIVE_PROVIDER_PROOF', 'MLB_AUTO_LIVE_PROVIDER_PROOF'):
        return live_provider_proof()
    if action in ('MARKET_INVENTORY', 'MLB_AUTO_MARKET_INVENTORY'):
        return discover_market_inventory()
    if action in ('INGEST_FORCE', 'MLB_AUTO_INGEST_FORCE'):
        return base.ingest(force_reason='DEPLOYMENT_MODEL_GUARD_PROOF')
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
