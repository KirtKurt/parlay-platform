"""BBS rate-limit degrade. Does not rewrite probabilities, locks, or ledgers.

A 429, transport failure, or missing key is a missing catalogue, not an identity
collision. Official games without a BBS row are missing_bbs_identity exclusions.
Truncation and schema errors stay fatal in bbs_catalogue and are not handled here.
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


def official_missing_bbs_exclusions(schedule, assignments, receipt):
    """Official games with no BBS row after a degraded catalogue.

    Does not drop locked rows. Callers retain a previous lock when one exists.
    """
    if not isinstance(receipt, dict) or receipt.get('degraded') != 'bbs_unavailable':
        raise ValueError('exclusions require a degraded BBS catalogue')
    if not isinstance(assignments, dict):
        raise ValueError('assignments must be a mapping')
    exclusions = []
    for game in schedule:
        pk = str(game.get('gamePk'))
        if pk in assignments:
            continue
        exclusions.append({
            'game_id': pk,
            'reason': 'missing_bbs_identity',
            'degraded': 'bbs_unavailable',
            'bbs_status': receipt.get('status'),
        })
    return exclusions
