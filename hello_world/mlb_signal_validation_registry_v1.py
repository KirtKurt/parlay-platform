from __future__ import annotations

import copy
import hashlib
import json
from types import MappingProxyType
from typing import Any, Dict, Optional


VERSION = "MLB-SIGNAL-VALIDATION-REGISTRY-v1-code-reviewed-only"

# Intentionally empty. A cohort is added only after its exact frozen signature
# passes prospective, outcome-untouched validation and a code-reviewed release.
_APPROVED_RECORDS: Dict[str, Dict[str, Any]] = {}
APPROVED_RECORDS = MappingProxyType(_APPROVED_RECORDS)


def _canonical_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        str(key): value
        for key, value in sorted(record.items(), key=lambda item: str(item[0]))
        if key != "recordFingerprint"
    }


def record_fingerprint(record: Dict[str, Any]) -> str:
    source = json.dumps(
        _canonical_record(record if isinstance(record, dict) else {}),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def get_record(signal_signature: Any) -> Optional[Dict[str, Any]]:
    record = APPROVED_RECORDS.get(str(signal_signature or ""))
    return copy.deepcopy(record) if isinstance(record, dict) else None


def record_is_trusted(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    signature = str(record.get("signalSignature") or "")
    approved = APPROVED_RECORDS.get(signature)
    if not isinstance(approved, dict):
        return False
    expected = str(approved.get("recordFingerprint") or record_fingerprint(approved))
    supplied = str(record.get("recordFingerprint") or "")
    return bool(supplied and supplied == expected and record_fingerprint(record) == expected)


def status() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "approvedSignalCount": len(APPROVED_RECORDS),
        "approvedSignalSignatures": sorted(APPROVED_RECORDS),
        "runtimeMutationSupported": False,
        "policy": (
            "Only code-reviewed records packaged with the deployed source are trusted. Prediction rows, environment "
            "payloads and ad-hoc audit results cannot self-approve a signal."
        ),
    }
