import importlib
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hello_world"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_SESSION_TOKEN", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("SNAPSHOTS_TABLE", "")
os.environ.setdefault("SIGNAL_LEDGER_TABLE", "")
os.environ.setdefault("MLB_PULL_START_AT_ET", "01:00")

module = importlib.import_module("mlb_manual_pull")
eastern = ZoneInfo("America/New_York")


def fixed_datetime(value: datetime):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return value.replace(tzinfo=None)
            return value.astimezone(tz)

    return FixedDateTime


module.MLB_PULL_START_AT_ET = "01:00"

before_start = datetime(2026, 7, 18, 0, 45, tzinfo=eastern)
parsed = module._parse_start_at_et(before_start)
assert parsed == datetime(2026, 7, 18, 1, 0, tzinfo=eastern), parsed

original_datetime = module.datetime
try:
    module.datetime = fixed_datetime(before_start)
    blocked = module._scheduled_start_gate({}, {})
    assert blocked and blocked.get("skipped") is True, blocked
    assert blocked.get("reason") == "WAITING_FOR_CONFIGURED_1AM_ET_START_GATE", blocked

    at_start = datetime(2026, 7, 18, 1, 0, tzinfo=eastern)
    module.datetime = fixed_datetime(at_start)
    assert module._scheduled_start_gate({}, {}) is None

    after_start = datetime(2026, 7, 18, 23, 0, tzinfo=eastern)
    module.datetime = fixed_datetime(after_start)
    assert module._scheduled_start_gate({}, {}) is None
finally:
    module.datetime = original_datetime

module.MLB_PULL_START_AT_ET = "2026-07-18T01:00:00-04:00"
legacy = module._parse_start_at_et(before_start)
assert legacy == datetime(2026, 7, 18, 1, 0, tzinfo=eastern), legacy

print("MLB daily 1:00 AM ET pull-start gate verification passed.")
