from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo

import boto3

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
RAW_ARCHIVE_BUCKET = os.environ.get("RAW_ARCHIVE_BUCKET", "")
SPORT_KEY = "baseball_mlb"
ODDS_MARKETS = "h2h,spreads,totals"

s3 = boto3.client("s3")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": "application/json", "access-control-allow-origin": "*"}, "body": json.dumps(body, default=str)}


def _odds_url() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": ODDS_MARKETS, "oddsFormat": "american", "dateFormat": "iso"}
    return f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds/?" + urllib.parse.urlencode(params)


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def archive_raw_snapshot(run: str = "hot_raw_archive") -> Dict[str, Any]:
    if not RAW_ARCHIVE_BUCKET:
        return {"ok": False, "sport": "mlb", "archive_enabled": False, "error": "RAW_ARCHIVE_BUCKET missing", "message": "S3 archive code is deployed but bucket/env wiring is not configured yet."}
    now = _now_iso()
    date = _today_et()
    raw = _http_get_json(_odds_url())
    key = f"raw/odds_api/mlb/date={date}/asof={now.replace(':', '-')}/{run}.json"
    payload = {"sport": "mlb", "sport_key": SPORT_KEY, "source": "theOddsAPI", "markets": ODDS_MARKETS.split(','), "asof": now, "date_et": date, "run": run, "raw": raw}
    s3.put_object(Bucket=RAW_ARCHIVE_BUCKET, Key=key, Body=json.dumps(payload, default=str).encode("utf-8"), ContentType="application/json")
    return {"ok": True, "sport": "mlb", "archive_enabled": True, "bucket": RAW_ARCHIVE_BUCKET, "key": key, "game_count": len(raw or [])}


def lambda_handler(event, context):
    event = event or {}
    try:
        run = event.get("run") or "hot_raw_archive"
        return _resp(200, archive_raw_snapshot(run=run))
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "error": str(exc)})
