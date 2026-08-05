"""Feature-aware, leakage-safe eligibility for MLB V8 historical context.

This module separates row-level core eligibility from feature-family eligibility.
A missing optional historical feature is represented explicitly and removed from
that row's feature vector instead of discarding otherwise safe point-in-time data.
Every feature that remains enabled must have effective-time evidence at or before
the immutable prediction lock.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, MutableMapping, Optional

VERSION = "MLB-V8-CONTEXT-ELIGIBILITY-v2-feature-aware"
MATERIALIZER_VERSION = "MLB-V8-OFFICIAL-CONTEXT-v2-projection-aware"

DOMAINS = (
    "pitchers",
    "bullpens",
    "lineups",
    "injuries",
    "team_context",
    "park",
    "weather",
)
CORE_REQUIRED_DOMAINS = ("pitchers", "bullpens", "team_context")
OPTIONAL_DOMAINS = tuple(name for name in DOMAINS if name not in CORE_REQUIRED_DOMAINS)
EFFECTIVE_TIME_KEYS = (
    "sourceEffectiveAtUtc",
    "asOfUtc",
    "asOf",
    "effectiveAtUtc",
    "effectiveAt",
    "snapshotAtUtc",
    "snapshotAt",
    "dataAsOfUtc",
    "dataAsOf",
    "updatedAt",
    "lastUpdated",
    "generatedAt",
    "timestamp",
)

_DOMAIN_FIELDS = {
    "pitchers": (
        "starterQuality",
        "starterRecentForm",
        "starterVelocity",
        "starterCommand",
        "starterExpectedInnings",
    ),
    "bullpens": ("bullpenQuality", "bullpenFreshness"),
    "lineups": ("lineupQuality",),
    "injuries": ("lineupAbsenceImpact",),
    "team_context": ("platoonMatchup", "defenseRating", "travelRestRating"),
}


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _parse_utc(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _effective_at(envelope: Any) -> Optional[datetime]:
    meta = _dict(_dict(envelope).get("meta"))
    for key in EFFECTIVE_TIME_KEYS:
        parsed = _parse_utc(meta.get(key))
        if parsed is not None:
            return parsed
    return None


def _has_data(envelope: Any) -> bool:
    data = _dict(envelope).get("data")
    return isinstance(data, (Mapping, list))


def _availability_mode(domain: str, envelope: Any) -> str:
    value = _dict(envelope)
    meta = _dict(value.get("meta"))
    data = value.get("data")
    identity_mode = str(meta.get("targetIdentityMode") or "").upper()
    if value.get("error") is not None or not isinstance(data, (Mapping, list)):
        return "unavailable"
    if domain == "injuries" and not data and meta.get("complete") is True:
        return "verified_empty"
    if (
        meta.get("confirmed") is True
        or "ARCHIVED_CONFIRMED" in identity_mode
        or "T_MINUS_45" in identity_mode
    ):
        return "confirmed_archive"
    if meta.get("pointInTimeProjectionVerified") is True:
        return "strict_prior_projection"
    return "available_unverified"


def inspect_domain(domain: str, envelope: Any, lock_at: Any) -> Dict[str, Any]:
    """Return leakage and availability evidence for one historical feature domain."""

    lock = _parse_utc(lock_at)
    value = _dict(envelope)
    meta = _dict(value.get("meta"))
    effective = _effective_at(value)
    errors = []
    if lock is None:
        errors.append("prediction_lock_invalid")
    if value.get("error") is not None or not _has_data(value):
        errors.append(f"{domain}_resource_unavailable")
    if effective is None:
        errors.append(f"{domain}_source_effective_time_missing")
    elif lock is not None and effective > lock + timedelta(seconds=1):
        errors.append(f"{domain}_source_effective_time_after_lock")

    mode = _availability_mode(domain, value)
    projection_verified = bool(
        meta.get("pointInTimeProjectionVerified") is True
        or mode in {"confirmed_archive", "verified_empty"}
    )
    if _has_data(value) and value.get("error") is None and not projection_verified:
        errors.append(f"{domain}_point_in_time_projection_unverified")

    errors = sorted(set(errors))
    eligible = not errors
    return {
        "eligible": eligible,
        "availabilityMode": mode if eligible else "unavailable",
        "sourceEffectiveAtUtc": effective.isoformat() if effective else None,
        "pointInTimeProjectionVerified": projection_verified,
        "complete": meta.get("complete") is True,
        "source": meta.get("source") or meta.get("provider") or meta.get("feed"),
        "errors": errors,
    }


def evaluate(resources: Mapping[str, Any], lock_at: Any) -> Dict[str, Any]:
    domain_evidence = {
        domain: inspect_domain(domain, resources.get(domain), lock_at)
        for domain in DOMAINS
    }
    feature_eligibility = {
        domain: evidence["eligible"] for domain, evidence in domain_evidence.items()
    }
    core_errors = sorted(
        {
            error
            for domain in CORE_REQUIRED_DOMAINS
            for error in domain_evidence[domain]["errors"]
        }
    )
    lock_valid = _parse_utc(lock_at) is not None
    if not lock_valid:
        core_errors = sorted(set(core_errors + ["prediction_lock_invalid"]))
    optional_warnings = sorted(
        {
            error
            for domain in OPTIONAL_DOMAINS
            for error in domain_evidence[domain]["errors"]
        }
    )
    training_eligible_core = lock_valid and not core_errors
    return {
        "eligibilityPolicyVersion": VERSION,
        "materializerVersion": MATERIALIZER_VERSION,
        "trainingEligibleCore": training_eligible_core,
        "trainingEligible": training_eligible_core,
        "featureEligibility": feature_eligibility,
        "featureMissingness": {
            domain: not feature_eligibility[domain] for domain in DOMAINS
        },
        "featureAvailabilityMode": {
            domain: domain_evidence[domain]["availabilityMode"] for domain in DOMAINS
        },
        "featureEvidence": domain_evidence,
        "requiredFeatureDomains": list(CORE_REQUIRED_DOMAINS),
        "optionalFeatureDomains": list(OPTIONAL_DOMAINS),
        "usedFeatureDomains": [
            domain for domain in DOMAINS if feature_eligibility[domain]
        ],
        "pointInTimeVerified": training_eligible_core,
        "eligibilityErrors": core_errors,
        "eligibilityWarnings": optional_warnings,
    }


def _clear_side_fields(snapshot: MutableMapping[str, Any], domain: str) -> None:
    fields = _DOMAIN_FIELDS.get(domain, ())
    for side in ("home", "away"):
        side_row = snapshot.get(side)
        if isinstance(side_row, MutableMapping):
            for field in fields:
                side_row[field] = None


def apply_to_snapshot(
    snapshot: Mapping[str, Any],
    resources: Mapping[str, Any],
    lock_at: Any,
) -> Dict[str, Any]:
    """Apply feature-aware eligibility and remove unavailable feature values."""

    output: Dict[str, Any] = dict(snapshot)
    evaluation = evaluate(resources, lock_at)
    for domain, eligible in evaluation["featureEligibility"].items():
        if eligible:
            continue
        if domain == "park":
            output["parkRunFactor"] = None
        elif domain == "weather":
            output["weatherRunFactor"] = None
        else:
            _clear_side_fields(output, domain)
    output.update(evaluation)
    return output


def summarize_batch(diagnostics_by_game: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Build source-honest rejection and feature coverage telemetry."""

    reason_counts: Counter[str] = Counter()
    domain_eligible_counts: Counter[str] = Counter()
    reasons_by_game: Dict[str, list[str]] = {}
    eligible_core = 0
    full_feature_rows = 0

    for game_pk, raw in diagnostics_by_game.items():
        row = dict(raw)
        feature_eligibility = _dict(row.get("featureEligibility"))
        for domain in DOMAINS:
            if feature_eligibility.get(domain) is True:
                domain_eligible_counts[domain] += 1
        errors = sorted(
            set(
                str(value)
                for value in (
                    list(row.get("eligibilityErrors") or [])
                    + list(row.get("eligibilityWarnings") or [])
                )
            )
        )
        reason_counts.update(errors)
        if errors:
            reasons_by_game[str(game_pk)] = errors
        if row.get("trainingEligibleCore") is True:
            eligible_core += 1
        if feature_eligibility and all(
            feature_eligibility.get(domain) is True for domain in DOMAINS
        ):
            full_feature_rows += 1

    total = len(diagnostics_by_game)
    return {
        "eligibilityPolicyVersion": VERSION,
        "materializerVersion": MATERIALIZER_VERSION,
        "diagnosedGameCount": total,
        "coreEligibleGameCount": eligible_core,
        "fullFeatureEligibleGameCount": full_feature_rows,
        "eligibilityReasonCounts": dict(sorted(reason_counts.items())),
        "eligibilityReasonsByGame": dict(sorted(reasons_by_game.items())),
        "domainCoverage": {
            domain: {
                "eligibleGameCount": int(domain_eligible_counts[domain]),
                "evaluatedGameCount": total,
                "coverage": round(domain_eligible_counts[domain] / total, 8)
                if total
                else 0.0,
            }
            for domain in DOMAINS
        },
    }
