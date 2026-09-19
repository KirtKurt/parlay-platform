import json

import pytest

import ks1.loss_trace as subject


def _source():
    rows = []
    for gid, p_home in [('1', .60), ('2', .55), ('3', .45)]:
        evidence = {
            'bucket': 'b', 'key': f'k/{gid}', 'version_id': f'v{gid}',
            'sha256': gid * 64, 'stored_at': '2026-09-18T19:50:00+00:00'
        }
        rows.append({'evidence': evidence, 'row': {
            'game_id': gid, 'date': '2026-09-18', 'model_version': 'M', 'p_home': p_home,
            'starter_profile_json': '{}', 'lineup_bullpen_profile_json': '{}',
            'signal_contributions_json': json.dumps({'groups': {
                'starter': {'decision_influence_pct': 70 + int(gid), 'signal_score': .1 * int(gid)}
            }}),
            '_flags': [{'code': 'A' if gid != '2' else 'B', 'strength': 'medium'}],
        }})
    return {'system': 'KS1', 'as_of': '2026-09-19T05:00:00+00:00', 'locked': rows}


def _ledger(source):
    rows = []
    wins = {'1': 1, '2': 0, '3': 1}
    for entry in source['locked']:
        row = entry['row']
        gid = row['game_id']
        rows.append({
            'game_id': gid, 'signature': f's{gid}', 'home_win': wins[gid],
            'locked_at': '2026-09-18T19:50:00+00:00', 'raw_model_version': 'M',
            'p_home': row['p_home'], 'lock_evidence': entry['evidence'],
        })
    return {'system': 'KS1', 'as_of': source['as_of'], 'rows': rows}


def _patch(monkeypatch, source):
    admitted = []
    wins = {'1': 1, '2': 0, '3': 1}
    for entry in source['locked']:
        gid = entry['row']['game_id']
        admitted.append({'game_id': gid, 'signature': f's{gid}', 'home_win': wins[gid]})
    monkeypatch.setattr(subject, 'dataset', lambda source, include_predecessors: (
        admitted, {'eligible_graded_rows': 3}
    ))
    monkeypatch.setattr(subject, 'evaluate', lambda row: {
        'selected_team': 'H' if row['p_home'] >= .5 else 'A',
        'model_selected_probability': max(row['p_home'], 1 - row['p_home']),
        'severity': 'watch', 'counter_signal_points': 1, 'flags': row['_flags'],
    })


def test_build_excludes_frozen_holdout_and_summarizes_patterns(monkeypatch):
    source = _source()
    ledger = _ledger(source)
    _patch(monkeypatch, source)
    out = subject.build(source, ledger, {'3'})
    assert out['sample']['committed_ledger_rows'] == 3
    assert out['sample']['frozen_holdout_ids_excluded'] == 1
    assert out['sample']['analyzed_non_holdout_rows'] == 2
    assert out['holdout_boundary']['holdout_labels_read'] == 0
    assert out['holdout_boundary']['holdout_predictions_scored'] == 0
    assert {row['pattern'] for row in out['flag_summary']} == {'A', 'B'}
    starter = next(row for row in out['contribution_summary'] if row['group'] == 'starter')
    assert starter['win_rows'] == 1 and starter['loss_rows'] == 1
    assert out['prediction_writes'] == out['official_ledger_writes'] == 0
    assert out['model_ref_writes'] == out['lock_writes'] == 0
    assert out['trained_lightgbm'] is False


def test_build_rejects_lock_evidence_drift(monkeypatch):
    source = _source()
    ledger = _ledger(source)
    _patch(monkeypatch, source)
    source['locked'][0]['evidence'] = dict(source['locked'][0]['evidence'], version_id='advanced')
    with pytest.raises(ValueError, match='committed lock evidence'):
        subject.build(source, ledger, set())


def test_holdout_id_contract_is_exactly_300_unique(tmp_path):
    path = tmp_path / 'holdout.json'
    path.write_text(json.dumps({'game_ids': [str(i) for i in range(300)]}))
    assert len(subject.holdout_ids(path)) == 300
    path.write_text(json.dumps({'game_ids': ['x'] * 300}))
    with pytest.raises(ValueError, match='300 unique'):
        subject.holdout_ids(path)


def test_publish_uses_latest_committed_checkpoint_and_readback(monkeypatch, tmp_path):
    source = _source()
    ledger = _ledger(source)
    monkeypatch.setattr(subject, 'require_main_workflow', lambda: None)
    monkeypatch.setattr(subject, 'latest_checkpoint', lambda s3, bucket, as_of: {
        'ledger': ledger, 'state': {'night_date': '2026-09-19', 'catchup_revision': 2}
    })
    monkeypatch.setattr(subject, 'holdout_ids', lambda: set())
    monkeypatch.setattr(subject, 'build', lambda source, ledger, frozen_ids: {
        'sample': {'analyzed_non_holdout_rows': 3}, 'authority_effect': 'none'
    })
    seen = {}

    def commit(s3, bucket, key, payload):
        seen['key'] = key
        return payload, {'key': key, 'sha256': 'a' * 64}

    monkeypatch.setattr(subject, 'commit_json', commit)
    out = subject.publish(source, tmp_path / 'loss_trace.json', s3=object(), bucket='bucket')
    assert seen['key'].endswith('date=2026-09-19/catchup=000002/loss_trace.json')
    assert out['status'] == 'published'
    assert json.loads((tmp_path / 'loss_trace.json').read_text())['sample']['analyzed_non_holdout_rows'] == 3
