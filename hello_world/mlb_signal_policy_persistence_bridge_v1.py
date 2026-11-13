from __future__ import annotations

import copy
from typing import Any, Dict


VERSION = "MLB-SIGNAL-POLICY-PERSISTENCE-BRIDGE-v1-idempotent-public-row"
_ENGINE_MARKER = "_INQSI_MLB_SIGNAL_POLICY_PERSISTENCE_BRIDGE_V1_APPLIED"
_FUNCTION_MARKER = "_inqsi_mlb_signal_policy_persistence_bridge_version"
_POLICY_IDEMPOTENCE_MARKER = "_INQSI_MLB_SIGNAL_POLICY_ROW_IDEMPOTENCE_V1_APPLIED"


def _policy_current(row: Dict[str, Any], signal_policy: Any) -> bool:
    policy = row.get("signalPolicyV13") if isinstance(row, dict) else None
    return bool(
        isinstance(policy, dict)
        and policy.get("applied") is True
        and str(policy.get("version") or "") == str(signal_policy.VERSION)
    )


def _install_idempotent_row_policy(signal_policy: Any) -> None:
    """Make repeated outer policy application safe.

    Lambda startup historically installed the signal policy, then later wrappers
    replaced ``engine.predict_all`` while leaving the module-level applied flag.
    Reasserting the policy after the final public wrapper is therefore required,
    but rows that already contain the exact current policy must not receive a
    second score or playability adjustment.
    """

    if getattr(signal_policy, _POLICY_IDEMPOTENCE_MARKER, False):
        return
    original_apply_row = signal_policy._apply_row

    def idempotent_apply_row(row: Dict[str, Any]) -> Dict[str, Any]:
        if _policy_current(row, signal_policy):
            return copy.deepcopy(row)
        return original_apply_row(row)

    signal_policy._apply_row = idempotent_apply_row
    setattr(signal_policy, _POLICY_IDEMPOTENCE_MARKER, True)


def apply(engine: Any, signal_policy: Any) -> Any:
    """Reassert current signal-policy evidence on final public prediction rows.

    This wrapper is installed after the canonical public-authority overlay and
    before the sole storage finalizer. It may add the current signal-policy
    metadata, score adjustment, and playability risk annotations when they are
    missing. It never changes ``predictedWinner``, ``predictedSide``, or any team
    win probability. Reapplication is idempotent.
    """

    _install_idempotent_row_policy(signal_policy)
    current = engine.predict_all
    if (
        getattr(engine, _ENGINE_MARKER, False)
        and getattr(current, _FUNCTION_MARKER, None) == VERSION
    ):
        return engine

    def patched_predict_all(*args, **kwargs):
        result = current(*args, **kwargs)
        enhanced = signal_policy.enhance_result(result)
        if isinstance(enhanced, dict):
            enhanced = dict(enhanced)
            enhanced["signalPolicyPersistenceBridge"] = {
                "applied": True,
                "version": VERSION,
                "signalPolicyVersion": signal_policy.VERSION,
                "winnerDirectionMayChange": False,
                "teamWinProbabilityMayChange": False,
                "idempotent": True,
            }
        return enhanced

    setattr(patched_predict_all, _FUNCTION_MARKER, VERSION)
    engine.predict_all = patched_predict_all
    engine.MLB_SIGNAL_POLICY_PERSISTENCE_BRIDGE_VERSION = VERSION
    setattr(engine, _ENGINE_MARKER, True)
    return engine
