"""Local progress for forensic development; never a resume or acceptance signal.

The existing workflow already retains the development output directory on failure.
Only metadata is written here: no row values, labels, source payloads or model edits.
The trace is scoped to one development invocation; ordinary trial callers are inert.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import argparse
import hashlib
import inspect
import json
import math
import os
import re
from pathlib import Path
from time import perf_counter, time

_STATE = ContextVar('ks1_forensic_runtime', default=None)
# Reuse the existing wall-clock-only progress filename already excluded by
# ks1.train.save_artifact(), so diagnostics remain in Actions artifacts without
# changing checksum-derived experiment identity or S3 experiment contents.
PROGRESS_FILE = 'progress.json'
WORK_BUDGET_SECONDS = 290 * 60  # Reserve cleanup inside the current 300-minute job.


def _digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _frame_identity(frame):
    try:
        if hasattr(frame, 'columns') and 'game_id' in frame.columns:
            values = [str(value) for value in frame['game_id'].tolist()]
        else:
            values = [str(value) for value in frame.index.tolist()]
    except Exception:
        values = [f'rows:{len(frame)}']
    return _digest(values)


def _candidate_identity(columns):
    return 'features:' + _digest([str(column) for column in columns])[:16]


def _fold_identity(fit, validation):
    return 'fold:' + _digest([_frame_identity(fit), _frame_identity(validation)])[:16]


def _known_trial(params):
    """Name an unchanged prespecified trial; hash only genuinely custom parameters."""
    try:
        from ks1.development import TRIALS
        from ks1.train import PARAMS
        current = dict(params)
        for name, updates in TRIALS.items():
            if current == {**PARAMS, **updates}:
                return name
    except Exception:
        pass
    return 'params:' + _digest(dict(params))[:16]


def _caller_source():
    """Return a source-location identifier without logging frame data."""
    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back else None
        if caller is None:
            return None
        module = caller.f_globals.get('__name__', '<unknown>')
        return f'{module}:{caller.f_code.co_name}:{caller.f_lineno}'
    finally:
        del frame


def _record(state, stage, **details):
    event = {
        'contract': 'KS1-forensic-runtime-v1',
        'stage': stage,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': round(perf_counter() - state['started'], 6),
        'trials_started': state['trials_started'],
        'trials_completed': state['trials_completed'],
        'qualification_evidence': False,
        **state.get('last_trial', {}),
        **details,
    }
    payload = (json.dumps(event, sort_keys=True, allow_nan=False) + '\n').encode()
    destination = state['output'] / PROGRESS_FILE
    temporary = destination.with_suffix('.tmp')
    temporary.write_bytes(payload)
    temporary.replace(destination)
    print(payload.decode(), end='', flush=True)


def _record_failure(state, stage, exc):
    # Preserve the original computation exception even if its diagnostic write
    # also fails. The last started checkpoint remains incomplete, never success.
    try:
        _record(state, stage, exception_type=type(exc).__name__)
    except Exception:
        pass


def trace_development_run(function):
    """Keep interrupted development visible without changing its return value."""
    @wraps(function)
    def wrapped(input_path, proof_path, output):
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        state = {'output': output, 'started': perf_counter(),
                 'trials_started': 0, 'trials_completed': 0}
        token = _STATE.set(state)
        try:
            _record(state, 'development_started')
            try:
                result = function(input_path, proof_path, output)
            except BaseException as exc:
                _record_failure(state, 'development_failed', exc)
                raise
            # Execution completion is not candidate acceptance or qualification.
            _record(state, 'development_completed')
            return result
        finally:
            _STATE.reset(token)
    return wrapped


@contextmanager
def trace_model_trial(phase, fit, validation, columns, *, recipe=None, trial=None,
                      candidate=None, fold=None, source=None):
    """Trace an existing fit/predict/score block without selecting or changing it."""
    state = _STATE.get()
    if state is None:
        yield
        return
    state['trials_started'] += 1
    started = perf_counter()
    state['last_trial'] = {
        'phase': phase,
        'trial_number': state['trials_started'],
        'fit_rows': len(fit),
        'validation_rows': len(validation),
        'feature_count': len(columns),
        'recipe': recipe,
        'trial': trial,
        'candidate': candidate or recipe or _candidate_identity(columns),
        'fold': fold or _fold_identity(fit, validation),
        'source': source,
    }
    _record(state, 'trial_started')
    try:
        yield
    except BaseException as exc:
        _record_failure(state, 'trial_failed', exc)
        raise
    state['trials_completed'] += 1
    _record(state, 'trial_completed', trial_seconds=round(perf_counter()-started, 6))


def trace_trial(function):
    """Record unchanged forensic trials with deterministic candidate/fold/trial IDs."""
    @wraps(function)
    def wrapped(fit, validation, columns, params):
        # Outside the explicitly scoped development run this decorator must be a
        # transparent no-op, including for test/synthetic callers with sentinel
        # arguments. Do not inspect or hash anything until tracing is active.
        if _STATE.get() is None:
            return function(fit, validation, columns, params)
        with trace_model_trial(
                'forensic', fit, validation, columns,
                trial=_known_trial(params),
                candidate=_candidate_identity(columns),
                fold=_fold_identity(fit, validation),
                source=_caller_source()):
            return function(fit, validation, columns, params)
    return wrapped


def remaining_evaluation_seconds(started_at, now):
    """Shared job work budget, not a fresh timeout allowance for each command."""
    if not isinstance(started_at, str) or not re.fullmatch(r'[0-9]{1,12}', started_at):
        raise ValueError('missing_or_invalid_evaluation_clock')
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError('invalid_current_evaluation_clock')
    started = int(started_at)
    current = int(now)
    if started <= 0 or current < started:
        raise ValueError('future_or_invalid_evaluation_clock')
    remaining = WORK_BUDGET_SECONDS - (current - started)
    if remaining <= 0:
        raise TimeoutError('evaluation_work_budget_exhausted')
    return remaining


def main(argv=None):
    parser = argparse.ArgumentParser(description='Return the existing job work budget remaining.')
    parser.add_argument('--remaining-budget', action='store_true', required=True)
    parser.parse_args(argv)
    try:
        remaining = remaining_evaluation_seconds(os.environ.get('KS1_EVALUATE_STARTED_EPOCH'), time())
    except TimeoutError:
        parser.exit(124, 'evaluation_work_budget_exhausted\n')
    except ValueError as exc:
        parser.error(str(exc))
    print(remaining, flush=True)


if __name__ == '__main__':
    main()
