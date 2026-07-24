"""Historical optimizer entrypoint with source-honest MLB slate canonicalization.

MLB's exact-date schedule endpoint can include postponed or resumed games whose
``officialDate`` belongs to a different slate.  Those provider cross-references
must not poison the requested day's canonical slate, and they must never be
silently discarded.  This entrypoint records deterministic exclusion evidence,
then delegates to the existing fail-closed optimizer handler.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Mapping, Optional

import inqsi_pull_history as history
import mlb_canonical_final_labels_v1 as final_labels
import mlb_historical_optimizer_handler as optimizer_handler


VERSION = "MLB-HISTORICAL-ENTRYPOINT-v1-cross-date-audited-exclusions"


def _team_name(raw: Mapping[str, Any], side: str) -> Optional[str]:
    teams = raw.get("teams") or {}
    side_row = teams.get(side) or {}
    value = ((side_row.get("team") or {}).get("name"))
    return str(value) if value else None


def _cross_date_evidence(raw: Mapping[str, Any], slate_date: str) -> Dict[str, Any]:
    status = raw.get("status") or {}
    return {
        "officialGamePk": str(raw.get("gamePk") or ""),
        "queriedSlateDateEt": slate_date,
        "officialDate": str(raw.get("officialDate") or ""),
        "gameDate": raw.get("gameDate"),
        "rescheduleDate": raw.get("rescheduleDate"),
        "resumeDate": raw.get("resumeDate"),
        "rescheduledFrom": raw.get("rescheduledFrom"),
        "resumeGameDate": raw.get("resumeGameDate"),
        "awayTeam": _team_name(raw, "away"),
        "homeTeam": _team_name(raw, "home"),
        "officialStatus": {
            "abstractGameState": status.get("abstractGameState"),
            "codedGameState": status.get("codedGameState"),
            "statusCode": status.get("statusCode"),
            "detailedState": status.get("detailedState"),
        },
        "exclusionReason": "provider_exact_date_response_cross_date_reference",
    }


def fetch_official_schedule_cross_date_safe(
    slate_date: str,
    *,
    timeout: int = 15,
    http_get: Optional[Callable[[str, int], Any]] = None,
) -> Dict[str, Any]:
    """Return the canonical official-date slate plus audited cross-date exclusions."""

    getter = http_get or (lambda url, seconds: final_labels._http_get_json(url, seconds))
    payload = getter(final_labels.official_finals_url(slate_date), timeout)
    if not isinstance(payload, dict):
        raise RuntimeError("MLB_OFFICIAL_FINAL_PAYLOAD_NOT_OBJECT")

    dates = payload.get("dates")
    if not isinstance(dates, list):
        raise RuntimeError("MLB_OFFICIAL_FINAL_DATES_INVALID")

    filtered_dates = []
    exclusions = []
    provider_game_count = 0
    seen_game_pks = set()

    for date_row in dates:
        if not isinstance(date_row, dict) or str(date_row.get("date") or "") != slate_date:
            raise RuntimeError("MLB_OFFICIAL_FINAL_NOT_EXACT_DATE")
        games = date_row.get("games")
        if not isinstance(games, list):
            raise RuntimeError("MLB_OFFICIAL_FINAL_GAMES_INVALID")

        kept = []
        for raw in games:
            if not isinstance(raw, dict):
                raise RuntimeError("MLB_OFFICIAL_FINAL_GAME_ROW_INVALID")
            provider_game_count += 1
            game_pk = str(raw.get("gamePk") or "").strip()
            if not game_pk or game_pk in seen_game_pks:
                raise RuntimeError("MLB_OFFICIAL_FINAL_GAME_PK_INVALID_OR_DUPLICATE")
            seen_game_pks.add(game_pk)

            official_date = str(raw.get("officialDate") or slate_date)
            if official_date != slate_date:
                evidence = _cross_date_evidence(raw, slate_date)
                if not evidence["officialDate"]:
                    raise RuntimeError(
                        f"MLB_OFFICIAL_FINAL_CROSS_DATE_IDENTITY_UNPROVEN:{game_pk}"
                    )
                exclusions.append(evidence)
                continue
            kept.append(copy.deepcopy(raw))

        filtered_row = copy.deepcopy(date_row)
        filtered_row["games"] = kept
        filtered_row["totalGames"] = len(kept)
        filtered_dates.append(filtered_row)

    filtered_payload = copy.deepcopy(payload)
    filtered_payload["dates"] = filtered_dates
    filtered_payload["totalGames"] = sum(len(row["games"]) for row in filtered_dates)

    canonical = final_labels.validate_official_schedule_payload(filtered_payload, slate_date)
    exclusions = sorted(exclusions, key=lambda row: row["officialGamePk"])
    canonical.update(
        {
            "crossDateCanonicalizationVersion": VERSION,
            "providerReportedGameCount": provider_game_count,
            "crossDateExcludedCount": len(exclusions),
            "crossDateExclusions": exclusions,
            "crossDateExclusionFingerprint": history.canonical_payload_fingerprint(exclusions),
            "canonicalOfficialDateGameCount": canonical["officialGameCount"],
        }
    )
    if provider_game_count != canonical["officialGameCount"] + len(exclusions):
        raise RuntimeError("MLB_OFFICIAL_FINAL_CROSS_DATE_ACCOUNTING_MISMATCH")
    return canonical


# The optimizer imports this module object once.  Replace only its schedule fetch
# authority; settlement validation and all downstream 80%/audit gates remain intact.
optimizer_handler.final_labels.fetch_official_schedule = fetch_official_schedule_cross_date_safe


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    return optimizer_handler.lambda_handler(event, context)
