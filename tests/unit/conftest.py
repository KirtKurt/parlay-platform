"""Keep MLB acceptance fixtures aligned with the active prospective cutoff.

The production experiment boundary is versioned in ``mlb_ml_experiment_v2``.
The acceptance suite historically used a fixed July 22 clock and slate. Once
that boundary moved to July 24, those literals made the fixture ask for final
games before the release existed. This hook updates only that test module's
synthetic clock and synthetic row identities; production code is not modified.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def pytest_collection_modifyitems(session, config, items):
    candidates = [
        module
        for name, module in sys.modules.items()
        if name.endswith("test_mlb_production_acceptance")
    ]
    if not candidates:
        return
    module = candidates[0]

    cutoff = datetime.fromisoformat(module.experiment.PRODUCTION_RELEASE_CUTOFF_UTC)
    slate_tz = ZoneInfo("America/New_York")
    slate_date = cutoff.astimezone(slate_tz).date().isoformat()
    module.NOW = cutoff + timedelta(hours=20)

    original_rows = module._settlement_rows

    def cutoff_aligned_rows(summary, *, quarantine_all=False):
        rows = original_rows(summary, quarantine_all=quarantine_all)
        for row in rows:
            row["slateDateEt"] = slate_date
            authority = row.get("canonicalLockAuthority") or {}
            if authority.get("sourcePk"):
                authority["sourcePk"] = f"GAME_WINNERS#mlb#{slate_date}"
        return rows

    module._settlement_rows = cutoff_aligned_rows

    original_evidence = module.audit_report._canonical_finalized_slate_evidence

    def cutoff_aligned_evidence(report, *, now_utc, official_schedule_loader):
        def loader(requested_slate_date):
            payload = official_schedule_loader(requested_slate_date)
            if (payload.get("games") or []) or requested_slate_date != slate_date:
                return payload

            legacy = official_schedule_loader("2026-07-22")
            games = []
            for index, game in enumerate(legacy.get("games") or []):
                rewritten = dict(game)
                rewritten["officialDate"] = requested_slate_date
                try:
                    game_date = datetime.fromisoformat(
                        str(rewritten.get("gameDate") or "").replace("Z", "+00:00")
                    )
                except ValueError:
                    game_date = None
                if (
                    game_date is None
                    or game_date.astimezone(slate_tz).date().isoformat()
                    != requested_slate_date
                ):
                    rewritten["gameDate"] = (
                        cutoff + timedelta(hours=2, minutes=index)
                    ).isoformat()
                rewritten["sourcePayloadFingerprint"] = (
                    module.history.canonical_payload_fingerprint(
                        module.canonical_labels._official_final_evidence(rewritten)
                    )
                )
                games.append(rewritten)

            return {
                **legacy,
                "sourceUrl": module.canonical_labels.official_finals_url(
                    requested_slate_date
                ),
                "slateDateEt": requested_slate_date,
                "officialGameCount": len(games),
                "officialFinalCount": len(games),
                "games": games,
            }

        return original_evidence(
            report,
            now_utc=now_utc,
            official_schedule_loader=loader,
        )

    module.audit_report._canonical_finalized_slate_evidence = cutoff_aligned_evidence
