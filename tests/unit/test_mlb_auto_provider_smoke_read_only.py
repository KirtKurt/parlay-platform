from __future__ import annotations

from datetime import datetime, timezone

import orchestrator_v2 as subject
import pytest


def _event() -> dict:
    return {
        "mode": subject.DEPLOYMENT_PROVIDER_SMOKE_MODE,
        "force_publish": False,
    }


def _schedule(slate: str, start: str) -> dict:
    return {
        "date": slate,
        "games": [
            {
                "gamePk": "provider-smoke-game",
                "gameDate": start,
                "home": {"name": "Home Team"},
                "away": {"name": "Away Team"},
            }
        ],
    }


def _packet(slate: str) -> dict:
    return {
        "slateDateEt": slate,
        "sourceStatus": {
            "mlbStatsApi": {"ok": True},
            "theOddsApi": {"ok": True},
            "bigBallsDataPro": {"ok": True},
        },
        "threeSourceCoverageComplete": True,
    }


def test_provider_smoke_is_read_only_before_cutoff_even_if_card_exists(
    monkeypatch,
) -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    slate = now.astimezone(subject.base.ET).date().isoformat()
    schedule = _schedule(slate, "2026-09-05T20:00:00Z")
    calls = []

    monkeypatch.setattr(subject.base, "_now", lambda: now)
    monkeypatch.setattr(subject.base, "_official_schedule", lambda value: schedule)
    monkeypatch.setattr(
        subject.base,
        "_deadline",
        lambda value: {"publishDeadlineUtc": "2026-09-05T19:00:00Z"},
    )
    monkeypatch.setattr(
        subject.base,
        "_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider smoke must not settle or inspect a card")
        ),
    )
    monkeypatch.setattr(
        subject.production,
        "_assemble_with_full_bbd",
        lambda value, *, expanded: calls.append((value, expanded))
        or _packet(value),
    )
    monkeypatch.setattr(
        subject.production,
        "lambda_handler",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider smoke must not enter normal runtime")
        ),
    )

    result = subject.lambda_handler(_event(), None)

    assert calls == [(slate, False)]
    assert result["slateDateEt"] == slate
    assert result["providerProbeUsedFutureSlate"] is False
    assert result["readOnlyProviderProbe"] is True
    assert result["writeGuardArmed"] is True
    assert result["persistenceAttempted"] is False


def test_provider_smoke_uses_future_pregame_slate_after_cutoff(monkeypatch) -> None:
    now = datetime(2026, 9, 5, 23, 0, tzinfo=timezone.utc)
    current_slate = now.astimezone(subject.base.ET).date().isoformat()
    future_slate = "2026-09-06"
    current_schedule = _schedule(current_slate, "2026-09-05T18:00:00Z")

    monkeypatch.setattr(subject.base, "_now", lambda: now)
    monkeypatch.setattr(
        subject.base,
        "_official_schedule",
        lambda value: current_schedule,
    )
    monkeypatch.setattr(
        subject.base,
        "_deadline",
        lambda value: {"publishDeadlineUtc": "2026-09-05T17:00:00Z"},
    )
    monkeypatch.setattr(
        subject,
        "_next_future_provider_probe",
        lambda slate, value: {
            "slate": future_slate,
            "schedule": _schedule(future_slate, "2026-09-06T17:00:00Z"),
            "deadline": {"publishDeadlineUtc": "2026-09-06T16:00:00Z"},
            "deadlineDt": datetime(
                2026, 9, 6, 16, 0, tzinfo=timezone.utc
            ),
        },
    )
    monkeypatch.setattr(
        subject.production,
        "_assemble_with_full_bbd",
        lambda slate, *, expanded: _packet(slate),
    )

    result = subject.lambda_handler(_event(), None)

    assert result["requestedSlateDateEt"] == current_slate
    assert result["slateDateEt"] == future_slate
    assert result["providerProbeUsedFutureSlate"] is True
    assert result["postStartPredictionCreationAllowed"] is False
    assert result["postStartOddsFabricationAllowed"] is False


def test_provider_smoke_write_guard_fails_closed_and_restores_put(monkeypatch) -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    slate = now.astimezone(subject.base.ET).date().isoformat()
    schedule = _schedule(slate, "2026-09-05T20:00:00Z")
    original_put = subject.base._put

    monkeypatch.setattr(subject.base, "_now", lambda: now)
    monkeypatch.setattr(subject.base, "_official_schedule", lambda value: schedule)
    monkeypatch.setattr(
        subject.base,
        "_deadline",
        lambda value: {"publishDeadlineUtc": "2026-09-05T19:00:00Z"},
    )

    def assemble(value, *, expanded):
        subject.base._put("PACKET#2026-09-05", "DISCOVERY#probe", {})
        return _packet(value)

    monkeypatch.setattr(
        subject.production,
        "_assemble_with_full_bbd",
        assemble,
    )

    with pytest.raises(
        RuntimeError,
        match="DEPLOYMENT_PROVIDER_SMOKE_WRITE_FORBIDDEN:PACKET#",
    ):
        subject.lambda_handler(_event(), None)

    assert subject.base._put is original_put


def test_provider_smoke_event_is_closed_not_configurable(monkeypatch) -> None:
    invoked = []
    monkeypatch.setattr(
        subject.production,
        "_assemble_with_full_bbd",
        lambda *_args, **_kwargs: invoked.append(True),
    )
    event = _event()
    event["slate_date"] = "2026-09-06"

    with pytest.raises(
        RuntimeError,
        match="^DEPLOYMENT_PROVIDER_SMOKE_EVENT_REJECTED$",
    ):
        subject.lambda_handler(event, None)

    assert invoked == []
