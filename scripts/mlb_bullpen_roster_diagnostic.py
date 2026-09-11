#!/usr/bin/env python3
"""Read-only probe for the official MLB pregame boxscore ``bullpen`` arrays.

This diagnostic is intentionally non-authoritative.  It proves only whether the
current official Preview feed exposes internally consistent bullpen player IDs
for each team.  It does NOT interpret that list as injury clearance, workload
clearance, manager intent, or actual pitcher availability, and therefore cannot
satisfy Fundamentals V2 ``availableRelievers`` or change production scoring.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

VERSION = "MLB-BULLPEN-ROSTER-DIAGNOSTIC-v1-passive-official-preview"
REPORT_TYPE = "MLB_BULLPEN_ROSTER_READ_ONLY_DIAGNOSTIC"
ET = ZoneInfo("America/New_York")
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _positive_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _http_json(url: str, timeout: int = 8) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": "inqsi-mlb-bullpen-diagnostic/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("provider response must be a JSON object")
    return payload


def _team_id(game: Mapping[str, Any], side: str) -> Any:
    return ((((game.get("teams") or {}).get(side) or {}).get("team") or {}).get("id"))


def validate_team_bullpen(team: Any) -> Dict[str, Any]:
    """Validate only the identity shape of one official boxscore bullpen list."""
    errors: list[str] = []
    if not isinstance(team, Mapping):
        return {
            "identityValid": False,
            "errors": ["team_payload_missing"],
            "bullpenCount": 0,
            "bullpenPlayerIds": [],
            "subsetOfPitchers": None,
            "missingFromPitchers": [],
        }

    raw = team.get("bullpen")
    if not isinstance(raw, list):
        errors.append("bullpen_list_missing")
        raw = []
    ids = list(raw)
    if not ids:
        errors.append("bullpen_list_empty")
    if any(not _positive_id(value) for value in ids):
        errors.append("bullpen_contains_invalid_player_id")
    valid_ids = [int(value) for value in ids if _positive_id(value)]
    if len(set(valid_ids)) != len(valid_ids) or len(valid_ids) != len(ids):
        errors.append("bullpen_player_ids_not_unique")

    players = team.get("players")
    players = players if isinstance(players, Mapping) else {}
    for identity in valid_ids:
        player = players.get(f"ID{identity}")
        person_id = (((player or {}).get("person") or {}).get("id")) if isinstance(player, Mapping) else None
        if person_id != identity:
            errors.append(f"bullpen_player_identity_mismatch:{identity}")

    pitchers = team.get("pitchers")
    subset: Optional[bool] = None
    missing_from_pitchers: list[int] = []
    if isinstance(pitchers, list) and pitchers and all(_positive_id(value) for value in pitchers):
        pitcher_ids = {int(value) for value in pitchers}
        missing_from_pitchers = sorted(identity for identity in valid_ids if identity not in pitcher_ids)
        subset = not missing_from_pitchers

    return {
        "identityValid": not errors,
        "errors": sorted(set(errors)),
        "bullpenCount": len(valid_ids),
        "bullpenPlayerIds": valid_ids,
        "subsetOfPitchers": subset,
        "missingFromPitchers": missing_from_pitchers,
    }


def diagnose_game(
    schedule_game: Mapping[str, Any],
    *,
    observed_at: datetime,
    fetch_json: Callable[[str, int], Dict[str, Any]],
) -> Dict[str, Any]:
    observed = observed_at.astimezone(timezone.utc)
    game_pk = schedule_game.get("gamePk")
    start = _parse_dt(schedule_game.get("gameDate"))
    schedule_status = str((schedule_game.get("status") or {}).get("abstractGameState") or "")
    home_id = _team_id(schedule_game, "home")
    away_id = _team_id(schedule_game, "away")
    base: Dict[str, Any] = {
        "gamePk": game_pk,
        "commenceTimeUtc": start.isoformat() if start else None,
        "scheduleStatus": schedule_status,
        "minutesToStart": round((start - observed).total_seconds() / 60.0, 3) if start else None,
        "preT45": bool(start and observed < start - timedelta(minutes=45)),
        "feedIdentityValid": False,
        "home": None,
        "away": None,
        "bothBullpenIdentityValid": False,
        "semanticAuthority": "CURRENT_OFFICIAL_BULLPEN_LIST_ONLY_NOT_AVAILABILITY_CLEARANCE",
        "canSatisfyAvailableRelievers": False,
        "canChangeProductionScoring": False,
    }
    if not _positive_id(game_pk) or start is None or not _positive_id(home_id) or not _positive_id(away_id):
        base["feedErrors"] = ["schedule_identity_incomplete"]
        return base
    if schedule_status != "Preview":
        base["feedErrors"] = ["schedule_not_preview"]
        return base

    url = FEED_URL.format(game_pk=game_pk)
    try:
        feed = fetch_json(url, 8)
    except Exception as exc:
        base["feedErrors"] = [f"feed_fetch_failed:{type(exc).__name__}"]
        return base

    game_data = feed.get("gameData") or {}
    box_teams = ((feed.get("liveData") or {}).get("boxscore") or {}).get("teams") or {}
    feed_pk = ((game_data.get("game") or {}).get("pk"))
    feed_status = str((game_data.get("status") or {}).get("abstractGameState") or "")
    feed_start = _parse_dt((game_data.get("datetime") or {}).get("dateTime"))
    identity_errors: list[str] = []
    if feed_pk != game_pk:
        identity_errors.append("feed_game_pk_mismatch")
    if feed_status != "Preview":
        identity_errors.append("feed_not_preview")
    if feed_start != start:
        identity_errors.append("feed_start_time_mismatch")
    for side, expected in (("home", home_id), ("away", away_id)):
        actual = ((((box_teams.get(side) or {}).get("team") or {}).get("id")))
        if actual != expected:
            identity_errors.append(f"feed_{side}_team_mismatch")
    if identity_errors:
        base["feedErrors"] = sorted(set(identity_errors))
        return base

    home = validate_team_bullpen(box_teams.get("home"))
    away = validate_team_bullpen(box_teams.get("away"))
    base.update(
        {
            "feedIdentityValid": True,
            "feedErrors": [],
            "home": home,
            "away": away,
            "bothBullpenIdentityValid": bool(home["identityValid"] and away["identityValid"]),
        }
    )
    return base


def build_report(
    *,
    observed_at: Optional[datetime] = None,
    fetch_json: Callable[[str, int], Dict[str, Any]] = _http_json,
) -> Dict[str, Any]:
    observed = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    slate_date = observed.astimezone(ET).date().isoformat()
    url = SCHEDULE_URL + "?" + urllib.parse.urlencode({"sportId": 1, "date": slate_date})
    schedule = fetch_json(url, 8)
    games = [
        game
        for date_row in (schedule.get("dates") or [])
        if isinstance(date_row, Mapping)
        for game in (date_row.get("games") or [])
        if isinstance(game, Mapping)
        and str((game.get("status") or {}).get("abstractGameState") or "") == "Preview"
    ]
    diagnostics = [
        diagnose_game(game, observed_at=observed, fetch_json=fetch_json)
        for game in games
    ]
    valid_both = sum(item.get("bothBullpenIdentityValid") is True for item in diagnostics)
    valid_pre_t45 = sum(
        item.get("bothBullpenIdentityValid") is True and item.get("preT45") is True
        for item in diagnostics
    )
    return {
        "ok": True,
        "version": VERSION,
        "reportType": REPORT_TYPE,
        "createdAtUtc": observed.isoformat().replace("+00:00", "Z"),
        "slateDateEt": slate_date,
        "readOnly": True,
        "provider": "MLB Stats API",
        "previewGameCount": len(diagnostics),
        "bothBullpenIdentityValidCount": valid_both,
        "preT45BothBullpenIdentityValidCount": valid_pre_t45,
        "availableRelieverSemanticClaimCount": 0,
        "fundamentalsCompletenessChanged": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "recommendation": (
            "Treat a valid official Preview bullpen array as passive current-bullpen roster evidence only. "
            "Do not map it to availableRelievers until an independent source or contract proves actual availability semantics."
        ),
        "games": diagnostics,
        "sourceOfTruth": {"scheduleEndpoint": url, "feedEndpointTemplate": FEED_URL},
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="runtime_reports/mlb_bullpen_roster_diagnostic_latest.json",
    )
    args = parser.parse_args(argv)
    report = build_report()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "previewGameCount": report["previewGameCount"],
                "bothBullpenIdentityValidCount": report["bothBullpenIdentityValidCount"],
                "preT45BothBullpenIdentityValidCount": report["preT45BothBullpenIdentityValidCount"],
                "availableRelieverSemanticClaimCount": report["availableRelieverSemanticClaimCount"],
                "output": str(path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
