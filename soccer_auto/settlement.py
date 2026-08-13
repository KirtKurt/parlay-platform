"""Immutable result capture from The Odds API's three-day scores horizon."""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Mapping

from .canonical import digest, iso_utc, stable_event_key
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


def _record_conflict(store: SoccerStore, existing: Mapping[str, Any], candidate: Mapping[str, Any], observed_at: str) -> None:
    store.ops.put_item(
        Item=ddb_safe(
            {
                "PK": "SETTLEMENT_CONFLICT",
                "SK": f"{candidate['event_key']}#{observed_at}",
                "entity_type": "SOCCER_SETTLEMENT_CONFLICT",
                "event_key": candidate["event_key"],
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
            store.record_quota(response, operation="scores", observed_at=observed_at)
            store.archive_json(
                "scores",
                response.data,
                observed_at=observed_at,
                identity=sport_key,
                metadata={"sport_key": sport_key},
            )
            for raw in response.data or []:
                if not raw.get("completed"):
                    continue
                raw = {**raw, "sport_key": raw.get("sport_key") or sport_key}
                if not raw.get("commence_time"):
                    raise ValueError("completed score event is missing commence_time")
                stored_event = store.put_event(raw, observed_at)
                raw = {
                    **raw,
                    "commence_time": stored_event["commence_time"],
                    "schedule_revision": stored_event["schedule_revision"],
                }
                candidate_event_key = stable_event_key(
                    str(raw["sport_key"]), str(raw.get("id") or "")
                )
                inventory = store.cumulative_market_inventory(
                    candidate_event_key,
                    observed_at=observed_at,
                )
                event_markets = {
                    str(market)
                    for detail in inventory.values()
                    for market in detail.get("markets") or []
                }
                candidate = build_settlement(
                    raw,
                    observed_at=observed_at,
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
                    if existing.get("settlement_digest") == candidate.get("settlement_digest"):
                        idempotent += 1
                        store.mark_completed(candidate["event_key"], observed_at)
                    else:
                        _record_conflict(store, existing, candidate, observed_at)
                        conflicts += 1
                    continue
                if store.put_settlement(candidate):
                    written += 1
                    store.mark_completed(candidate["event_key"], observed_at)
        except Exception as exc:
            failures.append({"sport_key": sport_key, "error": str(exc)})
    return {
        "ok": not failures and conflicts == 0,
        "system": "soccer_auto",
        "competitions_checked": len(sport_keys),
        "active_events_considered": len(active_events),
        "settlements_written": written,
        "idempotent": idempotent,
        "conflicts": conflicts,
        "failures": failures,
    }
