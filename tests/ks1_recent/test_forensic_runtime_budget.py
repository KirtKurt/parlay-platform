"""Review regressions for retained inner-timeout and baseline-fit diagnostics."""
import json

import numpy as np
import pandas as pd
import pytest

from ks1 import forensic_runtime as runtime


def progress(output):
    return json.loads((output / runtime.PROGRESS_FILE).read_text())


@pytest.mark.parametrize('elapsed,remaining', [(0, 17400), (60, 17340), (4800, 12600), (17399, 1)])
def test_all_commands_share_the_original_work_budget(elapsed, remaining):
    assert runtime.remaining_evaluation_seconds('1000000', 1000000 + elapsed) == remaining


@pytest.mark.parametrize('elapsed', [17400, 18000, 100000])
def test_exhausted_work_budget_stays_failure(elapsed):
    with pytest.raises(TimeoutError, match='evaluation_work_budget_exhausted'):
        runtime.remaining_evaluation_seconds('1000000', 1000000 + elapsed)


@pytest.mark.parametrize('started,now', [(None, 1000000), ('', 1000000), ('invalid', 1000000),
                                        ('1000001', 1000000), ('0', 1000000), ('-1', 1000000),
                                        ('1.2', 1000000), ('1000000', float('nan')),
                                        ('1000000', float('inf')), ('1000000', True)])
def test_budget_clock_is_required_finite_and_not_future(started, now):
    with pytest.raises(ValueError):
        runtime.remaining_evaluation_seconds(started, now)


def test_budget_cli_returns_seconds_or_nonzero_without_a_command(monkeypatch, capsys):
    monkeypatch.setenv('KS1_EVALUATE_STARTED_EPOCH', '1000000')
    monkeypatch.setattr(runtime, 'time', lambda: 1000600)
    runtime.main(['--remaining-budget'])
    assert capsys.readouterr().out.strip() == '16800'
    monkeypatch.setattr(runtime, 'time', lambda: 1017400)
    with pytest.raises(SystemExit) as caught:
        runtime.main(['--remaining-budget'])
    assert caught.value.code == 124
    assert 'evaluation_work_budget_exhausted' in capsys.readouterr().err
    monkeypatch.delenv('KS1_EVALUATE_STARTED_EPOCH')
    with pytest.raises(SystemExit) as caught:
        runtime.main(['--remaining-budget'])
    assert caught.value.code == 2


def baseline_fixture(monkeypatch):
    import ks1.retrain_recent as recent
    rng = np.random.default_rng(55)
    frame = pd.DataFrame(rng.normal(size=(240, 3)), columns=[
        'home_starter_era_7d', 'home_lineup_ops_7d', 'home_bullpen_context_era_7d'])
    frame['home_win'] = (frame.home_starter_era_7d + frame.home_lineup_ops_7d > 0).astype(int)
    frame['game_id'] = [f'synthetic-{i}' for i in range(len(frame))]
    fit, validation = frame.iloc[:180], frame.iloc[180:]
    columns = list(frame.columns[:3])
    monkeypatch.setattr(recent, 'split_development', lambda train: (fit, validation))
    monkeypatch.setattr(recent, 'choose_features', lambda train: (columns, [], {}))
    monkeypatch.setattr(recent, 'lineup_feature', lambda c: c == columns[1])
    monkeypatch.setattr(recent, 'bullpen_context_feature', lambda c: c == columns[2])
    return frame


def test_actual_baseline_selector_traces_all_15_fits_without_changing_result(tmp_path, monkeypatch, capsys):
    from ks1 import development as baseline
    frame = baseline_fixture(monkeypatch)
    expected = baseline.select(frame)
    classifier = baseline.lgb.LGBMClassifier
    observed = []
    def traced_classifier(**params):
        checkpoint = progress(tmp_path)
        assert checkpoint['stage'] == 'trial_started'
        assert checkpoint['phase'] == 'baseline_selection'
        assert checkpoint['trials_started'] == len(observed) + 1
        assert checkpoint['trials_completed'] == len(observed)
        observed.append((checkpoint['recipe'], checkpoint['trial']))
        return classifier(**params)
    monkeypatch.setattr(baseline.lgb, 'LGBMClassifier', traced_classifier)
    invoke = runtime.trace_development_run(lambda *args: baseline.select(frame))
    actual = invoke(None, None, tmp_path)
    assert actual == expected
    assert observed == [(recipe, trial) for recipe in (
        'starter', 'starter_plus_batters', 'starter_plus_batters_and_bullpen')
        for trial in baseline.TRIALS]
    assert len(observed) == 15
    assert progress(tmp_path)['trials_completed'] == 15
    assert 'synthetic-' not in capsys.readouterr().out


def test_failed_baseline_fit_keeps_its_phase_recipe_and_trial(tmp_path, monkeypatch):
    from ks1 import development as baseline
    frame = baseline_fixture(monkeypatch)
    failure = ValueError('baseline-fit-failure')
    def fail(**params):
        raise failure
    monkeypatch.setattr(baseline.lgb, 'LGBMClassifier', fail)
    invoke = runtime.trace_development_run(lambda *args: baseline.select(frame))
    with pytest.raises(ValueError) as caught:
        invoke(None, None, tmp_path)
    assert caught.value is failure
    checkpoint = progress(tmp_path)
    assert checkpoint['stage'] == 'development_failed'
    assert checkpoint['phase'] == 'baseline_selection'
    assert checkpoint['recipe'] == 'starter' and checkpoint['trial'] == 'baseline'
    assert checkpoint['trials_started'] == 1 and checkpoint['trials_completed'] == 0


@pytest.mark.parametrize('ignore_term', [False, True])
def test_inner_timeout_leaves_parent_alive_and_checkpoint_retainable(tmp_path, ignore_term):
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import sys
    worker = '''
from pathlib import Path
import sys, time, signal
if sys.argv[2] == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
from ks1.forensic_runtime import trace_development_run, trace_model_trial
@trace_development_run
def run(a, b, output):
    with trace_model_trial('baseline_selection', [1], [2], ['x'], recipe='starter', trial='baseline'):
        time.sleep(30)
run(None, None, Path(sys.argv[1]))
'''
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'PYTHONPATH': str(root)}
    completed = subprocess.run(['timeout', '--signal=TERM', '--kill-after=1s', '3s',
                                sys.executable, '-c', worker, str(tmp_path), 'ignore' if ignore_term else 'exit'],
                               cwd=root, env=env, capture_output=True, text=True, timeout=10)
    # subprocess reports a signal as negative; a shell maps SIGKILL to 128+9.
    shell_status = completed.returncode if completed.returncode >= 0 else 128 - completed.returncode
    assert shell_status == (137 if ignore_term else 124)
    checkpoint = progress(tmp_path)
    assert checkpoint['stage'] == 'trial_started'
    assert checkpoint['phase'] == 'baseline_selection'
    assert checkpoint['trials_started'] == 1 and checkpoint['trials_completed'] == 0
    assert checkpoint['qualification_evidence'] is False
    destination = tmp_path / 'retained'
    destination.mkdir()
    shutil.copy2(tmp_path / runtime.PROGRESS_FILE, destination / runtime.PROGRESS_FILE)
    assert progress(destination) == checkpoint


def test_workflow_reserves_cleanup_without_overriding_current_timeout_or_hiding_failure():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / '.github/workflows/ks1-retrain-recent.yml').read_text()
    evaluate = text.split('  evaluate:\n', 1)[1]
    assert '    timeout-minutes: 300\n' in evaluate
    assert evaluate.count('KS1_EVALUATE_STARTED_EPOCH=$(date -u +%s)') == 1
    assert evaluate.index('Start bounded KS1 evaluation clock') < evaluate.index('actions/checkout@v4')
    budget = 'remaining=$(python -m ks1.forensic_runtime --remaining-budget)'
    prefix = 'timeout --signal=TERM --kill-after=30s "${remaining}s" python -m ks1.'
    assert evaluate.count(budget) == evaluate.count(prefix) == 4
    for module in ('retrain_recent', 'forensic_unified', 'forensic_derived_development', 'historical_lineup_season_probe'):
        assert prefix + module in evaluate
    assert 'continue-on-error' not in evaluate and '|| true' not in evaluate
    assert '--preserve-status' not in evaluate and '--foreground' not in evaluate
    assert '      - uses: actions/upload-artifact@v4\n        if: always()\n' in evaluate
    assert '            /tmp/ks1-forensic-derived/\n' in evaluate
    assert '  group: ks1-retrain-recent-${{ github.ref }}\n' in text
    assert "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n" in text
    assert "       github.head_ref == 'codex/ks1-historical-starter-bridge-20260913') ||" in evaluate
    assert runtime.WORK_BUDGET_SECONDS == 290 * 60
