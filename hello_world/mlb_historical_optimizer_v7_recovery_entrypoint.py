"""Historical-only V7 recovery entrypoint.

This wrapper deliberately deploys only to the separate historical optimizer stack.
It does not alter the production MLB prediction runtime or bypass its release gate.
"""
from __future__ import annotations

from typing import Any, Dict

import mlb_historical_feature_rematerialization_v1 as rematerialization
import mlb_historical_optimizer_entrypoint as base

VERSION = "MLB-HISTORICAL-V7-RECOVERY-ENTRYPOINT-v2"


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    # Rebuild the already-collected slates first. This operation uses only immutable
    # S3 archives and must remain possible even when a later range extension is
    # temporarily blocked by schedule or quota validation.
    migration = rematerialization.run_once()
    if migration is not None:
        migration.setdefault("version", VERSION)
        return migration

    # After the feature migration has completed, append the strictly later,
    # deployment-authorized ledger and let the normal orchestrator resume ingestion.
    base._append_authorized_range_extension()
    return base.lambda_handler(event, context)
