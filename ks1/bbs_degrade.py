"""BBS rate-limit degrade. Does not rewrite probabilities, locks, or ledgers.

A 429 or transport failure is a missing catalogue, not an identity collision.
Official games without a BBS row stay missing_bbs_identity exclusions in daily.
Truncation, schema drift, and duplicate-BBS-to-one-game stay fatal elsewhere.
"""
from datetime import datetime, timezone
import hashlib

from ks1.inventory import encode

UNAVAILABLE = (429, "NETWORK_ERROR", "BBS_API_KEY_MISSING")


def bbs_unavailable(receipt):
    return isinstance(receipt, dict) and receipt.get("provider") == "bbs" and receipt.get("status") in UNAVAILABLE


def degraded_bbs_capture(receipt):
    """Empty data array. Callers must isolate-skip, not fail the slate."""
    if not bbs_unavailable(receipt):
        raise ValueError("refusing to degrade a non-unavailable BBS receipt")
    payload = {"data": []}
    stamped = {**receipt, "as_of": datetime.now(timezone.utc).isoformat(),
               "sha256": hashlib.sha256(encode(payload)).hexdigest(),
               "degraded": "bbs_unavailable", "match_catalogue": "empty"}
    return {"payload": payload, "receipt": stamped}


def record_bbs_failure(errors, receipt, output):
    """Degrade 429/transport to an empty catalogue. Other BBS failures stay fatal."""
    import json
    if not bbs_unavailable(receipt):
        errors.append(receipt)
        return False
    bbs = degraded_bbs_capture(receipt)
    (output / "bbs.json").write_bytes(encode(bbs))
    print(json.dumps(bbs["receipt"]))
    return True
