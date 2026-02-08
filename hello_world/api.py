import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from decimal import Decimal
import urllib.request
import urllib.parse

import boto3
from boto3.dynamodb.conditions import Key

from nba_algorithm import rank_nba_b11c1

# ======================================================
# AWS / ENV
# ======================================================
dynamodb = boto3.resource("dynamodb")

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None


# ======================================================
# BASIC HELPERS
# ======================================================
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json(body: Optional[str]) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        return json.loads(body)
    except Exception:
        return {}


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o % 1 == 0 else float(o)
    return str(o)


def _resp(status: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type",
        },
        "body": json.dumps(body, default=_json_default),
    }


# ======================================================
# ODDS API
# ======================================================
def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _build_oddsapi_url_nba_h2h() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")

    base = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    return base + "?" + urllib.parse.urlencode(params)


# ======================================================
# PLACEHOLDER FOR REST OF FILE
# (ranking helpers, lambda_handler, etc.)
# ======================================================


def lambda_handler(event, context):
    return _resp(200, {"ok": True})


# ======================================================
# SCHEDULER HANDLER (ALWAYS LAST)
# ======================================================
def scheduler_handler(event, context):
    return _pull_nba_snapshot((event or {}).get("run", "scheduled"))
