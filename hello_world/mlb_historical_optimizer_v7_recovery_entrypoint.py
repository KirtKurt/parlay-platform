"""Historical-only V7 recovery entrypoint.

This wrapper deliberately deploys only to the separate historical optimizer stack.
It does not alter the production MLB prediction runtime or bypass its release gate.
"""
from __future__ import annotations

from typing import Any, Dict

import mlb_historical_feature_rematerialization_v1 as rematerialization
import mlb_historical_optimizer_entrypoint as base

VERSION = "MLB-HISTORICAL-V7-RECOVERY-ENTRYPOINT-v1"


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    # Extend the already authorized ledger first so the post-migration state can
    # resume into the strictly later fresh-audit window.
    base._append_authorized_range_extension()
    migration = rematerialization.run_once()
    if migration is not None:
        migration.setdefault("version", VERSION)
        return migration
    return base.lambda_handler(event, context)
