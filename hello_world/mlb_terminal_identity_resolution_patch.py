from __future__ import annotations

import functools
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


VERSION = (
    "MLB-TERMINAL-IDENTITY-RESOLUTION-v1-"
    "unique-official-provider-crosswalk"
)
_APPLIED_FLAG = "_INQSI_MLB_TERMINAL_IDENTITY_RESOLUTION_V1"

_IDENTITY_FIELDS = (
    "gameIdentity",
    "gameId",
    "game_id",
    "id",
    "providerEventId",
    "provider_event_id",
    "officialGamePk",
    "official_game_pk",
)


def _identity_values(
    value: Dict[str, Any],
    original_game_identity: Any,
) -> set[str]:
    values = {
        str(value.get(key) or "").strip()
        for key in _IDENTITY_FIELDS
    }
    try:
        values.add(str(original_game_identity(value) or "").strip())
    except Exception:
        pass
    expanded = {item for item in values if item}
    expanded.update(
        item.split(":", 1)[1]
        for item in list(expanded)
        if ":" in item and item.split(":", 1)[1]
    )
    return expanded


def _annotate(
    report: Any,
    *,
    crosswalk_count: int,
    preflight_error: str | None = None,
) -> Any:
    if not isinstance(report, dict):
        return report
    out = dict(report)
    out["identityResolutionVersion"] = VERSION
    out["identityCrosswalkCount"] = crosswalk_count
    out["identityResolutionRequiredUniqueMatch"] = True
    out["postStartPredictionCreationAllowed"] = False
    if preflight_error:
        out["identityResolutionPreflightError"] = preflight_error
    return out


def apply(repair: Any) -> Any:
    """Make terminal replay resolve one exact manifest game across ID surfaces.

    The underlying repair remains the sole writer and still performs every
    pre-lock absence, start-time, stage, readback, and idempotency check. This
    wrapper changes only the temporary manifest key used during the protected
    replay. A status must crosswalk to exactly one manifest game; ambiguity is
    rejected fail-closed and no post-start prediction can be created.
    """

    if getattr(repair, _APPLIED_FLAG, False):
        return repair
    original = getattr(repair, "_repair_proven_no_prediction_misses", None)
    if not callable(original):
        return repair

    @functools.wraps(original)
    def resolve_then_repair(module: Any, patch: Any, slate: str) -> Dict[str, Any]:
        original_game_identity = getattr(patch, "game_identity", None)
        if not callable(original_game_identity):
            return _annotate(
                original(module, patch, slate),
                crosswalk_count=0,
                preflight_error="game_identity_not_callable",
            )

        try:
            now = module._now_utc().astimezone(timezone.utc)
            pulls = sorted(
                module._pulls_for_date(slate),
                key=lambda pull: patch._pull_at(module, pull)
                or datetime.min.replace(tzinfo=timezone.utc),
            )
            manifest = module._latest_games_for_date(slate, pulls)
            progress = patch._progress(
                module,
                slate,
                pulls,
                manifest,
                now,
                ensure_canonical=False,
            )
            missed = [
                row
                for row in progress.get("games") or []
                if isinstance(row, dict)
                and str(row.get("state") or "") == "MISSED_NOT_BACKFILLED"
            ]
        except Exception as exc:
            return _annotate(
                original(module, patch, slate),
                crosswalk_count=0,
                preflight_error=f"{type(exc).__name__}:{exc}",
            )

        mappings: List[Tuple[frozenset[str], str]] = []
        ambiguities: List[Dict[str, Any]] = []
        matched_manifest_indexes: set[int] = set()
        for status in missed:
            status_identity = str(status.get("gameIdentity") or "").strip()
            status_ids = _identity_values(status, original_game_identity)
            matches = [
                (index, game, _identity_values(game, original_game_identity))
                for index, game in enumerate(manifest)
                if isinstance(game, dict)
                and status_ids
                & _identity_values(game, original_game_identity)
            ]
            if len(matches) > 1:
                ambiguities.append(
                    {
                        "gameIdentity": status_identity,
                        "reason": "AMBIGUOUS_MANIFEST_GAME_IDENTITY",
                        "candidateCount": len(matches),
                    }
                )
                continue
            if len(matches) != 1 or not status_identity:
                continue
            index, _game, game_ids = matches[0]
            if index in matched_manifest_indexes:
                ambiguities.append(
                    {
                        "gameIdentity": status_identity,
                        "reason": "DUPLICATE_STATUS_TO_MANIFEST_GAME",
                        "candidateCount": 1,
                    }
                )
                continue
            matched_manifest_indexes.add(index)
            mappings.append((frozenset(game_ids), status_identity))

        if ambiguities:
            return {
                "ok": False,
                "version": getattr(
                    repair,
                    "MISSED_LOCK_TERMINAL_RECONCILIATION_VERSION",
                    VERSION,
                ),
                "slateDateEt": slate,
                "reconciledCount": 0,
                "remainingMissedCount": len(missed),
                "reason": "TERMINAL_IDENTITY_RESOLUTION_FAILED_CLOSED",
                "unresolved": ambiguities,
                "identityResolutionVersion": VERSION,
                "identityCrosswalkCount": 0,
                "identityResolutionRequiredUniqueMatch": True,
                "postStartPredictionCreationAllowed": False,
            }

        def compatible_game_identity(value: Dict[str, Any]) -> str:
            value_ids = _identity_values(value, original_game_identity)
            resolved = {
                status_identity
                for game_ids, status_identity in mappings
                if value_ids & set(game_ids)
            }
            if len(resolved) == 1:
                return next(iter(resolved))
            return str(original_game_identity(value) or "")

        setattr(patch, "game_identity", compatible_game_identity)
        try:
            report = original(module, patch, slate)
        finally:
            setattr(patch, "game_identity", original_game_identity)
        return _annotate(report, crosswalk_count=len(mappings))

    repair._repair_proven_no_prediction_misses = resolve_then_repair
    repair.MLB_TERMINAL_IDENTITY_RESOLUTION_VERSION = VERSION
    setattr(repair, _APPLIED_FLAG, True)
    return repair
