from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Mapping, Sequence, Tuple

VERSION = "MLB-YESTERDAY-AUDIT-LOCK-CARD-RESOLVER-v1"
LOCK_PREFIX = "DAILY_LOCK#TMINUS"
_LOCK_MINUTE_ENV_KEYS: Sequence[str] = (
    "MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME",
    "LOCK_MINUTES_BEFORE_FIRST_GAME",
    "MLB_LOCK_MINUTES_BEFORE_FIRST_GAME",
    "MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_PITCH",
)


def _configured_lock_keys() -> List[str]:
    keys: List[str] = []
    for name in _LOCK_MINUTE_ENV_KEYS:
        raw = str(os.environ.get(name) or "").strip()
        if not raw:
            continue
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            continue
        if minutes > 0:
            keys.append(f"{LOCK_PREFIX}{minutes}")
    return keys


def _query_lock_keys(module: Any, slate_date: str) -> Tuple[List[str], str | None]:
    history = getattr(module, "history", None)
    table = getattr(history, "PULLS", None)
    if table is None or not hasattr(table, "query"):
        return [], None

    keys: List[str] = []
    request: Dict[str, Any] = {
        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :prefix)",
        "ExpressionAttributeValues": {
            ":pk": f"LOCKED_PICKS#mlb#{slate_date}",
            ":prefix": LOCK_PREFIX,
        },
        "ConsistentRead": True,
    }
    try:
        while True:
            response = table.query(**request)
            for item in response.get("Items") or []:
                if not isinstance(item, Mapping):
                    continue
                sk = str(item.get("SK") or "")
                if sk.startswith(LOCK_PREFIX):
                    keys.append(sk)
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            request["ExclusiveStartKey"] = last_key
    except Exception as exc:
        return [], f"{type(exc).__name__}:{exc}"
    return keys, None


def _candidate_keys(module: Any, slate_date: str) -> Tuple[List[str], str | None]:
    preferred = str(getattr(module, "DAILY_LOCK_SK", "") or "")
    queried, query_error = _query_lock_keys(module, slate_date)
    ordered = [preferred, *_configured_lock_keys(), *queried]
    result: List[str] = []
    for value in ordered:
        value = str(value or "")
        if value.startswith(LOCK_PREFIX) and value not in result:
            result.append(value)
    return result, query_error


def apply(module: Any):
    """Resolve the one valid immutable daily lock card before grading.

    This wrapper never treats discovery as authority. Every discovered key is
    passed through the original yesterday-audit loader, so its complete-card,
    canonical per-game row, identity, timing, digest, source, and immutability
    checks remain the sole acceptance contract. Zero or multiple valid cards
    fail closed.
    """
    if getattr(module, "_INQSI_MLB_YESTERDAY_LOCK_CARD_RESOLVER_APPLIED", False):
        return module

    original_loader = module.load_locked_predictions
    unavailable = module.LockedEvidenceUnavailable

    def load_locked_predictions(slate_date: str) -> Dict[str, Any]:
        original_key = str(getattr(module, "DAILY_LOCK_SK", "") or "")
        candidates, query_error = _candidate_keys(module, str(slate_date))
        validation_errors: Dict[str, str] = {}
        valid: List[Tuple[str, Dict[str, Any]]] = []
        try:
            for key in candidates:
                module.DAILY_LOCK_SK = key
                try:
                    value = original_loader(str(slate_date))
                except unavailable as exc:
                    validation_errors[key] = str(exc)
                    continue
                if not isinstance(value, Mapping):
                    raise unavailable(f"IMMUTABLE_DAILY_LOCK_CARD_MALFORMED:{key}")
                valid.append((key, copy.deepcopy(dict(value))))
        finally:
            module.DAILY_LOCK_SK = original_key

        if len(valid) > 1:
            raise unavailable(
                "MULTIPLE_AUTHORITATIVE_DAILY_LOCK_CARDS:"
                + ",".join(key for key, _ in valid)
            )
        if len(valid) == 1:
            selected_key, result = valid[0]
            result["dailyLockCardResolution"] = {
                "ok": True,
                "version": VERSION,
                "selectedSk": selected_key,
                "preferredSk": original_key,
                "candidateSks": candidates,
                "queryError": query_error,
                "validationErrors": validation_errors,
                "authority": "original_immutable_yesterday_audit_loader",
                "discoveryIsAuthority": False,
                "multipleValidCardsRejected": True,
            }
            return result

        preferred_error = validation_errors.get(original_key)
        detail = {
            "version": VERSION,
            "preferredSk": original_key,
            "candidateSks": candidates,
            "queryError": query_error,
            "validationErrors": validation_errors,
        }
        if preferred_error:
            raise unavailable(
                f"{preferred_error}|LOCK_CARD_RESOLUTION={detail}"
            )
        raise unavailable(
            "IMMUTABLE_DAILY_LOCK_CARD_UNAVAILABLE|LOCK_CARD_RESOLUTION="
            + str(detail)
        )

    module.load_locked_predictions = load_locked_predictions
    module.MLB_YESTERDAY_AUDIT_LOCK_CARD_RESOLVER_VERSION = VERSION
    module.MLB_YESTERDAY_AUDIT_LOCK_CARD_DISCOVERY_IS_AUTHORITY = False
    module._INQSI_MLB_YESTERDAY_LOCK_CARD_RESOLVER_APPLIED = True
    return module
