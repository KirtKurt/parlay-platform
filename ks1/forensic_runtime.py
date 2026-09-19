"""Local progress for forensic development; never a resume or acceptance signal.

The existing workflow already retains the development output directory on failure.
Only metadata is written here: no row values, labels, source payloads or model edits.
The trace is scoped to one development invocation; ordinary trial callers are inert.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
from pathlib import Path
from time import perf_counter

_STATE = ContextVar('ks1_forensic_runtime', default=None)
PROGRESS_FILE = 'development_progress.json'


def _record(state, stage, **details):
    event = {
        'contract': 'KS1-forensic-runtime-v1',
        'stage': stage,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': round(perf_counter() - state['started'], 6),
        'trials_started': state['trials_started'],
        'trials_completed': state['trials_completed'],
        'qualification_evidence': False,
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


def trace_trial(function):
    """Record each unchanged trial's start/end so a timeout identifies its position."""
    @wraps(function)
    def wrapped(fit, validation, columns, params):
        state = _STATE.get()
        if state is None:
            return function(fit, validation, columns, params)
        state['trials_started'] += 1
        trial_number = state['trials_started']
        started = perf_counter()
        details = {'trial_number': trial_number, 'fit_rows': len(fit),
                   'validation_rows': len(validation), 'feature_count': len(columns)}
        _record(state, 'trial_started', **details)
        try:
            result = function(fit, validation, columns, params)
        except BaseException as exc:
            _record_failure(state, 'trial_failed', exc)
            raise
        state['trials_completed'] += 1
        _record(state, 'trial_completed', trial_seconds=round(perf_counter()-started, 6),
                **details)
        return result
    return wrapped
