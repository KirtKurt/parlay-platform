from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

USER_AGENT = "inqsi-arb-controller/1.2"
BBD_BASE = os.environ.get("BBD_BASE_URL", "https://api.bigballsdata.com").rstrip("/")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request(url: str, *, headers: Dict[str, str] | None = None, timeout: int = 60) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, headers={"user-agent": USER_AGENT, "accept": "application/json,text/html", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def http_json(url: str, *, headers: Dict[str, str] | None = None, timeout: int = 60) -> Tuple[int, Dict[str, str], Any]:
    status, response_headers, raw = _request(url, headers=headers, timeout=timeout)
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        body = {"text": raw.decode("utf-8", "replace")[:1000]}
    return status, response_headers, body


def http_text(url: str, *, timeout: int = 60) -> Tuple[int, Dict[str, str], str]:
    status, response_headers, raw = _request(url, timeout=timeout)
    return status, response_headers, raw.decode("utf-8", "replace")


def check_arb(api_url: str, sportsbook_url: str | None) -> Dict[str, Any]:
    base = api_url.rstrip("/")
    checks: Dict[str, Any] = {}
    failures = []
    for name, path in (
        ("health", "/v1/arb/health"),
        ("catalog", "/v1/arb/catalog?all=true"),
        ("rules", "/v1/arb/rules"),
        ("history", "/v1/arb/history?limit=5"),
    ):
        try:
            status, _, body = http_json(base + path)
            checks[name] = {"status": status, "body": body}
            if status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
                failures.append(name)
        except Exception as exc:
            checks[name] = {"status": None, "error": type(exc).__name__}
            failures.append(name)

    health = checks.get("health", {}).get("body") or {}
    expected_health = {
        "places_bets": False,
        "provider_key_present": True,
        "audit_persistence": True,
        "automatic_market_discovery": True,
        "websocket_push_configured": True,
        "websocket_public_url_present": True,
        "balance_aware_optimizer": True,
        "two_leg_completion_assistant": True,
        "opportunity_history": True,
    }
    mismatches = {
        key: {"expected": expected, "actual": health.get(key)}
        for key, expected in expected_health.items()
        if health.get(key) is not expected
    }
    checks["health_capabilities"] = {"ok": not mismatches, "mismatches": mismatches}
    if mismatches:
        failures.append("health_capabilities")

    catalog = checks.get("catalog", {}).get("body") or {}
    try:
        catalog_count = int(catalog.get("n_sports") or 0)
    except (TypeError, ValueError):
        catalog_count = 0
    if checks.get("catalog", {}).get("status") == 200 and catalog_count <= 0:
        failures.append("catalog_empty")

    rules = checks.get("rules", {}).get("body") or {}
    try:
        rule_count = int(rules.get("count") or 0)
    except (TypeError, ValueError):
        rule_count = 0
    if checks.get("rules", {}).get("status") == 200 and rule_count <= 0:
        failures.append("rules_empty")

    history = checks.get("history", {}).get("body") or {}
    history_rows = history.get("history") if isinstance(history, dict) else None
    history_contract_ok = history.get("kind") == "SCAN" and isinstance(history_rows, list)
    checks["history_contract"] = {"ok": history_contract_ok, "count": len(history_rows) if isinstance(history_rows, list) else None}
    if checks.get("history", {}).get("status") == 200 and not history_contract_ok:
        failures.append("history_contract")

    try:
        status, headers, text = http_text(base + "/v1/arb/ui")
        content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
        ui_ok = status == 200 and "text/html" in content_type.lower() and "Inqsi ARB Console" in text
        checks["ui"] = {"status": status, "ok": ui_ok, "content_type": content_type}
        if not ui_ok:
            failures.append("ui")
    except Exception as exc:
        checks["ui"] = {"status": None, "ok": False, "error": type(exc).__name__}
        failures.append("ui")

    if sportsbook_url:
        try:
            status, _, body = http_json(sportsbook_url, timeout=300)
            checks["sportsbooks"] = {"status": status, "body": body}
            if status != 200 or not isinstance(body, dict) or not body.get("complete") or body.get("sports_failed") != 0:
                failures.append("sportsbooks")
        except Exception as exc:
            checks["sportsbooks"] = {"status": None, "error": type(exc).__name__}
            failures.append("sportsbooks")
    else:
        checks["sportsbooks"] = {"status": None, "reason": "SPORTSBOOK_URL_NOT_CONFIGURED"}
        failures.append("sportsbooks")

    sportsbooks = checks.get("sportsbooks", {}).get("body") or {}
    summary = {
        "version": health.get("version"),
        "provider_key_present": health.get("provider_key_present"),
        "rules_registry_entries": health.get("rules_registry_entries"),
        "active_sports": catalog_count,
        "history_count": len(history_rows) if isinstance(history_rows, list) else None,
        "ui_ok": checks.get("ui", {}).get("ok"),
        "websocket_push_configured": health.get("websocket_push_configured"),
        "websocket_public_url_present": health.get("websocket_public_url_present"),
        "balance_aware_optimizer": health.get("balance_aware_optimizer"),
        "two_leg_completion_assistant": health.get("two_leg_completion_assistant"),
        "sportsbook_count": sportsbooks.get("sportsbook_count"),
        "sportsbook_audit_complete": sportsbooks.get("complete"),
        "sports_failed": sportsbooks.get("sports_failed"),
    }
    return {"ok": not failures, "failures": sorted(set(failures)), "summary": summary, "checks": checks}


def check_bbd() -> Dict[str, Any]:
    key = (
        os.environ.get("BBD_API_KEY")
        or os.environ.get("BIG_BALLS_DATA_API_KEY")
        or os.environ.get("BIGBALLS_DATA_API_KEY")
        or ""
    ).strip()
    if not key:
        return {
            "ok": False,
            "configured": False,
            "blocked": True,
            "reason": "BBD_API_KEY_NOT_CONFIGURED",
            "base_url": BBD_BASE,
        }
    headers = {"authorization": f"Bearer {key}"}
    result: Dict[str, Any] = {"configured": True, "blocked": False, "base_url": BBD_BASE}
    try:
        auth_status, _, _ = http_json(f"{BBD_BASE}/v1/user/me", headers=headers)
        sports_status, _, sports = http_json(f"{BBD_BASE}/v1/sports", headers=headers)
        rows = sports.get("data", []) if isinstance(sports, dict) else []
        result["auth"] = {"status": auth_status, "ok": auth_status == 200}
        result["sports"] = {
            "status": sports_status,
            "ok": sports_status == 200,
            "count": len(rows) if isinstance(rows, list) else None,
        }
        result["ok"] = auth_status == 200 and sports_status == 200
        if not result["ok"]:
            result["reason"] = "BBD_AUTH_OR_DISCOVERY_FAILED"
    except Exception as exc:
        result.update({"ok": False, "reason": "BBD_REQUEST_FAILED", "error": type(exc).__name__})
    return result


def build_report(api_url: str, sportsbook_url: str | None) -> Dict[str, Any]:
    arb = check_arb(api_url, sportsbook_url)
    bbd = check_bbd()
    severity = "healthy"
    if not arb["ok"]:
        severity = "repair_required"
    elif not bbd.get("ok"):
        severity = "degraded_external_dependency" if bbd.get("blocked") else "degraded_provider"
    return {
        "controller": "INQSI-ARB-CONTROLLER-v1.2",
        "checked_at": utcnow(),
        "severity": severity,
        "arb": arb,
        "bbd": bbd,
        "repair_authorized": os.environ.get("ARB_CONTROLLER_ALLOW_REDEPLOY", "").lower() == "true",
        "places_bets": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--sportsbook-url")
    parser.add_argument("--output", default="arb-controller-report.json")
    args = parser.parse_args()
    report = build_report(args.api_url, args.sportsbook_url)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
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
    return 2 if not report["arb"]["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
