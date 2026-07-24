"""Runtime contract for the MLB historical daily-slate optimizer.

The module is deliberately dependency-light so the same policy can be:

* searched against historical The Odds API snapshots,
* validated before activation,
* loaded from the production DynamoDB table, and
* applied inside the existing MLB game-winner Lambda without a redeploy.

A historical candidate has no authority unless every promotion invariant in
``validate_champion`` passes. Before first promotion, positively verified absence
of both champion and production cutover leaves the reviewed incumbent active.
First promotion atomically writes both records. After that cutover, any missing,
unreadable, disabled, or invalid historical authority fails closed and can never
silently restore the retired incumbent.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


VERSION = "MLB-HISTORICAL-SIGNAL-POLICY-v1.4-1000-train-200-validation-200-audit-prediction-only-wager-disabled"
CHAMPION_PK = "MLB_HISTORICAL_CHAMPION#V1"
CHAMPION_SK = "CHAMPION"
CHAMPION_RECORD_TYPE = "mlb_historical_daily_optimizer_champion_v1"
# The permanent cutover marker uses its own partition key so the optimizer role
# can be granted lease deletion without any capability to delete this marker.
CUTOVER_PK = "MLB_HISTORICAL_PRODUCTION_CUTOVER#V1"
CUTOVER_SK = "PRODUCTION_CUTOVER"
CUTOVER_RECORD_TYPE = "mlb_historical_production_cutover_v2"
CUTOVER_VERSION = "MLB-HISTORICAL-PRODUCTION-CUTOVER-v2-dedicated-partitions-write-once-no-legacy-fallback"
CUTOVER_AUTHORITY_MODE = "HISTORICAL_DAILY_OPTIMIZER_ONLY"
POLICY_SCHEMA_VERSION = "MLB-HISTORICAL-POLICY-SCHEMA-v1"
PROMOTION_GATE_VERSION = "MLB-HISTORICAL-DAILY-PROMOTION-GATE-v2-1000-200-200"

MIN_TRAINING_GAMES = 1000
MIN_WALK_FORWARD_GAMES = 200
MIN_UNTOUCHED_AUDIT_GAMES = 200
MIN_TOTAL_SETTLED_GAMES = (
    MIN_TRAINING_GAMES + MIN_WALK_FORWARD_GAMES + MIN_UNTOUCHED_AUDIT_GAMES
)
# Compatibility name used by older callers.  It now means the full evidence
# corpus, not the training partition.
MIN_SETTLED_GAMES = MIN_TOTAL_SETTLED_GAMES
MIN_DAILY_ACCURACY = 0.80
TARGET_DAILY_ACCURACY_HIGH = 0.90
MIN_UNTOUCHED_HOLDOUT_DAYS = 15
MIN_WALK_FORWARD_DAYS = 20
MIN_EXACT_SLATE_COVERAGE = 1.0

# The baseline is intentionally the current live market/movement formula.  It
# is used as the search incumbent and as a compatibility reference; the runtime
# patch does not apply at all when no approved champion exists.
BASELINE_POLICY: Dict[str, Any] = {
    "schemaVersion": POLICY_SCHEMA_VERSION,
    "movementWeight": 0.70,
    "movementClip": 0.030,
    "underdogMovementWeight": 0.35,
    "underdogMovementCap": 0.008,
    "heavyFavoritePenalty": 0.004,
    "heavyFavoritePrice": -185.0,
    "divergenceStart": 0.035,
    "divergenceWeight": 0.50,
    "divergenceCap": 0.012,
    "reversalPenalty": 0.004,
    "reversalCap": 0.018,
    "lowPullDepthMinimum": 4,
    "lowPullDepthMultiplier": 0.55,
    "velocity60mWeight": 0.0,
    "acceleration180mWeight": 0.0,
    "volatility180mPenalty": 0.0,
    "coverageShortfallPenalty": 0.0,
    "homeBias": 0.0,
    "favoriteBias": 0.0,
    "underdogBias": 0.0,
    "scoreEdgeWeight": 900.0,
    "scoreEvWeight": 220.0,
    "scoreDeltaWeight": 260.0,
    "scoreDivergencePenalty": 110.0,
    "scoreReversalPenalty": 2.5,
    "scorePositiveValueBonus": 4.0,
    "scoreHeavyFavoritePenalty": 4.0,
    "minimumModelProbability": 0.35,
    "maximumPromotedDogPrice": 180.0,
    "maximumBookDivergence": 0.075,
    "minimumPromotionEdge": 0.0015,
    "minimumPromotionEv": 0.0,
}

_NUMERIC_BOUNDS: Dict[str, Tuple[float, float]] = {
    "movementWeight": (0.0, 2.0),
    "movementClip": (0.005, 0.10),
    "underdogMovementWeight": (-0.5, 1.5),
    "underdogMovementCap": (0.0, 0.05),
    "heavyFavoritePenalty": (0.0, 0.05),
    "heavyFavoritePrice": (-400.0, -120.0),
    "divergenceStart": (0.0, 0.15),
    "divergenceWeight": (0.0, 2.0),
    "divergenceCap": (0.0, 0.10),
    "reversalPenalty": (0.0, 0.05),
    "reversalCap": (0.0, 0.15),
    "lowPullDepthMinimum": (2.0, 20.0),
    "lowPullDepthMultiplier": (0.10, 1.0),
    "velocity60mWeight": (-0.02, 0.02),
    "acceleration180mWeight": (-0.01, 0.01),
    "volatility180mPenalty": (0.0, 0.05),
    "coverageShortfallPenalty": (0.0, 0.10),
    "homeBias": (-0.03, 0.03),
    "favoriteBias": (-0.03, 0.03),
    "underdogBias": (-0.03, 0.03),
    "scoreEdgeWeight": (100.0, 2500.0),
    "scoreEvWeight": (0.0, 1000.0),
    "scoreDeltaWeight": (-500.0, 1000.0),
    "scoreDivergencePenalty": (0.0, 500.0),
    "scoreReversalPenalty": (0.0, 15.0),
    "scorePositiveValueBonus": (-5.0, 15.0),
    "scoreHeavyFavoritePenalty": (0.0, 15.0),
    "minimumModelProbability": (0.05, 0.80),
    "maximumPromotedDogPrice": (100.0, 500.0),
    "maximumBookDivergence": (0.01, 0.30),
    "minimumPromotionEdge": (-0.02, 0.10),
    "minimumPromotionEv": (-0.20, 0.30),
}

_CACHE: Dict[str, Any] = {
    "expires": 0.0,
    "champion": None,
    "status": "UNINITIALIZED",
    "error": None,
}
_CUTOVER_CACHE: Dict[str, Any] = {
    "expires": 0.0,
    "cutover": None,
    "status": "UNINITIALIZED",
    "error": None,
    "everActivated": False,
}
_CACHE_SECONDS = max(5, int(os.environ.get("MLB_HISTORICAL_POLICY_CACHE_SECONDS", "60")))


@dataclass(frozen=True)
class ChampionValidation:
    ok: bool
    errors: Tuple[str, ...]
    policy: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class CutoverValidation:
    ok: bool
    errors: Tuple[str, ...]
    cutover: Optional[Dict[str, Any]] = None


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _f(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _canonical(value: Any) -> Any:
    value = _plain(value)
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite value")
        return format(value, ".17g")
    return value


def digest(value: Any) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def policy_digest(policy: Mapping[str, Any]) -> str:
    return digest(dict(policy))


def validate_policy(policy: Any) -> Tuple[str, ...]:
    errors = []
    if not isinstance(policy, Mapping):
        return ("policy_not_object",)
    if policy.get("schemaVersion") != POLICY_SCHEMA_VERSION:
        errors.append("policy_schema_version_mismatch")
    missing = sorted(set(_NUMERIC_BOUNDS) - set(policy))
    if missing:
        errors.append("policy_fields_missing:" + ",".join(missing))
    unknown = sorted(set(policy) - ({"schemaVersion"} | set(_NUMERIC_BOUNDS)))
    if unknown:
        errors.append("policy_fields_unknown:" + ",".join(unknown))
    for name, (low, high) in _NUMERIC_BOUNDS.items():
        if name not in policy:
            continue
        value = _f(policy.get(name), float("nan"))
        if not math.isfinite(value) or value < low or value > high:
            errors.append(f"policy_field_out_of_bounds:{name}")
    minimum = _f(policy.get("lowPullDepthMinimum"), -1.0)
    if minimum != int(minimum):
        errors.append("low_pull_depth_minimum_not_integer")
    return tuple(sorted(set(errors)))


def validate_champion(item: Any) -> ChampionValidation:
    value = _plain(item)
    errors = []
    if not isinstance(value, Mapping):
        return ChampionValidation(False, ("champion_not_object",), None)
    if value.get("PK") not in {None, CHAMPION_PK}:
        errors.append("champion_partition_key_mismatch")
    if value.get("SK") not in {None, CHAMPION_SK}:
        errors.append("champion_sort_key_mismatch")
    if value.get("record_type") not in {None, CHAMPION_RECORD_TYPE}:
        errors.append("champion_record_type_mismatch")
    data = value.get("data") if isinstance(value.get("data"), Mapping) else value
    if not isinstance(data, Mapping):
        return ChampionValidation(False, ("champion_data_missing",), None)
    if data.get("version") != VERSION:
        errors.append("champion_version_mismatch")
    if data.get("recordType") != CHAMPION_RECORD_TYPE:
        errors.append("champion_data_record_type_mismatch")
    if data.get("liveAuthorityEnabled") is not True:
        errors.append("live_authority_not_enabled")
    if data.get("shadowOnly") is True:
        errors.append("champion_is_shadow_only")
    gate = data.get("promotionGate") or {}
    if not isinstance(gate, Mapping):
        errors.append("promotion_gate_missing")
        gate = {}
    if gate.get("version") != PROMOTION_GATE_VERSION:
        errors.append("promotion_gate_version_mismatch")
    if gate.get("passed") is not True:
        errors.append("promotion_gate_not_passed")
    partition_floors = (
        ("trainingGameCount", MIN_TRAINING_GAMES, "training_game_floor_not_met"),
        ("walkForwardGameCount", MIN_WALK_FORWARD_GAMES, "walk_forward_game_floor_not_met"),
        (
            "untouchedHoldoutGameCount",
            MIN_UNTOUCHED_AUDIT_GAMES,
            "untouched_audit_game_floor_not_met",
        ),
        ("settledGameCount", MIN_TOTAL_SETTLED_GAMES, "settled_game_floor_not_met"),
    )
    for field, required, code in partition_floors:
        if int(gate.get(field) or 0) < required:
            errors.append(code)
    if int(gate.get("walkForwardDayCount") or 0) < MIN_WALK_FORWARD_DAYS:
        errors.append("walk_forward_day_floor_not_met")
    if int(gate.get("untouchedHoldoutDayCount") or 0) < MIN_UNTOUCHED_HOLDOUT_DAYS:
        errors.append("holdout_day_floor_not_met")
    for name in (
        "walkForwardMinimumDailyAccuracy",
        "walkForwardMeanDailyAccuracy",
        "untouchedHoldoutMinimumDailyAccuracy",
        "untouchedHoldoutMeanDailyAccuracy",
    ):
        if _f(gate.get(name), -1.0) + 1e-12 < MIN_DAILY_ACCURACY:
            errors.append(f"daily_accuracy_gate_failed:{name}")
    for name in ("walkForwardSlateCoverage", "untouchedHoldoutSlateCoverage"):
        if _f(gate.get(name), -1.0) + 1e-12 < MIN_EXACT_SLATE_COVERAGE:
            errors.append(f"slate_coverage_gate_failed:{name}")
    if gate.get("holdoutWasUntouchedDuringSearch") is not True:
        errors.append("holdout_not_untouched")
    if gate.get("chronologicalWholeSlateSplits") is not True:
        errors.append("whole_slate_split_proof_missing")
    if gate.get("postLockDataExcluded") is not True:
        errors.append("post_lock_exclusion_proof_missing")
    if gate.get("gameSpecificLockClipping") is not True:
        errors.append("game_specific_lock_clipping_proof_missing")
    if gate.get("overfitChecksPassed") is not True:
        errors.append("overfit_checks_not_passed")
    policy = data.get("policy")
    errors.extend(validate_policy(policy))
    if isinstance(policy, Mapping):
        expected = policy_digest(policy)
        if data.get("policyDigest") != expected:
            errors.append("policy_digest_mismatch")
    artifact = data.get("artifact") or {}
    if not isinstance(artifact, Mapping) or not all(
        str(artifact.get(key) or "") for key in ("bucket", "key", "versionId", "sha256")
    ):
        errors.append("versioned_artifact_pointer_incomplete")
    return ChampionValidation(
        not errors,
        tuple(sorted(set(errors))),
        copy.deepcopy(dict(policy)) if isinstance(policy, Mapping) and not errors else None,
    )



def _is_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def build_cutover_payload(champion: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the write-once record that retires V15.10 production authority."""

    artifact = champion.get("artifact") or {}
    return {
        "version": CUTOVER_VERSION,
        "recordType": CUTOVER_RECORD_TYPE,
        "productionAuthorityMode": CUTOVER_AUTHORITY_MODE,
        "productionAlgorithmFamily": VERSION,
        "championPartitionKey": CHAMPION_PK,
        "cutoverPartitionKey": CUTOVER_PK,
        "historicalOnly": True,
        "legacyFallbackAllowed": False,
        "automaticLegacyRestoreAllowed": False,
        "incumbentProductionAuthorityDestroyed": True,
        "incumbentRuntimeRole": "quarantined_feature_and_manual_rollback_artifact_only",
        "irreversibleWithoutExplicitRollbackDeployment": True,
        "automaticWagerAllowed": False,
        "initialChampionPolicyDigest": str(champion.get("policyDigest") or ""),
        "initialChampionArtifactSha256": str(artifact.get("sha256") or ""),
        "promotionGateVersion": PROMOTION_GATE_VERSION,
        "activatedAtUtc": str(champion.get("activatedAtUtc") or ""),
    }


def validate_cutover(item: Any) -> CutoverValidation:
    value = _plain(item)
    errors = []
    if not isinstance(value, Mapping):
        return CutoverValidation(False, ("cutover_not_object",), None)
    if value.get("PK") not in {None, CUTOVER_PK}:
        errors.append("cutover_partition_key_mismatch")
    if value.get("SK") not in {None, CUTOVER_SK}:
        errors.append("cutover_sort_key_mismatch")
    if value.get("record_type") not in {None, CUTOVER_RECORD_TYPE}:
        errors.append("cutover_record_type_mismatch")
    data = value.get("data") if isinstance(value.get("data"), Mapping) else value
    if not isinstance(data, Mapping):
        return CutoverValidation(False, ("cutover_data_missing",), None)
    required_equal = {
        "version": CUTOVER_VERSION,
        "recordType": CUTOVER_RECORD_TYPE,
        "productionAuthorityMode": CUTOVER_AUTHORITY_MODE,
        "productionAlgorithmFamily": VERSION,
        "championPartitionKey": CHAMPION_PK,
        "cutoverPartitionKey": CUTOVER_PK,
        "promotionGateVersion": PROMOTION_GATE_VERSION,
    }
    for name, expected in required_equal.items():
        if data.get(name) != expected:
            errors.append(f"cutover_field_mismatch:{name}")
    for name in (
        "historicalOnly",
        "incumbentProductionAuthorityDestroyed",
        "irreversibleWithoutExplicitRollbackDeployment",
    ):
        if data.get(name) is not True:
            errors.append(f"cutover_true_field_missing:{name}")
    if data.get("legacyFallbackAllowed") is not False:
        errors.append("legacy_fallback_not_disabled")
    if data.get("automaticLegacyRestoreAllowed") is not False:
        errors.append("automatic_legacy_restore_not_disabled")
    if data.get("automaticWagerAllowed") is not False:
        errors.append("automatic_wager_not_disabled")
    if not str(data.get("activatedAtUtc") or "").strip():
        errors.append("cutover_activation_time_missing")
    if not _is_sha256(data.get("initialChampionPolicyDigest")):
        errors.append("cutover_initial_policy_digest_invalid")
    if not _is_sha256(data.get("initialChampionArtifactSha256")):
        errors.append("cutover_initial_artifact_digest_invalid")
    return CutoverValidation(
        not errors,
        tuple(sorted(set(errors))),
        copy.deepcopy(dict(data)) if not errors else None,
    )


def _read_cutover_from_dynamodb() -> Any:
    table_name = os.environ.get("SNAPSHOTS_TABLE", "").strip()
    if not table_name:
        return None
    import boto3  # Lambda runtime dependency; imported lazily for local tests.

    table = boto3.resource("dynamodb").Table(table_name)
    return table.get_item(
        Key={"PK": CUTOVER_PK, "SK": CUTOVER_SK}, ConsistentRead=True
    ).get("Item")


def active_production_cutover(
    *, force_refresh: bool = False, loader=None
) -> Optional[Dict[str, Any]]:
    """Load the permanent historical-only production cutover marker.

    Once observed, a transient read failure or unexpected deletion never makes
    the legacy selector eligible again in a warm process. A cold process reads
    the same persistent marker before allowing any historical-only prediction.
    """

    now = time.monotonic()
    if not force_refresh and now < float(_CUTOVER_CACHE.get("expires") or 0.0):
        cached = _CUTOVER_CACHE.get("cutover")
        return copy.deepcopy(cached) if isinstance(cached, dict) else None
    _CUTOVER_CACHE["expires"] = now + _CACHE_SECONDS
    if os.environ.get("MLB_HISTORICAL_POLICY_ENABLED", "true").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        _CUTOVER_CACHE["status"] = "DISABLED"
        _CUTOVER_CACHE["error"] = "historical_policy_disabled"
        cached = _CUTOVER_CACHE.get("cutover")
        return copy.deepcopy(cached) if isinstance(cached, dict) else None
    try:
        item = (loader or _read_cutover_from_dynamodb)()
    except Exception as exc:
        _CUTOVER_CACHE["status"] = "ERROR"
        _CUTOVER_CACHE["error"] = f"{type(exc).__name__}:{exc}"
        cached = _CUTOVER_CACHE.get("cutover")
        return copy.deepcopy(cached) if isinstance(cached, dict) else None
    if item is None:
        cached = _CUTOVER_CACHE.get("cutover")
        if isinstance(cached, dict) or _CUTOVER_CACHE.get("everActivated") is True:
            _CUTOVER_CACHE["status"] = "MISSING_AFTER_ACTIVATION"
            _CUTOVER_CACHE["error"] = "historical_only_cutover_record_missing"
            return copy.deepcopy(cached) if isinstance(cached, dict) else None
        _CUTOVER_CACHE["cutover"] = None
        _CUTOVER_CACHE["status"] = "ABSENT"
        _CUTOVER_CACHE["error"] = None
        return None
    validation = validate_cutover(item)
    if not validation.ok:
        _CUTOVER_CACHE["status"] = "INVALID"
        _CUTOVER_CACHE["error"] = ",".join(validation.errors)
        cached = _CUTOVER_CACHE.get("cutover")
        return copy.deepcopy(cached) if isinstance(cached, dict) else None
    cutover = copy.deepcopy(validation.cutover or {})
    _CUTOVER_CACHE["cutover"] = cutover
    _CUTOVER_CACHE["status"] = "ACTIVE"
    _CUTOVER_CACHE["error"] = None
    _CUTOVER_CACHE["everActivated"] = True
    return copy.deepcopy(cutover)


def production_cutover_status() -> Dict[str, Any]:
    cutover = _CUTOVER_CACHE.get("cutover")
    return {
        "status": str(_CUTOVER_CACHE.get("status") or "UNINITIALIZED"),
        "error": _CUTOVER_CACHE.get("error"),
        "active": isinstance(cutover, dict),
        "everActivated": bool(_CUTOVER_CACHE.get("everActivated")),
        "historicalOnly": bool((cutover or {}).get("historicalOnly"))
        if isinstance(cutover, dict)
        else False,
        "legacyFallbackAllowed": (cutover or {}).get("legacyFallbackAllowed")
        if isinstance(cutover, dict)
        else None,
        "productionAuthorityMode": (cutover or {}).get("productionAuthorityMode")
        if isinstance(cutover, dict)
        else None,
        "cacheExpiresMonotonic": float(_CUTOVER_CACHE.get("expires") or 0.0),
    }

def _read_champion_from_dynamodb() -> Any:
    table_name = os.environ.get("SNAPSHOTS_TABLE", "").strip()
    if not table_name:
        return None
    import boto3  # Lambda runtime dependency; imported lazily for local tests.

    table = boto3.resource("dynamodb").Table(table_name)
    return table.get_item(
        Key={"PK": CHAMPION_PK, "SK": CHAMPION_SK}, ConsistentRead=True
    ).get("Item")


def active_champion(*, force_refresh: bool = False, loader=None) -> Optional[Dict[str, Any]]:
    """Load the current champion while preserving last-known-good authority.

    A missing champion is a normal pre-promotion state.  A provider/read error
    or an invalid non-empty champion is not equivalent to absence: callers can
    inspect ``champion_load_status`` and fail closed instead of silently handing
    authority back to the incumbent.  Once a valid champion has been observed,
    transient lookup failures retain that last-known-good object.
    """

    now = time.monotonic()
    if not force_refresh and now < float(_CACHE.get("expires") or 0.0):
        cached = _CACHE.get("champion")
        return copy.deepcopy(cached) if isinstance(cached, dict) else None
    _CACHE["expires"] = now + _CACHE_SECONDS
    if os.environ.get("MLB_HISTORICAL_POLICY_ENABLED", "true").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        _CACHE["status"] = "DISABLED"
        _CACHE["error"] = "historical_policy_disabled"
        return copy.deepcopy(_CACHE.get("champion")) if isinstance(_CACHE.get("champion"), dict) else None
    try:
        item = (loader or _read_champion_from_dynamodb)()
    except Exception as exc:
        _CACHE["status"] = "ERROR"
        _CACHE["error"] = f"{type(exc).__name__}:{exc}"
        cached = _CACHE.get("champion")
        return copy.deepcopy(cached) if isinstance(cached, dict) else None
    if item is None:
        cached = _CACHE.get("champion")
        if isinstance(cached, dict):
            _CACHE["status"] = "MISSING_AFTER_ACTIVATION"
            _CACHE["error"] = "champion_pointer_missing_after_valid_activation"
            return copy.deepcopy(cached)
        _CACHE["champion"] = None
        _CACHE["status"] = "ABSENT"
        _CACHE["error"] = None
        return None
    validation = validate_champion(item)
    if not validation.ok:
        _CACHE["status"] = "INVALID"
        _CACHE["error"] = ",".join(validation.errors)
        cached = _CACHE.get("champion")
        return copy.deepcopy(cached) if isinstance(cached, dict) else None
    data = _plain(item.get("data") if isinstance(item, Mapping) and item.get("data") else item)
    champion = copy.deepcopy(dict(data))
    champion["policy"] = validation.policy
    _CACHE["champion"] = champion
    _CACHE["status"] = "ACTIVE"
    _CACHE["error"] = None
    return copy.deepcopy(champion)


def champion_load_status() -> Dict[str, Any]:
    return {
        "status": str(_CACHE.get("status") or "UNINITIALIZED"),
        "error": _CACHE.get("error"),
        "hasLastKnownGoodChampion": isinstance(_CACHE.get("champion"), dict),
        "cacheExpiresMonotonic": float(_CACHE.get("expires") or 0.0),
    }


def _nested(mapping: Any, *path: str) -> Any:
    value = mapping
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _american_decimal(value: Any) -> Optional[float]:
    price = _f(value, 0.0)
    if price == 0.0:
        return None
    return 1.0 + (100.0 / abs(price)) if price < 0 else 1.0 + (price / 100.0)


def _market_side(price: Any) -> str:
    value = _f(price, 0.0)
    if value <= -120:
        return "favorite"
    if value >= 105:
        return "underdog"
    return "pickem"


def _temporal_value(signal: Mapping[str, Any], horizon: str, name: str) -> float:
    return _f(_nested(signal, "temporalFeatures", "horizons", horizon, name), 0.0)


def apply_policy_to_signal(signal: Mapping[str, Any], policy: Mapping[str, Any]) -> Dict[str, Any]:
    """Apply one frozen candidate policy to one home/away signal.

    The formula is intentionally the same in the historical search and live
    patch. Temporal velocity is expressed in percentage points/hour, so its
    coefficient is scaled directly into probability points by the searched
    weight.
    """

    errors = validate_policy(policy)
    if errors:
        raise ValueError("invalid historical policy: " + ",".join(errors))
    out = copy.deepcopy(dict(signal or {}))
    fair = _f(out.get("fairProbability", out.get("probLatest")), 0.5)
    fair = min(0.999, max(0.001, fair))
    delta = _f(out.get("delta"), 0.0)
    divergence = max(0.0, _f(out.get("bookDivergence"), 0.0))
    reversals = max(0, int(_f(out.get("reversalCount"), 0.0)))
    pull_count = max(
        0,
        int(
            _f(
                out.get("pullCountForGame", _nested(out, "temporalFeatures", "sourcePointCount")),
                0.0,
            )
        ),
    )
    coverage = min(
        1.0,
        max(0.0, _temporal_value(out, "full", "coverageRatio")),
    )
    velocity60 = _temporal_value(out, "60m", "velocityPpHr")
    acceleration180 = _temporal_value(out, "180m", "accelerationPpHr2")
    volatility180 = max(0.0, _temporal_value(out, "180m", "volatilityPpPerPull"))
    price = out.get("americanOdds")
    side_type = str(out.get("marketSide") or _market_side(price)).lower()

    movement = max(
        -_f(policy.get("movementClip")),
        min(_f(policy.get("movementClip")), delta * _f(policy.get("movementWeight"))),
    )
    if side_type == "underdog" and delta > 0:
        movement += min(
            _f(policy.get("underdogMovementCap")),
            delta * _f(policy.get("underdogMovementWeight")),
        )
    if side_type == "favorite" and _f(price, 0.0) <= _f(policy.get("heavyFavoritePrice")):
        movement -= _f(policy.get("heavyFavoritePenalty"))
    if divergence > _f(policy.get("divergenceStart")):
        movement -= min(
            _f(policy.get("divergenceCap")),
            (divergence - _f(policy.get("divergenceStart")))
            * _f(policy.get("divergenceWeight")),
        )
    if reversals:
        movement -= min(
            _f(policy.get("reversalCap")),
            reversals * _f(policy.get("reversalPenalty")),
        )
    movement += velocity60 * _f(policy.get("velocity60mWeight"))
    movement += acceleration180 * _f(policy.get("acceleration180mWeight"))
    movement -= volatility180 * _f(policy.get("volatility180mPenalty"))
    movement -= (1.0 - coverage) * _f(policy.get("coverageShortfallPenalty"))
    if pull_count and pull_count < int(_f(policy.get("lowPullDepthMinimum"), 4.0)):
        movement *= _f(policy.get("lowPullDepthMultiplier"), 0.55)
    if str(out.get("side") or "").lower() == "home":
        movement += _f(policy.get("homeBias"))
    if side_type == "favorite":
        movement += _f(policy.get("favoriteBias"))
    elif side_type == "underdog":
        movement += _f(policy.get("underdogBias"))

    probability = min(0.95, max(0.05, fair + movement))
    edge = probability - fair
    decimal = _american_decimal(price)
    expected_value = probability * decimal - 1.0 if decimal else -1.0

    blocked = []
    if price in (None, ""):
        blocked.append("NO_BETTABLE_PRICE")
    if int(_f(out.get("bookCount"), 0.0)) < 1:
        blocked.append("NO_BOOK_CONSENSUS")
    if probability < _f(policy.get("minimumModelProbability")):
        blocked.append("MODEL_PROB_TOO_LOW")
    if side_type == "underdog" and _f(price, 0.0) > _f(policy.get("maximumPromotedDogPrice")):
        blocked.append("LONG_DOG_PRICE_GUARD")
    if divergence > _f(policy.get("maximumBookDivergence")):
        blocked.append("BOOK_DIVERGENCE_GUARD")
    if expected_value < _f(policy.get("minimumPromotionEv")):
        blocked.append("NEGATIVE_EV_GUARD")

    promoted = bool(
        not blocked
        and edge >= _f(policy.get("minimumPromotionEdge"))
        and expected_value >= _f(policy.get("minimumPromotionEv"))
    )
    score = (
        50.0
        + edge * _f(policy.get("scoreEdgeWeight"))
        + expected_value * _f(policy.get("scoreEvWeight"))
        + delta * _f(policy.get("scoreDeltaWeight"))
        - divergence * _f(policy.get("scoreDivergencePenalty"))
        - reversals * _f(policy.get("scoreReversalPenalty"))
    )
    if side_type in {"underdog", "pickem"} and edge > 0 and expected_value > 0:
        score += _f(policy.get("scorePositiveValueBonus"))
    if side_type == "favorite" and _f(price, 0.0) <= _f(policy.get("heavyFavoritePrice")):
        score -= _f(policy.get("scoreHeavyFavoritePenalty"))
    score = min(100.0, max(0.0, score))

    out.update(
        {
            "fairProbability": round(fair, 8),
            "fairProbabilityPct": round(fair * 100.0, 4),
            "winProbability": round(probability, 8),
            "winProbabilityPct": round(probability * 100.0, 4),
            "edgeVsBook": round(edge, 8),
            "edgeVsBookPct": round(edge * 100.0, 4),
            "expectedValue": round(expected_value, 8),
            "expectedValuePct": round(expected_value * 100.0, 4),
            "score": round(score, 4),
            "promoted": promoted,
            "promotionStatus": "PROMOTED" if promoted else "NO_PLAY",
            "blockedReasons": blocked,
            "historicalPolicyApplied": True,
            "historicalPolicyVersion": VERSION,
            "historicalPolicyDigest": policy_digest(policy),
            "historicalPolicyProbabilityAdjustment": round(probability - fair, 8),
            "historicalPolicyTemporalInputs": {
                "velocity60mPpHr": round(velocity60, 8),
                "acceleration180mPpHr2": round(acceleration180, 8),
                "volatility180mPpPerPull": round(volatility180, 8),
                "coverageRatioFull": round(coverage, 8),
            },
        }
    )
    return out


def _prob_from_score(score: Any) -> float:
    value = _f(score, 50.0)
    probability = 1.0 / (1.0 + math.exp(-(value - 50.0) / 12.0))
    return max(0.05, min(0.95, probability))


def fixed_rule_adjustment(signal: Mapping[str, Any]) -> float:
    """Mirror the fixed, non-learning rule adjustment used by production."""

    tags = {str(value) for value in signal.get("tags") or []}
    unstable = {
        "BOOK_DIVERGENCE",
        "COMPRESSED_MARKET",
        "UNCONFIRMED_RUN_LINE_MOVE",
        "LATE_INSTABILITY",
        "RESISTANCE",
    }
    reversal_count = int(_f(signal.get("reversalCount"), 0.0))
    market_prob = _f(
        signal.get("marketConsensusProbability", signal.get("probLatest")),
        _f(signal.get("fairProbability"), 0.5),
    )
    market_edge = market_prob - 0.5
    delta = _f(signal.get("delta"), 0.0)
    run_line_move = abs(_f(signal.get("runLineMovement"), 0.0))
    adjustment = 0.0
    for tag, penalty in (
        ("SINGLE_PULL_BASELINE", -8.0),
        ("LOW_PULL_DEPTH", -5.0),
        ("BOOK_DIVERGENCE", -3.0),
        ("LATE_INSTABILITY", -6.0),
        ("RESISTANCE", -4.0),
    ):
        if tag in tags:
            adjustment += penalty
    if market_prob < 0.50:
        adjustment -= 3.0
        if reversal_count >= 2:
            adjustment -= 2.5
    elif market_prob >= 0.54 and "BOOK_AGREEMENT" in tags and reversal_count <= 1:
        adjustment += 1.0
    if reversal_count >= 5:
        adjustment -= 7.5
    elif reversal_count >= 3:
        adjustment -= 5.5
    elif reversal_count == 2:
        adjustment -= 2.5
    elif reversal_count == 1 and "BOOK_AGREEMENT" not in tags:
        adjustment -= 0.75
    clean_confirmation = (
        "BOOK_AGREEMENT" in tags
        and reversal_count <= 1
        and market_edge >= 0.03
        and not (tags & unstable)
    )
    if "RUN_LINE_CONFIRMATION" in tags:
        adjustment += 2.0 if clean_confirmation else -2.25
    if "RUN_LINE_MOVEMENT" in tags and "RUN_LINE_CONFIRMATION" not in tags:
        if "BOOK_AGREEMENT" in tags and "STEAM" in tags and reversal_count <= 1 and market_edge >= 0.02:
            adjustment += 0.25
        else:
            adjustment -= 1.25
        if run_line_move >= 50 and market_edge < 0.05:
            adjustment -= 1.0
    if "STEAM" in tags:
        if "BOOK_AGREEMENT" in tags and reversal_count <= 1 and market_edge >= 0.02:
            adjustment += 1.0
        elif "BOOK_DIVERGENCE" in tags or reversal_count >= 3:
            adjustment -= 1.75
    if "COMPRESSED_MARKET" in tags:
        adjustment -= 1.25
        if market_edge < 0.03:
            adjustment -= 1.0
    aligned = clean_confirmation and ("STEAM" in tags or "RUN_LINE_CONFIRMATION" in tags)
    if delta > 0 and reversal_count >= 3 and not aligned:
        adjustment -= 4.0 if reversal_count < 5 else 6.0
    if "BOOK_AGREEMENT" in tags:
        if reversal_count <= 1 and not (tags & unstable):
            adjustment += 0.75 if market_edge >= 0.02 else 0.25
        elif reversal_count >= 3:
            adjustment -= 0.5
    return round(max(-16.0, min(12.0, adjustment)), 2)


def production_optimized_signal(signal: Mapping[str, Any], policy: Mapping[str, Any]) -> Dict[str, Any]:
    out = apply_policy_to_signal(signal, policy)
    raw_score = _f(out.get("score"), 0.0)
    rule_adjustment = fixed_rule_adjustment(out)
    optimized_score = max(0.0, min(100.0, raw_score + rule_adjustment))
    probability = _prob_from_score(optimized_score)
    out.update(
        {
            "rawScoreBeforeWinnerOptimizer": raw_score,
            "rolling24hLearningAdjustment": 0.0,
            "winnerRuleAdjustment": rule_adjustment,
            "optimizedWinnerScore": round(optimized_score, 4),
            "score": round(optimized_score, 4),
            "winProbability": round(probability, 8),
            "winProbabilityPct": round(probability * 100.0, 4),
            "historicalPolicyLearningIsolation": True,
        }
    )
    return out


def _market_probability(signal: Mapping[str, Any]) -> float:
    return _f(
        signal.get("marketConsensusProbability", signal.get("probLatest")),
        _f(signal.get("fairProbability"), 0.5),
    )


def _directional_override(candidate: Mapping[str, Any], market_anchor: Mapping[str, Any]) -> bool:
    if not candidate or not market_anchor or candidate.get("team") == market_anchor.get("team"):
        return True
    tags = {str(value) for value in candidate.get("tags") or []}
    bad = {"LOW_PULL_DEPTH", "SINGLE_PULL_BASELINE", "BOOK_DIVERGENCE", "LATE_INSTABILITY"}
    unstable = {
        "BOOK_DIVERGENCE",
        "COMPRESSED_MARKET",
        "UNCONFIRMED_RUN_LINE_MOVE",
        "LATE_INSTABILITY",
        "RESISTANCE",
    }
    reversal_count = int(_f(candidate.get("reversalCount"), 0.0))
    market_prob = _market_probability(candidate)
    delta = _f(candidate.get("delta"), 0.0)
    score = _f(candidate.get("optimizedWinnerScore"), 0.0)
    margin = score - _f(market_anchor.get("optimizedWinnerScore"), 0.0)
    return bool(
        market_prob >= 0.42
        and score >= 64.0
        and margin >= 8.0
        and delta >= 0.01
        and reversal_count <= 1
        and "BOOK_AGREEMENT" in tags
        and bool({"STEAM", "RUN_LINE_CONFIRMATION"} & tags)
        and not (tags & (bad | unstable))
    )


def select_winner(
    home_signal: Mapping[str, Any], away_signal: Mapping[str, Any], policy: Mapping[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    home = production_optimized_signal(home_signal, policy)
    away = production_optimized_signal(away_signal, policy)
    market_anchor = home if _market_probability(home) >= _market_probability(away) else away
    candidate = home if _f(home.get("optimizedWinnerScore")) >= _f(away.get("optimizedWinnerScore")) else away
    selected = candidate if _directional_override(candidate, market_anchor) else market_anchor
    return selected, home, away


def complementary_probabilities(
    home_signal: Mapping[str, Any], away_signal: Mapping[str, Any]
) -> Tuple[float, float]:
    """Return a calibrated complementary home/away pair from two side scores.

    The historical policy scores both sides independently.  Runtime probability
    contracts and Brier/log-loss evaluation require a coherent pair that sums to
    one, so normalize the bounded side probabilities without changing direction.
    """

    home_raw = min(0.999999, max(0.000001, _f(home_signal.get("winProbability"), 0.5)))
    away_raw = min(0.999999, max(0.000001, _f(away_signal.get("winProbability"), 0.5)))
    total = home_raw + away_raw
    if total <= 0.0 or not math.isfinite(total):
        return 0.5, 0.5
    home_probability = min(0.999999, max(0.000001, home_raw / total))
    return home_probability, 1.0 - home_probability


def apply(engine_module: Any, accuracy_module: Any = None) -> Any:
    """Install a fail-closed live patch on the current MLB engine.

    The patch is inert until a digest-valid champion meets the 1,000-game
    training, 200-game walk-forward validation, 200-game untouched-audit,
    whole-slate, and every-day accuracy gates.
    """

    if getattr(engine_module, "_INQSI_MLB_HISTORICAL_POLICY_V1_APPLIED", False):
        return engine_module
    original_side_score = engine_module._side_score

    def patched_side_score(series, side):
        signal = original_side_score(series, side)
        champion = active_champion()
        cutover = active_production_cutover()
        champion_state = champion_load_status()
        cutover_state = production_cutover_status()
        if champion and cutover:
            updated = apply_policy_to_signal(signal, champion["policy"])
            updated["historicalChampionArtifact"] = copy.deepcopy(champion.get("artifact") or {})
            updated["historicalPromotionGate"] = copy.deepcopy(champion.get("promotionGate") or {})
            updated["historicalChampionActivatedAtUtc"] = champion.get("activatedAtUtc")
            return updated
        if champion and not cutover:
            raise RuntimeError(
                "historical_champion_without_atomic_cutover_fail_closed:"
                + str(cutover_state.get("status") or "UNKNOWN")
            )
        if cutover and not champion:
            raise RuntimeError(
                "historical_only_cutover_missing_champion_fail_closed:"
                + str(champion_state.get("status") or "UNKNOWN")
            )
        unsafe = {"ERROR", "INVALID", "DISABLED", "MISSING_AFTER_ACTIVATION"}
        if champion_state.get("status") in unsafe or cutover_state.get("status") in unsafe:
            raise RuntimeError(
                "historical_authority_unavailable_fail_closed:"
                + str(champion_state.get("status") or "UNKNOWN")
                + ":"
                + str(cutover_state.get("status") or "UNKNOWN")
            )
        if champion_state.get("status") == "ABSENT" and cutover_state.get("status") == "ABSENT":
            return signal
        raise RuntimeError(
            "historical_authority_state_ambiguous_fail_closed:"
            + str(champion_state.get("status") or "UNKNOWN")
            + ":"
            + str(cutover_state.get("status") or "UNKNOWN")
        )

    engine_module._side_score = patched_side_score

    # The existing winner optimizer ordinarily adds mutable rolling-learning
    # adjustments. An activated historical policy was proved against a frozen
    # formula, so isolate it from that post-promotion mutation while retaining
    # the same fixed production safety rules.
    if accuracy_module is not None and hasattr(accuracy_module, "_optimized_signal"):
        original_optimized = accuracy_module._optimized_signal

        def patched_optimized_signal(signal):
            if not isinstance(signal, Mapping) or signal.get("historicalPolicyApplied") is not True:
                return original_optimized(signal)
            out = copy.deepcopy(dict(signal))
            raw_score = _f(out.get("score"), 0.0)
            rule_adjustment = fixed_rule_adjustment(out)
            optimized_score = max(0.0, min(100.0, raw_score + rule_adjustment))
            probability = _prob_from_score(optimized_score)
            out.update(
                {
                    "rawScoreBeforeWinnerOptimizer": raw_score,
                    "rolling24hLearningAdjustment": 0.0,
                    "winnerRuleAdjustment": rule_adjustment,
                    "optimizedWinnerScore": round(optimized_score, 4),
                    "score": round(optimized_score, 4),
                    "winProbability": round(probability, 8),
                    "winProbabilityPct": round(probability * 100.0, 4),
                    "historicalPolicyLearningIsolation": True,
                }
            )
            return out

        accuracy_module._optimized_signal = patched_optimized_signal

    engine_module._INQSI_MLB_HISTORICAL_POLICY_V1_APPLIED = True
    engine_module.MLB_HISTORICAL_POLICY_VERSION = VERSION
    return engine_module

def _normalized_model_probabilities(
    home_signal: Mapping[str, Any], away_signal: Mapping[str, Any]
) -> Tuple[float, float]:
    home = max(1e-9, _f(home_signal.get("winProbability"), 0.5))
    away = max(1e-9, _f(away_signal.get("winProbability"), 0.5))
    total = home + away
    if not math.isfinite(total) or total <= 0.0:
        return 0.5, 0.5
    home_probability = max(0.01, min(0.99, home / total))
    return home_probability, 1.0 - home_probability


def apply_champion_to_result(
    result: Any,
    champion: Optional[Mapping[str, Any]] = None,
    *,
    allow_locked_rows: bool = True,
) -> Any:
    """Make a validated historical champion the final MLB output authority.

    This function intentionally runs *after* all legacy/ranked wrappers.  The
    previous algorithms may remain loaded for diagnostics, but they cannot
    select a team, probability, or production-algorithm label once a valid
    historical champion exists.  The 80 percent assertion is attached only to
    the complete-day slate evidence, never to an individual game.
    """

    if not isinstance(result, Mapping):
        raise RuntimeError("historical_champion_result_not_object_fail_closed")
    active = copy.deepcopy(dict(champion)) if isinstance(champion, Mapping) else active_champion()
    selected_policy = (active or {}).get("policy") if active else None
    if not isinstance(selected_policy, Mapping):
        raise RuntimeError("historical_champion_policy_missing_fail_closed")
    output = copy.deepcopy(dict(result))
    rows = output.get("predictions")
    if not isinstance(rows, list):
        raise RuntimeError("historical_champion_predictions_missing_fail_closed")
    changed = 0
    eligible = 0
    failures = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not allow_locked_rows and (
            row.get("lockedPrediction") is True
            or row.get("immutableLocked") is True
            or str(row.get("lockStatus") or "").upper()
            in {"LOCKED", "LOCKED_PREDICTION", "FINAL_LOCKED"}
        ):
            continue
        eligible += 1
        home_signal = row.get("homeSignal")
        away_signal = row.get("awaySignal")
        if not isinstance(home_signal, Mapping) or not isinstance(away_signal, Mapping):
            failures.append("missing_home_or_away_signal")
            continue
        try:
            selected, home, away = select_winner(home_signal, away_signal, selected_policy)
        except Exception:
            # A promoted champion is fail-closed per row: do not silently stamp
            # authority metadata onto a row that could not be rescored.
            row["productionSelectionAllowed"] = False
            row["actionablePick"] = False
            row["selectionStatus"] = "PASS"
            row["historicalAuthorityError"] = "champion_row_rescore_failed"
            failures.append("champion_row_rescore_failed")
            continue
        side = str(selected.get("side") or "").lower()
        if side not in {"home", "away"}:
            row["productionSelectionAllowed"] = False
            row["actionablePick"] = False
            row["selectionStatus"] = "PASS"
            row["historicalAuthorityError"] = "champion_selected_side_invalid"
            failures.append("champion_selected_side_invalid")
            continue
        home_team = str(row.get("homeTeam") or home.get("team") or "")
        away_team = str(row.get("awayTeam") or away.get("team") or "")
        winner = home_team if side == "home" else away_team
        opponent = away_team if side == "home" else home_team
        if not winner:
            row["productionSelectionAllowed"] = False
            row["actionablePick"] = False
            row["selectionStatus"] = "PASS"
            row["historicalAuthorityError"] = "champion_selected_team_missing"
            failures.append("champion_selected_team_missing")
            continue
        home_probability, away_probability = _normalized_model_probabilities(home, away)
        selected_probability = home_probability if side == "home" else away_probability
        previous = {
            "modelVersion": row.get("modelVersion"),
            "predictedSide": row.get("predictedSide"),
            "predictedWinner": row.get("predictedWinner"),
            "winProbability": row.get("winProbability"),
        }
        row.update(
            {
                "homeSignal": home,
                "awaySignal": away,
                "predictedSide": side,
                "predictedWinner": winner,
                "opponent": opponent,
                "homeModelWinProbability": round(home_probability, 12),
                "awayModelWinProbability": round(away_probability, 12),
                "modelWinProbability": round(selected_probability, 12),
                "winProbability": round(selected_probability, 12),
                "winProbabilityPct": round(selected_probability * 100.0, 4),
                "teamWinProbabilityPct": round(selected_probability * 100.0, 4),
                "score": selected.get("score"),
                "americanOdds": selected.get("americanOdds"),
                "modelVersion": VERSION,
                "primaryAlgorithm": VERSION,
                "legacySelectorUsed": False,
                "legacyRecommendationAuthority": False,
                "legacyAlgorithmAuthorityDisabled": True,
                "soleProductionAlgorithm": True,
                "historicalPolicyApplied": True,
                "historicalPolicyDigest": active.get("policyDigest"),
                "historicalChampionArtifact": copy.deepcopy(active.get("artifact") or {}),
                "historicalPromotionGate": copy.deepcopy(active.get("promotionGate") or {}),
                "historicalChampionActivatedAtUtc": active.get("activatedAtUtc"),
                "dailySlateAccuracyGatePassed": True,
                "dailySlateAccuracyRequirement": MIN_DAILY_ACCURACY,
                "dailySlateAccuracyTargetHigh": TARGET_DAILY_ACCURACY_HIGH,
                "accuracyEvidenceScope": "complete_day_slate_not_individual_game",
                "precisionQualified": False,
                "precisionQualificationScope": "not_an_individual_game_80_percent_claim",
                "selectionStatus": "PICK",
                "productionSelectionAllowed": True,
                # The champion is the sole prediction authority, not an automatic
                # wagering authority. Keep row-level flags fail-closed so no
                # downstream consumer can bypass the explicit API-level wager ban.
                "actionablePick": False,
                "playable": False,
                "playablePick": False,
                "automaticWagerAllowed": False,
                "wagerAuthorization": "DISABLED",
                "officialPick": True,
                "officialPrediction": True,
                "historicalPreAuthorityDiagnostic": previous,
            }
        )
        blocked = [
            value
            for value in (row.get("blockedReasons") or [])
            if str(value) not in {"RELEASE_BLOCKED", "NO_PICK"}
        ]
        if "automatic_wagering_disabled" not in {str(value) for value in blocked}:
            blocked.append("automatic_wagering_disabled")
        row["blockedReasons"] = blocked
        tags = {str(value) for value in row.get("tags") or []}
        tags.difference_update({"NO_PICK", "RELEASE_BLOCKED", "ML_REJECTED", "NOT_PLAYABLE"})
        tags.update(
            {
                "HISTORICAL_DAILY_CHAMPION",
                "PICK",
                "PREDICTION_ONLY",
                "SOLE_PRODUCTION_ALGORITHM",
                "WAGER_DISABLED",
            }
        )
        row["tags"] = sorted(tags)
        changed += 1
    if failures or changed != eligible:
        raise RuntimeError(
            "historical_champion_incomplete_slate_rescore_fail_closed:"
            + ",".join(sorted(set(failures or ["coverage_mismatch"])))
        )
    if changed <= 0:
        # Empty responses and immutable pre-cutover locked rows remain readable;
        # no current winner row is allowed to fall back to the retired selector.
        return output
    output.update(
        {
            "predictions": rows,
            "primaryAlgorithm": VERSION,
            "primaryAlgorithmActive": changed > 0,
            "historicalChampionActive": changed > 0,
            "historicalChampionPolicyDigest": active.get("policyDigest"),
            "historicalChampionArtifact": copy.deepcopy(active.get("artifact") or {}),
            "historicalPromotionGate": copy.deepcopy(active.get("promotionGate") or {}),
            "dailySlateAccuracyGatePassed": True,
            "dailySlateAccuracyRequirement": MIN_DAILY_ACCURACY,
            "dailySlateAccuracyTargetHigh": TARGET_DAILY_ACCURACY_HIGH,
            "accuracyEvidenceScope": "complete_day_slate_not_individual_game",
            "legacyAlgorithmAuthorityDisabled": True,
            "soleProductionAlgorithm": True,
            "automaticWagerAllowed": False,
            "wagerAuthorization": "DISABLED",
            "productionSelectionCount": sum(
                1
                for row in rows
                if isinstance(row, Mapping) and row.get("selectionStatus") == "PICK"
            ),
            "passCount": sum(
                1
                for row in rows
                if isinstance(row, Mapping) and row.get("selectionStatus") != "PICK"
            ),
        }
    )
    return output


def apply_runtime_authority(engine_module: Any) -> Any:
    """Install the historical champion as the outermost production authority.

    The incumbent is usable only while champion and cutover are both positively
    absent. First promotion atomically writes both. Thereafter the historical
    champion is the only MLB winner selector and any authority loss fails closed.
    """

    if getattr(engine_module, "_INQSI_MLB_HISTORICAL_RUNTIME_AUTHORITY_V1_APPLIED", False):
        return engine_module

    def wrap(name: str, *, allow_locked_rows: bool) -> None:
        original = getattr(engine_module, name, None)
        if not callable(original):
            return

        def reader(*args, **kwargs):
            result = original(*args, **kwargs)
            champion = active_champion()
            cutover = active_production_cutover()
            champion_state = champion_load_status()
            cutover_state = production_cutover_status()
            if champion and cutover:
                return apply_champion_to_result(
                    result, champion, allow_locked_rows=allow_locked_rows
                )
            if champion and not cutover:
                raise RuntimeError(
                    "historical_champion_without_atomic_cutover_fail_closed:"
                    + str(cutover_state.get("status") or "UNKNOWN")
                )
            if cutover and not champion:
                raise RuntimeError(
                    "historical_only_cutover_missing_champion_fail_closed:"
                    + str(champion_state.get("status") or "UNKNOWN")
                )
            unsafe = {"ERROR", "INVALID", "DISABLED", "MISSING_AFTER_ACTIVATION"}
            if champion_state.get("status") in unsafe or cutover_state.get("status") in unsafe:
                raise RuntimeError(
                    "historical_champion_authority_unavailable_fail_closed:"
                    + str(champion_state.get("status") or "UNKNOWN")
                    + ":"
                    + str(cutover_state.get("status") or "UNKNOWN")
                )
            if champion_state.get("status") == "ABSENT" and cutover_state.get("status") == "ABSENT":
                return result
            raise RuntimeError(
                "historical_authority_state_ambiguous_fail_closed:"
                + str(champion_state.get("status") or "UNKNOWN")
                + ":"
                + str(cutover_state.get("status") or "UNKNOWN")
            )

        setattr(engine_module, name, reader)

    wrap("predict_all", allow_locked_rows=True)
    # Current pre-lock persisted rows may adopt the champion, but an immutable
    # locked prediction can never be rewritten by a later model activation.
    wrap("read_persisted_predictions", allow_locked_rows=False)
    engine_module._INQSI_MLB_HISTORICAL_RUNTIME_AUTHORITY_V1_APPLIED = True
    engine_module.MLB_HISTORICAL_POLICY_VERSION = VERSION
    engine_module.MLB_HISTORICAL_POLICY_PROMOTION_GATE_VERSION = PROMOTION_GATE_VERSION
    engine_module.MLB_HISTORICAL_CUTOVER_VERSION = CUTOVER_VERSION
    engine_module.MLB_HISTORICAL_LEGACY_FALLBACK_ALLOWED = False
    return engine_module
