from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def evaluate(context, base, discover_challenger, promote_challenger):
    rows, labels = context['rows'], context['labels']
    audit_count = base.MIN_VALID
    search_rows, search_labels = rows[:-audit_count], labels[:-audit_count]
    audit_rows, audit_labels = rows[-audit_count:], labels[-audit_count:]
    inner_validation = max(20, int(len(search_rows) * .2))
    inner_validation = min(len(search_rows) - 1, inner_validation)
    inner_train = len(search_rows) - inner_validation
    if inner_train < 50 or inner_validation < 20:
        return None, {
            'ok': True, 'trained': False,
            'reason': 'INSUFFICIENT_SEARCH_POPULATION',
            'count': context['count'], 'search_count': len(search_rows),
        }

    discovered = discover_challenger(
        search_rows, search_labels,
        min_train=inner_train, min_validation=inner_validation,
    )
    challenger = discovered.model
    incumbent_item = context['store'].get_model('CHAMPION')
    incumbent = base._model_from_item(incumbent_item)
    gate = promote_challenger(
        challenger=challenger, incumbent=incumbent,
        validation_rows=audit_rows, validation_labels=audit_labels,
    )
    manifest = {
        **dict(discovered.search_manifest or {}),
        'searchPopulationRows': len(search_rows),
        'untouchedAuditRows': len(audit_rows),
        'validationPolicy': 'nested_chronological_search_plus_untouched_audit_v1',
    }
    model_id = (
        f'MLB_AUTO_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_'
        f'{hashlib.sha256(challenger.dumps().encode()).hexdigest()[:12]}'
    )
    artifact = {
        'model_id': model_id, 'created_at': base._iso(),
        'model_json': challenger.dumps(),
        'training_count': len(search_rows),
        'validation_count': len(audit_rows), 'gate': gate,
        'sport': 'mlb_auto', 'autonomous_evolution': True,
        'search_manifest': manifest,
        'selected_features': list(discovered.feature_names),
        'discovery_metrics': discovered.metrics,
        'untouched_chronological_audit': True,
    }
    return {
        'challenger': challenger, 'incumbent_item': incumbent_item,
        'gate': gate, 'manifest': manifest, 'model_id': model_id,
        'artifact': artifact,
    }, None
