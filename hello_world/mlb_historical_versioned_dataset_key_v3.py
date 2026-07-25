"""Content-addressed dataset keys for repaired MLB historical slates.

Historical evidence is append-only.  A previously written legacy dataset must
never be overwritten when a corrected quarantine-aware reconstruction produces
different content.  This module routes complete-slate dataset writes to a
content-addressed key under the already authorized historical S3 prefix while
leaving raw snapshots, finals, and experiment artifacts unchanged.
"""
from __future__ import annotations

from typing import Any, Mapping

import mlb_historical_optimizer_handler as handler

VERSION = "MLB-HISTORICAL-VERSIONED-DATASET-KEY-v3"
HANDLER_VERSION = (
    "MLB-HISTORICAL-OPTIMIZER-AWS-v1.9-quarantine-ledger-complete-"
    "versioned-dataset-key-v3"
)
DATASET_RECORD_TYPE = "mlb_historical_complete_slate"


def dataset_key(value: Mapping[str, Any]) -> str:
    day = str(value.get("slateDateEt") or "unknown-date")
    digest = handler._sha256(handler._json_bytes(value))
    return (
        "mlb/historical-daily-v1/datasets-versioned/"
        f"{day}/{digest}.json"
    )


def install() -> None:
    """Install the content-addressed dataset writer exactly once."""

    if getattr(handler, "_versioned_dataset_key_v3_installed", False):
        return

    original = handler._put_immutable_json
    handler._versioned_dataset_key_v3_original_put = original

    def put_immutable_json(
        key: str,
        value: Any,
        *,
        record_type: str,
    ):
        target_key = key
        if record_type == DATASET_RECORD_TYPE and isinstance(value, Mapping):
            target_key = dataset_key(value)
        return original(target_key, value, record_type=record_type)

    handler._put_immutable_json = put_immutable_json
    handler.VERSION = HANDLER_VERSION
    handler._versioned_dataset_key_v3_installed = True
