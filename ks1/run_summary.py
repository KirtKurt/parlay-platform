"""Compact evidence from saved experiment files for the hosted run log.

This is reporting only; it cannot admit data, select a model, or write sources.
"""
import json


def artifact_summary(output):
    def read(name):
        path = output / name
        return json.loads(path.read_bytes()) if path.is_file() else {}

    metrics = read('metrics.json')
    recovery = read('statcast_recovery_report.json')
    coverage = read('historical_statcast_report.json')
    development = read('development_selection.json')
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
            'matchup_values_used': {
                name: [feature for feature in data['trials'][data['selected_trial']]['features_used_in_splits']
                       if not feature.endswith('_missing') and
                       ('_lineup_platoon_' in feature or '_lineup_pitch_type_matchup_' in feature)]
                for name, data in development.get('trials', {}).items()},
        },
    }
