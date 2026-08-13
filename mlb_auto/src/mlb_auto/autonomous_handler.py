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


def autonomous_backfill() -> dict:
    result = run_historical_backfill()
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
    if action in ('HISTORICAL_BACKFILL', 'MLB_AUTO_HISTORICAL_BACKFILL'):
        return autonomous_backfill()
    if action in ('REPAIR', 'MLB_AUTO_REPAIR'):
        original_train = base.train
        try:
            base.train = autonomous_train
            return base.repair()
        finally:
            base.train = original_train
    return base.handler(event, context)
