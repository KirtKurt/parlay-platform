#!/usr/bin/env python3
"""Run the official-only V8 context backfill without an optional stack dependency."""
from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from botocore.exceptions import ClientError

import mlb_v8_historical_bbs_overlay_v1 as retired_bbs_overlay
import mlb_v8_historical_point_in_time_context_v1 as context_source
import run_mlb_v8_historical_context_backfill_entrypoint as official

VERSION = "MLB-V8-HISTORICAL-CONTEXT-AUTONOMY-v2-projection-eligibility"
CONTEXT_WRITER_CONCURRENCY_GROUP = "mlb-v8-autonomous-control-plane"
PROJECTION_ELIGIBILITY_VERSION = (
    "MLB-V8-HISTORICAL-PROJECTION-ELIGIBILITY-v1-strictly-prior-verified"
)
RETIRED_BBS_AUTHORITY = "V8_HISTORICAL_BBS_SHADOW_ONLY"
_CONFIRMATION_ONLY_ERRORS = frozenset(
    {"confirmed_lineups_missing", "confirmed_starters_missing"}
)
_PROJECTION_MODES = {
    "pitchers": "STRICTLY_PRIOR_ROTATION_PROJECTION",
    "lineups": "STRICTLY_PRIOR_LINEUP_PROJECTION",
}
_SIDES = ("away", "home")
_OPTIONAL_CONTEXT = ("park", "weather")
_EFFECTIVE_TIME_KEYS = (
    "asOfUtc",
    "asOf",
    "sourceEffectiveAtUtc",
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


def _stack_missing(exc: ClientError) -> bool:
    error = exc.response.get("Error") or {}
    return bool(
        str(error.get("Code") or "") == "ValidationError"
        and "does not exist" in str(error.get("Message") or exc).lower()
    )


def install_artifact_bucket_alias(
    module: Any,
    *,
    historical_stack: str,
    fundamentals_stack: str,
) -> Any:
    """Map the absent optional fundamentals stack to the versioned history bucket."""

    if getattr(module, "_INQSI_MLB_V8_CONTEXT_BUCKET_ALIAS_INSTALLED", False):
        return module
    original_outputs = module._outputs

    def outputs(cloudformation: Any, stack_name: str) -> Dict[str, str]:
        try:
            values = dict(original_outputs(cloudformation, stack_name))
        except ClientError as exc:
            if stack_name != fundamentals_stack or not _stack_missing(exc):
                raise
            values = dict(original_outputs(cloudformation, historical_stack))
        if stack_name == fundamentals_stack:
            bucket = str(
                values.get("FundamentalsArtifactsBucketName")
                or values.get("HistoricalArtifactsBucketName")
                or ""
            ).strip()
            if not bucket:
                raise RuntimeError(
                    "V8 context artifacts bucket could not be resolved from the "
                    "fundamentals or historical stack"
                )
            values["FundamentalsArtifactsBucketName"] = bucket
            values["V8ContextArtifactsBucketResolution"] = VERSION
        return values

    module._outputs = outputs
    module._INQSI_MLB_V8_CONTEXT_BUCKET_ALIAS_INSTALLED = True
    return module


def _set_retired_overlay_authority(value: str) -> None:
    retired_bbs_overlay.AUTHORITY = value
    target_overlay = getattr(official, "target_overlay", None)
    if (
        target_overlay is not None
        and getattr(target_overlay, "base", None) is retired_bbs_overlay
    ):
        target_overlay.base.AUTHORITY = value


def install_scoped_official_authority(module: Any) -> Any:
    """Use official authority only while the official manifest is being built."""

    if getattr(module, "_INQSI_MLB_V8_SCOPED_OFFICIAL_AUTHORITY_INSTALLED", False):
        return module
    _set_retired_overlay_authority(RETIRED_BBS_AUTHORITY)
    original_run = module.run

    def run(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        previous = retired_bbs_overlay.AUTHORITY
        _set_retired_overlay_authority(official.AUTHORITY)
        try:
            return original_run(*args, **kwargs)
        finally:
            _set_retired_overlay_authority(previous)

    module.run = run
    module._INQSI_MLB_V8_SCOPED_OFFICIAL_AUTHORITY_INSTALLED = True
    return module


def _resource_projection_verified(resources: Mapping[str, Any], name: str) -> bool:
    envelope = resources.get(name)
    if not isinstance(envelope, Mapping) or envelope.get("error") is not None:
        return False
    meta = envelope.get("meta")
    if not isinstance(meta, Mapping):
        return False
    return bool(
        meta.get("complete") is True
        and meta.get("authoritative") is True
        and meta.get("pointInTimeProjectionVerified") is True
        and meta.get("targetIdentityMode") == _PROJECTION_MODES[name]
        and meta.get("derivationVersion") == context_source.VERSION
    )


def _parse_utc(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _effective_at(envelope: Any) -> Optional[datetime]:
    if not isinstance(envelope, Mapping):
        return None
    meta = envelope.get("meta")
    if not isinstance(meta, Mapping):
        return None
    for key in _EFFECTIVE_TIME_KEYS:
        parsed = _parse_utc(meta.get(key))
        if parsed is not None:
            return parsed
    return None


def _optional_context_verified(
    canonical: Mapping[str, Any],
    resources: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> bool:
    lock = _parse_utc(canonical.get("predictionLockAtUtc"))
    if lock is None:
        return False
    for name in _OPTIONAL_CONTEXT:
        envelope = resources.get(name)
        if not isinstance(envelope, Mapping) or envelope.get("error") is not None:
            return False
        effective = _effective_at(envelope)
        if effective is None or effective > lock:
            return False
    return bool(
        snapshot.get("parkRunFactor") is not None
        and snapshot.get("weatherRunFactor") is not None
    )


def _starter_projection_structure_ready(
    normalized_game: Mapping[str, Any],
) -> bool:
    pitchers = normalized_game.get("pitchers")
    if not isinstance(pitchers, Mapping):
        return False
    for side in _SIDES:
        row = pitchers.get(side)
        if not isinstance(row, Mapping):
            return False
        if not (row.get("id") or row.get("name")):
            return False
    return True


def _lineup_projection_structure_ready(
    normalized_game: Mapping[str, Any],
) -> bool:
    lineups = normalized_game.get("lineups")
    if not isinstance(lineups, Mapping):
        return False
    for side in _SIDES:
        row = lineups.get(side)
        if not isinstance(row, Mapping):
            return False
        players = [
            player
            for player in row.get("players") or []
            if isinstance(player, Mapping)
        ]
        identities = {
            str(player.get("id") or player.get("name") or "").strip()
            for player in players
        }
        slots = {
            str(player.get("slot") or "").strip()
            for player in players
            if player.get("slot") not in (None, "")
        }
        if len({value for value in identities if value}) != 9 or len(slots) != 9:
            return False
    return True


def install_verified_projection_eligibility(module: Any) -> Any:
    """Accept complete strictly-prior projections in place of live confirmations."""

    if getattr(
        module,
        "_INQSI_MLB_V8_VERIFIED_PROJECTION_ELIGIBILITY_INSTALLED",
        False,
    ):
        return module
    original = module.build_training_snapshot

    def build_training_snapshot(
        canonical: Mapping[str, Any],
        provider: Mapping[str, Any],
        normalized_game: Mapping[str, Any],
        resources: Mapping[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        snapshot = original(
            canonical,
            provider,
            normalized_game,
            resources,
            **kwargs,
        )
        errors = {
            str(value)
            for value in snapshot.get("eligibilityErrors") or []
            if value
        }
        coverage = normalized_game.get("coverage")
        coverage = coverage if isinstance(coverage, Mapping) else {}
        confirmation_only = bool(errors) and errors.issubset(
            _CONFIRMATION_ONLY_ERRORS
        )
        verified = bool(
            confirmation_only
            and not list(coverage.get("missingDomains") or [])
            and snapshot.get("pointInTimeVerified") is True
            and snapshot.get("authority") == official.AUTHORITY
            and snapshot.get("sameDayResultsExcluded") is True
            and snapshot.get("targetGameOutcomeUsed") is False
            and snapshot.get("selectionUsedOutcomes") is False
            and snapshot.get("productionAuthorityChanged") is False
            and _resource_projection_verified(resources, "pitchers")
            and _resource_projection_verified(resources, "lineups")
            and _optional_context_verified(canonical, resources, snapshot)
            and _starter_projection_structure_ready(normalized_game)
            and _lineup_projection_structure_ready(normalized_game)
        )
        proof = {
            "version": PROJECTION_ELIGIBILITY_VERSION,
            "accepted": verified,
            "confirmationErrorsObserved": sorted(
                errors.intersection(_CONFIRMATION_ONLY_ERRORS)
            ),
            "onlyStrictlyPriorProjectionConfirmationErrors": confirmation_only,
            "pitcherProjectionVerified": _resource_projection_verified(
                resources, "pitchers"
            ),
            "lineupProjectionVerified": _resource_projection_verified(
                resources, "lineups"
            ),
            "optionalParkWeatherVerified": _optional_context_verified(
                canonical, resources, snapshot
            ),
            "starterStructureVerified": _starter_projection_structure_ready(
                normalized_game
            ),
            "lineupStructureVerified": _lineup_projection_structure_ready(
                normalized_game
            ),
            "pointInTimeVerified": snapshot.get("pointInTimeVerified") is True,
            "targetGameOutcomeExcluded": (
                snapshot.get("targetGameOutcomeUsed") is False
            ),
            "sameDayResultsExcluded": (
                snapshot.get("sameDayResultsExcluded") is True
            ),
            "productionAuthorityUnchanged": (
                snapshot.get("productionAuthorityChanged") is False
            ),
        }
        snapshot = copy.deepcopy(dict(snapshot))
        snapshot["historicalProjectionEligibility"] = proof
        if verified:
            snapshot["trainingEligible"] = True
            snapshot["eligibilityErrors"] = []
        snapshot["fingerprint"] = module.overlay.snapshot_fingerprint(snapshot)
        return snapshot

    module.build_training_snapshot = build_training_snapshot
    module._INQSI_MLB_V8_VERIFIED_PROJECTION_ELIGIBILITY_INSTALLED = True
    return module


def install() -> Any:
    module = official.install()
    install_scoped_official_authority(module)
    install_verified_projection_eligibility(module)
    historical_stack = os.environ.get(
        "HISTORICAL_STACK", module.DEFAULT_HISTORICAL_STACK
    )
    fundamentals_stack = os.environ.get(
        "FUNDAMENTALS_STACK", module.DEFAULT_FUNDAMENTALS_STACK
    )
    install_artifact_bucket_alias(
        module,
        historical_stack=historical_stack,
        fundamentals_stack=fundamentals_stack,
    )
    return module


def main() -> int:
    return install().main()


if __name__ == "__main__":
    raise SystemExit(main())
