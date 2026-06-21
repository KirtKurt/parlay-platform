from typing import Any, Dict

import app
import creator_tracking
import ops_report
from frontend import html_response, manifest_response, robots_response, sitemap_response


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event or {}
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    method = (event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "GET").upper()

    if method == "OPTIONS":
        return app.response(200, {"ok": True})

    if path.startswith("/v1/ops"):
        routed_event = dict(event)
        routed_event["path"] = path
        routed_event["rawPath"] = path
        routed_event["httpMethod"] = method
        return ops_report.lambda_handler(routed_event, context)

    if path.startswith("/v1/creators") or path.startswith("/v1/attribution") or path.startswith("/v1/memberships"):
        routed_event = dict(event)
        routed_event["path"] = path
        routed_event["rawPath"] = path
        routed_event["httpMethod"] = method
        return creator_tracking.lambda_handler(routed_event, context)

    if path.startswith("/v1"):
        legacy_event = dict(event)
        legacy_event["path"] = path
        legacy_event["httpMethod"] = method
        return app.lambda_handler(legacy_event, context)

    if path == "/robots.txt":
        return robots_response()
    if path == "/sitemap.xml":
        return sitemap_response()
    if path == "/manifest.webmanifest":
        return manifest_response()

    return html_response(200)
