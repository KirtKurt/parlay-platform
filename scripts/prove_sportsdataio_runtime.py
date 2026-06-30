from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict

REPORT_PATH = "runtime_reports/sportsdataio_runtime_proof_latest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_json(url: str, timeout: int = 30) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return {"ok": True, "status": response.status, "json": json.loads(body)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]}


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def build_report(api_url: str) -> Dict[str, Any]:
    endpoints = {
        "health": "/v1/health",
        "sportsdataioStatus": "/v1/mlb/fundamentals/status",
        "sportsdataioLiveCheck": "/v1/mlb/fundamentals/status?fetch=true",
        "teamPower": "/v1/mlb/fundamentals/team-power?limit=5",
        "fundamentalsPreview": "/v1/mlb/fundamentals/preview",
        "modelVersion": "/v1/mlb/model/version",
        "today": "/v1/mlb/today",
    }
    checks: Dict[str, Any] = {}
    for name, path in endpoints.items():
        checks[name] = _get_json(_join(api_url, path))

    status_json = (checks.get("sportsdataioStatus") or {}).get("json") or {}
    live_json = (checks.get("sportsdataioLiveCheck") or {}).get("json") or {}
    team_power_json = (checks.get("teamPower") or {}).get("json") or {}
    today_json = (checks.get("today") or {}).get("json") or {}

    today_target = today_json.get("rolling24hAccuracyTarget") or today_json.get("accuracyTarget") or {}
    predictions = today_json.get("predictions") or []
    fundamentals_rows = [row for row in predictions if (row.get("winnerOptimizer") or {}).get("fundamentalsApplied")]

    proof = {
        "ok": True,
        "proofType": "SPORTSDATAIO_RUNTIME_PROOF",
        "createdAt": _now(),
        "apiUrlRedacted": api_url.split("amazonaws.com")[0] + "amazonaws.com/..." if "amazonaws.com" in api_url else "provided",
        "checks": checks,
        "summary": {
            "apiHealthOk": bool((checks.get("health") or {}).get("ok")),
            "sportsDataIoStatusEndpointOk": bool((checks.get("sportsdataioStatus") or {}).get("ok")),
            "sportsDataIoConfigured": bool(status_json.get("configured")),
            "sportsDataIoKeyExposed": bool(status_json.get("keyExposed")),
            "sportsDataIoLiveCheckOk": bool(((live_json.get("liveCheck") or {}) if isinstance(live_json, dict) else {}).get("ok")),
            "sportsDataIoTeamsCount": ((live_json.get("liveCheck") or {}) if isinstance(live_json, dict) else {}).get("teamsCount"),
            "teamPowerOk": bool(team_power_json.get("ok")),
            "teamPowerCount": team_power_json.get("count"),
            "todayEndpointOk": bool((checks.get("today") or {}).get("ok")),
            "modelVersion": today_json.get("modelVersion"),
            "fundamentalsEnabled": today_target.get("fundamentalsEnabled"),
            "fundamentalsAppliedCount": today_target.get("fundamentalsAppliedCount", len(fundamentals_rows)),
            "fundamentalsFlipCount": today_target.get("fundamentalsFlipCount"),
        },
        "policy": "This report proves runtime endpoint behavior without exposing SPORTSDATAIO_API_KEY. A configured=false result means code is deployed but AWS Lambda does not yet have the key in its environment.",
    }
    proof["ok"] = bool(proof["summary"]["apiHealthOk"] and proof["summary"]["sportsDataIoStatusEndpointOk"])
    return proof


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=os.environ.get("INQSI_API_URL") or os.environ.get("API_URL") or "")
    parser.add_argument("--out", default=REPORT_PATH)
    parser.add_argument("--strict", action="store_true", help="Exit nonzero if SportsDataIO is not configured/live.")
    args = parser.parse_args()
    if not args.api_url:
        print("Missing --api-url or API_URL", file=sys.stderr)
        return 2
    report = build_report(args.api_url)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
        f.write("\n")
    print(json.dumps(report.get("summary") or {}, indent=2, default=str))
    if args.strict:
        summary = report.get("summary") or {}
        if not summary.get("sportsDataIoConfigured") or not summary.get("sportsDataIoLiveCheckOk"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
