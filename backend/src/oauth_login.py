import json
import os
from typing import Any, Dict
from urllib.parse import urlencode

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type,authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status_code, "headers": CORS_HEADERS, "body": json.dumps(body)}


def redirect(location: str) -> Dict[str, Any]:
    return {"statusCode": 302, "headers": {"Location": location, **CORS_HEADERS}, "body": ""}


def base_url() -> str:
    return os.environ.get("PUBLIC_SITE_URL", "https://inqsi.app").rstrip("/")


def google_start() -> Dict[str, Any]:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    callback = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", f"{base_url()}/v1/oauth/google/callback")
    if not client_id:
        return response(503, {"status": "working_on_it", "provider": "google", "message": "Google OAuth client ID is not configured."})
    params = {
        "client_id": client_id,
        "redirect_uri": callback,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account"
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


def apple_start() -> Dict[str, Any]:
    client_id = os.environ.get("APPLE_CLIENT_ID", "").strip()
    callback = os.environ.get("APPLE_REDIRECT_URI", f"{base_url()}/v1/oauth/apple/callback")
    if not client_id:
        return response(503, {"status": "working_on_it", "provider": "apple", "message": "Apple client ID is not configured."})
    params = {
        "client_id": client_id,
        "redirect_uri": callback,
        "response_type": "code id_token",
        "scope": "name email",
        "response_mode": "form_post"
    }
    return redirect("https://appleid.apple.com/auth/authorize?" + urlencode(params))


def callback(provider: str) -> Dict[str, Any]:
    return response(501, {
        "status": "working_on_it",
        "provider": provider,
        "message": "OAuth callback route is reserved. Token exchange and identity verification must be enabled after provider secrets are configured. No unverified user session was created."
    })


def readiness() -> Dict[str, Any]:
    return response(200, {
        "google": {
            "ready": bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") and os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")),
            "required": ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI"]
        },
        "apple": {
            "ready": bool(os.environ.get("APPLE_CLIENT_ID") and os.environ.get("APPLE_TEAM_ID") and os.environ.get("APPLE_KEY_ID") and os.environ.get("APPLE_PRIVATE_KEY") and os.environ.get("APPLE_REDIRECT_URI")),
            "required": ["APPLE_CLIENT_ID", "APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_PRIVATE_KEY", "APPLE_REDIRECT_URI"]
        },
        "policy": "Do not create member sessions from unverified identity tokens."
    })


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", "GET")).upper()
    path = event.get("rawPath") or event.get("path") or "/"
    if method == "OPTIONS":
        return response(200, {"ok": True})
    if path == "/v1/oauth/readiness":
        return readiness()
    if path == "/v1/oauth/google/start":
        return google_start()
    if path == "/v1/oauth/apple/start":
        return apple_start()
    if path == "/v1/oauth/google/callback":
        return callback("google")
    if path == "/v1/oauth/apple/callback":
        return callback("apple")
    return response(404, {"error": "oauth_route_not_found", "path": path})
