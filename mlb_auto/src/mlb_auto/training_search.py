from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from .ml import calibrate_platt
from .threshold_policy import attach_learned_threshold, evaluate_model_threshold

MIN_OFFICIAL_AUDIT_SELECTIONS = max(1, int(os.getenv('MLB_AUTO_MIN_OFFICIAL_AUDIT_SELECTIONS', '10')))
MIN_OFFICIAL_AUDIT_ACCURACY = float(os.getenv('MLB_AUTO_MIN_OFFICIAL_AUDIT_ACCURACY', '0.58'))
MIN_OFFICIAL_AUDIT_WILSON = float(os.getenv('MLB_AUTO_MIN_OFFICIAL_AUDIT_WILSON', '0.42'))


def _official_pick_audit_gate(summary: dict) -> dict:
    count = int(summary.get('selection_count') or 0)
    accuracy = summary.get('selection_accuracy')
    wilson = float(summary.get('selection_wilson_lower_bound') or 0.0)
    passed = bool(
        count >= MIN_OFFICIAL_AUDIT_SELECTIONS
        and accuracy is not None
        and float(accuracy) >= MIN_OFFICIAL_AUDIT_ACCURACY
        and wilson >= MIN_OFFICIAL_AUDIT_WILSON
    )
    return {
        'pass': passed,
        'minimum_selection_count': MIN_OFFICIAL_AUDIT_SELECTIONS,
        'minimum_accuracy': MIN_OFFICIAL_AUDIT_ACCURACY,
        'minimum_wilson_lower_bound': MIN_OFFICIAL_AUDIT_WILSON,
    }


def _evaluate_gate(challenger, incumbent, audit_rows, audit_labels, base, promote_challenger):
    gate = promote_challenger(
        challenger=challenger, incumbent=incumbent,
        validation_rows=audit_rows, validation_labels=audit_labels,
    )
    official_pick_audit = evaluate_model_threshold(
        challenger, audit_rows, audit_labels, base.MIN_OFFICIAL_PROB,
    )
    official_pick_gate = _official_pick_audit_gate(official_pick_audit)
    gate = {
        **dict(gate or {}),
        'officialPickAudit': official_pick_audit,
        'officialPickAuditGate': official_pick_gate,
    }
    if gate.get('promote') and not official_pick_gate['pass']:
        gate.update({
            'promote': False,
            'reason': 'OFFICIAL_PICK_AUDIT_GATE',
            'priorGateReason': gate.get('reason'),
        })
    return gate


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
    calibration_rows = search_rows[-inner_validation:]
    calibration_labels = search_labels[-inner_validation:]
    challenger, threshold_metrics = attach_learned_threshold(
        discovered.model,
        calibration_rows,
        calibration_labels,
        base.MIN_OFFICIAL_PROB,
    )
    incumbent_item = context['store'].get_model('CHAMPION')
    incumbent = base._current_policy_model_from_item(incumbent_item)
    gate = _evaluate_gate(
        challenger, incumbent, audit_rows, audit_labels, base, promote_challenger,
    )

    calibration_recovery = {
        'attempted': False,
        'trigger_reason': None,
        'accepted': False,
        'policy': 'PLATT_ON_INNER_VALIDATION_THEN_UNTOUCHED_AUDIT_V1',
    }
    if gate.get('reason') == 'CALIBRATION_GATE':
        calibration_recovery.update({
            'attempted': True,
            'trigger_reason': 'CALIBRATION_GATE',
            'before_gate': dict(gate),
        })
        calibrated_base = calibrate_platt(
            discovered.model,
            calibration_rows,
            calibration_labels,
        )
        calibrated, calibrated_threshold_metrics = attach_learned_threshold(
            calibrated_base,
            calibration_rows,
            calibration_labels,
            base.MIN_OFFICIAL_PROB,
        )
        calibrated_gate = _evaluate_gate(
            calibrated, incumbent, audit_rows, audit_labels, base, promote_challenger,
        )
        calibration_recovery['after_gate'] = dict(calibrated_gate)
        calibration_recovery['calibration_metadata'] = {
            key: value for key, value in (calibrated.metadata or {}).items()
            if key.startswith('calibration_') or key == 'probability_calibration'
        }
        # The untouched audit is authoritative. Use the recalibrated challenger
        # whenever it clears the calibration gate and does not degrade to a
        # different hard failure. Promotion still requires all original gates.
        if calibrated_gate.get('reason') != 'CALIBRATION_GATE':
            challenger = calibrated
            threshold_metrics = calibrated_threshold_metrics
            gate = calibrated_gate
            calibration_recovery['accepted'] = True
            calibration_recovery['result_reason'] = gate.get('reason')
        else:
            calibration_recovery['result_reason'] = 'CALIBRATION_GATE'

    manifest = {
        **dict(discovered.search_manifest or {}),
        'searchPopulationRows': len(search_rows),
        'untouchedAuditRows': len(audit_rows),
        'validationPolicy': 'nested_chronological_search_plus_untouched_audit_v1',
        'officialPickThresholdPolicy': threshold_metrics,
        'calibrationRecovery': calibration_recovery,
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
        'official_probability_threshold': threshold_metrics.get('threshold'),
        'official_threshold_source': threshold_metrics.get('threshold_source'),
        'training_lock_minutes': base.LOCK_MINUTES,
        'lock_authority_policy': base.OFFICIAL_PICK_POLICY,
        'expected_value_selection_gate': False,
        'calibration_recovery': calibration_recovery,
    }
    return {
        'challenger': challenger, 'incumbent_item': incumbent_item,
        'gate': gate, 'manifest': manifest, 'model_id': model_id,
        'artifact': artifact,
    }, None
