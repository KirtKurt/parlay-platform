from __future__ import annotations

import copy
from contextvars import ContextVar
from importlib import import_module
from typing import Any, Dict, Optional


_INCLUDE_ATTEMPT_DIAGNOSTICS: ContextVar[bool] = ContextVar(
    "mlb_lock_status_include_attempt_diagnostics",
    default=True,
)
_DATE_ALIASES = {"", "current", "today", "now", "live"}


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
    return normalized.endswith("/lock-status") or normalized.endswith("/lock/status")


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
    marker = "_INQSI_MLB_LOCK_STATUS_ROUTE_V1"
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
            slate_date = (
                payload.get("slate_date")
                or payload.get("slateDateEt")
                or payload.get("date")
            )
            include_diagnostics = _explicit_bool(
                payload.get("includeAttemptDiagnostics"),
                default=False,
            )
            token = _INCLUDE_ATTEMPT_DIAGNOSTICS.set(include_diagnostics)
            try:
                body = module._status_payload(slate_date)
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
        return original_handle(event, context)

    module._payload = normalized_payload
    module.handle = handle
    setattr(module, marker, True)
    return module
