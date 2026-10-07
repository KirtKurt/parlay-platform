"""BBS rate-limit degrade. Does not rewrite probabilities, locks, or ledgers.

A 429, transport failure, or missing key is a missing catalogue, not an identity
collision. Daily isolate-skip turns official games without a BBS row into
missing_bbs_identity exclusions. Truncation and schema errors stay fatal in
bbs_catalogue and are not handled here.
"""
from datetime import datetime, timezone
import hashlib

from ks1.inventory import encode

UNAVAILABLE = (429, 'NETWORK_ERROR', 'BBS_API_KEY_MISSING')


def bbs_unavailable(receipt):
    return isinstance(receipt, dict) and receipt.get('provider') == 'bbs' and receipt.get('status') in UNAVAILABLE


def degraded_bbs_capture(receipt):
    """Empty data array. Callers must isolate-skip, not fail the slate."""
    if not bbs_unavailable(receipt):
        raise ValueError('refusing to degrade a non-unavailable BBS receipt')
    payload = {'data': []}
    stamped = {**receipt, 'as_of': datetime.now(timezone.utc).isoformat(),
               'sha256': hashlib.sha256(encode(payload)).hexdigest(),
               'degraded': 'bbs_unavailable', 'match_catalogue': 'empty'}
    return {'payload': payload, 'receipt': stamped}
