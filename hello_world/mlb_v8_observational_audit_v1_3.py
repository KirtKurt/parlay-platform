"""DynamoDB serialization hardening for the MLB V8 observational audit."""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Mapping

import mlb_v8_observational_audit_v1 as _core
import mlb_v8_observational_audit_v1_2 as _latched


VERSION = "MLB-V8-OBSERVATIONAL-AUDIT-v1.3-ddb-safe-pointers"
_ORIGINAL_WRITE_POINTER = _core._write_pointer


def to_ddb(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("observational pointer contains a non-finite float")
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {str(key): to_ddb(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_ddb(item) for item in value]
    return value


def _write_pointer(
    table: Any,
    *,
    previous_revision: int,
    data: Mapping[str, Any],
    created_at: str,
) -> int:
    return _ORIGINAL_WRITE_POINTER(
        table,
        previous_revision=previous_revision,
        data=to_ddb(data),
        created_at=created_at,
    )


_core.VERSION = VERSION
_latched.VERSION = VERSION
_core._write_pointer = _write_pointer
_latched._core._write_pointer = _write_pointer

for _name in dir(_latched):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_latched, _name)

globals()["VERSION"] = VERSION
globals()["to_ddb"] = to_ddb
globals()["_write_pointer"] = _write_pointer
