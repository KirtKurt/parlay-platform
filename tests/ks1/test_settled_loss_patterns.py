from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
import types

import pytest

from ks1 import settled_loss_patterns as subject


def evidence():
    start = datetime(2026, 9, 17, 18, tzinfo=timezone.utc)
    as_of = (start-timedelta(minutes=30)).isoformat()
    proof = {'bucket': 'test-bucket', 'key': 'mlb/ks1/predictions-v1/date=2026-09-17/predictions.parquet',
             'version_id': 'immutable-version', 'sha256': 'a'*64,
             'stored_at': (start-timedelta(minutes=20)).isoformat()}
    r = {'game_id': 'G1', 'date': '2026-09-17', 'as_of': as_of,
         'commence_time': start.isoformat(), 'home_id': '1', 'away_id': '2',
         'home_team': 'Home', 'away_team': 'Away', 'home_starter_id': '11', 'away_starter_id': '22',
         'p_home': .6, 'p_raw': .6, 'model_version': 'M1', 'market_home_prob': .6,
         'home_lineup_status': 'confirmed', 'away_lineup_status': 'confirmed'}
    starter = {'as_of': as_of, 'sha256': 'b'*64, 'sides': {}}
    context = {'as_of': as_of, 'sha256': 'c'*64, 'game_id': 'G1', 'commence_time': start.isoformat(), 'sides': {}}
    for side, pid, tid in [('home', '11', '1'), ('away', '22', '2')]:
        starter['sides'][side] = {'starter_id': pid, 'metrics': {
            'era_7d': 3., 'era_30d': 3., 'fip_7d': 3., 'fip_30d': 3.,
            'xwoba_7d': .3, 'xwoba_30d': .3, 'expected_innings_last5': 5.}}
        context['sides'][side] = {'team_id': tid, 'features': {
            'lineup_ops_7d': .7, 'lineup_xwoba_7d': .3, 'lineup_top4_ops': .7,
            'bullpen_context_fip_7d': 3., 'bullpen_context_era_7d': 3.},
            'reliever_history': [{'player_id': tid+'0', 'availability_state': 'UNKNOWN', 'windows': {
                w: {'appearances': 3, 'fip': 3., 'era': 3., 'xwoba': .3, 'k_bb_pct': 15.} for w in subject.WINDOWS}}]}
    r.update(starter_profile_json=json.dumps(starter), starter_profile_sha256='b'*64,
             lineup_bullpen_profile_json=json.dumps(context), lineup_bullpen_profile_sha256='c'*64)
    contribution = {'additivity_verified': True, 'bias': 0., 'raw_score': math.log(.6/.4),
                    'groups': {'starter': {'signal_score': math.log(.6/.4)}}}
    r['signal_contributions_json'] = json.dumps(contribution)
    final = {'home_id': '1', 'away_id': '2', 'home_score': 1, 'away_score': 3,
             'completed_at': (start+timedelta(hours=3)).isoformat(),
             'observed_at': (start+timedelta(hours=4)).isoformat()}
    grade = {'game_id': 'G1', 'as_of': as_of, 'locked_at': (start-timedelta(minutes=10)).isoformat(),
             'graded_at': (start+timedelta(hours=4)).isoformat(), 'p_home': .6, 'p_raw': .6,
             'raw_model_version': 'M1', 'official_probability_field': 'p_home',
             'lock_evidence': deepcopy(proof), 'home_win': 0, 'home_score': 1, 'away_score': 3,
             'final_evidence': [dict(proof, key='prior-games.json')]}
    capture_time = (start+timedelta(hours=5)).isoformat()
    source = {'system': 'KS1', 'as_of': capture_time, 'locked': [{'row': r, 'evidence': proof}],
              'finals': {'G1': final}}
    ledger = {'system': 'KS1', 'as_of': capture_time, 'rows': [grade]}
    source['committed_ledger'] = deepcopy(ledger)
    return source, ledger


def adjust_profile(source, field, change):
    row = source['locked'][0]['row']
    p = json.loads(row[field+'_json']); change(p)
    row[field+'_json'] = json.dumps(p)


def test_loss_and_win_are_retained_without_mutation():
    source, ledger = evidence(); originals = deepcopy((source, ledger))
    out = subject.build(source, ledger, set())
    assert (source, ledger) == originals
    assert out['summary']['losses'] == 1
    r = out['observations'][0]
    assert r['residual'] == -.6 and r['brier'] == .36
    assert r['active_model_attribution']['status'] == 'verified_against_locked_raw_probability'
    source['finals']['G1']['home_score'] = ledger['rows'][0]['home_score'] = 4
    ledger['rows'][0]['home_win'] = 1
    out = subject.build(source, ledger, set())
    assert out['summary']['wins'] == 1 and out['summary']['losses'] == 0
    assert out['observations'][0]['residual'] == .4
    for key in ('prediction_writes', 'official_ledger_writes', 'lock_writes', 'model_ref_writes', 'provider_calls'):
        assert out[key] == 0
    assert out['qualification_run'] is False and out['trained_LightGBM'] is False


def test_holdout_is_excluded_before_access_to_labels_or_profiles():
    source, ledger = evidence()
    ledger['rows'].append({'game_id': 'FORBIDDEN', 'home_win': 'INVALID_DO_NOT_READ'})
    source['locked'].append({'row': {'game_id': 'FORBIDDEN', 'starter_profile_json': 'INVALID_DO_NOT_READ'}})
    out = subject.build(source, ledger, {'FORBIDDEN'})
    assert out['frozen_holdout_games_excluded'] == 1
    assert out['summary']['n'] == 1
    assert 'INVALID_DO_NOT_READ' not in json.dumps(out)


@pytest.mark.parametrize('target', ['grades', 'locks'])
def test_duplicate_inventory_fails_closed(target):
    source, ledger = evidence()
    rows = ledger['rows'] if target == 'grades' else source['locked']
    rows.append(deepcopy(rows[0]))
    with pytest.raises(ValueError, match='duplicate_game_identity'):
        subject.build(source, ledger, set())


@pytest.mark.parametrize('field,value,error', [
    ('p_home', .7, 'official_lock_row_mismatch'),
    ('p_raw', .7, 'official_lock_row_mismatch'),
    ('raw_model_version', 'OTHER', 'official_model_mismatch'),
    ('official_probability_field', 'p_raw', 'not_official_probability'),
    ('home_win', 1, 'final_label_mismatch'),
    ('home_win', True, 'final_label_mismatch'),
    ('home_score', 99, 'final_label_mismatch'),
    ('locked_at', '2026-09-17T17:51:00+00:00', 'lock_or_label_chronology_mismatch'),
    ('graded_at', '2026-09-17T18:00:00+00:00', 'final_completion_mismatch'),
    ('graded_at', '2026-09-18T23:00:00+00:00', 'lock_or_label_chronology_mismatch'),
])
def test_official_binding_errors_rejected(field, value, error):
    source, ledger = evidence(); ledger['rows'][0][field] = value
    with pytest.raises(ValueError, match=error):
        subject.build(source, ledger, set())


def test_advanced_lock_receipt_is_not_substituted():
    source, ledger = evidence(); source['locked'][0]['evidence']['version_id'] = 'newer'
    with pytest.raises(ValueError, match='official_lock_receipt_mismatch'):
        subject.build(source, ledger, set())


@pytest.mark.parametrize('change,error', [
    (lambda p: p.update(as_of='2026-09-17T18:01:00+00:00'), 'profile_time_mismatch'),
    (lambda p: p.update(history_as_of='2026-09-17T18:01:00+00:00'), 'future_profile_source'),
    (lambda p: p.update(sha256='d'*64), 'profile_receipt_mismatch'),
    (lambda p: p['sides']['home'].update(starter_id='wrong'), 'profile_starter_mismatch'),
])
def test_starter_profile_provenance(change, error):
    source, ledger = evidence(); adjust_profile(source, 'starter_profile', change)
    with pytest.raises(ValueError, match=error):
        subject.build(source, ledger, set())


def test_wrong_game_or_team_context_fails_closed():
    source, ledger = evidence()
    adjust_profile(source, 'lineup_bullpen_profile', lambda p: p.update(game_id='OTHER'))
    with pytest.raises(ValueError, match='profile_game_mismatch'):
        subject.build(source, ledger, set())


def test_missing_profile_is_unknown_not_negative_control():
    source, ledger = evidence(); source['locked'][0]['row']['starter_profile_json'] = None
    out = subject.build(source, ledger, set())
    r = out['observations'][0]
    assert r['patterns']['SELECTED_STARTER_RECENT_DETERIORATION'] is None
    p = next(p for p in out['patterns'] if p['pattern'] == 'SELECTED_STARTER_RECENT_DETERIORATION')
    assert p['unknown_count'] == 1 and p['controls']['n'] == 0


def test_partial_or_evidence_keeps_unknown_unless_positive():
    source, ledger = evidence()
    adjust_profile(source, 'starter_profile', lambda p: p['sides']['home']['metrics'].update(xwoba_7d=None))
    assert subject.build(source, ledger, set())['observations'][0]['patterns']['SELECTED_STARTER_RECENT_DETERIORATION'] is None
    adjust_profile(source, 'starter_profile', lambda p: p['sides']['home']['metrics'].update(era_7d=6.))
    assert subject.build(source, ledger, set())['observations'][0]['patterns']['SELECTED_STARTER_RECENT_DETERIORATION'] is True


def test_projected_lineup_not_confirmed_performance():
    source, ledger = evidence(); source['locked'][0]['row']['home_lineup_status'] = 'projected'
    adjust_profile(source, 'lineup_bullpen_profile', lambda p: p['sides']['away']['features'].update(lineup_ops_7d=1.))
    out = subject.build(source, ledger, set())
    assert out['observations'][0]['patterns']['OPPONENT_LINEUP_OPS_ADVANTAGE'] is None


def test_reliever_priors_are_not_observed_performance():
    source, ledger = evidence()
    adjust_profile(source, 'lineup_bullpen_profile', lambda p: p['sides']['home']['reliever_history'][0]['windows']['7d'].update(appearances=0, fip=9., k_bb_pct=15.))
    r = subject.build(source, ledger, set())['observations'][0]
    assert r['patterns']['OPPONENT_RELIEVER_R1_FIP_ADVANTAGE'] is None
    assert r['individual_relievers']['home'][0]['windows']['7d']['k_bb_pct'] is None
    assert r['individual_relievers']['away'][0]['availability_state'] == 'UNKNOWN'


def test_reliever_ranking_uses_prior_appearances_not_final_usage():
    source, ledger = evidence()
    def change(p):
        entries = p['sides']['home']['reliever_history']
        entries.append(deepcopy(entries[0])); entries[1]['player_id'] = '09'
    adjust_profile(source, 'lineup_bullpen_profile', change)
    r = subject.build(source, ledger, set())['observations'][0]
    assert [r['player_id'] for r in r['individual_relievers']['home']] == ['09', '10']


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), True])
def test_nonfinite_or_boolean_signal_rejected(value):
    source, ledger = evidence()
    adjust_profile(source, 'lineup_bullpen_profile', lambda p: p['sides']['home']['features'].update(bullpen_context_fip_7d=value))
    with pytest.raises(ValueError, match='numeric_signal'):
        subject.build(source, ledger, set())


def test_shap_must_match_original_model_probability():
    source, ledger = evidence(); row = source['locked'][0]['row']
    parsed = json.loads(row['signal_contributions_json']); parsed['raw_score'] = 1.
    row['signal_contributions_json'] = json.dumps(parsed)
    with pytest.raises(ValueError, match='attribution_additivity_mismatch'):
        subject.build(source, ledger, set())
    parsed['groups']['starter']['signal_score'] = 1.; row['signal_contributions_json'] = json.dumps(parsed)
    with pytest.raises(ValueError, match='attribution_probability_mismatch'):
        subject.build(source, ledger, set())


def test_active_starter_opposition_is_separate_from_diagnostic_form():
    source, ledger = evidence(); row = source['locked'][0]['row']
    parsed = json.loads(row['signal_contributions_json'])
    parsed['groups']['starter']['signal_score'] = -.1
    parsed['bias'] = parsed['raw_score']+.1
    row['signal_contributions_json'] = json.dumps(parsed)
    r = subject.build(source, ledger, set())['observations'][0]
    assert r['patterns']['ACTIVE_STARTER_OPPOSES_PICK'] is True
    assert r['patterns']['SELECTED_STARTER_RECENT_DETERIORATION'] is False


def test_summary_never_mixes_models_or_missing_values():
    source, ledger = evidence(); r = subject.build(source, ledger, set())['observations'][0]
    second = deepcopy(r); second.update(game_id='2', model_version='M2', hit=1, residual=.4)
    ps = subject.summarize([r, second])
    assert {p['model_version'] for p in ps} == {'M1', 'M2'}
    assert all(p['exposed']['n']+p['controls']['n']+p['unknown_count'] == 1 for p in ps)
    assert all(p['automatic_probability_adjustment'] is False for p in ps)


def test_publish_is_content_addressed_readback_only(monkeypatch):
    module = types.ModuleType('ks1.calibration_store'); calls = []
    def commit(s3, bucket, key, value):
        calls.append(key)
        return value, {'bucket': bucket, 'key': key, 'version_id': 'v', 'sha256': 'a'*64}
    module.commit_json = commit
    monkeypatch.setitem(sys.modules, 'ks1.calibration_store', module)
    out = subject.publish({'contract': subject.CONTRACT}, object(), 'bucket')
    assert out['aws_readback_verified'] is True
    assert len(calls) == 1 and calls[0].startswith(subject.PREFIX)
    assert '/latest' not in calls[0]


def test_publication_guard_does_not_accept_pr_context(monkeypatch):
    for key, value in {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'KirtKurt/parlay-platform',
                       'GITHUB_REF': 'refs/pull/1/merge', 'GITHUB_EVENT_NAME': 'pull_request'}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match='requires_existing_main_workflow'):
        subject.require_workflow()


def test_main_reads_committed_ledger_on_no_new_grades(tmp_path):
    source, ledger = evidence(); source['committed_ledger'] = ledger
    root = tmp_path/'input'; root.mkdir(); output = tmp_path/'out'
    (root/'capture.json').write_text(json.dumps(source))
    (root/'report.json').write_text(json.dumps({'status': 'no_new_final_grades', 'ledger_rows': 1}))
    subject.main(['--root', str(root), '--output', str(output)])
    assert json.loads((output/'report.json').read_text())['summary']['n'] == 1
    assert json.loads((output/'status.json').read_text())['published'] is False


def test_core_audit_failure_cannot_be_hidden(tmp_path):
    root = tmp_path/'input'; root.mkdir(); output = tmp_path/'out'
    (root/'report.json').write_text(json.dumps({'status': 'source_refresh_not_verified'}))
    with pytest.raises(ValueError, match='nightly_not_successful'):
        subject.main(['--root', str(root), '--output', str(output)])
    assert json.loads((output/'failure.json').read_text())['status'] == 'blocked'
    assert not (output/'status.json').exists()


def test_not_due_is_not_fabricated_empty_learning_success(tmp_path):
    root = tmp_path/'input'; root.mkdir(); output = tmp_path/'out'
    (root/'report.json').write_text(json.dumps({'status': 'not_due_or_already_completed'}))
    subject.main(['--root', str(root), '--output', str(output)])
    assert json.loads((output/'status.json').read_text())['status'] == 'nightly_not_due'
    assert not (output/'report.json').exists()


def test_slate_date_cannot_move_a_game_between_descriptive_blocks():
    source, ledger = evidence(); source['locked'][0]['row']['date'] = '2026-09-16'
    with pytest.raises(ValueError, match='slate_date_mismatch'):
        subject.build(source, ledger, set())


@pytest.mark.parametrize('count', [-1, .5])
def test_invalid_reliever_counts_are_not_performance(count):
    source, ledger = evidence()
    adjust_profile(source, 'lineup_bullpen_profile', lambda p: p['sides']['home']['reliever_history'][0]['windows']['7d'].update(appearances=count))
    with pytest.raises(ValueError, match='invalid_reliever_appearances'):
        subject.build(source, ledger, set())


def test_publication_error_remains_an_error(monkeypatch):
    module = types.ModuleType('ks1.calibration_store')
    def commit(*args):
        raise PermissionError('denied')
    module.commit_json = commit
    monkeypatch.setitem(sys.modules, 'ks1.calibration_store', module)
    with pytest.raises(PermissionError, match='denied'):
        subject.publish({'contract': subject.CONTRACT}, object(), 'bucket')


def test_readback_mismatch_cannot_claim_published(monkeypatch):
    module = types.ModuleType('ks1.calibration_store')
    module.commit_json = lambda *args: ({'wrong': True}, {})
    monkeypatch.setitem(sys.modules, 'ks1.calibration_store', module)
    with pytest.raises(ValueError, match='loss_pattern_readback_mismatch'):
        subject.publish({'contract': subject.CONTRACT}, object(), 'bucket')


def test_workflow_is_a_separate_same_run_consumer_not_a_serving_dependency():
    # Phase 1 intentionally installs only the data-engine dependencies. Keep this
    # source-contract check stdlib-only instead of adding a YAML runtime dependency.
    import re
    path = Path(__file__).resolve().parents[2]/'.github/workflows/mlb-research-ingestion.yml'
    text = path.read_text()
    header, body = text.split('\njobs:\n', 1)
    headers = list(re.finditer(r'^  ([a-zA-Z0-9_-]+):\n', body, re.MULTILINE))
    assert len({m[1] for m in headers}) == len(headers)
    jobs = {m[1]: body[m.end():headers[i+1].start() if i+1 < len(headers) else len(body)]
            for i, m in enumerate(headers)}
    job, ingest = jobs['loss-patterns'], jobs['ingest']
    assert '    needs: ingest\n' in job and 'continue-on-error:' not in job
    assert ("    if: ${{ !cancelled() && github.ref == 'refs/heads/main' && "
            "needs.ingest.outputs.nightly_outcome == 'success' }}\n") in job
    assert '    outputs:\n      nightly_outcome: ${{ steps.nightly.outcome }}\n' in ingest
    assert 'loss-patterns' not in ingest and '    needs:' not in ingest
    assert ('        uses: actions/download-artifact@v4\n'
            '        with:\n'
            '          name: ks1-nightly-${{ github.run_id }}\n'
            '          path: /tmp/ks1-nightly\n') in job
    assert 'permissions:\n  contents: read\n  actions: read\nconcurrency:' in header
    assert '  cancel-in-progress: false\n' in header+'\n'
    assert ('        run: python -m ks1.settled_loss_patterns --root /tmp/ks1-nightly '
            '--output /tmp/ks1-loss-patterns --publish\n') in job
    assert '          ref: ${{ github.sha }}\n' in job
    # Existing failed-audit protection and independent pregame refresh remain.
    daily = ingest.split('        id: daily\n', 1)[1].split('      - name:', 1)[0]
    assert "        if: ${{ !cancelled() && steps.research.outcome == 'success' }}\n" in daily
