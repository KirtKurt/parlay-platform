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
SCORES_CADENCE_SECONDS = int(os.getenv("SOCCER_AUTO_SCORES_CADENCE_SECONDS", "300"))

# The scores endpoint does not distinguish regulation-only odds settlement from
# extra time/penalties.  These competitions remain captured but fail closed for
# supervised 1X2 labels unless a later authoritative regulation-time source is
# explicitly wired.
KNOCKOUT_OR_TOURNAMENT_KEYS = frozenset(
    {
        "soccer_africa_cup_of_nations",
        "soccer_england_efl_cup",
        "soccer_fa_cup",
        "soccer_fifa_world_cup",
        "soccer_fifa_world_cup_womens",
        "soccer_fifa_club_world_cup",
        "soccer_france_coupe_de_france",
        "soccer_germany_dfb_pokal",
        "soccer_italy_coppa_italia",
        "soccer_spain_copa_del_rey",
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

KNOCKOUT_KEY_MARKERS = (
    "_cup",
    "copa_",
    "_pokal",
    "coupe_",
    "_qualification",
    "_qualifier",
    "_playoff",
    "_knockout",
    "_trophy",
    "champions_league",
    "europa_league",
    "conference_league",
    "nations_league",
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
    if sport_key in KNOCKOUT_OR_TOURNAMENT_KEYS or "to_qualify" in (event_markets or set()):
        return True
    row = competition or {}
    searchable = " ".join(
        str(value or "").casefold().replace(" ", "_")
        for value in (sport_key, row.get("title"), row.get("description"))
    )
    return any(marker in searchable for marker in KNOCKOUT_KEY_MARKERS)


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
        else bool(regulation_ambiguous) or sport_key in KNOCKOUT_OR_TOURNAMENT_KEYS
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
        if settlement_records_equivalent(existing, candidate):
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


def settlement_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    store = SoccerStore()
    client = _client()
    observed_at = iso_utc(now_utc())
    written = 0
    idempotent = 0
    conflicts = 0
    untracked_completed_scores = 0
    failures: list[dict[str, str]] = []
    observed = now_utc()
    active_events = store.active_events_between(
        iso_utc(observed - timedelta(days=3)),
        iso_utc(observed + timedelta(hours=6)),
    )
    competition_rows = {
        str(row["sport_key"]): row
        for row in store.list_competitions()
    }
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
                    regulation_ambiguous=regulation_time_ambiguous(
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
        "untracked_completed_scores": untracked_completed_scores,
        "conflicts": conflicts,
        "failures": failures,
    }
