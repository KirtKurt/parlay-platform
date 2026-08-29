from __future__ import annotations

import copy
import math
from decimal import Decimal
from itertools import islice
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple


VERSION = "MLB-FUNDAMENTALS-SCORING-BRIDGE-v2-source-honest-shadow-only"
AUTHORITY_MODE = "SHADOW_ONLY_NO_LIVE_SCORING_AUTHORITY"
SHADOW_FIELD = "fundamentalsScoringShadow"
SNAPSHOT_DETERMINISM_VERSION = (
    "MLB-FUNDAMENTALS-SNAPSHOT-CAPTURE-TIME-v1-immutable-pregame-source"
)
DETERMINISTIC_MISSING_CAPTURE_TIME = "1970-01-01T00:00:00+00:00"

# These groups are required before fundamentals may influence live direction or
# confidence. Weather and park are intentionally not required for a side edge:
# they are primarily game-environment inputs and remain explicit missingness
# when unavailable.
ESSENTIAL_GROUPS: Tuple[str, ...] = (
    "confirmed_probable_pitchers",
    "bullpen_availability",
    "confirmed_lineups",
)
QUALITY_GROUPS: Tuple[str, ...] = (
    "starter_quality",
    "offense_quality",
)
SCORING_GROUPS: Tuple[str, ...] = (
    "starter_quality",
    "offense_quality",
    "bullpen_availability",
    "confirmed_lineups",
    "travel_rest",
    "injuries_late_scratches",
)
MAX_SIDE_ADJUSTMENT = 3.0
MAX_SHADOW_LIST_ITEMS = 12
MAX_SHADOW_TEXT_CHARS = 240
MAX_SHADOW_COMPONENTS = 12
EXPECTED_PASSIVE_PROVENANCE_ERROR = (
    "fundamentals_v2_evidence_not_at_or_before_"
    "persisted_prediction_and_lock"
)


def _f(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _bounded_text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)[:MAX_SHADOW_TEXT_CHARS]


def _bounded_strings(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    return [
        str(value)[:MAX_SHADOW_TEXT_CHARS]
        for value in islice(iter(values), MAX_SHADOW_LIST_ITEMS)
    ]


def _dynamo_projection(value: Any) -> Any:
    """Return the exact null/numeric shape written by prediction persistence."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_dynamo_projection(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: _dynamo_projection(item)
            for key, item in value.items()
            if item is not None
        }
    return value


def _matches_canonical_persistence_shape(actual: Any, canonical: Any) -> bool:
    """Accept only the in-memory canonical form or its exact Dynamo projection."""
    return actual == canonical or actual == _dynamo_projection(canonical)


def _snapshot_ref(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    game = snapshot.get("game")
    game = game if isinstance(game, Mapping) else {}
    return {
        "version": _bounded_text(snapshot.get("version")),
        "schemaCohort": _bounded_text(snapshot.get("schemaCohort")),
        "gameId": _bounded_text(game.get("gameId")),
        "sourcePullId": _bounded_text(snapshot.get("sourcePullId")),
        "evidenceCutoffUtc": _bounded_text(snapshot.get("evidenceCutoffUtc")),
        "fingerprintVersion": _bounded_text(snapshot.get("fingerprintVersion")),
        "fingerprint": _bounded_text(snapshot.get("fingerprint")),
    }


def _group(snapshot: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    groups = snapshot.get("groups")
    if not isinstance(groups, Mapping):
        return {}
    value = groups.get(name)
    return value if isinstance(value, Mapping) else {}


def _values(snapshot: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    values = _group(snapshot, name).get("values")
    return values if isinstance(values, Mapping) else {}


def _complete(snapshot: Mapping[str, Any], name: str) -> bool:
    return _group(snapshot, name).get("complete") is True


def _numeric_pair(
    snapshot: Mapping[str, Any],
    group: str,
    home_key: str,
    away_key: str,
) -> Optional[Tuple[float, float]]:
    values = _values(snapshot, group)
    home = _f(values.get(home_key))
    away = _f(values.get(away_key))
    if home is None or away is None:
        return None
    return home, away


def _list_count(value: Any) -> Optional[int]:
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return None


def deterministic_capture_time(row: Mapping[str, Any]) -> str:
    """Return an immutable pregame timestamp for a derived snapshot.

    A public read may execute the runtime wrappers more than once. Wall-clock
    capture time would therefore change the signed snapshot fingerprint between
    cached and uncached reads. Prefer the exact source pull bound to the row;
    fall back only to other persisted pregame timestamps. Rows with no durable
    timestamp remain deterministically incomplete rather than acquiring a new
    timestamp every time they are read.
    """

    lock = row.get("slatePredictionLock")
    lock = lock if isinstance(lock, Mapping) else {}
    candidates = (
        row.get("predictionSourcePullAt"),
        lock.get("latestScoringPullAt"),
        row.get("predictionPersistedAtUtc"),
        row.get("predictionPersistedAt"),
        row.get("persistedAtUtc"),
        row.get("persistedAt"),
        row.get("createdAtUtc"),
    )
    for candidate in candidates:
        if candidate not in (None, ""):
            return str(candidate)
    return DETERMINISTIC_MISSING_CAPTURE_TIME


def _vector(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("featureSnapshot") or row.get("frozenFeatureVector") or {}
    return value if isinstance(value, Mapping) else {}


def _prediction_persisted_at(row: Mapping[str, Any]) -> Any:
    vector = _vector(row)
    return row.get("predictionPersistedAtUtc") or vector.get(
        "predictionPersistedAtUtc"
    )


def _lock_at(row: Mapping[str, Any]) -> Any:
    vector = _vector(row)
    slate_lock = row.get("slatePredictionLock")
    slate_lock = slate_lock if isinstance(slate_lock, Mapping) else {}
    return (
        vector.get("lockAtUtc")
        or row.get("lockAtUtc")
        or row.get("lockedAtUtc")
        or row.get("lockedAt")
        or slate_lock.get("lockAtUtc")
    )


def is_expected_passive_provenance_block(
    shadow: Any,
    row: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Identify the one canonical, non-authoritative pre-persistence block."""
    if not isinstance(shadow, Mapping):
        return False
    row_mapping = row if isinstance(row, Mapping) else {}
    validation_errors = shadow.get("validationErrors")
    return bool(
        shadow.get("evaluated") is False
        and shadow.get("shadowOnly") is False
        and shadow.get("wouldApply") is False
        and shadow.get("liveScoringAuthority") is False
        and shadow.get("canInfluenceLivePick") is False
        and shadow.get("evaluationInputIsolatedCopy") is True
        and shadow.get("liveScoringInputUsedShadowCandidate") is False
        and shadow.get("evidenceBounded") is True
        and shadow.get("mode") == "SHADOW_NOT_EVALUATED"
        and shadow.get("reason") == "snapshot_v2_invalid_or_not_lock_safe"
        and isinstance(validation_errors, (list, tuple))
        and list(validation_errors) == [EXPECTED_PASSIVE_PROVENANCE_ERROR]
        and _prediction_persisted_at(row_mapping) in (None, "")
    )


def install_snapshot_determinism(snapshot_v2_module: Any) -> Any:
    if getattr(
        snapshot_v2_module,
        "_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_DETERMINISM_V1_INSTALLED",
        False,
    ):
        return snapshot_v2_module

    original_build = snapshot_v2_module.build

    def deterministic_build(
        row: Dict[str, Any], *, captured_at_utc: Optional[str] = None
    ) -> Dict[str, Any]:
        captured_at = captured_at_utc or deterministic_capture_time(row)
        return original_build(row, captured_at_utc=captured_at)

    snapshot_v2_module.build = deterministic_build
    snapshot_v2_module.MLB_FUNDAMENTALS_SNAPSHOT_DETERMINISM_VERSION = (
        SNAPSHOT_DETERMINISM_VERSION
    )
    snapshot_v2_module._INQSI_MLB_FUNDAMENTALS_SNAPSHOT_DETERMINISM_V1_INSTALLED = (
        True
    )
    return snapshot_v2_module


def install_snapshot_shadow_evaluation(snapshot_v2_module: Any) -> Any:
    """Attach passive shadow evidence after V2 has attached its snapshot."""
    if getattr(
        snapshot_v2_module,
        "_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_SHADOW_V2_INSTALLED",
        False,
    ):
        return snapshot_v2_module

    original_enhance_row = snapshot_v2_module.enhance_row

    def shadow_enhance_row(row: Dict[str, Any]) -> Dict[str, Any]:
        result = original_enhance_row(row)
        if isinstance(result, dict):
            result[SHADOW_FIELD] = evaluate_shadow(result)
        return result

    snapshot_v2_module.enhance_row = shadow_enhance_row
    snapshot_v2_module.MLB_FUNDAMENTALS_SNAPSHOT_SHADOW_VERSION = VERSION
    snapshot_v2_module._INQSI_MLB_FUNDAMENTALS_SNAPSHOT_SHADOW_V2_INSTALLED = (
        True
    )
    return snapshot_v2_module


def _snapshot_for_row(
    row: MutableMapping[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Sequence[str]]:
    """Read an attached snapshot without fetching or rebuilding live data."""
    existing = row.get("fundamentalsSnapshotV2")
    if isinstance(existing, dict):
        snapshot = existing
    else:
        return None, ("fundamentals_v2_not_attached_shadow_no_fetch",)

    try:
        try:
            import mlb_fundamentals_snapshot_v2 as snapshot_v2
        except ImportError:
            from hello_world import mlb_fundamentals_snapshot_v2 as snapshot_v2

        errors = list(snapshot_v2.validate(snapshot))
        if not errors and not snapshot_v2.provenance_is_lock_safe(
            snapshot,
            prediction_persisted_at=_prediction_persisted_at(row),
            lock_at=_lock_at(row),
        ):
            errors.append(EXPECTED_PASSIVE_PROVENANCE_ERROR)
        errors = tuple(errors)
    except Exception as exc:
        errors = (f"fundamentals_v2_validation_error:{type(exc).__name__}",)
    return snapshot, errors


def _component_edges(snapshot: Mapping[str, Any]) -> Dict[str, float]:
    components: Dict[str, float] = {}

    pair = _numeric_pair(snapshot, "starter_quality", "homeFip", "awayFip")
    if pair is not None:
        home_fip, away_fip = pair
        components["starterFipEdge"] = _clamp((away_fip - home_fip) * 0.80, -2.0, 2.0)
    else:
        pair = _numeric_pair(snapshot, "starter_quality", "homeComposite", "awayComposite")
        if pair is not None:
            home_composite, away_composite = pair
            components["starterCompositeEdge"] = _clamp(
                (home_composite - away_composite) * 2.0,
                -2.0,
                2.0,
            )

    pair = _numeric_pair(snapshot, "offense_quality", "homeWrcPlus", "awayWrcPlus")
    if pair is not None:
        home_wrc, away_wrc = pair
        components["offenseWrcPlusEdge"] = _clamp(
            (home_wrc - away_wrc) / 15.0,
            -1.5,
            1.5,
        )

    pair = _numeric_pair(
        snapshot,
        "bullpen_availability",
        "homeFatigueScore",
        "awayFatigueScore",
    )
    if pair is not None:
        home_fatigue, away_fatigue = pair
        components["bullpenFatigueEdge"] = _clamp(
            (away_fatigue - home_fatigue) * 0.50,
            -1.25,
            1.25,
        )
    else:
        pair = _numeric_pair(
            snapshot,
            "bullpen_availability",
            "homeComposite",
            "awayComposite",
        )
        if pair is not None:
            home_composite, away_composite = pair
            components["bullpenCompositeEdge"] = _clamp(
                (home_composite - away_composite) * 1.5,
                -1.25,
                1.25,
            )

    pair = _numeric_pair(
        snapshot,
        "confirmed_lineups",
        "homeStrengthDelta",
        "awayStrengthDelta",
    )
    if pair is not None:
        home_delta, away_delta = pair
        components["lineupStrengthEdge"] = _clamp(
            (home_delta - away_delta) * 0.50,
            -1.0,
            1.0,
        )
    else:
        pair = _numeric_pair(
            snapshot,
            "confirmed_lineups",
            "homeWrcPlus",
            "awayWrcPlus",
        )
        if pair is not None:
            home_wrc, away_wrc = pair
            components["lineupWrcPlusEdge"] = _clamp(
                (home_wrc - away_wrc) / 20.0,
                -1.0,
                1.0,
            )

    pair = _numeric_pair(snapshot, "travel_rest", "homeRestDays", "awayRestDays")
    if pair is not None:
        home_rest, away_rest = pair
        components["restEdge"] = _clamp((home_rest - away_rest) * 0.15, -0.45, 0.45)

    injury_values = _values(snapshot, "injuries_late_scratches")
    home_injuries = _list_count(injury_values.get("homeKeyInjuries"))
    away_injuries = _list_count(injury_values.get("awayKeyInjuries"))
    if home_injuries is not None and away_injuries is not None:
        components["injuryAvailabilityEdge"] = _clamp(
            (away_injuries - home_injuries) * 0.20,
            -0.60,
            0.60,
        )

    return {name: round(value, 6) for name, value in components.items()}


def _signal(row: Mapping[str, Any], side: str) -> Dict[str, Any]:
    key = "homeSignal" if side == "home" else "awaySignal"
    value = row.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _neutral_metadata(
    snapshot: Optional[Mapping[str, Any]],
    errors: Iterable[str],
    reason: str,
) -> Dict[str, Any]:
    missing = list((snapshot or {}).get("missingGroups") or [])
    return {
        "applied": False,
        "version": VERSION,
        "mode": "NEUTRAL_SOURCE_INCOMPLETE",
        "reason": reason,
        "snapshotVersion": (snapshot or {}).get("version"),
        "snapshotFingerprint": (snapshot or {}).get("fingerprint"),
        "snapshotRef": _snapshot_ref(snapshot or {}),
        "connectedGroups": list((snapshot or {}).get("connectedGroups") or []),
        "missingGroups": missing,
        "validationErrors": sorted(set(str(error) for error in errors if str(error))),
        "weatherMissing": "weather_roof" in missing,
        "missingnessIsFeature": True,
    }


def apply_to_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate the source-honest scoring candidate on an isolated copy.

    This function intentionally retains the original candidate calculation for
    shadow diagnostics and prospective validation.  The live winner-stack
    installer below never passes this returned candidate into production
    scoring.
    """
    out = copy.deepcopy(row or {})
    optimizer = dict(out.get("winnerOptimizer") or {})
    snapshot, validation_errors = _snapshot_for_row(out)
    if snapshot is None or validation_errors:
        metadata = _neutral_metadata(
            snapshot,
            validation_errors,
            "snapshot_missing_or_invalid",
        )
        optimizer.update(
            {
                "fundamentalsApplied": False,
                "fundamentalsMode": metadata["mode"],
                "fundamentals": metadata,
                "fundamentalsScoringBridgeVersion": VERSION,
            }
        )
        out["winnerOptimizer"] = optimizer
        out["fundamentalsLayer"] = metadata
        out["fundamentalsApplied"] = False
        out["fundamentalsMode"] = metadata["mode"]
        return out

    missing_essential = [name for name in ESSENTIAL_GROUPS if not _complete(snapshot, name)]
    quality_available = [name for name in QUALITY_GROUPS if _complete(snapshot, name)]
    components = _component_edges(snapshot)

    if missing_essential or not quality_available or not components:
        reason = (
            "essential_groups_incomplete"
            if missing_essential
            else "quality_group_or_numeric_edge_unavailable"
        )
        metadata = _neutral_metadata(snapshot, (), reason)
        metadata["missingEssentialGroups"] = missing_essential
        metadata["availableQualityGroups"] = quality_available
        optimizer.update(
            {
                "fundamentalsApplied": False,
                "fundamentalsMode": metadata["mode"],
                "fundamentals": metadata,
                "fundamentalsScoringBridgeVersion": VERSION,
            }
        )
        out["winnerOptimizer"] = optimizer
        out["fundamentalsLayer"] = metadata
        out["fundamentalsApplied"] = False
        out["fundamentalsMode"] = metadata["mode"]
        return out

    complete_count = sum(_complete(snapshot, name) for name in SCORING_GROUPS)
    reliability = _clamp(0.55 + 0.45 * (complete_count / len(SCORING_GROUPS)), 0.55, 1.0)
    raw_home_edge = sum(components.values())
    home_adjustment = round(
        _clamp(raw_home_edge * reliability, -MAX_SIDE_ADJUSTMENT, MAX_SIDE_ADJUSTMENT),
        6,
    )
    away_adjustment = round(-home_adjustment, 6)

    home_signal = _signal(out, "home")
    away_signal = _signal(out, "away")
    home_signal["fundamentalsAdjustment"] = home_adjustment
    away_signal["fundamentalsAdjustment"] = away_adjustment
    home_signal["fundamentalsScoringBridgeVersion"] = VERSION
    away_signal["fundamentalsScoringBridgeVersion"] = VERSION
    out["homeSignal"] = home_signal
    out["awaySignal"] = away_signal

    missing = list(snapshot.get("missingGroups") or [])
    metadata = {
        "applied": True,
        "version": VERSION,
        "mode": "TIMESTAMPED_FUNDAMENTALS_V2_PARTIAL_SAFE",
        "snapshotVersion": snapshot.get("version"),
        "snapshotFingerprint": snapshot.get("fingerprint"),
        "snapshotRef": _snapshot_ref(snapshot),
        "connectedGroups": list(snapshot.get("connectedGroups") or []),
        "missingGroups": missing,
        "missingEssentialGroups": [],
        "availableQualityGroups": quality_available,
        "componentEdgesHomePerspective": components,
        "rawHomeEdge": round(raw_home_edge, 6),
        "reliability": round(reliability, 6),
        "homeAdjustment": home_adjustment,
        "awayAdjustment": away_adjustment,
        "weatherMissing": "weather_roof" in missing,
        "weatherAffectsSideEdge": False,
        "missingnessIsFeature": True,
        "policy": (
            "Only source-provenance-valid pregame groups affect the side edge. "
            "Missing weather or park evidence remains explicit and contributes no directional value."
        ),
    }
    optimizer.update(
        {
            "fundamentalsApplied": True,
            "fundamentalsMode": metadata["mode"],
            "fundamentals": metadata,
            "fundamentalsScoringBridgeVersion": VERSION,
        }
    )
    out["winnerOptimizer"] = optimizer
    out["fundamentalsLayer"] = metadata
    out["fundamentalsApplied"] = True
    out["fundamentalsMode"] = metadata["mode"]
    return out


def _not_evaluated_shadow(reason: str) -> Dict[str, Any]:
    return {
        "evaluated": False,
        "version": VERSION,
        "authorityMode": AUTHORITY_MODE,
        "shadowOnly": False,
        "liveScoringAuthority": False,
        "canInfluenceLivePick": False,
        "evaluationInputIsolatedCopy": True,
        "liveScoringInputUsedShadowCandidate": False,
        "wouldApply": False,
        "mode": "SHADOW_NOT_EVALUATED",
        "reason": _bounded_text(reason),
        "snapshotVersion": None,
        "snapshotFingerprint": None,
        "snapshotRef": {},
        "connectedGroups": [],
        "missingGroups": [],
        "missingEssentialGroups": [],
        "availableQualityGroups": [],
        "validationErrors": [],
        "componentEdgesHomePerspective": {},
        "boundedHypotheticalAdjustments": {
            "home": None,
            "away": None,
            "maxAbsolute": MAX_SIDE_ADJUSTMENT,
        },
        "missingnessIsFeature": True,
        "evidenceBounded": True,
    }


def _bounded_snapshot_ref(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in _snapshot_ref(snapshot).items()
        if value not in (None, "")
    }


def evaluate_shadow(row: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate only an attached snapshot; never fetch on the scoring path."""
    if not isinstance((row or {}).get("fundamentalsSnapshotV2"), dict):
        return _not_evaluated_shadow("snapshot_v2_not_attached_no_live_fetch")
    snapshot, source_errors = _snapshot_for_row(dict(row or {}))
    if snapshot is None:
        return _not_evaluated_shadow("snapshot_v2_not_attached_no_live_fetch")
    if source_errors:
        shadow = _not_evaluated_shadow(
            "snapshot_v2_invalid_or_not_lock_safe"
        )
        shadow.update(
            {
                "snapshotVersion": _bounded_text(snapshot.get("version")),
                "snapshotFingerprint": _bounded_text(
                    snapshot.get("fingerprint")
                ),
                "snapshotRef": _bounded_snapshot_ref(snapshot),
                "connectedGroups": _bounded_strings(
                    snapshot.get("connectedGroups")
                ),
                "missingGroups": _bounded_strings(
                    snapshot.get("missingGroups")
                ),
                "validationErrors": _bounded_strings(source_errors),
            }
        )
        return shadow
    try:
        evaluated = apply_to_row(row)
    except Exception as exc:
        return _not_evaluated_shadow(
            f"shadow_evaluation_error:{type(exc).__name__}"
        )
    optimizer = evaluated.get("winnerOptimizer") or {}
    metadata = (
        optimizer.get("fundamentals")
        or evaluated.get("fundamentalsLayer")
        or {}
    )
    applied = bool(
        optimizer.get("fundamentalsApplied") is True
        or evaluated.get("fundamentalsApplied") is True
        or metadata.get("applied") is True
    )
    home_adjustment = _f(metadata.get("homeAdjustment")) if applied else None
    away_adjustment = _f(metadata.get("awayAdjustment")) if applied else None
    if home_adjustment is not None:
        home_adjustment = round(
            _clamp(home_adjustment, -MAX_SIDE_ADJUSTMENT, MAX_SIDE_ADJUSTMENT),
            6,
        )
    if away_adjustment is not None:
        away_adjustment = round(
            _clamp(away_adjustment, -MAX_SIDE_ADJUSTMENT, MAX_SIDE_ADJUSTMENT),
            6,
        )
    component_edges = metadata.get("componentEdgesHomePerspective")
    component_edges = component_edges if isinstance(component_edges, Mapping) else {}
    bounded_components = {
        str(name)[:MAX_SHADOW_TEXT_CHARS]: round(
            _clamp(value, -MAX_SIDE_ADJUSTMENT, MAX_SIDE_ADJUSTMENT),
            6,
        )
        for name, raw_value in islice(
            component_edges.items(), MAX_SHADOW_COMPONENTS
        )
        if (value := _f(raw_value)) is not None
    }
    snapshot_ref = metadata.get("snapshotRef")
    snapshot_ref = snapshot_ref if isinstance(snapshot_ref, Mapping) else {}
    return {
        "evaluated": True,
        "version": VERSION,
        "authorityMode": AUTHORITY_MODE,
        "shadowOnly": True,
        "liveScoringAuthority": False,
        "canInfluenceLivePick": False,
        "evaluationInputIsolatedCopy": True,
        "liveScoringInputUsedShadowCandidate": False,
        "wouldApply": applied,
        "mode": _bounded_text(
            metadata.get("mode") or optimizer.get("fundamentalsMode")
        ),
        "reason": _bounded_text(metadata.get("reason")),
        "snapshotVersion": _bounded_text(metadata.get("snapshotVersion")),
        "snapshotFingerprint": _bounded_text(
            metadata.get("snapshotFingerprint")
        ),
        "snapshotRef": {
            key: _bounded_text(snapshot_ref.get(key))
            for key in (
                "version",
                "schemaCohort",
                "gameId",
                "sourcePullId",
                "evidenceCutoffUtc",
                "fingerprintVersion",
                "fingerprint",
            )
            if snapshot_ref.get(key) not in (None, "")
        },
        "connectedGroups": _bounded_strings(metadata.get("connectedGroups")),
        "missingGroups": _bounded_strings(metadata.get("missingGroups")),
        "missingEssentialGroups": _bounded_strings(
            metadata.get("missingEssentialGroups")
        ),
        "availableQualityGroups": _bounded_strings(
            metadata.get("availableQualityGroups")
        ),
        "validationErrors": _bounded_strings(metadata.get("validationErrors")),
        "componentEdgesHomePerspective": bounded_components,
        "boundedHypotheticalAdjustments": {
            "home": home_adjustment,
            "away": away_adjustment,
            "maxAbsolute": MAX_SIDE_ADJUSTMENT,
        },
        "missingnessIsFeature": True,
        "evidenceBounded": True,
    }


def _strict_finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    return _f(value)


def validate_shadow_attestation(
    shadow: Any,
    row: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """Validate an attached shadow against a fresh canonical recomputation.

    The attestation is diagnostics only, but a malformed or self-authored
    payload must never be treated as proof that shadow inputs were isolated
    from live scoring.  This validator is deliberately total over JSON/Dynamo
    shapes: malformed nested values become bounded reason codes, never errors.
    """
    if not shadow:
        return []
    if not isinstance(shadow, Mapping):
        return ["shadow_payload_invalid_type"]

    row = row if isinstance(row, Mapping) else {}
    errors: list[str] = []
    if shadow.get("version") != VERSION:
        errors.append("shadow_version_invalid")
    if shadow.get("authorityMode") != AUTHORITY_MODE:
        errors.append("shadow_authority_mode_invalid")
    if shadow.get("liveScoringAuthority") is not False:
        errors.append("shadow_live_authority_invalid")
    if shadow.get("canInfluenceLivePick") is not False:
        errors.append("shadow_pick_influence_invalid")
    if shadow.get("evidenceBounded") is not True:
        errors.append("shadow_bounded_attestation_missing")
    if shadow.get("evaluationInputIsolatedCopy") is not True:
        errors.append("shadow_evaluation_input_not_isolated")
    if shadow.get("liveScoringInputUsedShadowCandidate") is not False:
        errors.append("shadow_live_scoring_used_candidate")

    evaluated = shadow.get("evaluated") is True
    would_apply = shadow.get("wouldApply") is True
    if evaluated and shadow.get("shadowOnly") is not True:
        errors.append("shadow_only_attestation_invalid")
    if not evaluated and would_apply:
        errors.append("unevaluated_shadow_cannot_apply")

    claimed_persistence = _prediction_persisted_at(row)
    requires_provenance = evaluated or claimed_persistence not in (None, "")
    snapshot = row.get("fundamentalsSnapshotV2")
    snapshot_mapping = snapshot if isinstance(snapshot, Mapping) else None
    expected_shadow_ref: Dict[str, Any] = {}
    if snapshot_mapping is None:
        if evaluated:
            errors.append("shadow_current_snapshot_missing")
    else:
        try:
            try:
                import mlb_fundamentals_snapshot_v2 as snapshot_contract
            except ImportError:
                from hello_world import (
                    mlb_fundamentals_snapshot_v2 as snapshot_contract,
                )

            snapshot_errors = list(snapshot_contract.validate(dict(snapshot_mapping)))
            if snapshot_errors:
                errors.append("shadow_current_snapshot_invalid")
            elif requires_provenance and not snapshot_contract.provenance_is_lock_safe(
                dict(snapshot_mapping),
                prediction_persisted_at=claimed_persistence,
                lock_at=_lock_at(row),
            ):
                errors.append("shadow_current_snapshot_provenance_invalid")
        except Exception:
            errors.append("shadow_current_snapshot_validation_unavailable")

        expected_row_ref = _snapshot_ref(snapshot_mapping)
        current_ref = row.get("fundamentalsSnapshotV2Ref")
        if not isinstance(current_ref, Mapping):
            errors.append("shadow_current_snapshot_ref_invalid")
        elif not _matches_canonical_persistence_shape(
            dict(current_ref), expected_row_ref
        ):
            errors.append("shadow_current_snapshot_ref_invalid")

        expected_shadow_ref = {
            key: value
            for key, value in expected_row_ref.items()
            if value not in (None, "")
        }
        if shadow.get("snapshotVersion") != snapshot_mapping.get("version"):
            errors.append("shadow_snapshot_version_binding_invalid")
        if shadow.get("snapshotFingerprint") != snapshot_mapping.get(
            "fingerprint"
        ):
            errors.append("shadow_current_snapshot_binding_invalid")

    shadow_ref = shadow.get("snapshotRef")
    if not isinstance(shadow_ref, Mapping):
        errors.append("shadow_snapshot_ref_invalid_type")
    elif dict(shadow_ref) != expected_shadow_ref:
        errors.append("shadow_snapshot_ref_binding_invalid")

    adjustments = shadow.get("boundedHypotheticalAdjustments")
    if not isinstance(adjustments, Mapping):
        errors.append("shadow_adjustments_invalid_type")
    else:
        maximum = _strict_finite_number(adjustments.get("maxAbsolute"))
        if maximum != MAX_SIDE_ADJUSTMENT:
            errors.append("shadow_adjustment_max_contract_invalid")
        if would_apply:
            home = _strict_finite_number(adjustments.get("home"))
            away = _strict_finite_number(adjustments.get("away"))
            if (
                home is None
                or away is None
                or maximum != MAX_SIDE_ADJUSTMENT
                or abs(home) > MAX_SIDE_ADJUSTMENT
                or abs(away) > MAX_SIDE_ADJUSTMENT
                or abs(home + away) > 1e-6
            ):
                errors.append("shadow_adjustment_bounds_invalid")

    try:
        canonical_input = copy.deepcopy(dict(row))
        canonical_input.pop(SHADOW_FIELD, None)
        canonical = evaluate_shadow(canonical_input)
        if not _matches_canonical_persistence_shape(dict(shadow), canonical):
            errors.append("shadow_canonical_evaluation_mismatch")
    except Exception:
        errors.append("shadow_canonical_evaluation_unavailable")

    return sorted(set(errors))


def install_winner_stack(winner_stack_module: Any) -> Any:
    """Install a shadow evaluator that cannot feed the live winner stack."""
    if getattr(
        winner_stack_module,
        "_INQSI_MLB_FUNDAMENTALS_SCORING_SHADOW_V2_INSTALLED",
        False,
    ):
        return winner_stack_module
    if getattr(
        winner_stack_module,
        "_INQSI_MLB_FUNDAMENTALS_SCORING_BRIDGE_V1_INSTALLED",
        False,
    ):
        # A v1 wrapper captured its live-scoring delegate in a closure and
        # cannot be safely unwrapped in place.  Refuse to place a shadow-only
        # attestation around potentially authoritative legacy behavior.  A
        # normal deployment starts a fresh process and installs v2 directly.
        raise RuntimeError(
            "legacy_live_fundamentals_wrapper_requires_process_restart"
        )

    original = winner_stack_module.enhance_prediction

    def patched_enhance_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
        # The live scorer receives the original row, never the evaluated
        # candidate.  Only the namespaced, non-authoritative evidence is added
        # after normal winner-stack scoring has completed.
        production_result = original(row)
        if not isinstance(production_result, dict):
            return production_result
        shadow = evaluate_shadow(row)
        # Add the namespaced evidence to a new outer mapping.  Existing
        # production fields and the object returned by the live scorer remain
        # untouched, even if the scorer returned the caller's input mapping.
        annotated_result = dict(production_result)
        annotated_result[SHADOW_FIELD] = shadow
        return annotated_result

    winner_stack_module.enhance_prediction = patched_enhance_prediction
    winner_stack_module.MLB_FUNDAMENTALS_SCORING_BRIDGE_VERSION = VERSION
    winner_stack_module.MLB_FUNDAMENTALS_SCORING_BRIDGE_AUTHORITY_MODE = (
        AUTHORITY_MODE
    )
    winner_stack_module.MLB_FUNDAMENTALS_SCORING_BRIDGE_SHADOW_ONLY = True
    winner_stack_module.MLB_FUNDAMENTALS_SCORING_BRIDGE_CAN_INFLUENCE_LIVE_PICK = (
        False
    )
    winner_stack_module._INQSI_MLB_FUNDAMENTALS_SCORING_BRIDGE_V1_INSTALLED = True
    winner_stack_module._INQSI_MLB_FUNDAMENTALS_SCORING_SHADOW_V2_INSTALLED = True
    return winner_stack_module
