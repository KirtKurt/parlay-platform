from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, Tuple


VERSION = "MLB-CANONICAL-MANIFEST-RETRY-BINDING-v1"
_MARKER = "_INQSI_MLB_CANONICAL_MANIFEST_RETRY_BINDING_PATCH_APPLIED"


def _as_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalizer(module: Any):
    official = getattr(module, "official_schedule", None)
    normalize = getattr(official, "normalize_team", None)
    if callable(normalize):
        return normalize

    def fallback(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().split())

    return fallback


def _membership(
    module: Any,
    games: Iterable[Dict[str, Any]],
) -> Tuple[str, Dict[str, Tuple[str, str]], list[str]]:
    rows = [row for row in (games or []) if isinstance(row, dict)]
    normalize = _normalizer(module)
    errors: list[str] = []

    official_ids = [str(row.get("official_game_pk") or "").strip() for row in rows]
    official_complete = bool(rows) and all(official_ids)
    mode = "official_game_pk" if official_complete else "provider_game_identity"
    membership: Dict[str, Tuple[str, str]] = {}

    for row in rows:
        if official_complete:
            identity = str(row.get("official_game_pk") or "").strip()
        else:
            identity = str(
                row.get("game_id")
                or row.get("id")
                or row.get("game_key")
                or ""
            ).strip()
        if not identity:
            errors.append("game_identity_missing")
            continue
        if identity in membership:
            errors.append(f"duplicate_game_identity:{identity}")
            continue
        membership[identity] = (
            normalize(row.get("away_team") or row.get("awayTeam")),
            normalize(row.get("home_team") or row.get("homeTeam")),
        )

    if len(membership) != len(rows):
        errors.append("game_membership_incomplete")
    return mode, membership, sorted(set(errors))


def _same_membership(
    module: Any,
    candidate_games: Iterable[Dict[str, Any]],
    canonical_games: Iterable[Dict[str, Any]],
) -> Tuple[bool, list[str]]:
    candidate_mode, candidate, candidate_errors = _membership(
        module, candidate_games
    )
    canonical_mode, canonical, canonical_errors = _membership(
        module, canonical_games
    )
    errors = [*candidate_errors, *canonical_errors]
    if candidate_mode != canonical_mode:
        errors.append(
            f"membership_identity_mode_mismatch:{candidate_mode}:{canonical_mode}"
        )
    if set(candidate) != set(canonical):
        errors.append("canonical_game_membership_changed")
    for identity in sorted(set(candidate) & set(canonical)):
        if candidate[identity] != canonical[identity]:
            errors.append(f"canonical_ordered_teams_changed:{identity}")
    return not errors, sorted(set(errors))


def _candidate_body(
    module: Any,
    *,
    game_date: str,
    asof: str,
    run: str,
    compact: Dict[str, Any],
    games: list[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "pull_id": module._safe_pull_id(game_date, asof),
        "sport": "mlb",
        "sport_key": "mlb",
        "slate_date": game_date,
        "pulled_at": asof,
        "source": "the_odds_api",
        "interval_minutes": module.MLB_SCHED_INTERVAL_MINUTES,
        "games": games,
        "meta": {
            "platform_version": module.PLATFORM_VERSION,
            "run": run,
            "provider_sport_key": module.SPORT_KEY,
            "provider_roster": compact.get("provider_roster") or {},
            "official_schedule_authority": compact.get(
                "official_schedule_authority"
            ),
            "date_isolated": True,
            "line_movement_prediction": True,
        },
    }


def _repair_retry_result(
    module: Any,
    result: Dict[str, Any],
    *,
    game_date: str,
    asof: str,
    run: str,
    compact: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    if result.get("ok") is True:
        return result
    if result.get("retryReturnedExistingCanonicalPull") is not True:
        return result
    if result.get("error") not in (None, ""):
        return result

    pull_history = getattr(module, "pull_history", None)
    current_games = module._canonical_games(compact)
    if not current_games or pull_history is None:
        return result

    try:
        stored = pull_history.store_pull(
            _candidate_body(
                module,
                game_date=game_date,
                asof=asof,
                run=run,
                compact=compact,
                games=current_games,
            )
        )
        stored_details = stored.get("stored") or {}
        stored_pull = stored.get("pull") or {}
        canonical_slot = stored.get("canonicalSlot") or {}
        manifest_summary = stored_details.get("provider_manifest") or {}
        canonical_manifest = stored_pull.get("provider_schedule_manifest") or {}
        canonical_binding = stored_pull.get("provider_manifest_binding") or {}
        canonical_games = list(stored_pull.get("games") or [])

        validate = getattr(
            pull_history,
            "validate_provider_schedule_manifest",
            None,
        )
        if not callable(validate):
            raise RuntimeError("provider_manifest_validator_unavailable")
        validation_errors = validate(
            stored_pull,
            game_date,
            verify_immutable_storage=True,
        )
        if validation_errors:
            raise RuntimeError(
                "canonical_manifest_validation_failed:"
                + ",".join(sorted(set(validation_errors)))
            )

        compatible, membership_errors = _same_membership(
            module,
            current_games,
            canonical_games,
        )
        if not compatible:
            raise RuntimeError(
                "canonical_manifest_retry_membership_mismatch:"
                + ",".join(membership_errors)
            )

        canonical_pull_id = str(
            canonical_slot.get("canonicalPullId")
            or stored_details.get("pull_id")
            or stored_pull.get("pull_id")
            or ""
        )
        canonical_pulled_at = str(
            canonical_slot.get("canonicalPulledAtUtc")
            or stored_pull.get("pulled_at")
            or ""
        )
        fingerprint_fn = getattr(
            pull_history,
            "pull_payload_fingerprint",
            None,
        )
        canonical_pull_fingerprint = (
            fingerprint_fn(stored_pull)
            if stored_pull and callable(fingerprint_fn)
            else ""
        )
        canonical_pk = str(stored_details.get("pk") or "")
        canonical_sk = str(stored_details.get("sk") or "")
        canonical_slot_start = str(canonical_slot.get("slotStartUtc") or "")
        canonical_binding_complete = all(
            (
                canonical_pull_id,
                canonical_pulled_at,
                canonical_pull_fingerprint,
                canonical_pk,
                canonical_sk,
                canonical_slot_start,
            )
        )

        canonical_authority = canonical_manifest.get("scheduleAuthority") or {}
        official_authority_bound = bool(
            not canonical_authority
            or (
                manifest_summary.get("official_schedule_backed") is True
                and manifest_summary.get("official_schedule_authority_version")
                == canonical_authority.get("version")
                and manifest_summary.get(
                    "official_schedule_authority_fingerprint"
                )
                == canonical_authority.get("fingerprint")
                and _as_int(
                    manifest_summary.get("official_schedule_game_count")
                )
                == len(canonical_games)
            )
        )
        manifest_bound = bool(
            stored.get("ok") is True
            and stored.get("deduped") is True
            and canonical_slot.get("retryReturnedExistingCanonicalPull") is True
            and canonical_binding_complete
            and manifest_summary.get("immutable") is True
            and manifest_summary.get("full_provider_schedule") is True
            and _as_int(manifest_summary.get("game_count"))
            == len(canonical_games)
            and manifest_summary.get("fingerprint")
            == canonical_manifest.get("fingerprint")
            and manifest_summary.get("pk") == canonical_binding.get("pk")
            and manifest_summary.get("sk") == canonical_binding.get("sk")
            and official_authority_bound
        )
        if not manifest_bound:
            raise RuntimeError("canonical_manifest_retry_binding_incomplete")

        repaired = copy.deepcopy(result)
        repaired.update(
            {
                "ok": True,
                "games": len(current_games),
                "stored": stored.get("stored"),
                "error": None,
                "pull_id": canonical_pull_id,
                "pk": canonical_pk,
                "canonicalPullId": canonical_pull_id,
                "canonicalPulledAtUtc": canonical_pulled_at,
                "canonicalSlotStartUtc": canonical_slot_start,
                "canonicalPullPayloadFingerprint": canonical_pull_fingerprint,
                "canonicalPullPk": canonical_pk,
                "canonicalPullSk": canonical_sk,
                "retryReturnedExistingCanonicalPull": True,
                "providerManifestVersion": manifest_summary.get("version"),
                "providerManifestFingerprint": manifest_summary.get(
                    "fingerprint"
                ),
                "providerManifestGameCount": manifest_summary.get(
                    "game_count"
                ),
                "providerManifestPk": manifest_summary.get("pk"),
                "providerManifestSk": manifest_summary.get("sk"),
                "providerManifestImmutable": True,
                "providerManifestFullSchedule": True,
                "providerManifestBound": True,
                "officialScheduleBacked": manifest_summary.get(
                    "official_schedule_backed"
                )
                is True,
                "officialScheduleAuthorityVersion": manifest_summary.get(
                    "official_schedule_authority_version"
                ),
                "officialScheduleAuthorityFingerprint": manifest_summary.get(
                    "official_schedule_authority_fingerprint"
                ),
                "officialScheduleGameCount": manifest_summary.get(
                    "official_schedule_game_count"
                ),
                "officialScheduleAuthorityBound": official_authority_bound,
                "canonicalMembershipCompatible": True,
                "manifestBindingRepairApplied": True,
                "manifestBindingRepairVersion": VERSION,
                "immutablePredictionHistoryRewritten": False,
                "postStartPredictionCreated": False,
                "productionAuthorityChanged": False,
            }
        )
        return repaired
    except Exception as exc:
        failed = copy.deepcopy(result)
        failed["manifestBindingRepairApplied"] = False
        failed["manifestBindingRepairVersion"] = VERSION
        failed["manifestBindingRepairError"] = str(exc)
        failed["immutablePredictionHistoryRewritten"] = False
        failed["postStartPredictionCreated"] = False
        failed["productionAuthorityChanged"] = False
        return failed


def install(module: Any) -> Dict[str, Any]:
    if getattr(module, _MARKER, False):
        return {
            "ok": True,
            "applied": True,
            "alreadyApplied": True,
            "version": VERSION,
        }

    original = getattr(module, "_store_canonical_pull_history", None)
    if not callable(original):
        # Several isolated unit tests provide a deliberately tiny writer stub
        # containing only lambda_handler. Defer there without weakening the
        # deployed module: the real mlb_manual_pull exposes the canonical
        # history writer and is patched during cold start.
        return {
            "ok": True,
            "applied": False,
            "deferredForMinimalStub": True,
            "version": VERSION,
            "error": None,
        }

    def patched_store_canonical_pull_history(
        *,
        game_date: str,
        asof: str,
        run: str,
        compact: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = original(
            game_date=game_date,
            asof=asof,
            run=run,
            compact=compact,
        )
        return _repair_retry_result(
            module,
            result,
            game_date=game_date,
            asof=asof,
            run=run,
            compact=compact,
        )

    patched_store_canonical_pull_history.__name__ = original.__name__
    patched_store_canonical_pull_history.__doc__ = original.__doc__
    setattr(
        patched_store_canonical_pull_history,
        "_inqsi_patch_version",
        VERSION,
    )
    module._store_canonical_pull_history = patched_store_canonical_pull_history
    setattr(module, _MARKER, True)
    module.MLB_CANONICAL_MANIFEST_RETRY_BINDING_VERSION = VERSION
    return {
        "ok": True,
        "applied": True,
        "alreadyApplied": False,
        "deferredForMinimalStub": False,
        "version": VERSION,
        "immutablePredictionHistoryRewritten": False,
        "postStartPredictionCreated": False,
        "productionAuthorityChanged": False,
    }
