from __future__ import annotations

from hello_world import mlb_odds_v8_shadow_collector as collector


def _events(count=15):
    return [
        {
            "id": f"event-{index:02d}",
            "commence_time": f"2026-07-27T{12 + index // 4:02d}:{(index % 4) * 15:02d}:00Z",
        }
        for index in range(count)
    ]


def test_rotating_window_covers_complete_slate_with_bounded_windows():
    events = _events()
    covered = set()
    metadata = []
    for slot in range(8):
        selected, rotation = collector._rotating_window(events, 2, slot)
        assert len(selected) == 2
        assert rotation["windowSize"] == 2
        assert rotation["slateEventCount"] == 15
        assert rotation["fullSlateCyclesRequired"] == 8
        ids = {row["id"] for row in selected}
        assert ids == set(rotation["selectedEventIds"])
        covered.update(ids)
        metadata.append(rotation)
    assert covered == {row["id"] for row in events}
    assert len({row["rotationOffset"] for row in metadata}) == 8


def test_rotating_window_is_deterministic_and_wraps():
    events = _events(5)
    first, first_meta = collector._rotating_window(events, 2, 2)
    second, second_meta = collector._rotating_window(list(reversed(events)), 2, 2)
    assert [row["id"] for row in first] == [row["id"] for row in second]
    assert first_meta == second_meta
    assert first_meta["selectedEventIds"] == ["event-04", "event-00"]


def test_explicit_rotation_slot_validation():
    assert collector._rotation_slot(historical_at=None, explicit_slot="7") == 7
    try:
        collector._rotation_slot(historical_at=None, explicit_slot="bad")
    except ValueError as exc:
        assert "rotationSlot" in str(exc)
    else:
        raise AssertionError("invalid rotation slot did not fail")
