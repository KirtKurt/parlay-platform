from __future__ import annotations

import sys
from pathlib import Path


UNIT_DIR = Path(__file__).resolve().parent
if str(UNIT_DIR) not in sys.path:
    sys.path.insert(0, str(UNIT_DIR))

import test_mlb_prospective_status_lifecycle_repair as lifecycle
import mlb_prospective_row_repair as repair
import mlb_terminal_identity_resolution_patch as identity_resolution


identity_resolution.apply(repair)


def test_forced_scheduled_reconciliation_writes_only_no_prediction_terminal():
    module = lifecycle.FakeModule()
    repair.install_prospective_row_repair(module, lifecycle.FakePatch)

    result = module.run_lock(
        slate_date=lifecycle.SLATE,
        force=True,
        scheduled=True,
    )

    assert module.original_calls == 1
    assert module.outcome is not None
    assert module.outcome["lock_status"] == "LOCKED_NO_PREDICTION_DATA"
    assert module.outcome["locked_prediction"] is False
    assert module.outcome["training_eligible"] is False
    assert result["ok"] is True
    assert result["reason"] == "PROVEN_NO_PREDICTION_TERMINALS_RECONCILED"
    assert result["postStartPredictionCreationAllowed"] is False
    assert result["durableNoPredictionTerminalReconciledCount"] == 1
    assert result["canonicalPredictionComplete"] is False
    assert result["lockStatusComplete"] is True
    reconciliation = result["missedLockTerminalReconciliation"]
    assert reconciliation["identityResolutionVersion"] == identity_resolution.VERSION
    assert reconciliation["identityCrosswalkCount"] == 1


def test_manual_force_probe_remains_fail_closed():
    module = lifecycle.FakeModule()
    repair.install_prospective_row_repair(module, lifecycle.FakePatch)

    result = module.run_lock(
        slate_date=lifecycle.SLATE,
        force=True,
        scheduled=False,
    )

    assert module.outcome is None
    assert result["ok"] is False
    assert result["reason"] == "MISSED_PER_GAME_LOCK_NOT_BACKFILLED"
    assert result["failClosed"] is True


def test_forced_replay_crosswalks_official_status_to_provider_manifest_game():
    official_game_pk = "822865"
    manifest_game = {
        "game_id": "provider:odds-event-822865",
        "providerEventId": "odds-event-822865",
        "officialGamePk": official_game_pk,
        "commence_time": "2026-08-05T18:00:00+00:00",
    }

    class CrossIdentityModule(lifecycle.FakeModule):
        def _latest_games_for_date(self, slate, pulls):
            assert slate == lifecycle.SLATE
            assert pulls
            return [dict(manifest_game)]

    class CrossIdentityPatch(lifecycle.FakePatch):
        @staticmethod
        def _progress(module, slate, pulls, manifest, now, *, ensure_canonical):
            if module.outcome is None:
                return {
                    "games": [
                        {
                            "gameIdentity": f"official:{official_game_pk}",
                            "officialGamePk": official_game_pk,
                            "state": "MISSED_NOT_BACKFILLED",
                        }
                    ],
                    "missedCount": 1,
                    "lockOutcomeCount": 0,
                    "canonicalCount": 0,
                    "noPredictionDataCount": 0,
                }
            return {
                "games": [
                    {
                        "gameIdentity": f"official:{official_game_pk}",
                        "officialGamePk": official_game_pk,
                        "state": "LOCKED_NO_PREDICTION_DATA",
                    }
                ],
                "missedCount": 0,
                "lockOutcomeCount": 1,
                "canonicalCount": 0,
                "noPredictionDataCount": 1,
            }

    module = CrossIdentityModule()
    repair.install_prospective_row_repair(module, CrossIdentityPatch)

    result = module.run_lock(
        slate_date=lifecycle.SLATE,
        force=True,
        scheduled=True,
    )

    assert module.outcome is not None
    assert module.outcome["lock_status"] == "LOCKED_NO_PREDICTION_DATA"
    assert result["ok"] is True
    assert result["lockStatusComplete"] is True
    reconciliation = result["missedLockTerminalReconciliation"]
    assert reconciliation["identityResolutionVersion"] == identity_resolution.VERSION
    assert reconciliation["identityCrosswalkCount"] == 1
    assert reconciliation["remainingMissedCount"] == 0


def test_ambiguous_official_identity_remains_fail_closed():
    official_game_pk = "822865"

    class AmbiguousModule(lifecycle.FakeModule):
        def _latest_games_for_date(self, slate, pulls):
            return [
                {
                    "game_id": "provider:event-a",
                    "officialGamePk": official_game_pk,
                    "commence_time": "2026-08-05T18:00:00+00:00",
                },
                {
                    "game_id": "provider:event-b",
                    "officialGamePk": official_game_pk,
                    "commence_time": "2026-08-05T19:00:00+00:00",
                },
            ]

    class AmbiguousPatch(lifecycle.FakePatch):
        @staticmethod
        def _progress(module, slate, pulls, manifest, now, *, ensure_canonical):
            return {
                "games": [
                    {
                        "gameIdentity": f"official:{official_game_pk}",
                        "officialGamePk": official_game_pk,
                        "state": "MISSED_NOT_BACKFILLED",
                    }
                ],
                "missedCount": 1,
                "lockOutcomeCount": 0,
                "canonicalCount": 0,
                "noPredictionDataCount": 0,
            }

    module = AmbiguousModule()
    repair.install_prospective_row_repair(module, AmbiguousPatch)

    result = module.run_lock(
        slate_date=lifecycle.SLATE,
        force=True,
        scheduled=True,
    )

    assert module.outcome is None
    assert result["ok"] is False
    assert result["reason"] == "MISSED_PER_GAME_LOCK_NOT_BACKFILLED"
    reconciliation = result["missedLockTerminalReconciliation"]
    assert reconciliation["ok"] is False
    assert reconciliation["reason"] == "TERMINAL_IDENTITY_RESOLUTION_FAILED_CLOSED"
    assert reconciliation["unresolved"] == [
        {
            "gameIdentity": f"official:{official_game_pk}",
            "reason": "AMBIGUOUS_MANIFEST_GAME_IDENTITY",
            "candidateCount": 2,
        }
    ]
