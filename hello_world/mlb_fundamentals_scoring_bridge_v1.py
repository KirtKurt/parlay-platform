from __future__ import annotations

import copy
import math
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple


VERSION = "MLB-FUNDAMENTALS-SCORING-BRIDGE-v1-source-honest-partial-safe"
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


def _f(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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


def _snapshot_for_row(row: MutableMapping[str, Any]) -> Tuple[Optional[Dict[str, Any]], Sequence[str]]:
    existing = row.get("fundamentalsSnapshotV2")
    if isinstance(existing, dict):
        snapshot = existing
    else:
        try:
            import mlb_fundamentals_snapshot_v2 as snapshot_v2

            install_snapshot_determinism(snapshot_v2)
            snapshot_v2.enhance_row(row)
            candidate = row.get("fundamentalsSnapshotV2")
            snapshot = candidate if isinstance(candidate, dict) else None
        except Exception as exc:
            return None, (f"fundamentals_v2_build_error:{type(exc).__name__}",)

    if not isinstance(snapshot, dict):
        return None, ("fundamentals_v2_missing",)

    try:
        import mlb_fundamentals_snapshot_v2 as snapshot_v2

        errors = tuple(snapshot_v2.validate(snapshot))
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
        "connectedGroups": list((snapshot or {}).get("connectedGroups") or []),
        "missingGroups": missing,
        "validationErrors": sorted(set(str(error) for error in errors if str(error))),
        "weatherMissing": "weather_roof" in missing,
        "missingnessIsFeature": True,
    }


def apply_to_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(row or {})
    optimizer = dict(out.get("winnerOptimizer") or {})
    if (
        optimizer.get("fundamentalsApplied") is True
        and optimizer.get("fundamentalsScoringBridgeVersion") == VERSION
    ):
        return out

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
        "snapshotRef": copy.deepcopy(out.get("fundamentalsSnapshotV2Ref") or {}),
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


def install_winner_stack(winner_stack_module: Any) -> Any:
    if getattr(
        winner_stack_module,
        "_INQSI_MLB_FUNDAMENTALS_SCORING_BRIDGE_V1_INSTALLED",
        False,
    ):
        return winner_stack_module

    original = winner_stack_module.enhance_prediction

    def patched_enhance_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
        return original(apply_to_row(row))

    winner_stack_module.enhance_prediction = patched_enhance_prediction
    winner_stack_module.MLB_FUNDAMENTALS_SCORING_BRIDGE_VERSION = VERSION
    winner_stack_module._INQSI_MLB_FUNDAMENTALS_SCORING_BRIDGE_V1_INSTALLED = True
    return winner_stack_module
