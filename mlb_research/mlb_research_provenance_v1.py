"""Bind research experiments to their executable source and numeric runtime.

Data and hyperparameters alone do not identify an experiment. Keep this
manifest in PROTOCOL so the existing content-addressed experiment registry
cannot silently reuse a result from different research code. No AWS calls,
network requests, timestamps, credentials, or deployment IDs enter the hash.
"""
import hashlib
import json
import platform
from pathlib import Path

VERSION = 'MLB-RESEARCH-IMPLEMENTATION-v1'
SOURCE_FILES = (
    'mlb_player_windows_v1.py',
    'mlb_research_dataset_v1.py',
    'mlb_research_models_v1.py',
    'mlb_research_provenance_v1.py',
    'mlb_research_runtime_v1.py',
    'mlb_research_signals_v1.py',
    'mlb_research_sources_v1.py',
    'mlb_research_store_v1.py',
    'requirements.txt',
)


def _digest(value: dict) -> str:
    body = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(body).hexdigest()


def implementation_manifest(root: Path | None = None) -> dict:
    """Return a deterministic manifest, failing closed on absent source.

    ``root`` supports offline tests; production defaults to this module's
    directory. Never substitute a Git SHA: unrelated sports and report
    commits must not invalidate an otherwise identical research experiment.
    """
    import numpy

    directory = Path(root) if root is not None else Path(__file__).resolve().parent
    files = {}
    for name in SOURCE_FILES:
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError('research provenance requires a regular source file: ' + name)
        body = path.read_bytes()
        if not body:
            raise ValueError('research provenance requires nonempty source: ' + name)
        files[name] = hashlib.sha256(body).hexdigest()
    manifest = {
        'version': VERSION,
        'files': files,
        'runtime': {
            'python': platform.python_version(),
            'pythonImplementation': platform.python_implementation(),
            'numpy': numpy.__version__,
            'machine': platform.machine(),
        },
    }
    return {**manifest, 'sha256': _digest(manifest)}
