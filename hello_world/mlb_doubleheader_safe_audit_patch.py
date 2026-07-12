from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

VERSION = "MLB-LOCKED-CARD-AUDIT-v3-provider-alias-nearest-time-doubleheader-safe"
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
    values = sorted(_provider_ids(row))
    return values[0] if values else ""


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
                for provider_id in _provider_ids(pred):
                    keep_best(provider, provider_id, pred)
                keep_best(timed, _time_key(pred), pred)
                matchup_candidates.setdefault(_matchup_key(pred), []).append(pred)

        unique_matchups: Dict[str, Dict[str, Any]] = {}
        candidate_diagnostics: Dict[str, List[Dict[str, Any]]] = {}
        for key, candidates in matchup_candidates.items():
            unique_by_identity: Dict[str, Dict[str, Any]] = {}
            for pred in candidates:
                identity = _provider_id(pred) or _time_key(pred)
                rank = base._candidate_rank(pred)
                current = unique_by_identity.get(identity)
                if rank is not None and (current is None or rank > (base._candidate_rank(current) or (-1, ""))):
                    unique_by_identity[identity] = pred
            values = list(unique_by_identity.values())
            if len(values) == 1:
                unique_matchups[key] = values[0]
            candidate_diagnostics[key] = [
                {
                    "providerIds": sorted(_provider_ids(pred)),
                    "commenceTime": _commence_dt(pred).isoformat() if _commence_dt(pred) else None,
                    "predictedWinner": pred.get("predictedWinner"),
                }
                for pred in values
            ]

        return {
            "provider": provider,
            "timed": timed,
            "uniqueMatchup": unique_matchups,
            "matchupCandidates": matchup_candidates,
            "matchupCandidateCount": {
                key: len({_provider_id(pred) or _time_key(pred) for pred in values})
                for key, values in matchup_candidates.items()
            },
            "candidateDiagnostics": candidate_diagnostics,
            "version": VERSION,
        }

    def lookup(index: Dict[str, Any], final: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
        final_provider_ids = sorted(_provider_ids(final))
        for provider_id in final_provider_ids:
            pred = (index.get("provider") or {}).get(provider_id)
            if pred:
                return pred, "provider_game_id", {"matchedProviderId": provider_id, "providerAliasAware": True}

        timed_key = _time_key(final)
        pred = (index.get("timed") or {}).get(timed_key)
        if pred:
            return pred, "teams_and_exact_commence_time", {}

        matchup_key = _matchup_key(final)
        final_time = _commence_dt(final)
        candidates = list((index.get("matchupCandidates") or {}).get(matchup_key) or [])
        if final_time and candidates:
            ranked: List[Tuple[float, Dict[str, Any]]] = []
            seen: Set[str] = set()
            for candidate in candidates:
                identity = _provider_id(candidate) or _time_key(candidate)
                if identity in seen:
                    continue
                seen.add(identity)
                candidate_time = _commence_dt(candidate)
                if candidate_time:
                    ranked.append((abs((candidate_time - final_time).total_seconds()) / 60.0, candidate))
            ranked.sort(key=lambda item: item[0])
            if ranked and ranked[0][0] <= MAX_TIME_DRIFT_MINUTES:
                second_distance = ranked[1][0] if len(ranked) > 1 else None
                if second_distance is None or (second_distance - ranked[0][0]) >= MIN_NEAREST_SEPARATION_MINUTES:
                    return ranked[0][1], "teams_and_nearest_commence_time", {
                        "timeDriftMinutes": round(ranked[0][0], 2),
                        "secondNearestMinutes": round(second_distance, 2) if second_distance is not None else None,
                    }

        if matchup_key in (index.get("uniqueMatchup") or {}):
            return (index.get("uniqueMatchup") or {}).get(matchup_key), "unique_matchup_fallback", {}

        return None, "no_unambiguous_locked_prediction_match", {
            "finalProviderIds": final_provider_ids,
            "finalCommenceTime": final_time.isoformat() if final_time else None,
            "candidateDiagnostics": (index.get("candidateDiagnostics") or {}).get(matchup_key, []),
        }

    def audit_rows(finals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        index = predictions_index(finals)
        rows: List[Dict[str, Any]] = []
        for final in finals:
            pred, method, diagnostics = lookup(index, final)
            if not pred:
                matchup_key = _matchup_key(final)
                rows.append({
                    **final,
                    "status": "MISSING_LOCKED_PREDICTION",
                    "lockedCardAudit": {
                        "applied": True,
                        "version": VERSION,
                        "selectionPolicy": "provider_alias_then_exact_time_then_safe_nearest_time_then_unique_matchup",
                        "missingReason": method,
                        "matchupCandidateCount": (index.get("matchupCandidateCount") or {}).get(matchup_key, 0),
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
                "providerGameId": next(iter(sorted(_provider_ids(final) or _provider_ids(pred))), ""),
                "doubleheaderSafe": True,
                "selectionPolicy": "provider_alias_then_exact_time_then_safe_nearest_time_then_unique_matchup",
                **diagnostics,
            })
            copied["lockedCardAudit"] = audit
            rows.append({**final, "status": "GRADED", **copied, "correct": correct})
        return rows

    module.predictions_index = predictions_index
    module.audit_rows = audit_rows
    module._INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_APPLIED = True
    module._INQSI_MLB_DOUBLEHEADER_SAFE_AUDIT_V3_APPLIED = True
    return module
