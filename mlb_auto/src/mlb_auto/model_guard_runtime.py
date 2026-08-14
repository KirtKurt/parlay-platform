from __future__ import annotations

import math
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Any, Mapping

from .features import bootstrap_home_probability
from .model_guard import (
    FALLBACK_MODE,
    MODEL_GUARD_VERSION,
    evaluate_model_input,
    policy_payload,
)

PLATFORM_VERSION = 'MLB-AUTO-v1.2-ood-guard'
OFFICIAL_PICK_POLICY = 'CHAMPION_CONFIDENCE_ONLY'
MIXED_MODE = 'MIXED_CHAMPION_OOD_FALLBACK'

_guard_context: ContextVar[dict[str, Any] | None] = ContextVar(
    'mlb_auto_model_guard_context', default=None,
)


def _prediction_error_guard(evaluation: Mapping[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        **dict(evaluation),
        'triggered': True,
        'fallback_required': True,
        'reason': 'MODEL_PREDICTION_ERROR',
        'prediction_error': f'{type(exc).__name__}:{str(exc)[:300]}',
    }


class _GuardedChampion:
    def __init__(self, model):
        self._model = model

    def __getattr__(self, name: str):
        return getattr(self._model, name)

    @property
    def inner_model(self):
        return self._model

    def predict(self, features: Mapping[str, Any]) -> float:
        evaluation = evaluate_model_input(self._model, features)
        raw_probability = None
        try:
            raw_probability = float(self._model.predict(features))
            if not math.isfinite(raw_probability):
                raise ValueError('NON_FINITE_MODEL_PROBABILITY')
        except Exception as exc:
            evaluation = _prediction_error_guard(evaluation, exc)

        if evaluation.get('triggered'):
            output = float(bootstrap_home_probability(features))
        else:
            output = float(raw_probability)

        _guard_context.set({
            'evaluation': evaluation,
            'raw_home_probability': raw_probability,
            'effective_home_probability': output,
        })
        return output


def _source_pull_at(row: Mapping[str, Any]) -> str:
    return str(row.get('source_pull_at') or '')


def _matching_snapshot(store, slate: str, event_id: str, source_pull_at: str) -> dict[str, Any] | None:
    matches = [
        row for row in store.query_snapshots(slate, event_id=event_id, limit=500)
        if _source_pull_at(row.get('prediction') or row) == source_pull_at
    ]
    return matches[-1] if matches else None


def _postprocess_latest_ingest(*, base, Store, original_model_from_item, result: dict[str, Any]) -> dict[str, Any]:
    if result.get('action') != 'INGEST':
        return {**result, 'model_input_guard': policy_payload()}

    store = Store()
    controller = store.get_state('controller')
    pull_at = str(controller.get('last_pull_at') or '')
    champion_item = store.get_model('CHAMPION')
    champion = original_model_from_item(champion_item)
    if not pull_at or champion is None:
        store.put_state('controller', {
            'platform_version': PLATFORM_VERSION,
            'official_pick_policy': OFFICIAL_PICK_POLICY,
            'model_guard_version': MODEL_GUARD_VERSION,
            'model_guard_enabled': True,
            'model_guard_evaluated_count': 0,
            'model_guard_trigger_count': 0,
            'model_guard_reason_counts': {},
            'model_guard_triggered_event_ids': [],
        })
        return {
            **result,
            'platform_version': PLATFORM_VERSION,
            'official_pick_policy': OFFICIAL_PICK_POLICY,
            'model_input_guard': policy_payload(),
            'model_guard_evaluated_count': 0,
            'model_guard_trigger_count': 0,
            'model_guard_reason_counts': {},
            'model_guard_triggered_event_ids': [],
        }

    now_et = datetime.now(base.ET).date()
    rows: list[tuple[str, dict[str, Any]]] = []
    for offset in range(-1, 8):
        slate = (now_et + timedelta(days=offset)).isoformat()
        for row in store.query_predictions(slate):
            if _source_pull_at(row) == pull_at:
                rows.append((slate, dict(row)))

    reasons: Counter[str] = Counter()
    triggered_ids: list[str] = []
    evaluated_count = 0
    trigger_count = 0
    max_abs_z = 0.0

    for slate, row in rows:
        event_id = str(row.get('event_id') or row.get('SK') or '')
        features = row.get('features') or {}
        evaluation = evaluate_model_input(champion, features)
        raw_home_probability = None
        try:
            raw_home_probability = float(champion.predict(features))
            if not math.isfinite(raw_home_probability):
                raise ValueError('NON_FINITE_MODEL_PROBABILITY')
        except Exception as exc:
            evaluation = _prediction_error_guard(evaluation, exc)

        evaluated_count += 1
        reason = str(evaluation.get('reason') or 'UNKNOWN')
        reasons[reason] += 1
        triggered = bool(evaluation.get('triggered'))
        if triggered:
            trigger_count += 1
            triggered_ids.append(event_id)
            row['official_pick'] = False
            row['promotion_status'] = 'PROVISIONAL_OOD_FALLBACK'
            row['prediction_mode'] = FALLBACK_MODE
        else:
            row['prediction_mode'] = 'ML_CHAMPION'

        abs_z = evaluation.get('max_abs_z')
        try:
            if abs_z is not None:
                max_abs_z = max(max_abs_z, float(abs_z))
        except Exception:
            pass

        row['platform_version'] = PLATFORM_VERSION
        row['official_pick_policy'] = OFFICIAL_PICK_POLICY
        row['model_guard_triggered'] = triggered
        row['model_guard_reason'] = reason
        row['model_guard_version'] = MODEL_GUARD_VERSION
        row['model_guard_fallback'] = triggered
        row['model_input_guard'] = evaluation
        row['model_probability_raw_home'] = raw_home_probability
        row['model_probability_raw_win'] = (
            max(raw_home_probability, 1.0 - raw_home_probability)
            if raw_home_probability is not None else None
        )
        store.put_prediction(slate, event_id, row)

        snapshot = _matching_snapshot(store, slate, event_id, pull_at)
        if snapshot:
            payload = {
                key: value for key, value in snapshot.items()
                if key not in ('PK', 'SK')
            }
            payload['event_id'] = event_id
            payload['source_pull_at'] = pull_at
            payload['prediction'] = row
            payload['prediction_fingerprint'] = row.get('prediction_fingerprint')
            store.put_snapshot(slate, pull_at, payload)

    if champion and rows:
        prediction_mode = (
            FALLBACK_MODE if trigger_count == len(rows)
            else MIXED_MODE if trigger_count
            else 'ML_CHAMPION'
        )
    else:
        prediction_mode = 'MARKET_BOOTSTRAP'

    guard_summary = {
        'platform_version': PLATFORM_VERSION,
        'official_pick_policy': OFFICIAL_PICK_POLICY,
        'model_guard_version': MODEL_GUARD_VERSION,
        'model_guard_enabled': True,
        'model_guard_last_evaluated_at': base._iso(),
        'model_guard_evaluated_count': evaluated_count,
        'model_guard_trigger_count': trigger_count,
        'model_guard_reason_counts': dict(reasons),
        'model_guard_triggered_event_ids': triggered_ids[:50],
        'model_guard_max_abs_z': round(max_abs_z, 6),
        'prediction_mode': prediction_mode,
    }
    store.put_state('controller', guard_summary)
    return {
        **result,
        **guard_summary,
        'model_input_guard': policy_payload(),
    }


def install(base, Store) -> None:
    if getattr(base, '_mlb_auto_model_guard_installed', False):
        return

    original_ingest = base.ingest
    original_model_from_item = base._model_from_item
    original_qualifies = base._qualifies_official_pick

    def guarded_model_from_item(item):
        model = original_model_from_item(item)
        return _GuardedChampion(model) if model is not None else None

    def guarded_qualifies(champion, win_probability: float) -> bool:
        context = _guard_context.get()
        if context and bool((context.get('evaluation') or {}).get('triggered')):
            return False
        return bool(original_qualifies(champion, win_probability))

    def guarded_ingest(*, force_reason: str | None = None):
        model_token = _guard_context.set(None)
        prior_model_from_item = base._model_from_item
        prior_qualifies = base._qualifies_official_pick
        base._model_from_item = guarded_model_from_item
        base._qualifies_official_pick = guarded_qualifies
        try:
            result = original_ingest(force_reason=force_reason)
        finally:
            base._model_from_item = prior_model_from_item
            base._qualifies_official_pick = prior_qualifies
            _guard_context.reset(model_token)
        if not isinstance(result, dict):
            return result
        return _postprocess_latest_ingest(
            base=base,
            Store=Store,
            original_model_from_item=original_model_from_item,
            result=result,
        )

    base.ingest = guarded_ingest
    base._mlb_auto_model_guard_policy = policy_payload
    base._mlb_auto_model_guard_official_pick_policy = OFFICIAL_PICK_POLICY
    base._mlb_auto_model_guard_platform_version = PLATFORM_VERSION
    base._mlb_auto_model_guard_installed = True
