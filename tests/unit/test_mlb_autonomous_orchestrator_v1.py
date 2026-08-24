import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))

import mlb_autonomous_orchestrator_v1 as orchestrator


SCHEDULE = {
    "source": "MLB Stats API exact-date schedule",
    "sourceUrl": "https://statsapi.mlb.com/api/v1/schedule",
    "verified": True,
    "officialGameCount": 3,
    "officialGameIds": ["1", "2", "3"],
    "games": [
        {"official_game_pk": "1", "official_commence_time": "2026-08-24T17:00:00+00:00"},
        {"official_game_pk": "2", "official_commence_time": "2026-08-24T18:10:00+00:00"},
        {"official_game_pk": "3", "official_commence_time": "2026-08-24T19:00:00+00:00"},
    ],
}


class FakeLambda:
    def __init__(self):
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "StatusCode": 200,
            "ExecutedVersion": "$LATEST",
            "Payload": io.BytesIO(json.dumps({"ok": True}).encode()),
        }


def test_full_card_is_forced_by_second_game_t45(monkeypatch):
    fake = FakeLambda()
    monkeypatch.setattr(orchestrator.official_schedule, "fetch_exact_date_schedule", lambda _: SCHEDULE)
    monkeypatch.setattr(orchestrator.boto3, "client", lambda *args, **kwargs: fake)
    monkeypatch.setenv("MLB_AUDITED_PULL_FUNCTION", "pull-fn")
    monkeypatch.setenv("MLB_DAILY_PICK_LOCK_FUNCTION", "lock-fn")

    result = orchestrator.lambda_handler(
        {"slateDate": "2026-08-24", "nowUtc": "2026-08-24T17:30:00+00:00"},
        None,
    )
    assert result["phase"] == "FULL_CARD_DUE"
    assert result["action"] == "COLLECT_THEN_PREDICT"
    assert len(fake.calls) == 2
    lock_payload = json.loads(fake.calls[1]["Payload"])
    assert lock_payload["forceFullSlate"] is True
    assert lock_payload["fullCardDeadlineUtc"] == "2026-08-24T17:25:00+00:00"
    assert lock_payload["allGamesNoPass"] is True


def test_no_post_start_recompute(monkeypatch):
    fake = FakeLambda()
    monkeypatch.setattr(orchestrator.official_schedule, "fetch_exact_date_schedule", lambda _: SCHEDULE)
    monkeypatch.setattr(orchestrator.boto3, "client", lambda *args, **kwargs: fake)
    result = orchestrator.lambda_handler(
        {"slateDate": "2026-08-24", "nowUtc": "2026-08-24T18:15:00+00:00"},
        None,
    )
    assert result["phase"] == "NO_POST_START_RECOMPUTE"
    assert result["action"] == "NO_MUTATION"
    assert fake.calls == []
