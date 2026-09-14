"""Retain the last completed stage even if a long-running job is interrupted."""
from datetime import datetime, timezone

from ks1.inventory import encode


def record_progress(output, stage, **details):
    output.mkdir(parents=True, exist_ok=True)
    event = {'stage': stage, 'at_utc': datetime.now(timezone.utc).isoformat(), **details}
    payload = encode(event)
    (output/'progress.json').write_bytes(payload)
    print(payload.decode(), flush=True)
