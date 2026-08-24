"""Deterministic Lambda-style imports and AWS defaults for offline tests.

Production Lambda receives its region, credentials, and flattened code root from
AWS. Repository tests import the same modules through ``hello_world`` and
``mlb_auto_llm`` as package/source roots, so CI provides equivalent non-network
defaults before collection.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
MLB_AUTO_LLM = ROOT / "mlb_auto_llm"
SCRIPTS = ROOT / "scripts"
for path in (HELLO_WORLD, MLB_AUTO_LLM, SCRIPTS):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")

# Import the same compatibility layer used by the runtime runner so direct tests
# exercise stable identities, the latched evidence window, and DynamoDB-safe writes.
try:
    import mlb_v8_observational_audit_v1_3  # noqa: F401
except Exception:
    pass
