import json
from typing import Any, Dict

import mlb_canonical_final_labels_v1 as canonical_settlement
from mlb_audit import (
    final_mlb_scores_report,
    settlement_proof_report as legacy_settlement_proof_report,
    settle_mlb_slate as legacy_settle_mlb_slate,
)
from mlb_signal_learning import build_signal_learning_report
from mlb_result_signals import build_result_signals, latest_result_signals


def _json_default(value: Any) -> Any:
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            return float(value)
    except Exception:
        pass
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if not body:
        return {}
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _payload(event: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    payload.update(event.get("queryStringParameters") or {})
    payload.update(_parse_body(event))
    for key in (
        "slate_date_et",
        "date",
        "days_from",
        "daysFrom",
        "fetch_scores",
        "store",
        "legacy_diagnostic",
    ):
        if key in event and key not in payload:
            payload[key] = event[key]
    return payload


def _bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _settlement_args(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "slate_date": payload.get("slate_date_et") or payload.get("date"),
        "days_from": int(payload.get("days_from") or payload.get("daysFrom") or 3),
        "fetch_scores": _bool(payload.get("fetch_scores"), True),
    }


def _legacy_diagnostic(
    args: Dict[str, Any],
    *,
    proof: bool = False,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Preserve the former settlement surface without granting it authority."""
    if not enabled:
        return {
            "ok": True,
            "executed": False,
            "authoritative": False,
            "status": "LEGACY_DIAGNOSTIC_DISABLED",
        }
    try:
        report = (
            legacy_settlement_proof_report(**args)
            if proof
            else legacy_settle_mlb_slate(**args)
        )
        return {
            "ok": bool(report.get("ok")) if isinstance(report, dict) else False,
            "executed": True,
            "authoritative": False,
            "report": report,
        }
    except Exception as exc:
        return {
            "ok": False,
            "executed": True,
            "authoritative": False,
            "error": f"{type(exc).__name__}:{exc}",
        }


def _canonical_with_legacy_diagnostic(
    report: Dict[str, Any],
    args: Dict[str, Any],
    *,
    proof: bool = False,
    enabled: bool = True,
) -> Dict[str, Any]:
    out = dict(report)
    out["legacyDiagnosticCompatibility"] = _legacy_diagnostic(
        args,
        proof=proof,
        enabled=enabled,
    )
    out["settlementAuthority"] = "CANONICAL_IMMUTABLE_LOCK_OFFICIAL_GAME_PK"
    out["legacyDiagnosticIsAuthoritative"] = False
    return out


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    try:
        payload = _payload(event)
        args = _settlement_args(payload)
        slate_date = args.get("slate_date")

        if method in {"GET", "POST"} and path in {"/v1/mlb/scores/final", "/v1/results/mlb/final-scores"}:
            return _resp(200, final_mlb_scores_report(**args))

        if method in {"GET", "POST"} and path in {"/v1/results/mlb/proof", "/v1/mlb/settlement/proof_report"}:
            proof_args = {**args, "fetch_scores": _bool(payload.get("fetch_scores"), False)}
            canonical = canonical_settlement.settlement_proof_report(**proof_args)
            report = _canonical_with_legacy_diagnostic(
                canonical,
                proof_args,
                proof=True,
                enabled=_bool(payload.get("legacy_diagnostic"), False),
            )
            return _resp(200 if canonical.get("ok") else 409, report)

        if method in {"GET", "POST"} and path in {"/v1/results/mlb/settlement", "/v1/mlb/settlement/slate"}:
            canonical = canonical_settlement.settle_mlb_slate(
                **args,
                store=_bool(payload.get("store"), True),
            )
            report = _canonical_with_legacy_diagnostic(
                canonical,
                args,
                enabled=_bool(payload.get("legacy_diagnostic"), False),
            )
            return _resp(200 if canonical.get("ok") else 409, report)

        if method in {"GET", "POST"} and path in {"/v1/results/mlb/signal-learning", "/v1/mlb/signal-learning"}:
            learn_args = {**args, "fetch_scores": _bool(payload.get("fetch_scores"), False)}
            return _resp(200, build_signal_learning_report(**learn_args))

        if method in {"GET", "POST"} and path in {"/v1/results/mlb/result-signals", "/v1/mlb/result-signals"}:
            if not slate_date:
                return _resp(400, {"ok": False, "sport": "mlb", "error": "date or slate_date_et is required"})
            if method == "POST" or _bool(payload.get("build"), False):
                return _resp(200, build_result_signals(slate_date, fetch_scores=_bool(payload.get("fetch_scores"), True), store=_bool(payload.get("store"), True)))
            return _resp(200, latest_result_signals(slate_date))

        # EventBridge scheduled execution: the canonical immutable lock -> official
        # MLB gamePk label is authoritative. The former settlement and derived
        # signal reports remain nested diagnostic compatibility only.
        if not method:
            settlement = (
                canonical_settlement.settle_mlb_slate(**args, store=True)
                if args.get("slate_date")
                else canonical_settlement.settle_recent_mlb_slates(
                    days_from=args.get("days_from", 3),
                    fetch_scores=args.get("fetch_scores", True),
                    store=True,
                )
            )
            # The former settlement mutates legacy rows. It is never invoked by
            # the scheduled authoritative path; compatibility is opt-in on an
            # explicit HTTP request only.
            legacy = _legacy_diagnostic(args, enabled=False)
            resolved_slate = (
                args.get("slate_date")
                or settlement.get("slateDateEt")
                or ((legacy.get("report") or {}).get("slate_date_et"))
            )
            learning = build_signal_learning_report(
                slate_date=resolved_slate,
                days_from=args.get("days_from", 3),
                fetch_scores=False,
            )
            result_signals = (
                build_result_signals(
                    resolved_slate,
                    fetch_scores=False,
                    store=True,
                )
                if resolved_slate
                else {"ok": False, "error": "No slate_date available for result signals"}
            )
            report = {
                **settlement,
                "settlementAuthority": "CANONICAL_IMMUTABLE_LOCK_OFFICIAL_GAME_PK",
                "legacyDiagnosticIsAuthoritative": False,
                "legacyDiagnosticCompatibility": legacy,
                "signalLearningDiagnostic": learning,
                "resultSignalsDiagnostic": result_signals,
                # Retain the old response keys for consumers while explicitly
                # classifying their contents as non-authoritative diagnostics.
                "signal_learning": learning,
                "result_signals": result_signals,
            }
            return _resp(200 if settlement.get("ok") else 409, report)

        return _resp(404, {"ok": False, "sport": "mlb", "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "error": str(exc)})
