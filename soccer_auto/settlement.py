"""Immutable result capture from The Odds API's three-day scores horizon."""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Mapping

from .canonical import digest, iso_utc, parse_utc, schedule_identity, stable_event_key
from .collector import _client
from .odds_api import DEFAULT_MAX_ATTEMPTS
from .storage import SoccerStore, ddb_safe, now_utc, plain


SETTLEMENT_VERSION = "soccer-auto-settlement-v1"
SETTLEMENT_ADMISSIBILITY_CERTIFICATE_VERSION = (
    "soccer-auto-settlement-admissibility-v1"
)
SCORES_CADENCE_SECONDS = int(os.getenv("SOCCER_AUTO_SCORES_CADENCE_SECONDS", "300"))
SETTLEMENT_ADMISSIBILITY_RECONCILIATION_PAGE_LIMIT = max(
    1,
    int(
        os.getenv(
            "SOCCER_AUTO_SETTLEMENT_ADMISSIBILITY_PAGE_LIMIT",
            "200",
        )
    ),
)

# The scores endpoint does not distinguish regulation-only odds settlement from
# extra time/penalties.  These competitions remain captured but fail closed for
# supervised 1X2 labels unless a later authoritative regulation-time source is
# explicitly wired.
ALWAYS_REGULATION_AMBIGUOUS_KEYS = frozenset(
    {
        "soccer_england_efl_cup",
        "soccer_fa_cup",
        "soccer_france_coupe_de_france",
        "soccer_germany_dfb_pokal",
        "soccer_italy_coppa_italia",
        "soccer_spain_copa_del_rey",
    }
)

# These competitions contain both league/group matches and knockout matches.
# Quarantining the whole competition made every group-stage settlement
# unusable.  A fixture in this set is now quarantined only when its own market
# inventory exposes ``to_qualify`` (or another explicit event-level knockout
# signal).  The decision is persisted in an immutable admissibility
# certificate, so the policy can be audited without rewriting a final score.
MIXED_FORMAT_TOURNAMENT_KEYS = frozenset(
    {
        "soccer_africa_cup_of_nations",
        "soccer_fifa_world_cup",
        "soccer_fifa_world_cup_womens",
        "soccer_fifa_club_world_cup",
        "soccer_uefa_europa_conference_league",
        "soccer_uefa_champs_league",
        "soccer_uefa_champs_league_qualification",
        "soccer_uefa_champs_league_women",
        "soccer_uefa_europa_league",
        "soccer_uefa_european_championship",
        "soccer_uefa_euro_qualification",
        "soccer_uefa_nations_league",
        "soccer_concacaf_gold_cup",
        "soccer_concacaf_leagues_cup",
        "soccer_conmebol_copa_america",
        "soccer_conmebol_copa_libertadores",
        "soccer_conmebol_copa_sudamericana",
        "soccer_fifa_world_cup_qualifiers_europe",
        "soccer_fifa_world_cup_qualifiers_south_america",
    }
)

# Compatibility alias retained for callers/tests importing the prior name.
KNOCKOUT_OR_TOURNAMENT_KEYS = ALWAYS_REGULATION_AMBIGUOUS_KEYS

KNOCKOUT_KEY_MARKERS = (
    "_cup",
    "copa_",
    "_pokal",
    "coupe_",
    "_playoff",
    "_knockout",
    "_trophy",
)


def _score_map(score_event: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in score_event.get("scores") or []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        try:
            result[name] = int(row.get("score"))
        except (TypeError, ValueError):
            continue
    return result


def regulation_time_ambiguous(
    sport_key: str,
    *,
    competition: Mapping[str, Any] | None = None,
    event_markets: set[str] | frozenset[str] | None = None,
) -> bool:
    """Fail closed for dynamically introduced knockout scopes.

    The provider's final-score payload does not identify regulation, extra
    time, or penalties. Static known cups remain quarantined, and dynamic cups
    are detected from their provider key/title. A per-event ``to_qualify``
    market catches playoff/knockout fixtures that share a broader league key.
    """
    markets = {str(value) for value in (event_markets or set()) if value}
    if "to_qualify" in markets:
        return True
    if sport_key in ALWAYS_REGULATION_AMBIGUOUS_KEYS:
        return True
    if sport_key in MIXED_FORMAT_TOURNAMENT_KEYS:
        return False
    row = competition or {}
    searchable = " ".join(
        str(value or "").casefold().replace(" ", "_")
        for value in (sport_key, row.get("title"), row.get("description"))
    )
    return any(marker in searchable for marker in KNOCKOUT_KEY_MARKERS)


def settlement_scope_regulation_ambiguous(
    sport_key: str,
    *,
    competition: Mapping[str, Any] | None,
    event_markets: set[str] | frozenset[str],
) -> bool:
    """Require positive regulation-market evidence before certifying a label."""
    markets = {str(value) for value in event_markets if value}
    if not markets.intersection({"h2h", "h2h_3_way"}):
        return True
    return regulation_time_ambiguous(
        sport_key,
        competition=competition,
        event_markets=markets,
    )


def settlement_admissibility_classification_basis(
    sport_key: str,
    *,
    competition: Mapping[str, Any] | None,
    event_markets: set[str] | frozenset[str],
) -> str:
    """Return the deterministic policy reason signed into a certificate."""
    markets = {str(value) for value in event_markets if value}
    if not markets.intersection({"h2h", "h2h_3_way"}):
        return "REGULATION_MARKET_EVIDENCE_MISSING"
    if "to_qualify" in markets:
        return "EVENT_TO_QUALIFY_MARKET_PRESENT"
    if sport_key in ALWAYS_REGULATION_AMBIGUOUS_KEYS:
        return "ALWAYS_KNOCKOUT_COMPETITION"
    if sport_key in MIXED_FORMAT_TOURNAMENT_KEYS:
        return "MIXED_FORMAT_EVENT_WITHOUT_KNOCKOUT_MARKET"
    if regulation_time_ambiguous(
        sport_key,
        competition=competition,
        event_markets=markets,
    ):
        return "DYNAMIC_KNOCKOUT_COMPETITION"
    return "LEAGUE_OR_EVENT_SCOPE"


def build_settlement(
    score_event: Mapping[str, Any],
    *,
    observed_at: str,
    regulation_ambiguous: bool | None = None,
) -> dict[str, Any]:
    if not score_event.get("completed"):
        raise ValueError("cannot settle an incomplete event")
    sport_key = str(score_event.get("sport_key") or "")
    event_id = str(score_event.get("id") or "")
    schedule_revision = int(score_event.get("schedule_revision") or 0)
    if schedule_revision <= 0:
        raise ValueError("a positive schedule_revision is required for settlement")
    commence_time = iso_utc(str(score_event["commence_time"]))
    if parse_utc(observed_at) < parse_utc(commence_time):
        raise ValueError("completed score cannot be settled before scheduled kickoff")
    home_team = str(score_event.get("home_team") or "")
    away_team = str(score_event.get("away_team") or "")
    scores = _score_map(score_event)
    if home_team not in scores or away_team not in scores:
        raise ValueError("completed event is missing a home or away score")
    home_score = scores[home_team]
    away_score = scores[away_team]
    result_1x2 = "home" if home_score > away_score else "away" if away_score > home_score else "draw"
    ambiguous = (
        regulation_time_ambiguous(sport_key)
        if regulation_ambiguous is None
        else bool(regulation_ambiguous)
        or sport_key in ALWAYS_REGULATION_AMBIGUOUS_KEYS
    )
    allow_ambiguous = os.getenv("SOCCER_AUTO_ALLOW_UNVERIFIED_KNOCKOUT_LABELS", "false").lower() == "true"
    event_key = stable_event_key(sport_key, event_id)
    identity = str(score_event.get("schedule_identity") or schedule_identity(score_event))
    item = {
        "PK": event_key,
        "SK": "FINAL#v1",
        "entity_type": "SOCCER_FINAL_SETTLEMENT",
        "settlement_version": SETTLEMENT_VERSION,
        "event_key": event_key,
        "event_id": event_id,
        "sport_key": sport_key,
        "commence_time": commence_time,
        "schedule_revision": schedule_revision,
        "schedule_identity": identity,
        "completed_at": score_event.get("last_update") or observed_at,
        "observed_at": observed_at,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "result_1x2": result_1x2,
        "total_goals": home_score + away_score,
        "goal_difference_home": home_score - away_score,
        "btts": home_score > 0 and away_score > 0,
        "correct_score": f"{home_score}-{away_score}",
        "source": "the_odds_api_scores",
        "source_last_update": score_event.get("last_update"),
        "settlement_semantics": "provider_final_score_regulation_semantics_unverified" if ambiguous else "provider_final_score_league_match",
        "regulation_time_ambiguous": ambiguous,
        "training_eligible_1x2": not ambiguous or allow_ambiguous,
        "training_eligible_score_derived": not ambiguous or allow_ambiguous,
        "unsupported_labels": [
            "player_props", "corners", "cards", "first_half", "halftime_fulltime", "to_qualify"
        ],
    }
    item["settlement_digest"] = digest(
        {
            "event_key": event_key,
            "schedule_revision": schedule_revision,
            "schedule_identity": identity,
            "commence_time": commence_time,
            "sport_key": sport_key,
            "home_team": home_team,
            "away_team": away_team,
            "home_score": home_score,
            "away_score": away_score,
            "result_1x2": result_1x2,
            "settlement_semantics": item["settlement_semantics"],
        }
    )
    return item


def build_settlement_admissibility_certificate(
    settlement: Mapping[str, Any],
    *,
    observed_at: str,
    competition: Mapping[str, Any] | None,
    event_markets: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Append an immutable event-scope classification beside a final score.

    The certificate never changes teams, kickoff, scores, or the signed result
    row. It only records whether this specific fixture can safely use the
    provider's final score as a regulation-time 1X2 label under the current
    event-market evidence. The key is stable for identical evidence but changes
    when event markets or the bound competition policy evidence changes.
    """
    sport_key = str(settlement.get("sport_key") or "")
    markets = sorted({str(value) for value in event_markets if value})
    competition_snapshot = {
        "sport_key": sport_key,
        "title": str((competition or {}).get("title") or ""),
        "description": str((competition or {}).get("description") or ""),
    }
    ambiguous = settlement_scope_regulation_ambiguous(
        sport_key,
        competition=competition_snapshot,
        event_markets=set(markets),
    )
    classification_basis = settlement_admissibility_classification_basis(
        sport_key,
        competition=competition_snapshot,
        event_markets=set(markets),
    )
    source_digest = str(settlement.get("settlement_digest") or "")
    event_key = str(settlement.get("event_key") or "")
    classification_evidence = {
        "certificate_version": SETTLEMENT_ADMISSIBILITY_CERTIFICATE_VERSION,
        "event_key": event_key,
        "event_id": str(settlement.get("event_id") or ""),
        "sport_key": sport_key,
        "commence_time": iso_utc(str(settlement.get("commence_time") or "")),
        "schedule_revision": int(settlement.get("schedule_revision") or 0),
        "schedule_identity": str(
            settlement.get("schedule_identity") or schedule_identity(settlement)
        ),
        "source_settlement_digest": source_digest,
        "event_markets": markets,
        "event_markets_digest": digest(markets),
        "competition_snapshot": competition_snapshot,
        "regulation_time_ambiguous": ambiguous,
        "training_eligible_1x2": not ambiguous,
        "training_eligible_score_derived": not ambiguous,
        "classification_basis": classification_basis,
    }
    classification_evidence_digest = digest(classification_evidence)
    payload = {
        **classification_evidence,
        "classification_evidence_digest": classification_evidence_digest,
        "observed_at": iso_utc(observed_at),
    }
    certificate = {
        "PK": event_key,
        "SK": (
            f"ADMISSIBILITY#v1#SOURCE#{source_digest}#"
            f"CLASSIFICATION#{classification_evidence_digest}"
        ),
        "entity_type": "SOCCER_SETTLEMENT_ADMISSIBILITY_CERTIFICATE",
        **payload,
        "immutable": True,
    }
    certificate["certificate_digest"] = digest(payload)
    return certificate


def _settlement_digest_payload(
    row: Mapping[str, Any], *, include_schedule_identity: bool
) -> dict[str, Any]:
    """Return the signed immutable result evidence for a settlement row.

    ``schedule_identity`` was added to the v1 digest after the first production
    settlements had already been written.  Keeping the two known payload forms
    explicit lets us recognize that one schema transition without treating a
    real score, team, kickoff, or revision change as idempotent.
    """
    payload = {
        "event_key": str(row.get("event_key") or ""),
        "schedule_revision": int(row.get("schedule_revision") or 0),
        "commence_time": iso_utc(str(row.get("commence_time") or "")),
        "sport_key": str(row.get("sport_key") or ""),
        "home_team": str(row.get("home_team") or ""),
        "away_team": str(row.get("away_team") or ""),
        "home_score": int(row.get("home_score")),
        "away_score": int(row.get("away_score")),
        "result_1x2": str(row.get("result_1x2") or ""),
        "settlement_semantics": str(row.get("settlement_semantics") or ""),
    }
    if include_schedule_identity:
        payload["schedule_identity"] = str(
            row.get("schedule_identity") or schedule_identity(row)
        )
    return payload


def _settlement_score_identity_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return result evidence while deliberately excluding classification."""
    return {
        "event_key": str(row.get("event_key") or ""),
        "event_id": str(row.get("event_id") or ""),
        "sport_key": str(row.get("sport_key") or ""),
        "commence_time": iso_utc(str(row.get("commence_time") or "")),
        "schedule_revision": int(row.get("schedule_revision") or 0),
        "schedule_identity": str(
            row.get("schedule_identity") or schedule_identity(row)
        ),
        "home_team": str(row.get("home_team") or ""),
        "away_team": str(row.get("away_team") or ""),
        "home_score": int(row.get("home_score")),
        "away_score": int(row.get("away_score")),
        "result_1x2": str(row.get("result_1x2") or ""),
        "source": str(row.get("source") or ""),
    }


def _has_recognized_settlement_digest(row: Mapping[str, Any]) -> bool:
    actual = str(row.get("settlement_digest") or "")
    if not actual:
        return False
    try:
        expected = {
            digest(
                _settlement_digest_payload(
                    row, include_schedule_identity=include_identity
                )
            )
            for include_identity in (False, True)
        }
    except (KeyError, TypeError, ValueError):
        return False
    return actual in expected


def settlement_training_evidence_valid(row: Mapping[str, Any]) -> bool:
    """Validate one immutable Odds API final-score row before ML admission.

    Historical odds are never labels.  A supervised row may only use the
    separately persisted final-score authority, and every identity/result
    field must still agree with its signed settlement digest.
    """
    try:
        if row.get("entity_type") != "SOCCER_FINAL_SETTLEMENT":
            return False
        if row.get("settlement_version") != SETTLEMENT_VERSION:
            return False
        if row.get("source") != "the_odds_api_scores":
            return False
        expected_event_key = stable_event_key(
            str(row.get("sport_key") or ""), str(row.get("event_id") or "")
        )
        if (
            str(row.get("event_key") or "") != expected_event_key
            or str(row.get("PK") or "") != expected_event_key
            or str(row.get("SK") or "") != "FINAL#v1"
        ):
            return False
        ambiguous = row.get("regulation_time_ambiguous") is True
        expected_semantics = (
            "provider_final_score_regulation_semantics_unverified"
            if ambiguous
            else "provider_final_score_league_match"
        )
        allow_ambiguous = (
            os.getenv("SOCCER_AUTO_ALLOW_UNVERIFIED_KNOCKOUT_LABELS", "false").lower()
            == "true"
        )
        expected_eligibility = not ambiguous or allow_ambiguous
        if row.get("settlement_semantics") != expected_semantics:
            return False
        if row.get("training_eligible_1x2") is not expected_eligibility:
            return False
        if row.get("training_eligible_score_derived") is not expected_eligibility:
            return False
        if int(row.get("schedule_revision") or 0) <= 0:
            return False
        expected_identity = schedule_identity(row)
        if row.get("schedule_identity") and str(row["schedule_identity"]) != expected_identity:
            return False
        home_score = int(row.get("home_score"))
        away_score = int(row.get("away_score"))
        expected_result = (
            "home" if home_score > away_score else "away" if away_score > home_score else "draw"
        )
        if str(row.get("result_1x2") or "") != expected_result:
            return False
        return _has_recognized_settlement_digest(row)
    except (KeyError, TypeError, ValueError):
        return False


def settlement_score_records_equivalent(
    existing: Mapping[str, Any], candidate: Mapping[str, Any]
) -> bool:
    """Recognize identical signed scores with a classification-only change."""
    if not (
        settlement_training_evidence_valid(existing)
        and settlement_training_evidence_valid(candidate)
    ):
        return False
    try:
        return _settlement_score_identity_payload(
            existing
        ) == _settlement_score_identity_payload(candidate)
    except (KeyError, TypeError, ValueError):
        return False


def settlement_admissibility_certificate_valid(
    settlement: Mapping[str, Any], certificate: Mapping[str, Any]
) -> bool:
    """Verify an append-only event-scope classification certificate."""
    try:
        if not settlement_training_evidence_valid(settlement):
            return False
        if (
            certificate.get("entity_type")
            != "SOCCER_SETTLEMENT_ADMISSIBILITY_CERTIFICATE"
            or certificate.get("certificate_version")
            != SETTLEMENT_ADMISSIBILITY_CERTIFICATE_VERSION
            or certificate.get("immutable") is not True
        ):
            return False
        source_digest = str(settlement["settlement_digest"])
        event_key = str(settlement["event_key"])
        sport_key = str(settlement["sport_key"])
        markets = sorted(
            {
                str(value)
                for value in certificate.get("event_markets") or []
                if value
            }
        )
        competition = certificate.get("competition_snapshot") or {}
        if not isinstance(competition, Mapping):
            return False
        normalized_competition = {
            "sport_key": sport_key,
            "title": str(competition.get("title") or ""),
            "description": str(competition.get("description") or ""),
        }
        if str(competition.get("sport_key") or "") != sport_key:
            return False
        ambiguous = settlement_scope_regulation_ambiguous(
            sport_key,
            competition=normalized_competition,
            event_markets=set(markets),
        )
        classification_basis = settlement_admissibility_classification_basis(
            sport_key,
            competition=normalized_competition,
            event_markets=set(markets),
        )
        classification_evidence = {
            "certificate_version": SETTLEMENT_ADMISSIBILITY_CERTIFICATE_VERSION,
            "event_key": event_key,
            "event_id": str(settlement["event_id"]),
            "sport_key": sport_key,
            "commence_time": iso_utc(str(settlement["commence_time"])),
            "schedule_revision": int(settlement["schedule_revision"]),
            "schedule_identity": str(
                settlement.get("schedule_identity")
                or schedule_identity(settlement)
            ),
            "source_settlement_digest": source_digest,
            "event_markets": markets,
            "event_markets_digest": digest(markets),
            "competition_snapshot": normalized_competition,
            "regulation_time_ambiguous": ambiguous,
            "training_eligible_1x2": not ambiguous,
            "training_eligible_score_derived": not ambiguous,
            "classification_basis": classification_basis,
        }
        classification_evidence_digest = digest(classification_evidence)
        payload = {
            **classification_evidence,
            "classification_evidence_digest": classification_evidence_digest,
            "observed_at": iso_utc(str(certificate["observed_at"])),
        }
        return bool(
            str(certificate.get("PK") or "") == event_key
            and str(certificate.get("SK") or "")
            == (
                f"ADMISSIBILITY#v1#SOURCE#{source_digest}#"
                f"CLASSIFICATION#{classification_evidence_digest}"
            )
            and str(certificate.get("event_key") or "") == event_key
            and str(certificate.get("event_id") or "")
            == str(settlement["event_id"])
            and str(certificate.get("sport_key") or "") == sport_key
            and str(certificate.get("commence_time") or "")
            == iso_utc(str(settlement["commence_time"]))
            and int(certificate.get("schedule_revision") or 0)
            == int(settlement["schedule_revision"])
            and str(certificate.get("schedule_identity") or "")
            == str(
                settlement.get("schedule_identity")
                or schedule_identity(settlement)
            )
            and str(certificate.get("source_settlement_digest") or "")
            == source_digest
            and str(certificate.get("event_markets_digest") or "")
            == digest(markets)
            and str(certificate.get("classification_evidence_digest") or "")
            == classification_evidence_digest
            and str(certificate.get("classification_basis") or "")
            == classification_basis
            and certificate.get("regulation_time_ambiguous") is ambiguous
            and certificate.get("training_eligible_1x2") is (not ambiguous)
            and certificate.get("training_eligible_score_derived")
            is (not ambiguous)
            and parse_utc(str(certificate["observed_at"]))
            >= parse_utc(str(settlement["observed_at"]))
            and str(certificate.get("certificate_digest") or "")
            == digest(payload)
        )
    except (KeyError, TypeError, ValueError):
        return False


def settlement_training_views(
    rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach only valid immutable certificates to their signed score rows."""
    finals = [
        dict(row)
        for row in rows
        if row.get("entity_type") == "SOCCER_FINAL_SETTLEMENT"
    ]
    certificates: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if (
            row.get("entity_type")
            != "SOCCER_SETTLEMENT_ADMISSIBILITY_CERTIFICATE"
        ):
            continue
        certificates.setdefault(
            (
                str(row.get("event_key") or row.get("PK") or ""),
                str(row.get("source_settlement_digest") or ""),
            ),
            [],
        ).append(dict(row))
    result: list[dict[str, Any]] = []
    for settlement in finals:
        candidates = certificates.get(
            (
                str(settlement.get("event_key") or ""),
                str(settlement.get("settlement_digest") or ""),
            ),
            [],
        )
        valid_certificates = [
            certificate
            for certificate in candidates
            if settlement_admissibility_certificate_valid(
                settlement, certificate
            )
        ]
        certificate = max(
            valid_certificates,
            key=lambda row: (
                iso_utc(str(row.get("observed_at") or "")),
                str(row.get("certificate_digest") or ""),
            ),
            default=None,
        )
        if certificate:
            settlement["training_admissibility_certificate"] = certificate
            settlement["effective_training_eligible_1x2"] = bool(
                certificate.get("training_eligible_1x2")
            )
            settlement["training_admissibility_source"] = (
                "IMMUTABLE_EVENT_SCOPE_CERTIFICATE"
            )
        else:
            settlement["effective_training_eligible_1x2"] = bool(
                settlement.get("training_eligible_1x2")
            )
            settlement["training_admissibility_source"] = "FINAL_SCORE_ROW"
        result.append(settlement)
    return result


def settlement_training_admissible(row: Mapping[str, Any]) -> bool:
    """Require both signed evidence and an explicitly eligible 1X2 label."""
    certificate = row.get("training_admissibility_certificate")
    if isinstance(certificate, Mapping) and settlement_admissibility_certificate_valid(
        row, certificate
    ):
        # A valid later certificate is authoritative in either direction. This
        # lets newly discovered ``to_qualify`` evidence revoke eligibility just
        # as safely as a mixed-format group-stage certificate can grant it.
        return bool(
            certificate.get("training_eligible_1x2") is True
            and certificate.get("training_eligible_score_derived") is True
        )
    return bool(
        row.get("training_eligible_1x2") is True
        and row.get("training_eligible_score_derived") is True
        and settlement_training_evidence_valid(row)
    )


def settlement_records_equivalent(
    existing: Mapping[str, Any], candidate: Mapping[str, Any]
) -> bool:
    """Recognize only the deployed legacy-to-identity digest transition.

    Both rows must carry a valid known digest and must agree on the complete
    schedule/result evidence plus the training-eligibility semantics.  This is
    intentionally narrower than ignoring a digest mismatch: provider score or
    schedule changes still become hard quarantine records.
    """
    if not (
        _has_recognized_settlement_digest(existing)
        and _has_recognized_settlement_digest(candidate)
    ):
        return False
    try:
        existing_payload = _settlement_digest_payload(
            existing, include_schedule_identity=True
        )
        candidate_payload = _settlement_digest_payload(
            candidate, include_schedule_identity=True
        )
    except (KeyError, TypeError, ValueError):
        return False
    if existing_payload != candidate_payload:
        return False
    return all(
        bool(existing.get(field)) == bool(candidate.get(field))
        for field in (
            "regulation_time_ambiguous",
            "training_eligible_1x2",
            "training_eligible_score_derived",
        )
    )


def settlement_conflict_blocks_training(row: Mapping[str, Any]) -> bool:
    """Return whether an immutable conflict row represents active label risk."""
    if not row.get("training_blocked"):
        return False
    if str(row.get("reason") or "") == "SCORE_SCHEDULE_IDENTITY_MISMATCH":
        return True
    existing = row.get("existing")
    candidate = row.get("candidate")
    if isinstance(existing, Mapping) and isinstance(candidate, Mapping):
        # The August 2026 deployment added schedule_identity to the digest.
        # Rows recording only that recognized transition are audit artifacts,
        # not contradictory labels, and must not poison training forever.
        if settlement_records_equivalent(
            existing, candidate
        ) or settlement_score_records_equivalent(existing, candidate):
            return False
    return True


def _record_conflict(store: SoccerStore, existing: Mapping[str, Any], candidate: Mapping[str, Any], observed_at: str) -> None:
    evidence_digest = digest(
        {
            "event_key": candidate["event_key"],
            "reason": "SETTLEMENT_EVIDENCE_CONFLICT",
            "existing_digest": existing.get("settlement_digest"),
            "candidate_digest": candidate.get("settlement_digest"),
        }
    )
    store.ops.put_item(
        Item=ddb_safe(
            {
                "PK": "SETTLEMENT_CONFLICT",
                # One row per distinct evidence pair prevents a five-minute
                # provider retry from inflating health counts indefinitely.
                "SK": f"{candidate['event_key']}#SETTLEMENT_EVIDENCE#{evidence_digest}",
                "entity_type": "SOCCER_SETTLEMENT_CONFLICT",
                "event_key": candidate["event_key"],
                "reason": "SETTLEMENT_EVIDENCE_CONFLICT",
                "conflict_evidence_digest": evidence_digest,
                "existing_digest": existing.get("settlement_digest"),
                "candidate_digest": candidate.get("settlement_digest"),
                "existing": dict(existing),
                "candidate": dict(candidate),
                "observed_at": observed_at,
                "training_blocked": True,
            }
        )
    )


def reconcile_settlement_admissibility(
    store: SoccerStore,
    *,
    observed_at: str,
    competition_rows: Mapping[str, Mapping[str, Any]],
    checkpoint: bool = True,
    acquire_claim: bool = True,
) -> dict[str, Any]:
    """Certify immutable score rows using stored event-market evidence.

    Production uses a bounded, checkpointed scan page. A failed page is not
    checkpointed, so it is retried idempotently; a successful final page starts
    a new bounded audit cycle on the next scheduled invocation. This performs
    no provider request and never rewrites ``FINAL#v1``.
    """
    bounded_scan = all(
        hasattr(store, attribute)
        for attribute in (
            "settlement_admissibility_migration_page",
            "checkpoint_settlement_admissibility_migration",
        )
    )
    legacy_scan = all(
        hasattr(store, attribute)
        for attribute in (
            "settlements",
            "scan_all",
            "cumulative_market_inventory",
            "put_settlement",
        )
    )
    if not bounded_scan and not legacy_scan:
        return {
            "final_score_rows_examined": 0,
            "certificates_written": 0,
            "certificates_existing": 0,
            "pending_market_evidence": 0,
            "failures": [],
            "scan_mode": "UNAVAILABLE",
            "skipped": "STORE_CAPABILITY_UNAVAILABLE",
        }

    claim_acquired = True
    claim_key = ""
    if bounded_scan and acquire_claim and hasattr(store, "claim_job"):
        cadence_slot = int(parse_utc(observed_at).timestamp()) // SCORES_CADENCE_SECONDS
        claim_key = f"SETTLEMENT_ADMISSIBILITY#{cadence_slot}"
        claim_acquired = bool(
            store.claim_job(
                claim_key,
                (cadence_slot + 2) * SCORES_CADENCE_SECONDS,
            )
        )
        if not claim_acquired:
            return {
                "final_score_rows_examined": 0,
                "certificates_written": 0,
                "certificates_existing": 0,
                "pending_market_evidence": 0,
                "failures": [],
                "scan_mode": "BOUNDED_CHECKPOINTED",
                "claim_acquired": False,
                "skipped": "RECONCILIATION_CADENCE_ALREADY_CLAIMED",
            }

    page: dict[str, Any] = {}
    if bounded_scan:
        page = dict(
            store.settlement_admissibility_migration_page(
                limit=SETTLEMENT_ADMISSIBILITY_RECONCILIATION_PAGE_LIMIT
            )
        )
        source_rows = [plain(row) for row in page.get("rows") or []]
        scan_mode = "BOUNDED_CHECKPOINTED"
    else:
        source_rows = [
            plain(row)
            for row in store.scan_all(store.settlements, ConsistentRead=True)
        ]
        scan_mode = "LEGACY_TEST_STORE"

    rows = [
        row
        for row in source_rows
        if row.get("entity_type") == "SOCCER_FINAL_SETTLEMENT"
    ]
    written = 0
    existing = 0
    pending_evidence = 0
    failures: list[dict[str, str]] = []
    for settlement in rows:
        try:
            if not settlement_training_evidence_valid(settlement):
                continue
            event_key = str(settlement["event_key"])
            inventory = store.cumulative_market_inventory(
                event_key,
                observed_at=observed_at,
            )
            event_markets = {
                str(market)
                for detail in inventory.values()
                for market in detail.get("markets") or []
                if market
            }
            sport_key = str(settlement["sport_key"])
            if (
                sport_key not in ALWAYS_REGULATION_AMBIGUOUS_KEYS
                and not event_markets.intersection({"h2h", "h2h_3_way"})
            ):
                pending_evidence += 1
                continue
            certificate = build_settlement_admissibility_certificate(
                settlement,
                observed_at=observed_at,
                competition=competition_rows.get(sport_key),
                event_markets=event_markets,
            )
            if store.put_settlement(certificate):
                written += 1
            else:
                existing += 1
        except Exception as exc:
            failures.append(
                {
                    "event_key": str(settlement.get("event_key") or ""),
                    "error": str(exc),
                }
            )

    checkpointed = False
    if bounded_scan and checkpoint and not failures:
        checkpointed = bool(
            store.checkpoint_settlement_admissibility_migration(
                expected_cursor_digest=str(page.get("cursor_digest") or ""),
                next_start_key=page.get("next_start_key"),
                cycle=int(page.get("cycle") or 0),
                page_index=int(page.get("page_index") or 0),
                observed_at=observed_at,
            )
        )
        if not checkpointed:
            failures.append(
                {
                    "event_key": "",
                    "error": "SETTLEMENT_ADMISSIBILITY_CURSOR_CHANGED",
                }
            )

    return {
        "final_score_rows_examined": len(rows),
        "settlement_table_rows_scanned": len(source_rows),
        "certificates_written": written,
        "certificates_existing": existing,
        "pending_market_evidence": pending_evidence,
        "scan_mode": scan_mode,
        "scan_page_limit": (
            SETTLEMENT_ADMISSIBILITY_RECONCILIATION_PAGE_LIMIT
            if bounded_scan
            else len(source_rows)
        ),
        "scan_has_more": bool(page.get("next_start_key")) if bounded_scan else False,
        "scan_cycle": int(page.get("cycle") or 0) if bounded_scan else 0,
        "scan_page_index": int(page.get("page_index") or 0) if bounded_scan else 0,
        "scan_checkpointed": checkpointed if bounded_scan else True,
        "checkpoint_requested": bool(checkpoint),
        "claim_requested": bool(acquire_claim),
        "claim_acquired": claim_acquired,
        "claim_key": claim_key,
        "failures": failures,
    }


def settlement_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    store = SoccerStore()
    observed = now_utc()
    observed_at = iso_utc(observed)
    written = 0
    idempotent = 0
    admissibility_certificates_written = 0
    admissibility_certificates_idempotent = 0
    conflicts = 0
    untracked_completed_scores = 0
    failures: list[dict[str, str]] = []
    competition_rows = {
        str(row["sport_key"]): row
        for row in store.list_competitions()
    }
    action = str((event or {}).get("action") or "")
    reconciliation_only = action == "reconcile_admissibility_only"
    admissibility_reconciliation = reconcile_settlement_admissibility(
        store,
        observed_at=observed_at,
        competition_rows=competition_rows,
        checkpoint=not reconciliation_only,
        acquire_claim=not reconciliation_only,
    )
    failures.extend(admissibility_reconciliation.get("failures") or [])
    if reconciliation_only:
        return {
            "ok": not failures,
            "system": "soccer_auto",
            "component": "settlement",
            "action": action,
            "provider_calls": 0,
            "admissibility_reconciliation": admissibility_reconciliation,
            "failures": failures,
        }

    client = _client()
    active_events = store.active_events_between(
        iso_utc(observed - timedelta(days=3)),
        iso_utc(observed + timedelta(hours=6)),
    )
    score_capability = {
        sport_key: row.get("scores_supported", True)
        for sport_key, row in competition_rows.items()
    }
    sport_keys = sorted(
        {
            str(row["sport_key"])
            for row in active_events
            if row.get("sport_key") and score_capability.get(str(row["sport_key"]), True)
        }
    )
    for sport_key in sport_keys:
        try:
            cadence_slot = int(observed.timestamp()) // SCORES_CADENCE_SECONDS
            claim = f"SCORES#{sport_key}#{cadence_slot}"
            if not store.claim_job(
                claim,
                (cadence_slot + 2) * SCORES_CADENCE_SECONDS,
            ):
                continue
            if not store.provider_budget_available(
                "scores",
                observed_at,
                estimated_cost=DEFAULT_MAX_ATTEMPTS * 2,
            ):
                failures.append({"sport_key": sport_key, "error": "SHARED_PROVIDER_QUOTA_RESERVE"})
                break
            try:
                response = client.scores(sport_key, days_from=3)
            except Exception:
                store.release_job(claim)
                raise
            # Chronology and quota observations are evidence about when the
            # provider response was in hand, never when the request began.
            score_observed_at = iso_utc(now_utc())
            store.record_quota(
                response, operation="scores", observed_at=score_observed_at
            )
            store.archive_json(
                "scores",
                response.data,
                observed_at=score_observed_at,
                identity=sport_key,
                metadata={"sport_key": sport_key},
            )
            for raw in response.data or []:
                if not raw.get("completed"):
                    continue
                raw = {**raw, "sport_key": raw.get("sport_key") or sport_key}
                if not raw.get("commence_time"):
                    raise ValueError("completed score event is missing commence_time")
                candidate_event_key = stable_event_key(
                    str(raw["sport_key"]), str(raw.get("id") or "")
                )
                # Scores are label evidence, never schedule authority. An
                # out-of-order or mismatched score response cannot repaint the
                # event revision that collection and T-45 locking used.
                stored_event = store.get_event(candidate_event_key)
                if not stored_event:
                    # Scores returns every completed match in the provider's
                    # horizon, including matches this deployment never
                    # inventoried or locked. The archived raw response is enough
                    # evidence; an unjoined row is not a contradiction and has
                    # no label that needs quarantining.
                    untracked_completed_scores += 1
                    continue
                try:
                    score_schedule_identity = schedule_identity(raw)
                    stored_schedule_identity = str(
                        stored_event.get("schedule_identity")
                        or schedule_identity(stored_event)
                    )
                    identity_matches = bool(
                        score_schedule_identity == stored_schedule_identity
                    )
                except (KeyError, TypeError, ValueError):
                    score_schedule_identity = "INVALID_SCORE_SCHEDULE_IDENTITY"
                    try:
                        stored_schedule_identity = str(
                            stored_event.get("schedule_identity")
                            or schedule_identity(stored_event)
                        )
                    except (KeyError, TypeError, ValueError):
                        stored_schedule_identity = "INVALID_STORED_SCHEDULE_IDENTITY"
                    identity_matches = False
                if not identity_matches:
                    evidence_digest = digest(
                        {
                            "event_key": candidate_event_key,
                            "reason": "SCORE_SCHEDULE_IDENTITY_MISMATCH",
                            "score_schedule_identity": score_schedule_identity,
                            "stored_schedule_identity": stored_schedule_identity,
                        }
                    )
                    store.ops.put_item(
                        Item=ddb_safe(
                            {
                                "PK": "SETTLEMENT_CONFLICT",
                                "SK": f"{candidate_event_key}#SCHEDULE_IDENTITY#{evidence_digest}",
                                "entity_type": "SOCCER_SETTLEMENT_CONFLICT",
                                "event_key": candidate_event_key,
                                "reason": "SCORE_SCHEDULE_IDENTITY_MISMATCH",
                                "score_schedule_identity": score_schedule_identity,
                                "stored_schedule_identity": stored_schedule_identity,
                                "conflict_evidence_digest": evidence_digest,
                                "observed_at": score_observed_at,
                                "training_blocked": True,
                            }
                        )
                    )
                    conflicts += 1
                    continue
                raw = {
                    **raw,
                    "commence_time": stored_event["commence_time"],
                    "schedule_revision": stored_event["schedule_revision"],
                    "schedule_identity": str(
                        stored_event.get("schedule_identity")
                        or schedule_identity(stored_event)
                    ),
                    "home_team": stored_event["home_team"],
                    "away_team": stored_event["away_team"],
                }
                inventory = store.cumulative_market_inventory(
                    candidate_event_key,
                    observed_at=score_observed_at,
                )
                event_markets = {
                    str(market)
                    for detail in inventory.values()
                    for market in detail.get("markets") or []
                }
                candidate = build_settlement(
                    raw,
                    observed_at=score_observed_at,
                    regulation_ambiguous=settlement_scope_regulation_ambiguous(
                        str(raw["sport_key"]),
                        competition=competition_rows.get(str(raw["sport_key"])),
                        event_markets=event_markets,
                    ),
                )
                existing = store.settlements.get_item(
                    Key={"PK": candidate["PK"], "SK": candidate["SK"]}, ConsistentRead=True
                ).get("Item")
                if existing:
                    existing = plain(existing)
                    if (
                        existing.get("settlement_digest")
                        == candidate.get("settlement_digest")
                        or settlement_records_equivalent(existing, candidate)
                    ):
                        idempotent += 1
                        store.mark_completed(candidate["event_key"], score_observed_at)
                    elif settlement_score_records_equivalent(
                        existing, candidate
                    ):
                        certificate = (
                            build_settlement_admissibility_certificate(
                                existing,
                                observed_at=score_observed_at,
                                competition=competition_rows.get(
                                    str(raw["sport_key"])
                                ),
                                event_markets=event_markets,
                            )
                        )
                        if store.put_settlement(certificate):
                            admissibility_certificates_written += 1
                        else:
                            admissibility_certificates_idempotent += 1
                        idempotent += 1
                        store.mark_completed(
                            candidate["event_key"], score_observed_at
                        )
                    else:
                        _record_conflict(
                            store, existing, candidate, score_observed_at
                        )
                        conflicts += 1
                    continue
                if store.put_settlement(candidate):
                    written += 1
                    store.mark_completed(candidate["event_key"], score_observed_at)
        except Exception as exc:
            failures.append({"sport_key": sport_key, "error": str(exc)})
    return {
        "ok": not failures and conflicts == 0,
        "system": "soccer_auto",
        "competitions_checked": len(sport_keys),
        "active_events_considered": len(active_events),
        "settlements_written": written,
        "idempotent": idempotent,
        "admissibility_certificates_written": (
            admissibility_certificates_written
        ),
        "admissibility_certificates_idempotent": (
            admissibility_certificates_idempotent
        ),
        "admissibility_reconciliation": admissibility_reconciliation,
        "untracked_completed_scores": untracked_completed_scores,
        "conflicts": conflicts,
        "failures": failures,
    }
