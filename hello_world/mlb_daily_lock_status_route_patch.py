from __future__ import annotations

import copy
import json
import os
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from importlib import import_module
from typing import Any, Dict, Optional


CACHE_VERSION = "MLB-LOCK-STATUS-CACHE-v1-durable-summary"
CACHE_RECORD_TYPE = "mlb_lock_status_summary_cache_v1"
CACHE_SK = "SUMMARY"
DEFAULT_CACHE_MAX_AGE_SECONDS = 20 * 60
CACHE_TTL_SECONDS = 24 * 60 * 60

_INCLUDE_ATTEMPT_DIAGNOSTICS: ContextVar[bool] = ContextVar(
    "mlb_lock_status_include_attempt_diagnostics",
    default=True,
)
_DATE_ALIASES = {"", "current", "today", "now", "live"}
_LOCK_STATUS_SUFFIXES = (
    "/lock-status",
    "/lock/status",
    "/locks/status",
    "/locks/today",
)


def _normalized_slate_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _DATE_ALIASES:
        return None
    return text


def _explicit_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _lock_status_path(path: Any) -> bool:
    normalized = "/" + str(path or "").strip().strip("/")
    return any(normalized.endswith(suffix) for suffix in _LOCK_STATUS_SUFFIXES)


def _parse_utc(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cache_max_age_seconds() -> int:
    try:
        value = int(
            os.environ.get(
                "MLB_LOCK_STATUS_CACHE_MAX_AGE_SECONDS",
                str(DEFAULT_CACHE_MAX_AGE_SECONDS),
            )
        )
    except (TypeError, ValueError):
        value = DEFAULT_CACHE_MAX_AGE_SECONDS
    return max(30, min(value, CACHE_TTL_SECONDS))


def _resolved_slate_date(module: Any, value: Any) -> Optional[str]:
    """Resolve a cache key without replacing the lock module's date authority.

    Explicit dates are safe to use immediately. Date aliases use the lock
    module's canonical ET resolver when it exposes one. Otherwise this returns
    ``None`` so ``_status_payload(None)`` retains the module's own default-date
    behavior; the returned payload then supplies the durable cache key.
    """

    normalized = _normalized_slate_date(value)
    if normalized:
        return normalized
    resolver = getattr(module, "_today_et", None)
    if callable(resolver):
        resolved = resolver()
        if resolved:
            return str(resolved)
    return None


def _cache_key(slate_date: str) -> Dict[str, str]:
    return {
        "PK": f"MLB_LOCK_STATUS_CACHE#{slate_date}",
        "SK": CACHE_SK,
    }


def _status_table(module: Any) -> Any:
    return getattr(module, "TABLE", None)


def _load_cached_summary(module: Any, slate_date: str) -> Optional[Dict[str, Any]]:
    table = _status_table(module)
    if table is None:
        return None
    try:
        item = table.get_item(
            Key=_cache_key(slate_date),
            ConsistentRead=True,
        ).get("Item")
    except Exception:
        return None
    if not isinstance(item, dict) or item.get("record_type") != CACHE_RECORD_TYPE:
        return None
    updated = _parse_utc(item.get("updated_at"))
    if updated is None:
        return None
    age_seconds = max(
        0.0,
        (datetime.now(timezone.utc) - updated).total_seconds(),
    )
    if age_seconds > _cache_max_age_seconds():
        return None
    try:
        body = json.loads(str(item.get("data_json") or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    if str(body.get("slateDateEt") or "") != slate_date:
        return None
    body = copy.deepcopy(body)
    body["statusCache"] = {
        "version": CACHE_VERSION,
        "hit": True,
        "ageSeconds": round(age_seconds, 3),
        "updatedAtUtc": updated.isoformat(),
    }
    return body


def _store_cached_summary(module: Any, slate_date: str, body: Dict[str, Any]) -> bool:
    table = _status_table(module)
    if table is None or not slate_date or body.get("ok") is not True:
        return False
    now = datetime.now(timezone.utc)
    cached = copy.deepcopy(body)
    cached["statusCache"] = {
        "version": CACHE_VERSION,
        "hit": False,
        "ageSeconds": 0.0,
        "updatedAtUtc": now.isoformat(),
    }
    try:
        table.put_item(
            Item={
                **_cache_key(slate_date),
                "record_type": CACHE_RECORD_TYPE,
                "updated_at": now.isoformat(),
                "expires_at": int((now + timedelta(seconds=CACHE_TTL_SECONDS)).timestamp()),
                "model_version": body.get("modelVersion"),
                "data_json": json.dumps(cached, sort_keys=True, default=str),
            }
        )
        return True
    except Exception:
        return False


def _decorate_summary(body: Dict[str, Any], *, cache_hit: bool) -> Dict[str, Any]:
    output = copy.deepcopy(body)
    output.update(
        {
            "readOnly": True,
            "statusDetail": "SUMMARY",
            "attemptDiagnosticsIncluded": False,
        }
    )
    if not cache_hit:
        output["statusCache"] = {
            "version": CACHE_VERSION,
            "hit": False,
            "ageSeconds": 0.0,
            "updatedAtUtc": datetime.now(timezone.utc).isoformat(),
        }
    return output


def _refresh_cached_summary(module: Any, requested_slate: Optional[str]) -> bool:
    """Refresh the public summary outside API Gateway's request deadline."""

    token = _INCLUDE_ATTEMPT_DIAGNOSTICS.set(False)
    try:
        body = module._status_payload(requested_slate)
        if not isinstance(body, dict):
            return False
        actual_slate = str(body.get("slateDateEt") or requested_slate or "")
        if not actual_slate:
            return False
        return _store_cached_summary(
            module,
            actual_slate,
            _decorate_summary(body, cache_hit=False),
        )
    except Exception:
        return False
    finally:
        _INCLUDE_ATTEMPT_DIAGNOSTICS.reset(token)


def _game_identity(patch_module: Any, game: Any) -> Optional[str]:
    if not isinstance(game, dict):
        return None
    resolver = getattr(patch_module, "game_identity", None)
    if callable(resolver):
        try:
            value = resolver(game)
            return str(value) if value else None
        except Exception:
            pass
    value = game.get("gameIdentity") or game.get("game_id") or game.get("id")
    return str(value) if value else None


def _summary_diagnostics(
    patch_module: Any,
    slate_date: str,
    game: Any,
) -> Dict[str, Any]:
    return {
        "ok": True,
        "version": getattr(
            patch_module,
            "ATTEMPT_DIAGNOSTICS_VERSION",
            "mlb-lock-attempt-diagnostics-summary-v1",
        ),
        "slateDateEt": slate_date,
        "gameIdentity": _game_identity(patch_module, game),
        "attemptCount": None,
        "selectedAttemptId": None,
        "attempts": [],
        "included": False,
        "omitted": True,
        "omissionReason": "READ_ONLY_STATUS_SUMMARY",
    }


def _install_diagnostic_wrapper(patch_module: Any) -> None:
    marker = "_INQSI_MLB_LOCK_STATUS_SUMMARY_DIAGNOSTIC_V1"
    if getattr(patch_module, marker, False):
        return
    original = getattr(patch_module, "_diagnostic_history", None)
    if not callable(original):
        return

    def diagnostic_history(module: Any, slate_date: str, game: Any) -> Dict[str, Any]:
        if _INCLUDE_ATTEMPT_DIAGNOSTICS.get():
            return original(module, slate_date, game)
        return _summary_diagnostics(patch_module, slate_date, game)

    patch_module._diagnostic_history = diagnostic_history
    patch_module._inqsi_original_diagnostic_history = original
    setattr(patch_module, marker, True)


def apply(module: Any) -> Any:
    marker = "_INQSI_MLB_LOCK_STATUS_ROUTE_V2_DURABLE_CACHE"
    if getattr(module, marker, False):
        return module

    patch_module = import_module("mlb_daily_per_game_lock_patch")
    _install_diagnostic_wrapper(patch_module)

    original_payload = module._payload
    original_handle = module.handle

    def normalized_payload(event: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(original_payload(event) or {})
        for key in ("slate_date", "slateDateEt", "date"):
            if key in payload:
                normalized = _normalized_slate_date(payload.get(key))
                if normalized is None:
                    payload.pop(key, None)
                else:
                    payload[key] = normalized
        return payload

    def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        event = event or {}
        method = str(event.get("httpMethod") or "").upper()
        path = event.get("path") or event.get("rawPath") or ""
        if method in {"GET", "POST"} and _lock_status_path(path):
            payload = module._payload(event)
            requested_slate = (
                payload.get("slate_date")
                or payload.get("slateDateEt")
                or payload.get("date")
            )
            normalized_slate = _normalized_slate_date(requested_slate)
            cache_slate = _resolved_slate_date(module, requested_slate)
            include_diagnostics = _explicit_bool(
                payload.get("includeAttemptDiagnostics"),
                default=False,
            )
            if not include_diagnostics and cache_slate:
                cached = _load_cached_summary(module, cache_slate)
                if cached is not None:
                    return module._resp(200, _decorate_summary(cached, cache_hit=True))

            token = _INCLUDE_ATTEMPT_DIAGNOSTICS.set(include_diagnostics)
            try:
                body = module._status_payload(normalized_slate)
                if isinstance(body, dict):
                    body = copy.deepcopy(body)
                    body.update(
                        {
                            "readOnly": True,
                            "statusDetail": (
                                "FULL" if include_diagnostics else "SUMMARY"
                            ),
                            "attemptDiagnosticsIncluded": include_diagnostics,
                        }
                    )
                    if not include_diagnostics:
                        body = _decorate_summary(body, cache_hit=False)
                        actual_slate = str(
                            body.get("slateDateEt")
                            or cache_slate
                            or normalized_slate
                            or ""
                        )
                        if actual_slate:
                            _store_cached_summary(module, actual_slate, body)
                return module._resp(200, body)
            except Exception as exc:
                return module._resp(
                    500,
                    {
                        "ok": False,
                        "sport": "mlb",
                        "modelVersion": module.MODEL_VERSION,
                        "readOnly": True,
                        "error": str(exc),
                    },
                )
            finally:
                _INCLUDE_ATTEMPT_DIAGNOSTICS.reset(token)

        response = original_handle(event, context)
        scheduled = not bool(method or event.get("requestContext"))
        if scheduled:
            payload = module._payload(event)
            requested_slate = (
                payload.get("slate_date")
                or payload.get("slateDateEt")
                or payload.get("date")
            )
            _refresh_cached_summary(
                module,
                _normalized_slate_date(requested_slate),
            )
        return response

    module._payload = normalized_payload
    module.handle = handle
    setattr(module, marker, True)
    return module
