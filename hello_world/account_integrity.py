import json
import os
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key


dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("SNAPSHOTS_TABLE", "")


DISPOSABLE_DOMAINS = {
    "mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com", "throwawaymail.com",
    "yopmail.com", "getnada.com", "trashmail.com", "sharklasers.com", "fakeinbox.com",
}


POLICY = {
    "ok": True,
    "service": "inqsi-account-integrity",
    "version": "stage_1",
    "minimumAge": 18,
    "requiredSignupFields": ["email", "dateOfBirth", "state", "termsAccepted", "ageCertified"],
    "ageStatuses": ["SELF_CERTIFIED_18_PLUS", "FAILED_UNDER_18", "NEEDS_MANUAL_REVIEW"],
    "riskStatuses": ["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "BLOCKED", "MANUAL_REVIEW"],
    "accountQualityStatuses": ["NEW_UNVERIFIED", "EMAIL_VERIFICATION_REQUIRED", "SUSPICIOUS", "BLOCKED_UNDER_18", "MANUAL_REVIEW"],
    "message": "Inqis is for users 18+. Sports betting laws vary by state and may require you to be 21+. Inqis does not place bets or guarantee outcomes.",
}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def today_key() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def clean(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v % 1 == 0 else float(v)
    if isinstance(v, list):
        return [clean(x) for x in v]
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    return v


def out(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-member-id,x-inqsi-session-id,x-inqsi-device-id",
        },
        "body": json.dumps(clean(body)),
    }


def body(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = json.loads(event.get("body") or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def headers(event: Dict[str, Any]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items() if v is not None}


def table():
    if not TABLE_NAME:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    return dynamodb.Table(TABLE_NAME)


def source_ip(event: Dict[str, Any]) -> str:
    rc = event.get("requestContext") or {}
    http = rc.get("http") or {}
    return http.get("sourceIp") or rc.get("identity", {}).get("sourceIp") or "unknown"


def parse_birth_date(raw: str) -> Optional[date]:
    try:
        return datetime.strptime(str(raw or "")[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def age_years(dob: date) -> int:
    today = datetime.now(timezone.utc).date()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def email_domain(email: str) -> str:
    email = str(email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1]


def query_counter(pk: str) -> int:
    try:
        result = table().query(KeyConditionExpression=Key("PK").eq(pk), Limit=101)
        return len(result.get("Items") or [])
    except Exception:
        return 0


def risk_check(event: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    email = str(data.get("email") or "").strip().lower()
    domain = email_domain(email)
    h = headers(event)
    ip = str(data.get("ipAddress") or source_ip(event) or "unknown")
    device_id = str(data.get("deviceId") or data.get("device_id") or h.get("x-inqsi-device-id") or "unknown")
    session_id = str(data.get("sessionId") or data.get("session_id") or h.get("x-inqsi-session-id") or "")
    score = 0
    reasons: List[str] = []

    if not email or "@" not in email:
        score += 35
        reasons.append("invalid_email")
    if domain in DISPOSABLE_DOMAINS:
        score += 45
        reasons.append("disposable_email_domain")
    if device_id == "unknown":
        score += 10
        reasons.append("missing_device_id")

    day = today_key()
    ip_count = query_counter(f"INTEGRITY#IP#{day}#{ip}")
    device_count = query_counter(f"INTEGRITY#DEVICE#{day}#{device_id}") if device_id != "unknown" else 0
    domain_count = query_counter(f"INTEGRITY#DOMAIN#{day}#{domain}") if domain else 0

    if ip_count >= 5:
        score += 30
        reasons.append("high_signup_velocity_ip")
    if device_count >= 3:
        score += 35
        reasons.append("high_signup_velocity_device")
    if domain_count >= 25:
        score += 15
        reasons.append("high_signup_velocity_domain")

    status = "LOW_RISK"
    if score >= 75:
        status = "HIGH_RISK"
    elif score >= 40:
        status = "MEDIUM_RISK"

    return {
        "riskScore": min(score, 100),
        "riskStatus": status,
        "riskReasons": reasons,
        "ipAddress": ip,
        "deviceId": device_id,
        "sessionId": session_id,
        "emailDomain": domain,
        "counters": {"ipToday": ip_count, "deviceToday": device_count, "domainToday": domain_count},
    }


def store_attempt(data: Dict[str, Any], result: Dict[str, Any], risk: Dict[str, Any]) -> None:
    created_at = now()
    attempt_id = data.get("attemptId") or data.get("attempt_id") or f"integrity_{uuid.uuid4().hex[:16]}"
    email = str(data.get("email") or "").strip().lower()
    member_id = str(data.get("memberId") or data.get("member_id") or "")
    day = today_key()
    base = {
        "recordType": "account_integrity_attempt",
        "attemptId": attempt_id,
        "memberId": member_id,
        "email": email,
        "emailDomain": risk.get("emailDomain") or "",
        "state": data.get("state") or "",
        "ageStatus": result.get("ageStatus"),
        "eligible": bool(result.get("eligible")),
        "accountQualityStatus": result.get("accountQualityStatus"),
        "riskScore": Decimal(str(risk.get("riskScore") or 0)),
        "riskStatus": risk.get("riskStatus"),
        "riskReasons": risk.get("riskReasons") or [],
        "createdAt": created_at,
    }
    table().put_item(Item={"PK": f"INTEGRITY#ATTEMPT#{day}", "SK": f"ATTEMPT#{created_at}#{attempt_id}", **base})
    table().put_item(Item={"PK": f"INTEGRITY#IP#{day}#{risk.get('ipAddress')}", "SK": f"ATTEMPT#{created_at}#{attempt_id}", **base})
    if risk.get("deviceId") and risk.get("deviceId") != "unknown":
        table().put_item(Item={"PK": f"INTEGRITY#DEVICE#{day}#{risk.get('deviceId')}", "SK": f"ATTEMPT#{created_at}#{attempt_id}", **base})
    if risk.get("emailDomain"):
        table().put_item(Item={"PK": f"INTEGRITY#DOMAIN#{day}#{risk.get('emailDomain')}", "SK": f"ATTEMPT#{created_at}#{attempt_id}", **base})


def signup_check(event: Dict[str, Any]) -> Dict[str, Any]:
    data = body(event)
    missing = []
    for field in ["email", "dateOfBirth", "state"]:
        if not data.get(field):
            missing.append(field)
    if data.get("termsAccepted") is not True:
        missing.append("termsAccepted")
    if data.get("ageCertified") is not True:
        missing.append("ageCertified")

    dob = parse_birth_date(data.get("dateOfBirth"))
    risk = risk_check(event, data)

    eligible = False
    age_status = "NEEDS_MANUAL_REVIEW"
    quality = "MANUAL_REVIEW"
    allowed_actions = ["VIEW_MARKETING_SITE"]
    reasons = list(missing)

    if missing:
        quality = "NEW_UNVERIFIED"
    elif not dob:
        reasons.append("invalid_date_of_birth")
    else:
        years = age_years(dob)
        if years < 18:
            age_status = "FAILED_UNDER_18"
            quality = "BLOCKED_UNDER_18"
            reasons.append("under_18")
        else:
            age_status = "SELF_CERTIFIED_18_PLUS"
            if risk["riskStatus"] == "HIGH_RISK":
                quality = "SUSPICIOUS"
                allowed_actions = ["VIEW_MARKETING_SITE", "VERIFY_EMAIL"]
                reasons.extend(risk["riskReasons"])
            else:
                eligible = True
                quality = "EMAIL_VERIFICATION_REQUIRED"
                allowed_actions = ["CREATE_ACCOUNT", "VERIFY_EMAIL"]
                if risk["riskStatus"] == "MEDIUM_RISK":
                    reasons.extend(risk["riskReasons"])

    result = {
        "ok": True,
        "eligible": eligible,
        "ageStatus": age_status,
        "accountRiskStatus": risk["riskStatus"],
        "riskScore": risk["riskScore"],
        "riskReasons": sorted(set(reasons + risk.get("riskReasons", []))),
        "accountQualityStatus": quality,
        "allowedActions": allowed_actions,
        "emailVerificationRequired": eligible or quality in {"EMAIL_VERIFICATION_REQUIRED", "SUSPICIOUS"},
        "policy": POLICY["message"],
    }
    store_attempt(data, result, risk)
    return out(200, result)


def summary(event: Dict[str, Any]) -> Dict[str, Any]:
    q = event.get("queryStringParameters") or {}
    day = str(q.get("date") or today_key())
    res = table().query(KeyConditionExpression=Key("PK").eq(f"INTEGRITY#ATTEMPT#{day}"), Limit=500)
    items = res.get("Items") or []
    by_quality: Dict[str, int] = {}
    by_risk: Dict[str, int] = {}
    by_age: Dict[str, int] = {}
    blocked_under_18 = 0
    for item in items:
        by_quality[item.get("accountQualityStatus") or "unknown"] = by_quality.get(item.get("accountQualityStatus") or "unknown", 0) + 1
        by_risk[item.get("riskStatus") or "unknown"] = by_risk.get(item.get("riskStatus") or "unknown", 0) + 1
        by_age[item.get("ageStatus") or "unknown"] = by_age.get(item.get("ageStatus") or "unknown", 0) + 1
        if item.get("accountQualityStatus") == "BLOCKED_UNDER_18":
            blocked_under_18 += 1
    return out(200, {
        "ok": True,
        "dashboard": "account_integrity_summary",
        "date": day,
        "totalAttempts": len(items),
        "blockedUnder18": blocked_under_18,
        "byAccountQualityStatus": by_quality,
        "byRiskStatus": by_risk,
        "byAgeStatus": by_age,
        "recentAttempts": sorted(items, key=lambda x: x.get("createdAt", ""), reverse=True)[:50],
    })


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/account-integrity") or path.startswith("/v1/account-integrity") or path.startswith("/v1/inqsi/admin/account-integrity") or path.startswith("/v1/admin/account-integrity")):
        return out(200, {"ok": True})
    if path in {"/v1/inqsi/account-integrity/policy", "/v1/account-integrity/policy"} and method == "GET":
        return out(200, POLICY)
    if path in {"/v1/inqsi/account-integrity/signup-check", "/v1/account-integrity/signup-check"} and method == "POST":
        return signup_check(event)
    if path in {"/v1/inqsi/admin/account-integrity/summary", "/v1/admin/account-integrity/summary"} and method == "GET":
        return summary(event)
    return None
