from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VERSION = "MLB-ML-CANONICAL-SLATE-CONTINUITY-v3-independent-exact-slate-quarantine"
FINGERPRINT_VERSION = "MLB-ML-CANONICAL-SLATE-CONTINUITY-SHA256-v1"
TRAINING_ENVELOPE_VERSION = "MLB-ML-T45-TRAINING-ENVELOPE-v1"


class CanonicalContinuityError(ValueError):
    pass


@dataclass(frozen=True)
class SlateEvaluation:
    slate_date_et: str
    accepted: bool
    official_game_pks: Tuple[str, ...]
    accepted_rows: Tuple[Dict[str, Any], ...]
    quarantined_game_pks: Tuple[str, ...]
    missing_game_pks: Tuple[str, ...]
    extra_game_pks: Tuple[str, ...]
    errors: Tuple[str, ...]
    authority: Optional[Dict[str, Any]]


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _sha256(value: Any) -> str:
    encoded = json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return None


def _game_pk(row: Mapping[str, Any]) -> str:
    audit = row.get("lockedCardAudit") if isinstance(row.get("lockedCardAudit"), Mapping) else {}
    vector = row.get("frozenFeatureVector") if isinstance(row.get("frozenFeatureVector"), Mapping) else {}
    return str(
        _first(
            row,
            "officialGamePk",
            "official_game_pk",
            "providerGameId",
            "provider_game_id",
            "gameId",
            "game_id",
            "id",
        )
        or _first(audit, "officialGamePk", "providerGameId", "gameId")
        or _first(vector, "officialGamePk", "providerGameId", "gameId")
        or ""
    ).strip()


def _is_locked(row: Mapping[str, Any]) -> bool:
    tags = {str(value) for value in (row.get("tags") or [])}
    audit = row.get("lockedCardAudit") if isinstance(row.get("lockedCardAudit"), Mapping) else {}
    return bool(
        row.get("lockedPrediction") is True
        or row.get("officialPrediction") is True
        or row.get("officialPredictionStatus") == "OFFICIAL_LOCKED_PREDICTION"
        or audit.get("lockedFlag") is True
        or "OFFICIAL_LOCKED_PREDICTION" in tags
        or "PER_GAME_TMINUS45_LOCKED" in tags
    )


def _lock_time(row: Mapping[str, Any]) -> Optional[datetime]:
    audit = row.get("lockedCardAudit") if isinstance(row.get("lockedCardAudit"), Mapping) else {}
    slate_lock = row.get("slatePredictionLock") if isinstance(row.get("slatePredictionLock"), Mapping) else {}
    vector = row.get("frozenFeatureVector") if isinstance(row.get("frozenFeatureVector"), Mapping) else {}
    for value in (
        row.get("lockedAtUtc"),
        row.get("locked_at_utc"),
        audit.get("lockAtUtc"),
        slate_lock.get("lockAtUtc"),
        vector.get("lockAtUtc"),
    ):
        parsed = _parse_dt(value)
        if parsed:
            return parsed
    return None


def _commence_time(row: Mapping[str, Any]) -> Optional[datetime]:
    vector = row.get("frozenFeatureVector") if isinstance(row.get("frozenFeatureVector"), Mapping) else {}
    for value in (
        row.get("commenceTime"),
        row.get("commence_time"),
        row.get("gameStartUtc"),
        vector.get("commenceTime"),
        vector.get("commence_time"),
    ):
        parsed = _parse_dt(value)
        if parsed:
            return parsed
    return None


def _source_pull_time(row: Mapping[str, Any]) -> Optional[datetime]:
    vector = row.get("frozenFeatureVector") if isinstance(row.get("frozenFeatureVector"), Mapping) else {}
    for value in (
        row.get("predictionSourcePullAt"),
        row.get("sourcePullAtUtc"),
        vector.get("sourcePullAtUtc"),
        vector.get("sourcePullAt"),
    ):
        parsed = _parse_dt(value)
        if parsed:
            return parsed
    return None


def _outcome(row: Mapping[str, Any]) -> Tuple[Optional[bool], Optional[str]]:
    settled = row.get("settlement") if isinstance(row.get("settlement"), Mapping) else {}
    outcome = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
    final = bool(
        row.get("settled") is True
        or row.get("final") is True
        or settled.get("settled") is True
        or settled.get("final") is True
        or outcome.get("final") is True
        or str(row.get("status") or "").upper() in {"FINAL", "SETTLED"}
    )
    winner = str(
        _first(row, "actualWinner", "winningTeam", "winner", "outcomeWinner")
        or _first(settled, "actualWinner", "winningTeam", "winner")
        or _first(outcome, "actualWinner", "winningTeam", "winner")
        or ""
    ).strip()
    return (True if final else None), (winner or None)


def training_envelope_errors(row: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    game_pk = _game_pk(row)
    vector = row.get("frozenFeatureVector")
    lock_at = _lock_time(row)
    commence_at = _commence_time(row)
    source_at = _source_pull_time(row)
    selected_odds = _first(
        row,
        "lockedAmericanOdds",
        "selectedSideLockedOdds",
        "selected_side_locked_odds",
        "americanOdds",
    )
    final, winner = _outcome(row)

    if not game_pk:
        errors.append("missing_official_game_pk")
    if not _is_locked(row):
        errors.append("not_immutable_t45_locked_prediction")
    if lock_at is None:
        errors.append("missing_lock_timestamp")
    if commence_at is None:
        errors.append("missing_commence_timestamp")
    if lock_at and commence_at:
        cutoff = commence_at - timedelta(minutes=45)
        # The lock checker can run seconds after the exact minute. It may never
        # use a source observation after the T-45 cutoff.
        if lock_at > cutoff + timedelta(minutes=2):
            errors.append("lock_timestamp_after_t45_grace")
    if source_at is None:
        errors.append("missing_source_pull_timestamp")
    if source_at and commence_at and source_at > commence_at - timedelta(minutes=45):
        errors.append("source_pull_after_t45_cutoff")
    if source_at and lock_at and source_at > lock_at:
        errors.append("source_pull_after_lock")
    if not isinstance(vector, Mapping) or not vector:
        errors.append("missing_frozen_feature_vector")
    else:
        features = vector.get("features")
        if not isinstance(features, Mapping) or not features:
            errors.append("missing_frozen_features")
        vector_game_pk = str(
            _first(vector, "officialGamePk", "providerGameId", "gameId") or ""
        ).strip()
        if vector_game_pk and game_pk and vector_game_pk != game_pk:
            errors.append("frozen_vector_game_identity_mismatch")
        if not str(vector.get("fingerprint") or row.get("frozenFeatureVectorFingerprint") or "").strip():
            errors.append("missing_frozen_vector_fingerprint")
    if selected_odds in (None, ""):
        errors.append("missing_selected_side_locked_odds")
    if final is not True:
        errors.append("missing_final_settlement")
    if not winner:
        errors.append("missing_outcome_winner")
    if row.get("currentCanonical") is False:
        errors.append("superseded_canonical_row")
    return sorted(set(errors))


def build_training_envelope(row: Mapping[str, Any]) -> Dict[str, Any]:
    errors = training_envelope_errors(row)
    envelope = {
        "version": TRAINING_ENVELOPE_VERSION,
        "officialGamePk": _game_pk(row),
        "slateDateEt": str(_first(row, "slateDateEt", "slate_date_et", "gameDateEt") or ""),
        "lockAtUtc": _lock_time(row).isoformat() if _lock_time(row) else None,
        "sourcePullAtUtc": _source_pull_time(row).isoformat() if _source_pull_time(row) else None,
        "commenceTimeUtc": _commence_time(row).isoformat() if _commence_time(row) else None,
        "selectedSideLockedOdds": _first(
            row,
            "lockedAmericanOdds",
            "selectedSideLockedOdds",
            "selected_side_locked_odds",
            "americanOdds",
        ),
        "outcomeWinner": _outcome(row)[1],
        "eligible": not errors,
        "errors": errors,
        "immutable": True,
    }
    envelope["fingerprintVersion"] = FINGERPRINT_VERSION
    envelope["fingerprint"] = _sha256(envelope)
    return envelope


def evaluate_exact_slate(
    *,
    slate_date_et: str,
    authority: Optional[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
) -> SlateEvaluation:
    errors: List[str] = []
    if not isinstance(authority, Mapping):
        return SlateEvaluation(
            slate_date_et=slate_date_et,
            accepted=False,
            official_game_pks=(),
            accepted_rows=(),
            quarantined_game_pks=(),
            missing_game_pks=(),
            extra_game_pks=(),
            errors=("official_finalized_slate_authority_missing",),
            authority=None,
        )
    official = tuple(sorted(str(value) for value in (authority.get("officialGamePks") or []) if str(value)))
    if authority.get("slateFinalized") is not True:
        errors.append("official_slate_not_finalized")
    if not official:
        errors.append("official_game_pk_set_empty")
    if len(set(official)) != len(official):
        errors.append("official_game_pk_set_not_unique")

    grouped: Dict[str, List[Mapping[str, Any]]] = {}
    for row in rows or []:
        game_pk = _game_pk(row)
        if game_pk:
            grouped.setdefault(game_pk, []).append(row)

    accepted_rows: List[Dict[str, Any]] = []
    quarantined: List[str] = []
    for game_pk in official:
        candidates = grouped.get(game_pk) or []
        eligible: List[Tuple[Mapping[str, Any], Dict[str, Any]]] = []
        for candidate in candidates:
            envelope = build_training_envelope(candidate)
            if envelope.get("eligible") is True:
                eligible.append((candidate, envelope))
        if len(eligible) != 1:
            quarantined.append(game_pk)
            errors.append(
                "missing_unique_training_eligible_canonical_row:"
                + game_pk
                + f":{len(eligible)}"
            )
            continue
        row, envelope = eligible[0]
        accepted_rows.append({**_plain(dict(row)), "mlbT45TrainingEnvelope": envelope})

    row_game_pks = set(grouped)
    official_set = set(official)
    missing = tuple(sorted(official_set - row_game_pks))
    extra = tuple(sorted(row_game_pks - official_set))
    if missing:
        errors.append("official_game_rows_missing")
    if extra:
        errors.append("non_official_game_rows_present")

    accepted = bool(
        not errors
        and len(accepted_rows) == len(official)
        and not quarantined
        and not missing
        and not extra
    )
    return SlateEvaluation(
        slate_date_et=slate_date_et,
        accepted=accepted,
        official_game_pks=official,
        accepted_rows=tuple(accepted_rows),
        quarantined_game_pks=tuple(sorted(set(quarantined))),
        missing_game_pks=missing,
        extra_game_pks=extra,
        errors=tuple(sorted(set(errors))),
        authority=_plain(dict(authority)),
    )


def _date_range(start_date_et: str, end_date_et: str) -> Iterable[str]:
    start = date.fromisoformat(start_date_et)
    end = date.fromisoformat(end_date_et)
    if end < start:
        raise CanonicalContinuityError("end date precedes start date")
    current = start
    while current <= end:
        yield current.isoformat()
        current += timedelta(days=1)


def scan_independent_slates(
    *,
    start_date_et: str,
    end_date_et: str,
    authority_loader: Callable[[str], Optional[Mapping[str, Any]]],
    row_loader: Callable[[str], Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Validate each slate independently and continue after quarantined dates.

    This intentionally replaces the old stop-at-first-unresolved behavior. A bad
    slate never contaminates training, but it also cannot indefinitely prevent
    later independently finalized and exact slates from being evaluated.
    """

    accepted_slates: List[Dict[str, Any]] = []
    quarantined_slates: List[Dict[str, Any]] = []
    accepted_rows: List[Dict[str, Any]] = []
    processed_dates: List[str] = []
    for slate_date in _date_range(start_date_et, end_date_et):
        processed_dates.append(slate_date)
        result = evaluate_exact_slate(
            slate_date_et=slate_date,
            authority=authority_loader(slate_date),
            rows=row_loader(slate_date),
        )
        payload = {
            "slateDateEt": result.slate_date_et,
            "accepted": result.accepted,
            "officialGamePks": list(result.official_game_pks),
            "acceptedGameCount": len(result.accepted_rows),
            "quarantinedGamePks": list(result.quarantined_game_pks),
            "missingGamePks": list(result.missing_game_pks),
            "extraGamePks": list(result.extra_game_pks),
            "errors": list(result.errors),
            "authority": result.authority,
        }
        if result.accepted:
            accepted_slates.append(payload)
            accepted_rows.extend(result.accepted_rows)
        else:
            quarantined_slates.append(payload)

    proof = {
        "ok": True,
        "version": VERSION,
        "policy": "independent_exact_slate_validation_continue_after_quarantine",
        "startSlateDate": start_date_et,
        "endSlateDate": end_date_et,
        "processedSlateDates": processed_dates,
        "processedThroughSlateDate": processed_dates[-1] if processed_dates else None,
        "acceptedSlateDates": [item["slateDateEt"] for item in accepted_slates],
        "quarantinedSlateDates": [item["slateDateEt"] for item in quarantined_slates],
        "acceptedSlates": accepted_slates,
        "quarantinedSlates": quarantined_slates,
        "acceptedRowCount": len(accepted_rows),
        "acceptedRows": accepted_rows,
        "unresolvedSlateStopsLaterEvaluation": False,
        "quarantineIsNonAuthoritative": True,
        "exactOfficialGameSetEqualityRequired": True,
    }
    proof["fingerprintVersion"] = FINGERPRINT_VERSION
    proof["fingerprint"] = _sha256(proof)
    return proof
