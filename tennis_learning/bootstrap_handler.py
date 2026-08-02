from __future__ import annotations

from typing import Any, Mapping

import backfill


def lambda_handler(event: Mapping[str, Any], context: Any):
    return backfill.lambda_handler(event, context)
