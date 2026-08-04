from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any, Dict, List, Mapping, Sequence, Tuple

VERSION = "MLB-YESTERDAY-AUDIT-LOCK-CARD-RESOLVER-v2-canonical-per-game-source"
LOCK_PREFIX = "DAILY_LOCK#TMINUS"
CANONICAL_ROW_PREFIX = "LOCKED#GAME#"
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


def _query_items(
    table: Any,
    *,
    pk: str,
    prefix: str,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    request: Dict[str, Any] = {
        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :prefix)",
        "ExpressionAttributeValues": {
            ":pk": pk,
            ":prefix": prefix,
        },
        "ConsistentRead": True,
    }
    while True:
        response = table.query(**request)
        for item in response.get("Items") or []:
            if isinstance(item, Mapping):
                items.append(copy.deepcopy(dict(item)))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        request["ExclusiveStartKey"] = last_key
    return items


def _query_lock_keys(module: Any, slate_date: str) -> Tuple[List[str], str | None]:
    history = getattr(module, "history", None)
    table = getattr(history, "PULLS", None)
    if table is None or not hasattr(table, "query"):
        return [], None

    try:
        items = _query_items(
            table,
            pk=f"LOCKED_PICKS#mlb#{slate_date}",
            prefix=LOCK_PREFIX,
        )
    except Exception as exc:
        return [], f"{type(exc).__name__}:{exc}"
    keys = [
        str(item.get("SK") or "")
        for item in items
        if str(item.get("SK") or "").startswith(LOCK_PREFIX)
    ]
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


def _required_row_fields_present(module: Any, row: Mapping[str, Any]) -> bool:
    return bool(
        module._game_id(row)
        and module._identity(row)
        and module._commence(row)
        and row.get("homeTeam")
        and row.get("awayTeam")
        and row.get("predictedWinner")
        and row.get("predictedSide") in {"home", "away"}
    )


def _direct_canonical_rows(module: Any, slate_date: str) -> Dict[str, Any]:
    history = getattr(module, "history", None)
    table = getattr(history, "PULLS", None)
    unavailable = module.LockedEvidenceUnavailable
    if table is None or not hasattr(table, "query"):
        raise unavailable("CANONICAL_IMMUTABLE_PER_GAME_TABLE_UNAVAILABLE")

    canonical_pk = f"GAME_WINNERS#mlb#{slate_date}"
    try:
        items = _query_items(
            table,
            pk=canonical_pk,
            prefix=CANONICAL_ROW_PREFIX,
        )
    except Exception as exc:
        raise unavailable(
            f"CANONICAL_IMMUTABLE_PER_GAME_QUERY_FAILED:{type(exc).__name__}:{exc}"
        ) from exc
    if not items:
        raise unavailable("CANONICAL_IMMUTABLE_PER_GAME_ROWS_UNAVAILABLE")

    try:
        import mlb_immutable_locked_storage_patch as storage_contract
        import mlb_daily_lock_ml_vector_preservation_patch as vector_contract
    except Exception as exc:
        raise unavailable(
            f"CANONICAL_IMMUTABLE_PER_GAME_VALIDATOR_UNAVAILABLE:{type(exc).__name__}:{exc}"
        ) from exc

    rows: List[Dict[str, Any]] = []
    keys: List[Dict[str, str]] = []
    game_ids: List[str] = []
    identities: List[str] = []
    item_digests: List[str] = []
    for item in items:
        sk = str(item.get("SK") or "")
        row = item.get("data") if isinstance(item.get("data"), Mapping) else None
        if not isinstance(row, Mapping) or not row:
            raise unavailable(f"CANONICAL_IMMUTABLE_PER_GAME_PAYLOAD_MISSING:{sk}")
        row = copy.deepcopy(dict(row))
        identity = str(module._identity(row) or "")
        game_id = str(module._game_id(row) or "")
        commence = str(module._commence(row) or "")
        expected_sk = f"{CANONICAL_ROW_PREFIX}{commence}#{identity}"
        item_game_id = str(item.get("game_id") or "")
        if item_game_id.startswith("provider:"):
            item_game_id = item_game_id[len("provider:") :]
        item_identity = str(item.get("game_identity") or "")
        row_stage = (
            row.get("canonicalPerGameStageAuthority")
            if isinstance(row.get("canonicalPerGameStageAuthority"), Mapping)
            else {}
        )

        metadata_valid = bool(
            item.get("PK") == canonical_pk
            and sk == expected_sk
            and item.get("record_type")
            == "mlb_immutable_locked_single_game_prediction"
            and str(item.get("slate_date") or "") == str(slate_date)
            and item.get("immutable_locked") is True
            and item.get("stage_authority_verified") is True
            and item.get("selection_lock_verified") is True
            and item.get("stage_authority_version") == storage_contract.AUTHORITY_VERSION
            and item.get("immutable_locked_storage_version") == storage_contract.VERSION
            and item.get("stage_fingerprint")
            == row_stage.get("stageFingerprint")
            and item_game_id == game_id
            and item_identity == identity
            and module.normalize_team(item.get("predicted_winner"))
            == module.normalize_team(row.get("predictedWinner"))
            and _required_row_fields_present(module, row)
        )
        if not metadata_valid:
            raise unavailable(f"CANONICAL_IMMUTABLE_PER_GAME_METADATA_INVALID:{sk}")

        stage_errors = storage_contract.validate_canonical_stage_authority(table, row)
        if stage_errors:
            raise unavailable(
                f"CANONICAL_IMMUTABLE_PER_GAME_STAGE_INVALID:{sk}:"
                + ",".join(sorted(set(str(value) for value in stage_errors)))
            )
        vector_errors = vector_contract.validate_selection_lock_vector_status(row)
        if vector_errors:
            raise unavailable(
                f"CANONICAL_IMMUTABLE_PER_GAME_SELECTION_STATUS_INVALID:{sk}:"
                + ",".join(sorted(set(str(value) for value in vector_errors)))
            )

        rows.append(row)
        keys.append({"PK": canonical_pk, "SK": sk})
        game_ids.append(game_id)
        identities.append(identity)
        item_digests.append(
            hashlib.sha256(
                json.dumps(
                    item,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
        )

    if (
        len(set(game_ids)) != len(game_ids)
        or any(not value for value in game_ids)
        or len(set(identities)) != len(identities)
        or any(not value for value in identities)
    ):
        raise unavailable("CANONICAL_IMMUTABLE_PER_GAME_DUPLICATE_IDENTITY")

    ordered = sorted(
        zip(rows, keys, item_digests),
        key=lambda value: (
            str(module._commence(value[0]) or ""),
            str(module._identity(value[0]) or ""),
        ),
    )
    rows = [value[0] for value in ordered]
    keys = [value[1] for value in ordered]
    item_digests = [value[2] for value in ordered]
    partition_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "slateDateEt": slate_date,
                "canonicalKeys": keys,
                "itemDigests": item_digests,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return {
        "rows": rows,
        "dailyPicks": copy.deepcopy(rows),
        "authority": {
            "version": module.VERSION,
            "resolverVersion": VERSION,
            "authorityClass": "CANONICAL_IMMUTABLE_PER_GAME_ROWS_DIRECT_QUERY",
            "historicalPredictionsRecomputed": False,
            "consistentRead": True,
            "writeOnce": True,
            "cardPk": None,
            "cardSk": None,
            "cardCreatedAtUtc": None,
            "cardModelVersion": None,
            "perGameLock": True,
            "canonicalSingleGameRowsVerified": True,
            "canonicalRowsQueriedDirectly": True,
            "dailyCardRequired": False,
            "canonicalKeys": keys,
            "predictionCount": len(rows),
            "stageAuthorityValidated": True,
            "selectionLockVectorStatusValidated": True,
            "partitionFingerprint": partition_fingerprint,
            "discoveryIsAuthority": False,
        },
    }


def apply(module: Any):
    """Resolve one immutable MLB prediction authority before grading.

    A complete valid daily card remains preferred. When no valid daily card
    exists, the reader may use the canonical immutable per-game partition only
    after each row passes the existing persisted-stage and selection-lock
    validators. No prediction is recomputed, reconstructed, or inferred. Zero,
    duplicate, tampered, ambiguous, or partially valid evidence fails closed.
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
                "fallbackSource": None,
                "dailyCardPresent": True,
                "discoveryIsAuthority": False,
                "multipleValidCardsRejected": True,
            }
            return result

        try:
            result = _direct_canonical_rows(module, str(slate_date))
        except unavailable as direct_exc:
            detail = {
                "version": VERSION,
                "preferredSk": original_key,
                "candidateSks": candidates,
                "queryError": query_error,
                "validationErrors": validation_errors,
                "canonicalPerGameError": str(direct_exc),
            }
            preferred_error = validation_errors.get(original_key)
            if preferred_error:
                raise unavailable(
                    f"{preferred_error}|LOCK_CARD_RESOLUTION={detail}"
                ) from direct_exc
            raise unavailable(
                "IMMUTABLE_DAILY_LOCK_CARD_UNAVAILABLE|LOCK_CARD_RESOLUTION="
                + str(detail)
            ) from direct_exc

        result["dailyLockCardResolution"] = {
            "ok": True,
            "version": VERSION,
            "selectedSk": None,
            "preferredSk": original_key,
            "candidateSks": candidates,
            "queryError": query_error,
            "validationErrors": validation_errors,
            "authority": "canonical_immutable_per_game_rows",
            "fallbackSource": "canonical_immutable_per_game_partition",
            "dailyCardPresent": False,
            "discoveryIsAuthority": False,
            "multipleValidCardsRejected": True,
            "historicalPredictionsRecomputed": False,
        }
        return result

    module.load_locked_predictions = load_locked_predictions
    module.MLB_YESTERDAY_AUDIT_LOCK_CARD_RESOLVER_VERSION = VERSION
    module.MLB_YESTERDAY_AUDIT_LOCK_CARD_DISCOVERY_IS_AUTHORITY = False
    module.MLB_YESTERDAY_AUDIT_CANONICAL_PER_GAME_FALLBACK_ENABLED = True
    module._INQSI_MLB_YESTERDAY_LOCK_CARD_RESOLVER_APPLIED = True
    return module
