from __future__ import annotations

from types import SimpleNamespace

import mlb_daily_lock_status_route_patch as route_patch


def test_status_summary_wrapper_preserves_diagnostic_history_limit_keyword():
    calls = []

    def original(module, slate_date, game, limit=20):
        calls.append((module, slate_date, game, limit))
        return {"ok": True, "limit": limit}

    patch = SimpleNamespace(_diagnostic_history=original)
    route_patch._install_diagnostic_wrapper(patch)

    module = object()
    game = {"game_id": "provider:test"}
    result = patch._diagnostic_history(
        module,
        "2026-08-25",
        game,
        limit=7,
    )

    assert result == {"ok": True, "limit": 7}
    assert calls == [(module, "2026-08-25", game, 7)]
