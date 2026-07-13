#!/usr/bin/env python3
"""Build the read-only MLB pull-guard proof.

The canonical pull writer intentionally does not store an empty snapshot.  The
guard therefore verifies the exact-date official MLB schedule before deciding
whether a slate is expected to have quarter-hour pull rows.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")
SCHEDULE_SOURCE = "MLB Stats API exact-date schedule"
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


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


def _s_value(item: Dict[str, Any], key: str) -> str:
    value = item.get(key, {})
    if isinstance(value, dict):
        return str(value.get("S") or "")
    return str(value or "")


def _schedule_url(slate_date: str) -> str:
    query = urllib.parse.urlencode(
        {
            "sportId": "1",
            "startDate": slate_date,
            "endDate": slate_date,
        }
    )
    return f"{SCHEDULE_URL}?{query}"


def fetch_official_schedule(slate_date: str, timeout: int = 15) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
    """Fetch the exact Eastern slate date; errors remain explicit and fail closed."""

    url = _schedule_url(slate_date)
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "inqsi-mlb-pull-guard/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload, None, url
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}", url


def validate_official_schedule(payload: Any, slate_date: str) -> Dict[str, Any]:
    """Validate that a Stats API response is complete and only for slate_date."""

    if not isinstance(payload, dict):
        return {"verified": False, "error": "schedule payload is not an object"}

    total_games = payload.get("totalGames")
    dates = payload.get("dates")
    if isinstance(total_games, bool) or not isinstance(total_games, int) or total_games < 0:
        return {"verified": False, "error": "schedule totalGames is missing or invalid"}
    if not isinstance(dates, list):
        return {"verified": False, "error": "schedule dates is missing or invalid"}

    games: List[Dict[str, Any]] = []
    for date_row in dates:
        if not isinstance(date_row, dict):
            return {"verified": False, "error": "schedule date row is not an object"}
        if str(date_row.get("date") or "") != slate_date:
            return {
                "verified": False,
                "error": f"schedule returned a non-requested date: {date_row.get('date')!r}",
            }
        row_games = date_row.get("games")
        if not isinstance(row_games, list):
            return {"verified": False, "error": "schedule games is missing or invalid"}
        games.extend(row_games)

    if total_games != len(games):
        return {
            "verified": False,
            "error": f"schedule totalGames={total_games} but returned {len(games)} games",
        }

    game_ids = []
    game_types = []
    for game in games:
        if not isinstance(game, dict):
            return {"verified": False, "error": "schedule game is not an object"}
        game_pk = game.get("gamePk")
        if game_pk is None:
            return {"verified": False, "error": "schedule game is missing gamePk"}
        game_ids.append(str(game_pk))
        game_types.append(str(game.get("gameType") or "UNKNOWN"))

    if len(set(game_ids)) != len(game_ids):
        return {"verified": False, "error": "schedule contains duplicate gamePk values"}

    return {
        "verified": True,
        "error": None,
        "officialGameCount": total_games,
        "officialGameIds": game_ids,
        "officialGameTypes": sorted(set(game_types)),
        # Every official MLB event, including the All-Star Game, requires pulls.
        "pullsRequired": total_games > 0,
    }


def _floor_slot_et(dt_utc: datetime) -> datetime:
    local = dt_utc.astimezone(EASTERN)
    minute = (local.minute // 15) * 15
    return local.replace(minute=minute, second=0, microsecond=0)


def _pull_rows(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pulls: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sk = _s_value(item, "SK")
        parts = sk.split("#")
        pulled_at = parts[1] if len(parts) > 1 and parts[0] == "PULL" else _s_value(item, "pulled_at")
        dt = _parse_dt(pulled_at)
        if dt:
            pulls.append({"sk": sk, "dt": dt, "pulledAt": dt.isoformat()})
    pulls.sort(key=lambda row: row["dt"])
    return pulls


def build_pull_guard_proof(
    *,
    slate_date: str,
    pull_items: Optional[List[Dict[str, Any]]],
    aws_ok: bool,
    schedule_payload: Optional[Dict[str, Any]],
    schedule_error: Optional[str] = None,
    pull_data_error: Optional[str] = None,
    aws_error: Optional[str] = None,
    schedule_url: Optional[str] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return a deterministic guard report; ``ok`` means report generation only."""

    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_et = now_utc.astimezone(EASTERN)
    start_et = datetime.fromisoformat(f"{slate_date}T01:00:00").replace(tzinfo=EASTERN)
    schedule = validate_official_schedule(schedule_payload, slate_date) if schedule_error is None else {
        "verified": False,
        "error": schedule_error,
    }
    schedule_verified = bool(schedule.get("verified"))
    pulls_required = schedule.get("pullsRequired") if schedule_verified else None
    official_game_count = schedule.get("officialGameCount") if schedule_verified else None

    items = pull_items if isinstance(pull_items, list) else []
    pulls = _pull_rows(items)
    raw_count = len(items)
    latest = pulls[-1] if pulls else {"sk": "", "dt": None, "pulledAt": ""}
    latest_dt = latest.get("dt")

    expected_count = 0
    expected_end_et: Optional[datetime] = None
    expected_slots: List[datetime] = []
    if pulls_required and now_et >= start_et:
        # Preserve the existing guard horizon: judge through the latest stored
        # pull when present, otherwise through the current quarter-hour.
        ref_et = latest_dt.astimezone(EASTERN) if latest_dt else now_et
        if ref_et > now_et:
            ref_et = now_et
        minutes = int((ref_et - start_et).total_seconds() // 60)
        if minutes >= 0:
            expected_count = minutes // 15 + 1
            expected_end_et = start_et + timedelta(minutes=(expected_count - 1) * 15)
            expected_slots = [start_et + timedelta(minutes=i * 15) for i in range(expected_count)]

    pre_start = [row for row in pulls if row["dt"].astimezone(EASTERN) < start_et]
    clean_window: List[Dict[str, Any]] = []
    if expected_end_et is not None:
        clean_window = [
            row
            for row in pulls
            if start_et
            <= row["dt"].astimezone(EASTERN)
            <= expected_end_et + timedelta(minutes=14, seconds=59)
        ]

    actual_slots = sorted({_floor_slot_et(row["dt"]).isoformat() for row in clean_window})
    expected_slot_values = [slot.isoformat() for slot in expected_slots]
    missing_slots = [slot for slot in expected_slot_values if slot not in actual_slots]
    duplicate_or_extra = max(len(clean_window) - len(actual_slots), 0)
    excess_vs_expected = max(raw_count - expected_count, 0) if expected_count else raw_count

    # Failure ordering is intentional. An empty schedule can never hide loss of
    # AWS access or a schedule response that was not independently verified.
    if not aws_ok:
        clean_status = "FAIL_AWS_UNREACHABLE"
    elif not schedule_verified:
        clean_status = "FAIL_OFFICIAL_SCHEDULE_UNVERIFIED"
    elif pull_data_error:
        clean_status = "FAIL_PULL_DATA_UNVERIFIED"
    elif pulls_required is False:
        clean_status = "FAIL_UNEXPECTED_PULLS_ON_EMPTY_SLATE" if raw_count else "PASS_NO_GAMES_SCHEDULED"
    elif now_et < start_et:
        clean_status = "PRE_START"
    elif len(actual_slots) < expected_count:
        clean_status = "FAIL_MISSING_PULLS"
    elif len(clean_window) > expected_count:
        clean_status = "FAIL_EXTRA_OR_DUPLICATE_PULLS"
    elif len(actual_slots) == expected_count:
        clean_status = "PASS_CLEAN_EXPECTED_COUNT"
    else:
        clean_status = "UNKNOWN"

    if pulls_required is False or now_et < start_et or not aws_ok or not schedule_verified or pull_data_error:
        age: Optional[int] = None
        fresh: Optional[bool] = None
    else:
        age = int((now_utc - latest_dt).total_seconds() // 60) if latest_dt else 9999
        fresh = age <= 20

    count_passed = clean_status in {"PASS_NO_GAMES_SCHEDULED", "PASS_CLEAN_EXPECTED_COUNT", "PRE_START"}
    freshness_passed = pulls_required is False or clean_status == "PRE_START" or fresh is True
    guard_passed = bool(count_passed and freshness_passed)

    if clean_status == "PASS_NO_GAMES_SCHEDULED":
        watchdog_reason = "official_schedule_verified_no_games_no_pulls_required"
    elif clean_status == "FAIL_UNEXPECTED_PULLS_ON_EMPTY_SLATE":
        watchdog_reason = "unexpected_pulls_on_verified_empty_slate"
    elif not aws_ok:
        watchdog_reason = "aws_unreachable"
    elif not schedule_verified:
        watchdog_reason = "official_schedule_unverified"
    elif pull_data_error:
        watchdog_reason = "pull_data_unverified"
    elif fresh:
        watchdog_reason = "latest_pull_fresh"
    else:
        watchdog_reason = "latest_pull_stale_report_only"

    errors = [value for value in (aws_error, schedule.get("error"), pull_data_error) if value]
    return {
        "ok": True,
        "guardPassed": guard_passed,
        "proofType": "MLB_PULL_GUARD_READ_ONLY_PROOF",
        "createdAtUtc": now_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "createdAtEt": now_et.isoformat(),
        "slateDateEt": slate_date,
        "schedule": "0/15 * * * *",
        "sourceOfTruth": "AWS_EventBridge_primary_pull_only",
        "githubCreatesPulls": False,
        "awsReachable": aws_ok,
        "pullDataVerified": pull_data_error is None,
        "officialScheduleSource": SCHEDULE_SOURCE,
        "officialScheduleUrl": schedule_url or _schedule_url(slate_date),
        "officialScheduleDate": slate_date,
        "officialScheduleVerified": schedule_verified,
        "officialScheduleError": schedule.get("error"),
        "officialGameCount": official_game_count,
        "officialGameIds": schedule.get("officialGameIds") or [],
        "officialGameTypes": schedule.get("officialGameTypes") or [],
        "pullsRequired": pulls_required,
        "emptySlatePolicy": "only an exact-date verified official schedule with totalGames=0 suppresses pull expectations",
        "gameCoverageScope": "scheduled pull-slot presence; full per-game slate coverage remains enforced by MLB lock/audit invariants",
        "judgedAgainstCleanExpected": bool(aws_ok and schedule_verified and pull_data_error is None),
        "cleanExpectedStartEt": start_et.isoformat(),
        "cleanExpectedThroughEt": expected_end_et.isoformat() if expected_end_et else None,
        "cleanExpectedPullCount": expected_count,
        "cleanActualScheduledSlotCount": len(actual_slots),
        "cleanRawPullCountSinceStart": len(clean_window),
        "cleanCountStatus": clean_status,
        "missingCleanScheduledSlots": missing_slots[:20],
        "duplicateOrExtraPullsSinceStart": duplicate_or_extra,
        "rawPullCount": raw_count,
        "preStartPollutedPullCount": len(pre_start),
        "excessRawVsCleanExpected": excess_vs_expected,
        "latestRawPullAt": latest.get("pulledAt", ""),
        "latestRawPullSk": latest.get("sk", ""),
        "latestRawPullAgeMinutes": age,
        "fresh": fresh,
        "staleThresholdMinutes": 20,
        "watchdogReason": watchdog_reason,
        "invokedBackupPull": False,
        "recoveryAction": "none_read_only_no_github_lambda_invoke",
        "errorSummary": " | ".join(str(value).replace("\n", " ")[:300] for value in errors),
        "secretExposed": False,
    }


def _read_pull_items(path: Path) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict) or not isinstance(payload.get("Items"), list):
        return None, "DynamoDB query payload must contain an Items list"
    return payload["Items"], None


def _read_error(path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    try:
        value = path.read_text()[:300].strip().replace("\n", " ")
        return value or None
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slate-date", required=True)
    parser.add_argument("--pulls-json", required=True, type=Path)
    parser.add_argument("--aws-ok", required=True, choices=("true", "false"))
    parser.add_argument("--aws-error-file", type=Path)
    parser.add_argument("--output", action="append", required=True, type=Path)
    args = parser.parse_args()

    items, pull_data_error = _read_pull_items(args.pulls_json)
    schedule_payload, schedule_error, schedule_url = fetch_official_schedule(args.slate_date)
    proof = build_pull_guard_proof(
        slate_date=args.slate_date,
        pull_items=items,
        aws_ok=args.aws_ok == "true",
        schedule_payload=schedule_payload,
        schedule_error=schedule_error,
        pull_data_error=pull_data_error,
        aws_error=_read_error(args.aws_error_file),
        schedule_url=schedule_url,
    )
    for output in args.output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
