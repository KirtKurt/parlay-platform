from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.mlb_pull_guard_status import build_pull_guard_proof, validate_official_schedule


SLATE = "2026-07-13"
NOW = datetime(2026, 7, 13, 13, 8, tzinfo=timezone.utc)  # 09:08 ET


def official_schedule(*games):
    if not games:
        return {"totalGames": 0, "dates": []}
    return {
        "totalGames": len(games),
        "dates": [
            {
                "date": SLATE,
                "games": list(games),
            }
        ],
    }


def official_game(game_pk=1, game_type="R"):
    return {
        "gamePk": game_pk,
        "gameType": game_type,
        "gameDate": "2026-07-13T23:10:00Z",
    }


def pull_item(pulled_at: datetime):
    value = pulled_at.astimezone(timezone.utc).isoformat()
    return {"SK": {"S": f"PULL#{value}#test_pull"}, "pulled_at": {"S": value}}


def canonical_pull_item(pulled_at: datetime):
    slot = pulled_at.astimezone(timezone.utc).replace(
        minute=(pulled_at.minute // 15) * 15,
        second=0,
        microsecond=0,
    )
    value = slot.isoformat()
    return {"SK": {"S": f"PULL#SLOT#{value}"}, "pulled_at": {"S": pulled_at.isoformat()}}


def proof(*, schedule, pulls=None, aws_ok=True, schedule_error=None, pull_data_error=None):
    return build_pull_guard_proof(
        slate_date=SLATE,
        pull_items=[] if pulls is None else pulls,
        aws_ok=aws_ok,
        schedule_payload=schedule,
        schedule_error=schedule_error,
        pull_data_error=pull_data_error,
        now_utc=NOW,
    )


def test_verified_empty_schedule_passes_without_pull_expectations():
    result = proof(schedule=official_schedule())

    assert result["ok"] is True  # report generation is separate from health
    assert result["guardPassed"] is True
    assert result["officialScheduleVerified"] is True
    assert result["officialGameCount"] == 0
    assert result["pullsRequired"] is False
    assert result["cleanCountStatus"] == "PASS_NO_GAMES_SCHEDULED"
    assert result["cleanExpectedPullCount"] == 0
    assert result["missingCleanScheduledSlots"] == []
    assert result["fresh"] is None
    assert result["latestRawPullAgeMinutes"] is None


def test_unexpected_pull_on_verified_empty_schedule_fails():
    result = proof(schedule=official_schedule(), pulls=[pull_item(NOW)])

    assert result["guardPassed"] is False
    assert result["cleanCountStatus"] == "FAIL_UNEXPECTED_PULLS_ON_EMPTY_SLATE"
    assert result["rawPullCount"] == 1
    assert result["pullsRequired"] is False
    assert result["fresh"] is None


def test_schedule_failure_never_turns_zero_rows_into_empty_slate_pass():
    result = proof(schedule=None, schedule_error="official schedule timed out")

    assert result["guardPassed"] is False
    assert result["officialScheduleVerified"] is False
    assert result["pullsRequired"] is None
    assert result["cleanCountStatus"] == "FAIL_OFFICIAL_SCHEDULE_UNVERIFIED"


def test_aws_failure_has_priority_over_schedule_failure():
    result = proof(
        schedule=None,
        aws_ok=False,
        schedule_error="official schedule timed out",
    )

    assert result["guardPassed"] is False
    assert result["cleanCountStatus"] == "FAIL_AWS_UNREACHABLE"


def test_malformed_or_wrong_date_schedule_fails_closed():
    malformed = validate_official_schedule({"totalGames": 0}, SLATE)
    wrong_date = validate_official_schedule(
        {"totalGames": 1, "dates": [{"date": "2026-07-14", "games": [official_game()]}]},
        SLATE,
    )

    assert malformed["verified"] is False
    assert wrong_date["verified"] is False


def test_regular_game_with_zero_pulls_still_fails_missing_pulls():
    result = proof(schedule=official_schedule(official_game()))

    assert result["officialGameCount"] == 1
    assert result["pullsRequired"] is True
    assert result["guardPassed"] is False
    assert result["cleanCountStatus"] == "FAIL_MISSING_PULLS"
    assert result["cleanExpectedPullCount"] == 33
    assert result["missingCleanScheduledSlots"]
    assert result["fresh"] is False


def test_all_star_game_also_requires_pulls():
    result = proof(schedule=official_schedule(official_game(game_type="A")))

    assert result["officialGameTypes"] == ["A"]
    assert result["pullsRequired"] is True
    assert result["guardPassed"] is False
    assert result["cleanCountStatus"] == "FAIL_MISSING_PULLS"


def test_complete_quarter_hour_slots_pass_for_scheduled_game():
    first = datetime(2026, 7, 13, 5, 0, 10, tzinfo=timezone.utc)  # 01:00 ET
    pulls = [pull_item(first + timedelta(minutes=15 * index)) for index in range(33)]

    result = proof(schedule=official_schedule(official_game()), pulls=pulls)

    assert result["cleanExpectedPullCount"] == 33
    assert result["cleanActualScheduledSlotCount"] == 33
    assert result["missingCleanScheduledSlots"] == []
    assert result["cleanCountStatus"] == "PASS_CLEAN_EXPECTED_COUNT"
    assert result["fresh"] is True
    assert result["guardPassed"] is True


def test_legacy_raw_duplicates_are_diagnostic_when_unique_slots_are_complete():
    first = datetime(2026, 7, 13, 5, 0, 10, tzinfo=timezone.utc)
    pulls = [pull_item(first + timedelta(minutes=15 * index)) for index in range(33)]
    pulls.append(pull_item(first + timedelta(seconds=20)))

    result = proof(schedule=official_schedule(official_game()), pulls=pulls)

    assert result["guardPassed"] is True
    assert result["cleanCountStatus"] == "PASS_CANONICALIZED_EXPECTED_SLOTS"
    assert result["duplicateOrExtraPullsSinceStart"] == 1
    assert result["scoringCanonicalizationRequired"] is True


def test_duplicate_write_in_a_canonical_slot_fails():
    first = datetime(2026, 7, 13, 5, 0, 10, tzinfo=timezone.utc)
    pulls = [canonical_pull_item(first + timedelta(minutes=15 * index)) for index in range(33)]
    pulls.append(pull_item(first + timedelta(seconds=20)))

    result = proof(schedule=official_schedule(official_game()), pulls=pulls)

    assert result["guardPassed"] is False
    assert result["cleanCountStatus"] == "FAIL_DUPLICATE_AFTER_CANONICAL_WRITER"
    assert result["duplicateAfterCanonicalWriterCount"] == 1


def test_partial_slot_history_is_not_exempted_by_schedule_awareness():
    result = proof(
        schedule=official_schedule(official_game(1), official_game(2)),
        pulls=[pull_item(datetime(2026, 7, 13, 5, 0, 10, tzinfo=timezone.utc))],
    )

    assert result["officialGameCount"] == 2
    assert result["pullsRequired"] is True
    assert result["cleanCountStatus"] == "PASS_CLEAN_EXPECTED_COUNT"
    # The single stored slot is stale, so overall guard health still fails.
    assert result["fresh"] is False
    assert result["guardPassed"] is False


def test_unreadable_pull_query_fails_after_verified_schedule():
    result = proof(
        schedule=official_schedule(),
        pull_data_error="DynamoDB output was malformed",
    )

    assert result["guardPassed"] is False
    assert result["cleanCountStatus"] == "FAIL_PULL_DATA_UNVERIFIED"


def main() -> int:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"MLB pull guard policy verified: {len(tests)} deterministic cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
