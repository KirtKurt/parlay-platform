"""Collect bounded live MLB inputs and existing S3 history; no AWS writes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ks1.features import ET, day, utc
from ks1.inventory import Reader, RESEARCH, RECONSTRUCTED, encode
from ks1.passive_context import read_date as read_passive_context
from ks1.refresh import pregame_status
from ks1.sources import aws_clients


def shape(value, depth=0):
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): shape(v, depth+1) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return {'type': 'array', 'count': len(value), 'item': shape(value[0], depth+1) if value else None}
    return type(value).__name__


class ProviderFailure(ValueError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__(json.dumps(receipt, sort_keys=True))


def odds_auth_unavailable(receipt):
    """401/403/missing key cannot supply a market. Do not kill official scoring."""
    return (isinstance(receipt, dict) and receipt.get('provider') == 'odds'
            and receipt.get('status') in (401, 403, 'ODDS_API_KEY_MISSING'))


def degraded_odds_capture(receipt):
    """Empty odds catalogue. Daily already treats missing markets as unavailable."""
    payload = []
    stamped = {**receipt, 'as_of': datetime.now(timezone.utc).isoformat(),
               'sha256': hashlib.sha256(encode(payload)).hexdigest(),
               'degraded': 'odds_auth_unavailable', 'market_status': 'unavailable'}
    return {'payload': payload, 'receipt': stamped}
