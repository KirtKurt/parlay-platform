"""State-integrity guards for the MLB historical optimizer.

The optimizer is scheduled frequently. A settled-range cursor can legitimately be
unable to advance until yesterday's MLB slate is final, but that state is not active
backfilling and must not generate a new DynamoDB revision on every invocation.

The active ledger is also authoritative for the proven historical end date. If a
prior invocation completed a slate but a retry persisted a stale top-level endDate,
repair that bookkeeping before deciding whether range extension is exhausted.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import math
from typing import Any, Mapping

import mlb_historical_incremental_range_extension_v1 as incremental_range_extension

VERSION = "MLB-HISTORICAL-STATE-INTEGRITY-v3-ledger-end-reconcile"
WAITING_PROOF_VERSION = (
    "MLB-HISTORICAL-STATE-INTEGRITY-v2-settled-horizon-ledger-aware"
)
WAITING_PHASE = "WAITING_FOR_SETTLED_HORIZON"
_VOLATILE_STATE_FIELDS = frozenset({"revision", "updatedAtUtc"})


def _date(value: Any, fallback: str) -> date:
    try:
        return date.fromisoformat(str(value or fallback))
    except ValueError:
        return date.fromisoformat(fallback)


def _canonical_ddb_value(value: Any, *, in_mapping: bool = False) -> Any:
    """Normalize exactly the JSON-like material DynamoDB persists and reloads.

    The optimizer's persistence adapter omits mapping entries whose value is
    ``None`` and converts finite floats to ``Decimal`` before writing. The read
    adapter converts integral decimals to ``int`` and non-integral decimals to
    ``float``. Comparing pre-write Python dictionaries directly therefore treats
    a semantically identical state as changed. This normalizer mirrors that
    round-trip before the state fingerprint is calculated.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("historical state contains non-finite decimal")
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("historical state contains non-finite float")
        decimal_value = Decimal(str(value))
        return (
            int(decimal_value)
            if decimal_value == decimal_value.to_integral_value()
            else float(decimal_value)
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, raw_item in value.items():
            if raw_item is None:
                continue
            key = str(raw_key)
            if key in output:
                raise ValueError(
                    "historical state contains duplicate keys after string normalization"
                )
            output[key] = _canonical_ddb_value(raw_item, in_mapping=True)
        return output
    if isinstance(value, list):
        return [_canonical_ddb_value(item) for item in value]
    if isinstance(value, bytes):
        return {"__ddb_binary_hex__": value.hex()}
    if isinstance(value, (set, frozenset)):
        normalized = [_canonical_ddb_value(item) for item in value]
        return {
            "__ddb_set__": sorted(
                normalized,
                key=lambda item: json.dumps(
                    item,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            )
        }
    raise ValueError(
        f"historical state contains unsupported value type:{type(value).__name__}"
    )


def _material(handler: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    value = handler._migrate_state(copy.deepcopy(dict(state or {})))
    for key in _VOLATILE_STATE_FIELDS:
        value.pop(key, None)
    normalized = _canonical_ddb_value(value)
    if not isinstance(normalized, dict):
        raise ValueError("historical state material is not an object")
    return normalized


def _material_fingerprint(handler: Any, state: Mapping[str, Any]) -> str:
    body = json.dumps(
        _material(handler, state),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _proven_ledger_end(state: Mapping[str, Any], fallback: str) -> date:
    """Return the latest date already proven by the active authorized ledger."""
    candidates = [_date(state.get("endDate"), fallback)]
    plan = state.get("plan") or {}
    if isinstance(plan, Mapping):
        raw_plan_end = plan.get("endDate") or plan.get("plannedThroughDate")
        if raw_plan_end:
            candidates.append(_date(raw_plan_end, fallback))
        for row in plan.get("slates") or []:
            if not isinstance(row, Mapping) or not row.get("slateDateEt"):
                continue
            candidates.append(_date(row.get("slateDateEt"), fallback))
    return max(candidates)


def _waiting_proof_matches(
    state: Mapping[str, Any],
    *,
    previous_end: date,
    horizon: date,
    configured_end: date,
) -> bool:
    """Return true when an existing settled-horizon proof is already sufficient.

    Older proofs intentionally remain valid. Optional telemetry may be added when
    entering a new wait, but it must not force a one-time rewrite of an otherwise
    semantically identical waiting state.
    """
    if state.get("phase") != WAITING_PHASE:
        return False
    proof = state.get("settledHorizonWait") or {}
    if not isinstance(proof, Mapping):
        return False
    expected_next = (previous_end + timedelta(days=1)).isoformat()
    return bool(
        str(proof.get("authorizedThroughDate") or "") == previous_end.isoformat()
        and str(proof.get("settledHorizonDate") or "") == horizon.isoformat()
        and str(proof.get("configuredCeilingDate") or "") == configured_end.isoformat()
        and str(proof.get("nextEligibleSlateDate") or "") == expected_next
        and proof.get("blockingError") is False
        and str(state.get("rangeExtensionNextRetryDate") or "") == expected_next
        and not state.get("lastError")
    )


def install(handler: Any, base: Any) -> None:
    """Install idempotent state writes and an honest settled-horizon phase."""
    if getattr(handler, "_INQSI_HISTORICAL_STATE_INTEGRITY_V1_INSTALLED", False):
        return

    original_save_state = handler._save_state

    def save_state_if_changed(state: Mapping[str, Any]) -> dict[str, Any]:
        candidate = handler._migrate_state(copy.deepcopy(dict(state or {})))
        candidate["version"] = handler.VERSION
        current = handler._load_state()
        if isinstance(current, Mapping) and _material_fingerprint(
            handler, current
        ) == _material_fingerprint(handler, candidate):
            return copy.deepcopy(dict(current))
        return original_save_state(candidate)

    handler._save_state = save_state_if_changed

    original_append = base._append_authorized_range_extension

    def append_with_settled_horizon_state() -> None:
        state = handler._load_state()
        if isinstance(state, dict):
            persisted_end = _date(state.get("endDate"), handler.END_DATE)
            ledger_end = _proven_ledger_end(state, handler.END_DATE)
            configured_end = _date(handler.END_DATE, handler.END_DATE)

            # A completed/fingerprinted ledger row is stronger evidence than a stale
            # top-level endDate. Repair only forward, never past the configured ceiling.
            repaired_end = min(ledger_end, configured_end)
            if repaired_end > persisted_end:
                repaired = copy.deepcopy(state)
                repaired["endDate"] = repaired_end.isoformat()
                repaired["rangeExtensionStateRepair"] = {
                    "version": VERSION,
                    "priorEndDate": persisted_end.isoformat(),
                    "repairedEndDate": repaired_end.isoformat(),
                    "authority": "existing_fingerprinted_plan_only",
                    "newProviderEvidenceCreated": False,
                }
                handler._save_state(repaired)
                state = handler._load_state()

            previous_end = _date(state.get("endDate"), handler.END_DATE)
            horizon = min(
                configured_end,
                incremental_range_extension.settled_horizon(),
            )
            if state.get("phase") == WAITING_PHASE and horizon > previous_end:
                resumed = copy.deepcopy(state)
                resumed["phase"] = "DATA_RANGE_EXHAUSTED"
                resumed["lastError"] = None
                resumed.pop("settledHorizonWait", None)
                handler._save_state(resumed)

        original_append()

        state = handler._load_state()
        if not isinstance(state, dict):
            return
        previous_end = _date(state.get("endDate"), handler.END_DATE)
        configured_end = _date(handler.END_DATE, handler.END_DATE)
        horizon = min(
            configured_end,
            incremental_range_extension.settled_horizon(),
        )
        current = _date(state.get("currentDate"), previous_end.isoformat())
        phase = str(state.get("phase") or "")
        target = int(state.get("targetSettledGames") or 0)
        eligible = int(state.get("eligibleGameCount") or 0)

        # DATA_RANGE_EXHAUSTED at the settled horizon is a temporal wait, not an
        # execution failure. BACKFILLING should enter the same wait only after its
        # cursor is beyond the proven range; otherwise there may still be slots left.
        should_wait = (
            configured_end > previous_end
            and horizon <= previous_end
            and phase in {"BACKFILLING", "DATA_RANGE_EXHAUSTED", WAITING_PHASE}
            and (phase == "DATA_RANGE_EXHAUSTED" or current > previous_end or phase == WAITING_PHASE)
        )
        if not should_wait:
            return
        if _waiting_proof_matches(
            state,
            previous_end=previous_end,
            horizon=horizon,
            configured_end=configured_end,
        ):
            return

        waiting = copy.deepcopy(state)
        waiting["phase"] = WAITING_PHASE
        if str(waiting.get("lastError") or "").startswith(
            "configured historical range ended before"
        ):
            waiting["lastError"] = None
        waiting["rangeExtensionNextRetryDate"] = (
            previous_end + timedelta(days=1)
        ).isoformat()
        waiting["settledHorizonWait"] = {
            "version": WAITING_PROOF_VERSION,
            "authorizedThroughDate": previous_end.isoformat(),
            "settledHorizonDate": horizon.isoformat(),
            "configuredCeilingDate": configured_end.isoformat(),
            "nextEligibleSlateDate": (
                previous_end + timedelta(days=1)
            ).isoformat(),
            "blockingError": False,
            "eligibleGameCount": eligible,
            "targetSettledGames": target,
            "remainingEvidenceGames": max(0, target - eligible),
        }
        handler._save_state(waiting)

    base._append_authorized_range_extension = append_with_settled_horizon_state
    handler._INQSI_HISTORICAL_STATE_INTEGRITY_V1_INSTALLED = True
