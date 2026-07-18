from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

VERSION = "MLB-LOCKED-CARD-AUDIT-v4-canonical-provider-id-only"
CANONICAL_LOCK_AUTHORITY_VERSION = "MLB-ROLLING-AUDIT-CANONICAL-LOCK-AUTHORITY-v1"
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
    for key in ("gameId", "game_id", "providerGameId", "provider_game_id", "id"):
        value = row.get(key)
        if value not in (None, ""):
            text = str(value).strip()
            return text[len("provider:"):] if text.startswith("provider:") else text
    identity = str(row.get("gameIdentity") or "").strip()
    return identity[len("provider:"):] if identity.startswith("provider:") else ""


def _canonical_authority(row: Dict[str, Any], authority_version: str) -> bool:
    authority = row.get("canonicalLockAuthority") or {}
    slate = str(row.get("slateDateEt") or row.get("slate_date") or "")
    return bool(
        isinstance(authority, dict)
        and authority.get("version") == authority_version
        and authority.get("verified") is True
        and authority.get("consistentRead") is True
        and authority.get("immutableLocked") is True
        and authority.get("stageAuthorityVerified") is True
        and authority.get("persistedStageAuthorityValidated") is True
        and authority.get("exactLockVectorValidated") is True
        and authority.get("legacyOrDailyCardFallbackUsed") is False
        and authority.get("sourcePk") == f"GAME_WINNERS#mlb#{slate}"
        and str(authority.get("sourceSk") or "").startswith("LOCKED#GAME#")
        and authority.get("recordType")
        == "mlb_immutable_locked_single_game_prediction"
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
        return pred, "exact_provider_game_id_and_teams", {
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
                        "selectionPolicy": "exact_canonical_provider_game_id_and_teams_only",
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
                "selectionPolicy": "exact_canonical_provider_game_id_and_teams_only",
                **diagnostics,
            })
            copied["lockedCardAudit"] = audit
            authority = dict(copied.get("canonicalLockAuthority") or {})
            authority.update({
                "exactProviderIdentityMatched": True,
                "matchMethod": "exact_provider_game_id_and_teams",
            })
            copied["canonicalLockAuthority"] = authority
            rows.append({**final, "status": "GRADED", **copied, "correct": correct})
        return rows

    module.predictions_index = predictions_index
    module.audit_rows = audit_rows
    module._INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_APPLIED = True
    module._INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_V3_APPLIED = True
    return module
