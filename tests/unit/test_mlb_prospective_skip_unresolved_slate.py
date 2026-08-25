import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'hello_world'))
import mlb_ml_aws_training_v1 as trainer


def test_unresolved_slate_is_date_local_fail_closed_not_global_blocker():
    source = inspect.getsource(trainer._contiguous_finalized_slate_prefix)
    assert 'skipped_unresolved_slate_dates.append(slate_date)' in source
    assert 'unresolved_slate_errors[slate_date]' in source
    assert 'continue' in source
    assert '"skippedUnresolvedSlateDates": skipped_unresolved_slate_dates' in source
    assert '"unresolvedSlateErrors": unresolved_slate_errors' in source
    old_global_block = '''blocked_date = slate_date\n            blocker = f"OFFICIAL_SLATE_UNRESOLVED:{type(exc).__name__}:{exc}"\n            break'''
    assert old_global_block not in source
