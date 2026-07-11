from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VERSION = "MLB-LOCKED-CARD-AUDIT-v2-doubleheader-safe-provider-time-match"


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
    return " ".join(str(value or "").lower().strip().split())


def _provider_id(row: Dict[str, Any]) -> str:
    return str(row.get("gameId") or row.get("game_id") or row.get("id") or row.get("gameIdentity") or "").strip()


def _matchup_key(row: Dict[str, Any]) -> str:
    away = _norm_team(row.get("awayTeam") or row.get("away_team"))
    home = _norm_team(row.get("homeTeam") or row.get("home_team"))
    return f"{away}|{home}"


def _time_key(row: Dict[str, Any]) -> str:
    dt = _parse_dt(row.get("commenceTime") or row.get("commence_time"))
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
    if getattr(module, "_INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_APPLIED", False):
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
        timed: Dict[str, Dict[str, Any]] = {}
        matchup_candidates: Dict[str, List[Dict[str, Any]]] = {}

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
                rank = base._candidate_rank(pred)
                if rank is None:
                    continue
                provider_id = _provider_id(pred)
                if provider_id:
                    keep_best(provider, provider_id, pred)
                keep_best(timed, _time_key(pred), pred)
                matchup_candidates.setdefault(_matchup_key(pred), []).append(pred)

        unique_matchups: Dict[str, Dict[str, Any]] = {}
        for key, candidates in matchup_candidates.items():
            unique_by_provider: Dict[str, Dict[str, Any]] = {}
            for pred in candidates:
                identity = _provider_id(pred) or _time_key(pred)
                rank = base._candidate_rank(pred)
                current = unique_by_provider.get(identity)
                if rank is not None and (current is None or rank > (base._candidate_rank(current) or (-1, ""))):
                    unique_by_provider[identity] = pred
            if len(unique_by_provider) == 1:
                unique_matchups[key] = next(iter(unique_by_provider.values()))

        return {
            "provider": provider,
            "timed": timed,
            "uniqueMatchup": unique_matchups,
            "matchupCandidateCount": {key: len({_provider_id(pred) or _time_key(pred) for pred in values}) for key, values in matchup_candidates.items()},
            "version": VERSION,
        }

    def lookup(index: Dict[str, Any], final: Dict[str, Any]):
        provider_id = _provider_id(final)
        if provider_id and provider_id in (index.get("provider") or {}):
            return (index.get("provider") or {}).get(provider_id), "provider_game_id"
        timed_key = _time_key(final)
        if timed_key in (index.get("timed") or {}):
            return (index.get("timed") or {}).get(timed_key), "teams_and_commence_time"
        matchup_key = _matchup_key(final)
        if matchup_key in (index.get("uniqueMatchup") or {}):
            return (index.get("uniqueMatchup") or {}).get(matchup_key), "unique_matchup_fallback"
        return None, "no_unambiguous_locked_prediction_match"

    def audit_rows(finals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        index = predictions_index(finals)
        rows: List[Dict[str, Any]] = []
        for final in finals:
            pred, method = lookup(index, final)
            if not pred:
                matchup_key = _matchup_key(final)
                rows.append({
                    **final,
                    "status": "MISSING_LOCKED_PREDICTION",
                    "lockedCardAudit": {
                        "applied": True,
                        "version": VERSION,
                        "selectionPolicy": "provider_id_then_exact_commence_time_then_unique_matchup_only",
                        "missingReason": method,
                        "matchupCandidateCount": (index.get("matchupCandidateCount") or {}).get(matchup_key, 0),
                        "doubleheaderSafe": True,
                    },
                })
                continue
            correct = module.normalize_team(pred.get("predictedWinner")) == module.normalize_team(final.get("winner"))
            copied = base._copy_audit_fields(pred)
            audit = dict(copied.get("lockedCardAudit") or {})
            audit.update({
                "version": VERSION,
                "matchMethod": method,
                "providerGameId": _provider_id(final) or _provider_id(pred),
                "doubleheaderSafe": True,
                "selectionPolicy": "provider_id_then_exact_commence_time_then_unique_matchup_only",
            })
            copied["lockedCardAudit"] = audit
            rows.append({**final, "status": "GRADED", **copied, "correct": correct})
        return rows

    module.predictions_index = predictions_index
    module.audit_rows = audit_rows
    module._INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_APPLIED = True
    return module
