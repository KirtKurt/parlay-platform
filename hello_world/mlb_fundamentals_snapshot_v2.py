from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple


VERSION = "MLB-FUNDAMENTALS-SNAPSHOT-v2-immutable-source-provenance"
SCHEMA_COHORT = "MLB-ML-FUNDAMENTALS-v2"
FINGERPRINT_VERSION = "INQSI-EXACT-TYPED-JSON-SHA256-v1"

# These groups are pregame inputs.  Closing-line value is intentionally absent:
# it is a postgame evaluation metric and cannot be part of a T-45 feature set.
GROUP_SPECS: Tuple[Tuple[str, str, Tuple[Tuple[str, str], ...]], ...] = (
    (
        "confirmed_probable_pitchers",
        "confirmed_probable_pitchers",
        (
            ("homeName", "home_probable_pitcher"),
            ("awayName", "away_probable_pitcher"),
            ("homeId", "home_pitcher_id"),
            ("awayId", "away_pitcher_id"),
            ("gameStatus", "game_status"),
        ),
    ),
    (
        "starter_quality",
        "fip_xfip",
        (
            ("homeFip", "home_starter_fip"),
            ("awayFip", "away_starter_fip"),
            ("homeXfip", "home_starter_xfip"),
            ("awayXfip", "away_starter_xfip"),
            ("homeEra", "home_starter_era"),
            ("awayEra", "away_starter_era"),
            ("homeXera", "home_starter_xera"),
            ("awayXera", "away_starter_xera"),
            ("homeKMinusBbPct", "home_starter_k_minus_bb_pct"),
            ("awayKMinusBbPct", "away_starter_k_minus_bb_pct"),
            ("homeRecentPitchCount", "home_starter_recent_pitch_count"),
            ("awayRecentPitchCount", "away_starter_recent_pitch_count"),
            ("homeRecentInnings", "home_starter_recent_innings"),
            ("awayRecentInnings", "away_starter_recent_innings"),
            ("homeHealthStatus", "home_starter_health_status"),
            ("awayHealthStatus", "away_starter_health_status"),
            ("homeComposite", "home_starter_composite"),
            ("awayComposite", "away_starter_composite"),
        ),
    ),
    (
        "offense_quality",
        "wrc_plus",
        (
            ("homeWrcPlus", "home_team_wrc_plus"),
            ("awayWrcPlus", "away_team_wrc_plus"),
            ("homeWrcPlusVsHand", "home_wrc_plus_vs_pitcher_hand"),
            ("awayWrcPlusVsHand", "away_wrc_plus_vs_pitcher_hand"),
        ),
    ),
    (
        "starter_handedness_splits",
        "starter_handedness_splits",
        (
            ("homeStarterHand", "home_starter_hand"),
            ("awayStarterHand", "away_starter_hand"),
            ("homeOffenseVsOpponentHand", "home_offense_vs_opp_hand"),
            ("awayOffenseVsOpponentHand", "away_offense_vs_opp_hand"),
            ("homePitchMix", "home_pitch_mix"),
            ("awayPitchMix", "away_pitch_mix"),
            ("homeAverageVelocityMph", "home_average_velocity_mph"),
            ("awayAverageVelocityMph", "away_average_velocity_mph"),
        ),
    ),
    (
        "bullpen_availability",
        "bullpen_fatigue",
        (
            ("homeFatigueScore", "home_bullpen_fatigue_score"),
            ("awayFatigueScore", "away_bullpen_fatigue_score"),
            ("homeUsage1d3d5d", "home_reliever_usage_1d_3d_5d"),
            ("awayUsage1d3d5d", "away_reliever_usage_1d_3d_5d"),
            ("homeAvailableRelievers", "home_available_relievers"),
            ("awayAvailableRelievers", "away_available_relievers"),
            ("homeUnavailableRelievers", "home_unavailable_relievers"),
            ("awayUnavailableRelievers", "away_unavailable_relievers"),
            ("homeHighLeverageRoles", "home_high_leverage_roles"),
            ("awayHighLeverageRoles", "away_high_leverage_roles"),
            ("homeComposite", "home_bullpen_composite"),
            ("awayComposite", "away_bullpen_composite"),
        ),
    ),
    (
        "confirmed_lineups",
        "confirmed_lineups",
        (
            ("homeConfirmed", "home_lineup_confirmed"),
            ("awayConfirmed", "away_lineup_confirmed"),
            ("homeWrcPlus", "home_lineup_wrc_plus"),
            ("awayWrcPlus", "away_lineup_wrc_plus"),
            ("homeStrengthDelta", "home_lineup_strength_delta"),
            ("awayStrengthDelta", "away_lineup_strength_delta"),
            ("homeBattingOrder", "home_batting_order"),
            ("awayBattingOrder", "away_batting_order"),
        ),
    ),
    (
        "weather_roof",
        "weather_wind_roof",
        (
            ("temperatureF", "temperature"),
            ("windSpeedMph", "wind_speed"),
            ("windDirection", "wind_direction"),
            ("precipitationRisk", "precipitation_risk"),
            ("roofStatus", "roof_status"),
        ),
    ),
    (
        "ballpark_factors",
        "ballpark_factors",
        (
            ("venueName", "venue_name"),
            ("venueId", "venue_id"),
            ("runsFactor", "park_factor_runs"),
            ("homeRunFactor", "park_factor_hr"),
        ),
    ),
    (
        "injuries_late_scratches",
        "injuries_late_scratches_news",
        (
            ("homeKeyInjuries", "home_key_injuries"),
            ("awayKeyInjuries", "away_key_injuries"),
            ("lateScratchFlags", "late_scratch_flags"),
            ("pitcherChangeFlag", "pitcher_change_flag"),
        ),
    ),
    (
        "travel_rest",
        "travel_rest",
        (
            ("homeRestDays", "home_rest_days"),
            ("awayRestDays", "away_rest_days"),
            ("homeTravelMiles", "home_travel_miles"),
            ("awayTravelMiles", "away_travel_miles"),
        ),
    ),
)

SOURCE_PRESENT_STATUSES = {
    "CONNECTED",
    "PARTIAL",
    "PARTIAL_MISSING_REQUIRED_VALUES",
}

# Historical rows may still carry this provider name. The integration and
# credentials are retired, and its old optimizer used neutral-filled values
# and team/date joins, so those rows must never earn V2 completeness credit.
RETIRED_PROVIDER_TOKENS = ("sportsdataio",)

# These are the minimum fields needed before a group is complete for the new
# prospective cohort. Optional descriptive fields remain in the signed schema,
# but cannot turn missing evidence into a made-up zero.
REQUIRED_VALUE_KEYS: Dict[str, Tuple[str, ...]] = {
    "confirmed_probable_pitchers": ("homeName", "awayName"),
    "starter_quality": (
        "homeFip",
        "awayFip",
        "homeXfip",
        "awayXfip",
        "homeKMinusBbPct",
        "awayKMinusBbPct",
    ),
    "offense_quality": ("homeWrcPlus", "awayWrcPlus"),
    "starter_handedness_splits": (
        "homeStarterHand",
        "awayStarterHand",
        "homePitchMix",
        "awayPitchMix",
        "homeAverageVelocityMph",
        "awayAverageVelocityMph",
    ),
    "bullpen_availability": (
        "homeUsage1d3d5d",
        "awayUsage1d3d5d",
        "homeAvailableRelievers",
        "awayAvailableRelievers",
    ),
    "confirmed_lineups": (
        "homeConfirmed",
        "awayConfirmed",
        "homeBattingOrder",
        "awayBattingOrder",
    ),
    "weather_roof": ("temperatureF", "precipitationRisk", "roofStatus"),
    "ballpark_factors": ("venueName", "runsFactor"),
    "injuries_late_scratches": (
        "homeKeyInjuries",
        "awayKeyInjuries",
        "lateScratchFlags",
        "pitcherChangeFlag",
    ),
    "travel_rest": ("homeRestDays", "awayRestDays"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _canonical(value: Any) -> Any:
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if not decimal_value.is_finite():
            return ["number", str(decimal_value)]
        sign, raw_digits, exponent = decimal_value.as_tuple()
        digits = list(raw_digits)
        while digits and digits[-1] == 0:
            digits.pop()
            exponent += 1
        if not digits:
            exact = "0"
        else:
            coefficient = "".join(str(digit) for digit in digits)
            exact = f"{'-' if sign else ''}{coefficient}e{exponent}"
        return ["number", exact]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, (list, tuple)):
        return ["list", [_canonical(item) for item in value]]
    if isinstance(value, dict):
        return [
            "object",
            [
                [str(key), _canonical(item)]
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ],
        ]
    return ["other", f"{type(value).__module__}.{type(value).__qualname__}", str(value)]


def fingerprint_for_snapshot(snapshot: Dict[str, Any]) -> str:
    material = copy.deepcopy(snapshot)
    material.pop("fingerprint", None)
    encoded = json.dumps(_canonical(material), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _status(item: Dict[str, Any]) -> str:
    return str(item.get("source_status") or item.get("status") or "MISSING").upper()


def _present(value: Any) -> bool:
    # Empty lists are valid evidence for "no injuries/scratches". Empty text is
    # not a value. Numeric zero and False are both legitimate observations.
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _source_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    source = item.get("sourceProvenance") if isinstance(item.get("sourceProvenance"), dict) else {}
    return {
        "provider": source.get("provider") or item.get("provider") or item.get("source"),
        "endpoint": source.get("endpoint") or item.get("endpoint") or item.get("sourceEndpoint"),
        "dataset": source.get("dataset") or item.get("dataset") or item.get("sourceDataset"),
        "retrievedAtUtc": source.get("retrievedAtUtc") or item.get("retrievedAtUtc") or item.get("retrieved_at_utc"),
        "sourceEffectiveAtUtc": source.get("sourceEffectiveAtUtc") or item.get("sourceEffectiveAtUtc") or item.get("source_effective_at_utc"),
        "payloadFingerprint": source.get("payloadFingerprint") or item.get("payloadFingerprint") or item.get("sourcePayloadFingerprint"),
    }


def _retired_provider(value: Any) -> bool:
    normalized = "".join(
        character for character in str(value or "").lower() if character.isalnum()
    )
    return any(token in normalized for token in RETIRED_PROVIDER_TOKENS)


def _identifiers(item: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "gameId": row.get("gameId") or row.get("game_id") or row.get("gameIdentity"),
        "officialGamePk": row.get("officialGamePk") or item.get("game_pk"),
        "providerEventId": row.get("providerEventId") or row.get("provider_event_id"),
        "homeTeam": row.get("homeTeam") or row.get("home_team"),
        "awayTeam": row.get("awayTeam") or row.get("away_team"),
        "homeEntityId": item.get("home_pitcher_id") or item.get("home_team_id"),
        "awayEntityId": item.get("away_pitcher_id") or item.get("away_team_id"),
    }


def _group(
    row: Dict[str, Any],
    context: Dict[str, Any],
    output_name: str,
    context_name: str,
    values: Iterable[Tuple[str, str]],
) -> Dict[str, Any]:
    raw = context.get(context_name) if isinstance(context.get(context_name), dict) else {}
    status = _status(raw)
    source = _source_metadata(raw)
    value_map = {output_key: copy.deepcopy(raw.get(input_key)) for output_key, input_key in values}
    required = REQUIRED_VALUE_KEYS.get(output_name, tuple(value_map))
    missing_value_keys = [key for key in required if not _present(value_map.get(key))]
    has_value = any(_present(value) for value in value_map.values())
    if status == "CONNECTED" and not has_value:
        status = "INVALID_EMPTY_CONNECTED_SOURCE"
    elif status == "CONNECTED" and missing_value_keys:
        status = "PARTIAL_MISSING_REQUIRED_VALUES"
    if status in SOURCE_PRESENT_STATUSES:
        if _retired_provider(source.get("provider")):
            status = "REJECTED_RETIRED_PROVIDER"
        elif not _parse_dt(source.get("retrievedAtUtc")):
            status = "INVALID_MISSING_RETRIEVAL_TIME"
        elif not _present(source.get("provider")):
            status = "INVALID_MISSING_SOURCE_PROVIDER"
        elif not (
            _present(source.get("endpoint")) or _present(source.get("dataset"))
        ):
            status = "INVALID_MISSING_SOURCE_LOCATOR"
        elif not _present(source.get("payloadFingerprint")):
            status = "INVALID_MISSING_PAYLOAD_FINGERPRINT"
        elif source.get("sourceEffectiveAtUtc") not in (None, "") and not _parse_dt(
            source.get("sourceEffectiveAtUtc")
        ):
            status = "INVALID_SOURCE_EFFECTIVE_TIME"
    missing_reason = raw.get("missingReason") or raw.get("reason") or raw.get("note") or raw.get("error")
    if status != "CONNECTED" and not missing_reason:
        missing_reason = f"{output_name} was not returned by a connected pregame source."
    return {
        "status": status,
        **source,
        "identifiers": _identifiers(raw, row),
        "values": value_map,
        "requiredValueKeys": list(required),
        "missingValueKeys": missing_value_keys,
        "complete": status == "CONNECTED" and not missing_value_keys,
        "missingReason": None if status == "CONNECTED" else str(missing_reason),
    }


def _context_for_row(row: Dict[str, Any]) -> Dict[str, Any]:
    existing = row.get("advanced_context") or row.get("advancedContext")
    if isinstance(existing, dict) and existing:
        return copy.deepcopy(existing)
    try:
        import mlb_advanced_context

        slate = str(row.get("slate_date") or row.get("slateDateEt") or "")
        game = {
            "game_key": row.get("gameKey") or row.get("game_key"),
            "game_id": row.get("gameId") or row.get("game_id"),
            "official_game_pk": row.get("officialGamePk"),
            "home_team": row.get("homeTeam") or row.get("home_team"),
            "away_team": row.get("awayTeam") or row.get("away_team"),
        }
        return mlb_advanced_context.build_advanced_context(slate, game, row)
    except Exception as exc:
        return {"snapshotBuildError": f"{type(exc).__name__}:{str(exc)[:240]}"}


def build(row: Dict[str, Any], *, captured_at_utc: Optional[str] = None) -> Dict[str, Any]:
    captured_at = captured_at_utc or _utc_now()
    context = _context_for_row(row)
    groups = {
        output_name: _group(row, context, output_name, context_name, values)
        for output_name, context_name, values in GROUP_SPECS
    }
    connected = sorted(name for name, group in groups.items() if group.get("complete") is True)
    partial = sorted(
        name
        for name, group in groups.items()
        if str(group.get("status") or "").startswith("PARTIAL")
    )
    missing = sorted(name for name in groups if name not in connected)
    evidence_times = [
        value
        for value in (_parse_dt(group.get("retrievedAtUtc")) for group in groups.values())
        if value is not None
    ]
    source_pull_at = row.get("predictionSourcePullAt") or (row.get("slatePredictionLock") or {}).get("latestScoringPullAt")
    parsed_pull_at = _parse_dt(source_pull_at)
    if parsed_pull_at:
        evidence_times.append(parsed_pull_at)
    evidence_cutoff = max(evidence_times).isoformat() if evidence_times else None
    training_exclusions = [f"fundamentals_v2_incomplete:{name}" for name in missing]
    if not parsed_pull_at:
        training_exclusions.append("fundamentals_v2_source_pull_timestamp_missing")
    snapshot: Dict[str, Any] = {
        "version": VERSION,
        "recordType": "mlb_pregame_fundamentals_snapshot",
        "schemaCohort": SCHEMA_COHORT,
        "snapshotRole": "T_MINUS_45_FROZEN_PREGAME_FEATURE_INPUT",
        "createdAtUtc": captured_at,
        "evidenceCutoffUtc": evidence_cutoff,
        "sourcePullAtUtc": source_pull_at,
        "sourcePullId": row.get("predictionSourcePullId"),
        "game": {
            "gameId": row.get("gameId") or row.get("game_id") or row.get("gameIdentity"),
            "officialGamePk": row.get("officialGamePk"),
            "providerEventId": row.get("providerEventId"),
            "slateDateEt": row.get("slateDateEt") or row.get("slate_date"),
            "commenceTimeUtc": row.get("commenceTime") or row.get("commence_time"),
            "homeTeam": row.get("homeTeam") or row.get("home_team"),
            "awayTeam": row.get("awayTeam") or row.get("away_team"),
        },
        "groups": groups,
        "connectedGroups": connected,
        "partialGroups": partial,
        "missingGroups": missing,
        "pregameCompletenessNumerator": len(connected),
        "pregameCompletenessDenominator": len(GROUP_SPECS),
        "completenessRatio": round(len(connected) / len(GROUP_SPECS), 4),
        "sourceAvailabilityRatio": round(
            sum(
                1
                for group in groups.values()
                if group.get("status") in SOURCE_PRESENT_STATUSES
            )
            / len(GROUP_SPECS),
            4,
        ),
        "allConnectedGroupsTimestamped": all(
            _parse_dt(group.get("retrievedAtUtc")) is not None
            for group in groups.values()
            if group.get("status") in SOURCE_PRESENT_STATUSES
        ),
        "pregameComplete": not missing,
        "trainingEligibleAtCapture": bool(not missing and parsed_pull_at),
        "trainingExclusionReasons": sorted(set(training_exclusions)),
        "missingValuesAreNull": True,
        "postgameFieldsExcluded": ["closing_line_value", "closingLineValue", "beatsClose"],
        "closingLineValueCountsTowardPregameCompleteness": False,
        "immutableAtTMinus45": True,
        "latePlayabilityMayBlockReleaseOnly": True,
        "latePlayabilityMayRewriteSnapshotOrVector": False,
        "lateContextPolicy": "T-30/T-15 context may block release but cannot rewrite this T-45 outcome vector.",
        "sourceHonestyPolicy": "Unavailable fields remain null with a source status and missing reason; no neutral zero or postgame reconstruction is allowed.",
        "fingerprintVersion": FINGERPRINT_VERSION,
    }
    snapshot["fingerprint"] = fingerprint_for_snapshot(snapshot)
    return snapshot


def validate(snapshot: Any) -> List[str]:
    if not isinstance(snapshot, dict):
        return ["fundamentals_v2_missing"]
    errors: List[str] = []
    if snapshot.get("version") != VERSION:
        errors.append("fundamentals_v2_wrong_version")
    if snapshot.get("schemaCohort") != SCHEMA_COHORT:
        errors.append("fundamentals_v2_wrong_schema_cohort")
    if snapshot.get("fingerprintVersion") != FINGERPRINT_VERSION:
        errors.append("fundamentals_v2_wrong_fingerprint_version")
    if snapshot.get("fingerprint") != fingerprint_for_snapshot(snapshot):
        errors.append("fundamentals_v2_fingerprint_mismatch")
    groups = snapshot.get("groups") if isinstance(snapshot.get("groups"), dict) else {}
    expected = {name for name, _context_name, _values in GROUP_SPECS}
    if set(groups) != expected:
        errors.append("fundamentals_v2_group_set_mismatch")
    for name, group in groups.items():
        if not isinstance(group, dict):
            errors.append(f"fundamentals_v2_{name}_invalid")
            continue
        if group.get("status") in SOURCE_PRESENT_STATUSES:
            if _retired_provider(group.get("provider")):
                errors.append(f"fundamentals_v2_{name}_retired_provider")
            if not _parse_dt(group.get("retrievedAtUtc")):
                errors.append(f"fundamentals_v2_{name}_missing_retrieval_time")
            if not _present(group.get("provider")):
                errors.append(f"fundamentals_v2_{name}_missing_source_provider")
            if not (
                _present(group.get("endpoint")) or _present(group.get("dataset"))
            ):
                errors.append(f"fundamentals_v2_{name}_missing_source_locator")
            if not _present(group.get("payloadFingerprint")):
                errors.append(f"fundamentals_v2_{name}_missing_payload_fingerprint")
            if group.get("sourceEffectiveAtUtc") not in (None, "") and not _parse_dt(
                group.get("sourceEffectiveAtUtc")
            ):
                errors.append(f"fundamentals_v2_{name}_invalid_source_effective_time")
        required = set(REQUIRED_VALUE_KEYS.get(name, ()))
        if set(group.get("requiredValueKeys") or []) != required:
            errors.append(f"fundamentals_v2_{name}_required_value_contract_mismatch")
        actual_missing = sorted(
            key
            for key in required
            if not _present((group.get("values") or {}).get(key))
        )
        if sorted(group.get("missingValueKeys") or []) != actual_missing:
            errors.append(f"fundamentals_v2_{name}_missing_value_mask_mismatch")
        if group.get("complete") is not (
            group.get("status") == "CONNECTED" and not actual_missing
        ):
            errors.append(f"fundamentals_v2_{name}_complete_flag_mismatch")
    if any(value in groups for value in ("closing_line_value", "closingLineValue")):
        errors.append("fundamentals_v2_contains_postgame_clv")
    connected = sorted(name for name, group in groups.items() if group.get("complete") is True)
    partial = sorted(
        name
        for name, group in groups.items()
        if str(group.get("status") or "").startswith("PARTIAL")
    )
    missing = sorted(name for name in expected if name not in connected)
    if sorted(snapshot.get("connectedGroups") or []) != connected:
        errors.append("fundamentals_v2_connected_group_summary_mismatch")
    if sorted(snapshot.get("partialGroups") or []) != partial:
        errors.append("fundamentals_v2_partial_group_summary_mismatch")
    if sorted(snapshot.get("missingGroups") or []) != missing:
        errors.append("fundamentals_v2_missing_group_summary_mismatch")
    if snapshot.get("pregameCompletenessDenominator") != len(GROUP_SPECS):
        errors.append("fundamentals_v2_completeness_denominator_mismatch")
    if snapshot.get("pregameCompletenessNumerator") != len(connected):
        errors.append("fundamentals_v2_completeness_numerator_mismatch")
    expected_completeness = Decimal(len(connected)) / Decimal(len(GROUP_SPECS))
    try:
        actual_completeness = Decimal(str(snapshot.get("completenessRatio")))
    except Exception:
        actual_completeness = Decimal("-1")
    if actual_completeness != expected_completeness.quantize(Decimal("0.0001")):
        errors.append("fundamentals_v2_completeness_ratio_mismatch")
    source_present_count = sum(
        1
        for group in groups.values()
        if group.get("status") in SOURCE_PRESENT_STATUSES
    )
    expected_availability = Decimal(source_present_count) / Decimal(len(GROUP_SPECS))
    try:
        actual_availability = Decimal(str(snapshot.get("sourceAvailabilityRatio")))
    except Exception:
        actual_availability = Decimal("-1")
    if actual_availability != expected_availability.quantize(Decimal("0.0001")):
        errors.append("fundamentals_v2_source_availability_ratio_mismatch")
    all_timestamped = all(
        _parse_dt(group.get("retrievedAtUtc")) is not None
        for group in groups.values()
        if group.get("status") in SOURCE_PRESENT_STATUSES
    )
    if snapshot.get("allConnectedGroupsTimestamped") is not all_timestamped:
        errors.append("fundamentals_v2_timestamp_summary_mismatch")
    if snapshot.get("pregameComplete") is not (not missing):
        errors.append("fundamentals_v2_pregame_complete_flag_mismatch")
    source_pull_present = _parse_dt(snapshot.get("sourcePullAtUtc")) is not None
    expected_exclusions = {
        f"fundamentals_v2_incomplete:{name}" for name in missing
    }
    if not source_pull_present:
        expected_exclusions.add("fundamentals_v2_source_pull_timestamp_missing")
    actual_exclusions = {
        str(reason)
        for reason in (snapshot.get("trainingExclusionReasons") or [])
        if str(reason)
    }
    if actual_exclusions != expected_exclusions:
        errors.append("fundamentals_v2_training_exclusion_summary_mismatch")
    if snapshot.get("trainingEligibleAtCapture") is not bool(
        not missing and source_pull_present
    ):
        errors.append("fundamentals_v2_training_eligibility_flag_mismatch")
    if snapshot.get("missingValuesAreNull") is not True:
        errors.append("fundamentals_v2_null_missingness_policy_missing")
    if snapshot.get("immutableAtTMinus45") is not True:
        errors.append("fundamentals_v2_immutable_t45_policy_missing")
    if snapshot.get("latePlayabilityMayBlockReleaseOnly") is not True:
        errors.append("fundamentals_v2_late_playability_policy_missing")
    if snapshot.get("closingLineValueCountsTowardPregameCompleteness") is not False:
        errors.append("fundamentals_v2_clv_exclusion_flag_missing")
    if snapshot.get("latePlayabilityMayRewriteSnapshotOrVector") is not False:
        errors.append("fundamentals_v2_late_rewrite_protection_missing")
    return sorted(set(errors))


def provenance_is_lock_safe(
    snapshot: Any,
    *,
    prediction_persisted_at: Any,
    lock_at: Any,
) -> bool:
    if validate(snapshot):
        return False
    persisted = _parse_dt(prediction_persisted_at)
    locked = _parse_dt(lock_at)
    if not persisted or not locked or persisted > locked:
        return False
    created = _parse_dt(snapshot.get("createdAtUtc"))
    if not created or created > persisted or created > locked:
        return False
    source_pull = _parse_dt(snapshot.get("sourcePullAtUtc"))
    if not source_pull or source_pull > persisted or source_pull > locked:
        return False
    evidence = _parse_dt(snapshot.get("evidenceCutoffUtc"))
    if evidence and (evidence > persisted or evidence > locked):
        return False
    for group in (snapshot.get("groups") or {}).values():
        if group.get("status") not in SOURCE_PRESENT_STATUSES:
            continue
        retrieved = _parse_dt(group.get("retrievedAtUtc"))
        effective = _parse_dt(group.get("sourceEffectiveAtUtc"))
        if not retrieved or retrieved > persisted or retrieved > locked:
            return False
        if effective and (effective > retrieved or effective > persisted or effective > locked):
            return False
    return True


def validate_snapshot(
    snapshot: Any,
    *,
    prediction_time_utc: Any,
    lock_time_utc: Any,
) -> Tuple[bool, List[str]]:
    """Validate the exact snapshot plus its pre-lock evidence boundary.

    ``prediction_time_utc`` is the durable candidate persistence time, not a
    client-created timestamp sampled before the DynamoDB write.
    """
    reasons = validate(snapshot)
    if not reasons and not provenance_is_lock_safe(
        snapshot,
        prediction_persisted_at=prediction_time_utc,
        lock_at=lock_time_utc,
    ):
        reasons.append("fundamentals_v2_evidence_not_at_or_before_persisted_prediction_and_lock")
    if not reasons and snapshot.get("pregameComplete") is not True:
        reasons.extend(snapshot.get("trainingExclusionReasons") or [
            "fundamentals_v2_pregame_sources_incomplete"
        ])
    reasons = sorted(set(reasons))
    return not reasons, reasons


def _snapshot_ref(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "schemaCohort": SCHEMA_COHORT,
        "gameId": (snapshot.get("game") or {}).get("gameId"),
        "sourcePullId": snapshot.get("sourcePullId"),
        "evidenceCutoffUtc": snapshot.get("evidenceCutoffUtc"),
        "fingerprintVersion": FINGERPRINT_VERSION,
        "fingerprint": snapshot.get("fingerprint"),
    }


def _apply_training_status(row: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
    reasons = {
        str(reason)
        for reason in (row.get("trainingExclusionReasons") or [])
        if str(reason)
    }
    reasons.update(str(reason) for reason in (snapshot.get("trainingExclusionReasons") or []))
    errors = validate(snapshot)
    reasons.update(errors)
    eligible = bool(snapshot.get("trainingEligibleAtCapture") is True and not reasons)
    row["fundamentalsV2TrainingEligible"] = eligible
    if not eligible:
        row["trainingEligible"] = False
        freeze = dict(row.get("mlFeatureFreeze") or {})
        freeze["trainingEligible"] = False
        freeze_reasons = {
            str(reason)
            for reason in (freeze.get("trainingExclusionReasons") or [])
            if str(reason)
        }
        freeze_reasons.update(reasons or {"fundamentals_v2_not_training_eligible"})
        freeze["trainingExclusionReasons"] = sorted(freeze_reasons)
        row["mlFeatureFreeze"] = freeze
    row["trainingExclusionReasons"] = sorted(reasons)


def enhance_row(row: Dict[str, Any]) -> Dict[str, Any]:
    existing = row.get("fundamentalsSnapshotV2")
    if existing is not None:
        errors = validate(existing)
        if errors:
            row["fundamentalsSnapshotV2Errors"] = errors
        row["fundamentalsSnapshotV2Ref"] = _snapshot_ref(existing)
        row["fundamentalsSnapshotRefV2"] = copy.deepcopy(
            row["fundamentalsSnapshotV2Ref"]
        )
        _apply_training_status(row, existing)
        return row
    snapshot = build(row)
    row["fundamentalsSnapshotV2"] = snapshot
    row["fundamentalsSnapshotV2Ref"] = _snapshot_ref(snapshot)
    # Temporary compatibility alias for already-written consumers. New code
    # uses fundamentalsSnapshotV2Ref consistently.
    row["fundamentalsSnapshotRefV2"] = copy.deepcopy(row["fundamentalsSnapshotV2Ref"])
    _apply_training_status(row, snapshot)
    return row


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows = [row for row in (result.get("predictions") or []) if isinstance(row, dict)]
    for row in rows:
        enhance_row(row)
    result["fundamentalsSnapshotV2"] = {
        "applied": True,
        "version": VERSION,
        "schemaCohort": SCHEMA_COHORT,
        "rowCount": len(rows),
        "fullyConnectedCount": sum(not (row.get("fundamentalsSnapshotV2") or {}).get("missingGroups") for row in rows),
        "trainingEligibleAtCaptureCount": sum(
            (row.get("fundamentalsSnapshotV2") or {}).get("trainingEligibleAtCapture")
            is True
            for row in rows
        ),
        "sourceHonestyEnabled": True,
        "closingLineExcludedFromPregame": True,
        "latePlayabilityCannotRewrite": True,
    }
    return result


def apply(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V2_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        return enhance_result(original(*args, **kwargs))

    module.predict_all = patched_predict_all
    module.MLB_FUNDAMENTALS_SNAPSHOT_V2_VERSION = VERSION
    module._INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V2_APPLIED = True
    return module
