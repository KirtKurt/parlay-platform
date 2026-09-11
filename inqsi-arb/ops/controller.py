from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

USER_AGENT = "inqsi-arb-controller/1.0"
BBD_BASE = os.environ.get("BBD_BASE_URL", "https://api.bigballsdata.com").rstrip("/")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_json(url: str, *, headers: Dict[str, str] | None = None, timeout: int = 60) -> Tuple[int, Dict[str, str], Any]:
    req = urllib.request.Request(url, headers={"user-agent": USER_AGENT, "accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, dict(r.headers.items()), json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {"text": raw.decode("utf-8", "replace")[:1000]}
        return exc.code, dict(exc.headers.items()), body


def check_arb(api_url: str, sportsbook_url: str | None) -> Dict[str, Any]:
    base = api_url.rstrip("/")
    checks: Dict[str, Any] = {}
    failures = []

    for name, path in (
        ("health", "/v1/arb/health"),
        ("catalog", "/v1/arb/catalog?all=true"),
        ("rules", "/v1/arb/rules"),
    ):
        try:
            status, _, body = http_json(base + path)
            checks[name] = {"status": status, "body": body}
            if status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
                failures.append(name)
        except Exception as exc:
            checks[name] = {"status": None, "error": type(exc).__name__}
            failures.append(name)

    if sportsbook_url:
        try:
            status, _, body = http_json(sportsbook_url, timeout=300)
            checks["sportsbooks"] = {"status": status, "body": body}
            if status != 200 or not body.get("complete") or body.get("sports_failed") != 0:
                failures.append("sportsbooks")
        except Exception as exc:
            checks["sportsbooks"] = {"status": None, "error": type(exc).__name__}
            failures.append("sportsbooks")
    else:
        checks["sportsbooks"] = {"status": None, "reason": "SPORTSBOOK_URL_NOT_CONFIGURED"}
        failures.append("sportsbooks")

    health = checks.get("health", {}).get("body") or {}
    catalog = checks.get("catalog", {}).get("body") or {}
    sportsbooks = checks.get("sportsbooks", {}).get("body") or {}
    summary = {
        "version": health.get("version"),
        "provider_key_present": health.get("provider_key_present"),
        "rules_registry_entries": health.get("rules_registry_entries"),
        "active_sports": catalog.get("n_sports"),
        "sportsbook_count": sportsbooks.get("sportsbook_count"),
        "sportsbook_audit_complete": sportsbooks.get("complete"),
    }
    return {"ok": not failures, "failures": sorted(set(failures)), "summary": summary, "checks": checks}


def check_bbd() -> Dict[str, Any]:
    key = (os.environ.get("BBD_API_KEY") or os.environ.get("BIG_BALLS_DATA_API_KEY") or "").strip()
    if not key:
        return {
            "ok": False,
            "configured": False,
            "blocked": True,
            "reason": "BBD_API_KEY_NOT_CONFIGURED",
            "base_url": BBD_BASE,
        }
    headers = {"authorization": f"Bearer {key}"}
    out: Dict[str, Any] = {"configured": True, "blocked": False, "base_url": BBD_BASE}
    try:
        me_status, _, me = http_json(f"{BBD_BASE}/v1/user/me", headers=headers)
        sports_status, _, sports = http_json(f"{BBD_BASE}/v1/sports", headers=headers)
        out["auth"] = {"status": me_status, "ok": me_status == 200}
        out["sports"] = {
            "status": sports_status,
            "ok": sports_status == 200,
            "count": len(sports.get("data", [])) if isinstance(sports, dict) and isinstance(sports.get("data"), list) else None,
            "meta": sports.get("meta") if isinstance(sports, dict) else None,
        }
        out["ok"] = me_status == 200 and sports_status == 200
        if not out["ok"]:
            out["reason"] = "BBD_AUTH_OR_DISCOVERY_FAILED"
    except Exception as exc:
        out.update({"ok": False, "reason": "BBD_REQUEST_FAILED", "error": type(exc).__name__})
    return out


def build_report(api_url: str, sportsbook_url: str | None) -> Dict[str, Any]:
    arb = check_arb(api_url, sportsbook_url)
    bbd = check_bbd()
    severity = "healthy"
    if not arb["ok"]:
        severity = "repair_required"
    elif not bbd.get("ok"):
        severity = "degraded_external_dependency" if bbd.get("blocked") else "degraded_provider"
    return {
        "controller": "INQSI-ARB-CONTROLLER-v1",
        "checked_at": utcnow(),
        "severity": severity,
        "arb": arb,
        "bbd": bbd,
        "repair_authorized": bool(os.environ.get("ARB_CONTROLLER_ALLOW_REDEPLOY", "").lower() == "true"),
        "places_bets": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--sportsbook-url")
    parser.add_argument("--output", default="arb-controller-report.json")
    args = parser.parse_args()

    report = build_report(args.api_url, args.sportsbook_url)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")

    # Print only non-secret aggregate evidence.
    print(json.dumps({
        "controller": report["controller"],
        "checked_at": report["checked_at"],
        "severity": report["severity"],
        "arb_ok": report["arb"]["ok"],
        "arb_failures": report["arb"]["failures"],
        "arb_summary": report["arb"]["summary"],
        "bbd_configured": report["bbd"].get("configured"),
        "bbd_ok": report["bbd"].get("ok"),
        "bbd_reason": report["bbd"].get("reason"),
        "repair_authorized": report["repair_authorized"],
    }, indent=2, sort_keys=True))

    # Runtime ARB failure is actionable and should fail the controller run.
    # Missing BBD credentials are a documented external/configuration blocker,
    # not a reason to break otherwise valid Odds API arbitrage service.
    return 2 if not report["arb"]["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
