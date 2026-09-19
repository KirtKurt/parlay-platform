"""Compact evidence from saved experiment files for the hosted run log.

This is reporting only; it cannot admit data, select a model, or write sources.
"""
import json


def _development_feature_evidence(development):
    """Summarize feature admission/usage for legacy and unified development reports."""
    trials = development.get('trials', {})
    # The original development selector stores one entry per recipe, then nests
    # regularization trials under that recipe. Preserve that shape exactly.
    if any(isinstance(data, dict) and ('trials' in data or 'features' in data)
           for data in trials.values()):
        admitted = {
            name: ([feature for feature in data['features']
                   if '_lineup_pitch_type_matchup_' in feature
                   and not feature.endswith('_missing')]
                   if 'features' in data else None)
            for name, data in trials.items()}
        pitch_used, matchup_used = {}, {}
        for name, data in trials.items():
            selected = data.get('selected_trial')
            chosen = data.get('trials', {}).get(selected, {}) if selected else {}
            used = chosen.get('features_used_in_splits', [])
            pitch_used[name] = [feature for feature in used
                                if '_lineup_pitch_type_matchup_' in feature
                                and not feature.endswith('_missing')]
            matchup_used[name] = [feature for feature in used
                                  if not feature.endswith('_missing') and
                                  ('_lineup_platoon_' in feature
                                   or '_lineup_pitch_type_matchup_' in feature)]
        return admitted, pitch_used, matchup_used

    # Unified forensic development stores a flat matrix of alpha/regularization
    # trials plus one top-level selected_trial. It shares one admitted feature
    # list across all trials, so report that as a single unified recipe. This is
    # reporting compatibility only; it cannot alter selection or qualification.
    if trials or development.get('admitted_features') is not None:
        features = development.get('admitted_features')
        admitted = {'unified': ([feature for feature in features
                                 if '_lineup_pitch_type_matchup_' in feature
                                 and not feature.endswith('_missing')]
                                if isinstance(features, list) else None)}
        selected = development.get('selected_trial')
        chosen = trials.get(selected, {}) if selected else {}
        used = chosen.get('features_used_in_splits', []) if isinstance(chosen, dict) else []
        pitch_used = {'unified': [feature for feature in used
                                  if '_lineup_pitch_type_matchup_' in feature
                                  and not feature.endswith('_missing')]}
        matchup_used = {'unified': [feature for feature in used
                                    if not feature.endswith('_missing') and
                                    ('_lineup_platoon_' in feature
                                     or '_lineup_pitch_type_matchup_' in feature)]}
        return admitted, pitch_used, matchup_used

    return {}, {}, {}


def artifact_summary(output):
    def read(name):
        path = output / name
        return json.loads(path.read_bytes()) if path.is_file() else {}

    metrics = read('metrics.json')
    recovery = read('statcast_recovery_report.json')
    coverage = read('historical_statcast_report.json')
    development = read('development_selection.json')
    admitted_pitch, used_pitch, used_matchup = _development_feature_evidence(development)
    decisions = {key: metrics.get(key) for key in
                 ('accepted', 'qualification_run', 'reason', 'prediction_writes',
                  'official_ledger_writes', 'input_table_sha256')}
    return {
        'decision': decisions,
        'coverage': {k: coverage.get(k) for k in
                     ('verified_physical_pitch_objects', 'verified_pitch_objects',
                      'retained_pitch_rows')},
        'recovery': {
            'method': recovery.get('recovery_method'),
            'prediction_writes': recovery.get('prediction_writes'),
            'recovered_dates': recovery.get('recovered_dates', []),
            'attempts': [{key: attempt.get(key) for key in
                          ('date', 'reason', 'verified_artifact')}
                         for attempt in recovery.get('attempts', [])],
        },
        'development': {
            'selected': development.get('selected'),
            'metrics': development.get('metrics'),
            'final_holdout_used_for_selection': development.get('final_holdout_used_for_selection'),
            # Legacy artifacts must remain unknown, not appear fully covered.
            'matchup_value_admission': development.get('matchup_value_admission'),
            'explicit_pitch_type_values_admitted': admitted_pitch,
            'explicit_pitch_type_values_used': used_pitch,
            'matchup_values_used': used_matchup,
        },
    }
