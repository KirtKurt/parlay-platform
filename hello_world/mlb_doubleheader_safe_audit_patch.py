from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

VERSION = "MLB-LOCKED-CARD-AUDIT-v5-provider-alias-vector-separation"
CANONICAL_LOCK_AUTHORITY_VERSION = "MLB-ROLLING-AUDIT-CANONICAL-LOCK-AUTHORITY-v1"
EXACT_PROVIDER_MATCH_METHOD = "exact_provider_game_id_and_teams"
VERIFIED_PROVIDER_ALIAS_MATCH_METHOD = (
    "verified_immutable_pull_official_game_pk_provider_alias_and_teams"
)
MAX_TIME_DRIFT_MINUTES = 45.0
MIN_NEAREST_SEPARATION_MINUTES = 10.0


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _norm_team(value: Any) -> str:
    text = " ".join(str(value or "").lower().replace(".", " ").replace("'", "").strip().split())
    aliases = {
        "oakland athletics": "athletics",
        "sacramento athletics": "athletics",
        "as": "athletics",
        "la angels": "los angeles angels",
        "ny yankees": "new york yankees",
        "ny mets": "new york mets",
    }
    return aliases.get(text, text)


def _walk_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                yield from _walk_dicts(child)


def _provider_ids(row: Dict[str, Any]) -> Set[str]:
    keys = {
        "id", "gameId", "game_id", "providerGameId", "provider_game_id",
        "eventId", "event_id", "providerEventId", "provider_event_id",
        "oddsApiEventId", "odds_api_event_id",
    }
    out: Set[str] = set()
    for container in _walk_dicts(row):
        for key in keys:
            value = container.get(key)
            if value not in (None, ""):
                out.add(str(value).strip())
    return {value for value in out if value}


def _provider_id(row: Dict[str, Any]) -> str:
    for key in (
        "providerEventId",
        "provider_event_id",
        "providerGameId",
        "provider_game_id",
        "gameId",
        "game_id",
        "id",
    ):
        value = row.get(key)
        if value not in (None, ""):
            text = str(value).strip()
            return text[len("provider:"):] if text.startswith("provider:") else text
    identity = str(row.get("gameIdentity") or "").strip()
    return identity[len("provider:"):] if identity.startswith("provider:") else ""


def _canonical_authority(row: Dict[str, Any], authority_version: str) -> bool:
    authority = row.get("canonicalLockAuthority") or {}
    slate = str(row.get("slateDateEt") or row.get("slate_date") or "")
    vector_audit_authorized = bool(
        authority.get("exactLockVectorValidated") is True
        or (
            authority.get("officialAuditEligible") is True
            and authority.get("selectionLockIndependentOfTrainingVector") is True
        )
    )
    return bool(
        isinstance(authority, dict)
        and authority.get("version") == authority_version
        and authority.get("verified") is True
        and authority.get("consistentRead") is True
        and authority.get("immutableLocked") is True
        and authority.get("stageAuthorityVerified") is True
        and authority.get("persistedStageAuthorityValidated") is True
        and vector_audit_authorized
        and authority.get("legacyOrDailyCardFallbackUsed") is False
        and authority.get("sourcePk") == f"GAME_WINNERS#mlb#{slate}"
        and str(authority.get("sourceSk") or "").startswith("LOCKED#GAME#")
        and authority.get("recordType")
        == "mlb_immutable_locked_single_game_prediction"
    )


def _verified_provider_alias_authority(row: Dict[str, Any]) -> bool:
    authority = row.get("canonicalLockAuthority") or {}
    proof = authority.get("providerAliasCrosswalk") or {}
    official_pk = str(authority.get("officialGamePk") or "")
    provider_id = _provider_id(row)
    fingerprints = proof.get("manifestFingerprints") or []
    row_teams = (
        " ".join(str(row.get("awayTeam") or row.get("away_team") or "").lower().strip().split()),
        " ".join(str(row.get("homeTeam") or row.get("home_team") or "").lower().strip().split()),
    )
    return bool(
        isinstance(authority, dict)
        and official_pk
        and provider_id
        and authority.get("providerIdentityMatchMethod")
        == VERIFIED_PROVIDER_ALIAS_MATCH_METHOD
        and authority.get("matchMethod") == VERIFIED_PROVIDER_ALIAS_MATCH_METHOD
        and authority.get("verifiedProviderAliasCrosswalkMatched") is True
        and authority.get("exactProviderIdentityMatched") is False
        and isinstance(proof, dict)
        and proof.get("immutableManifestValidated") is True
        and proof.get("uniqueBidirectionalCrosswalk") is True
        and str(proof.get("officialGamePk") or "") == official_pk
        and str(proof.get("providerEventId") or "") == provider_id
        and str(authority.get("providerGameId") or "") == provider_id
        and str(authority.get("canonicalLockedGameId") or "")
        == f"mlb_statsapi:{official_pk}"
        and isinstance(fingerprints, list)
        and bool(fingerprints)
        and all(str(value).strip() for value in fingerprints)
        and len({str(value) for value in fingerprints}) == len(fingerprints)
        and proof.get("evidenceCount") == len(fingerprints)
        and (
            str(proof.get("awayTeamNormalized") or ""),
            str(proof.get("homeTeamNormalized") or ""),
        )
        == row_teams
        and all(row_teams)
    )


def _matchup_key(row: Dict[str, Any]) -> str:
    away = _norm_team(row.get("awayTeam") or row.get("away_team"))
    home = _norm_team(row.get("homeTeam") or row.get("home_team"))
    return f"{away}|{home}"


def _commence_dt(row: Dict[str, Any]) -> Optional[datetime]:
    for container in _walk_dicts(row):
        for key in ("commenceTime", "commence_time", "startTime", "start_time", "scheduledStart", "scheduled_start"):
            dt = _parse_dt(container.get(key))
            if dt:
                return dt
    return None


def _time_key(row: Dict[str, Any]) -> str:
    dt = _commence_dt(row)
    return f"{_matchup_key(row)}|{dt.isoformat() if dt else 'unknown'}"


def _strict_playable(row: Dict[str, Any]) -> bool:
    tags = {str(value) for value in (row.get("tags") or [])}
    recommendation = str(row.get("recommendationStatus") or "").upper()
    actionability = str(row.get("actionability") or "").upper()
    if (
        "NOT_PLAYABLE" in tags
        or "ML_REJECTED" in tags
        or "NOT_PLAYABLE" in recommendation
        or "LOW_CONFIDENCE" in recommendation
        or "NOT_PLAYABLE" in actionability
        or actionability in {"PASS_NO_PICK", "NO_PICK", "NO_ACTIONABLE_PICK"}
    ):
        return False
    return bool(
        row.get("playable") is True
        or row.get("playablePick") is True
        or row.get("actionablePick") is True
        or row.get("accuracyTargetEligible") is True
        or recommendation == "PLAYABLE_PREDICTION"
        or "ACTIONABLE_PICK" in tags
        or "ML_CONFIRMED" in tags
    )


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_V3_APPLIED", False):
        return module

    import mlb_locked_card_audit_v1 as base

    original_pipeline_state = base._pipeline_state

    def pipeline_state(row: Dict[str, Any]) -> Dict[str, Any]:
        state = dict(original_pipeline_state(row))
        state["playable"] = _strict_playable(row)
        state["officialPrediction"] = bool(row.get("officialPrediction") is True or base._locked_flag(row))
        state["classificationVersion"] = VERSION
        return state

    base._pipeline_state = pipeline_state
    base.VERSION = VERSION

    def predictions_index(finals: List[Dict[str, Any]]) -> Dict[str, Any]:
        dates = sorted({str(final.get("slateDateEt")) for final in finals if final.get("slateDateEt")})
        provider: Dict[str, Dict[str, Any]] = {}

        def keep_best(bucket: Dict[str, Dict[str, Any]], key: str, pred: Dict[str, Any]) -> None:
            if not key:
                return
            rank = base._candidate_rank(pred)
            if rank is None:
                return
            current = bucket.get(key)
            if current is None or rank > (base._candidate_rank(current) or (-1, "")):
                bucket[key] = pred

        for slate in dates:
            for pred in module._query_predictions_for_slate(slate):
                if not _canonical_authority(
                    pred,
                    CANONICAL_LOCK_AUTHORITY_VERSION,
                ):
                    continue
                rank = base._candidate_rank(pred)
                if rank is None:
                    continue
                keep_best(provider, _provider_id(pred), pred)

        return {
            "provider": provider,
            "version": VERSION,
        }

    def lookup(index: Dict[str, Any], final: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
        provider_id = _provider_id(final)
        pred = (index.get("provider") or {}).get(provider_id) if provider_id else None
        if not pred:
            return None, "no_exact_canonical_provider_game_id_match", {"finalProviderGameId": provider_id or None}
        if _matchup_key(pred) != _matchup_key(final):
            return None, "canonical_provider_id_team_mismatch", {
                "finalProviderGameId": provider_id,
                "canonicalMatchup": _matchup_key(pred),
                "finalMatchup": _matchup_key(final),
            }
        final_time = _commence_dt(final)
        predicted_time = _commence_dt(pred)
        drift = (
            abs((predicted_time - final_time).total_seconds()) / 60.0
            if final_time and predicted_time
            else None
        )
        authority = pred.get("canonicalLockAuthority") or {}
        provider_method = authority.get("providerIdentityMatchMethod")
        match_method = authority.get("matchMethod")
        if provider_method and match_method and provider_method != match_method:
            return None, "canonical_provider_identity_method_conflict", {
                "finalProviderGameId": provider_id,
                "providerIdentityMatchMethod": provider_method,
                "matchMethod": match_method,
            }
        declared_method = provider_method or match_method
        if declared_method == VERIFIED_PROVIDER_ALIAS_MATCH_METHOD:
            if not _verified_provider_alias_authority(pred):
                return None, "canonical_provider_alias_authority_invalid", {
                    "finalProviderGameId": provider_id,
                }
            method = VERIFIED_PROVIDER_ALIAS_MATCH_METHOD
        elif declared_method in (None, "", EXACT_PROVIDER_MATCH_METHOD):
            if authority.get("verifiedProviderAliasCrosswalkMatched") is True:
                return None, "canonical_exact_provider_authority_conflict", {
                    "finalProviderGameId": provider_id,
                }
            method = EXACT_PROVIDER_MATCH_METHOD
        else:
            return None, "canonical_provider_identity_method_invalid", {
                "finalProviderGameId": provider_id,
                "declaredMatchMethod": declared_method,
            }
        return pred, method, {
            "matchedProviderId": provider_id,
            "commenceTimeDriftMinutes": round(drift, 2) if drift is not None else None,
        }

    def audit_rows(finals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        index = predictions_index(finals)
        rows: List[Dict[str, Any]] = []
        for final in finals:
            pred, method, diagnostics = lookup(index, final)
            if not pred:
                matchup_key = _matchup_key(final)
                authority_diagnostics = (
                    module._canonical_rejection_diagnostics(final)
                    if hasattr(module, "_canonical_rejection_diagnostics")
                    else {}
                )
                invalid = (
                    authority_diagnostics.get("canonicalLockEvidenceStatus")
                    == "INVALID"
                )
                rows.append({
                    **final,
                    "status": "INVALID_CANONICAL_LOCK" if invalid else "MISSING_CANONICAL_LOCK",
                    **authority_diagnostics,
                    "lockedCardAudit": {
                        "applied": True,
                        "version": VERSION,
                        "selectionPolicy": "exact_provider_id_or_verified_immutable_official_pk_alias_and_teams",
                        "missingReason": "canonical_lock_failed_validation" if invalid else method,
                        "doubleheaderSafe": True,
                        **diagnostics,
                    },
                })
                continue
            correct = module.normalize_team(pred.get("predictedWinner")) == module.normalize_team(final.get("winner"))
            copied = base._copy_audit_fields(pred)
            audit = dict(copied.get("lockedCardAudit") or {})
            audit.update({
                "version": VERSION,
                "matchMethod": method,
                "providerGameId": _provider_id(final),
                "doubleheaderSafe": True,
                "selectionPolicy": "exact_provider_id_or_verified_immutable_official_pk_alias_and_teams",
                **diagnostics,
            })
            copied["lockedCardAudit"] = audit
            authority = dict(copied.get("canonicalLockAuthority") or {})
            if method == VERIFIED_PROVIDER_ALIAS_MATCH_METHOD:
                authority.update({
                    "exactProviderIdentityMatched": False,
                    "verifiedProviderAliasCrosswalkMatched": True,
                    "providerIdentityMatchMethod": VERIFIED_PROVIDER_ALIAS_MATCH_METHOD,
                    "matchMethod": VERIFIED_PROVIDER_ALIAS_MATCH_METHOD,
                })
            else:
                authority.update({
                    "exactProviderIdentityMatched": True,
                    "verifiedProviderAliasCrosswalkMatched": False,
                    "providerIdentityMatchMethod": EXACT_PROVIDER_MATCH_METHOD,
                    "matchMethod": EXACT_PROVIDER_MATCH_METHOD,
                })
            copied["canonicalLockAuthority"] = authority
            rows.append({**final, "status": "GRADED", **copied, "correct": correct})
        return rows

    module.predictions_index = predictions_index
    module.audit_rows = audit_rows
    module._INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_APPLIED = True
    module._INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_V3_APPLIED = True
    return module
