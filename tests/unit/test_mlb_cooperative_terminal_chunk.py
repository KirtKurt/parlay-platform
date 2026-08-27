from __future__ import annotations

import copy
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_prospective_row_repair as repair


SLATE = "2026-08-05"
TODAY = "2026-08-06"


def _game(index: int) -> dict:
    return {
        "game_id": f"provider:game-{index}",
        "id": f"provider:game-{index}",
        "home_team": f"Home {index}",
        "away_team": f"Away {index}",
        "commence_time": f"2026-08-05T{17 + index:02d}:00:00+00:00",
    }


class BudgetContext:
    def __init__(self, *remaining_millis: int):
        assert remaining_millis
        self.values = list(remaining_millis)
        self.index = 0

    def get_remaining_time_in_millis(self) -> int:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


class ChunkModule:
    def __init__(self, game_count: int = 2):
        self.games = [_game(index) for index in range(game_count)]
        self.outcomes = {}
        self.terminal_writes = []
        self.original_calls = 0

    def _today_et(self):
        return TODAY

    def _now_utc(self):
        return datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)

    def _pulls_for_date(self, slate):
        assert slate == SLATE
        return [
            {
                "pull_id": "pull-1",
                "pulled_at": "2026-08-05T16:00:00+00:00",
            }
        ]

    def _latest_games_for_date(self, slate, pulls):
        assert slate == SLATE
        assert pulls
        return copy.deepcopy(self.games)

    def run_lock(self, slate_date=None, force=False, *, scheduled=False):
        self.original_calls += 1
        raise AssertionError(
            "the cooperative chunk must not call the whole-slate run_lock"
        )


class ChunkPatch:
    @staticmethod
    def game_identity(game):
        return game["game_id"]

    @staticmethod
    def _pull_at(module, pull):
        del module
        return datetime.fromisoformat(pull["pulled_at"])

    @staticmethod
    def _start(module, game):
        del module
        return datetime.fromisoformat(game["commence_time"])

    @staticmethod
    def _post_window_manifest_fingerprint(module, manifest):
        del module
        return "manifest:" + ",".join(
            game["game_id"] for game in manifest
        )

    @staticmethod
    def _get_stage(module, slate, game):
        del module, slate, game
        return None

    @staticmethod
    def _get_lock_outcome(module, slate, game):
        assert slate == SLATE
        return copy.deepcopy(module.outcomes.get(game["game_id"]))

    @staticmethod
    def _scoring_pulls(module, pulls, game):
        del module, pulls, game
        return []

    @staticmethod
    def _last_prelock_candidate(module, slate, game, scoring):
        del module, slate, game, scoring
        return (
            None,
            None,
            [],
            [
                "no_persisted_user_visible_platform_prelock_prediction_"
                "at_or_before_cutoff"
            ],
        )

    @staticmethod
    def _is_no_prediction_candidate_failure(errors):
        return errors == [
            "no_persisted_user_visible_platform_prelock_prediction_"
            "at_or_before_cutoff"
        ]

    @staticmethod
    def _select_provider_manifest_authority(
        module, pulls, slate, manifest
    ):
        del module
        assert pulls
        assert slate == SLATE
        return {
            "fingerprint": "verified-manifest",
            "gameCount": len(manifest),
            "canonicalGameIdentities": [
                game["game_id"] for game in manifest
            ],
        }

    @staticmethod
    def _put_no_prediction_outcome(
        module,
        slate,
        game,
        now,
        reasons,
        authority,
    ):
        assert slate == SLATE
        assert now >= datetime.fromisoformat(game["commence_time"])
        assert authority["fingerprint"] == "verified-manifest"
        identity = game["game_id"]
        if identity not in module.outcomes:
            module.terminal_writes.append(identity)
            module.outcomes[identity] = {
                "lock_status": "LOCKED_NO_PREDICTION_DATA",
                "lock_outcome_recorded": True,
                "locked_prediction": False,
                "training_eligible": False,
                "reasons": list(reasons),
            }
        return copy.deepcopy(module.outcomes[identity])


def _install(module, patch=ChunkPatch):
    repair.install_prospective_row_repair(module, patch)
    assert callable(module.run_cooperative_terminal_chunk)
    return module


def test_chunk_processes_at_most_one_terminal_per_owner_then_zero_work_completes():
    module = _install(ChunkModule(game_count=2))

    first = module.run_cooperative_terminal_chunk(
        slate_date=SLATE,
        checkpoint=None,
        context=BudgetContext(900_000),
    )
    assert first["ok"] is True
    assert first["complete"] is False
    assert first["terminalWrittenThisInvocation"] is True
    assert first["checkpoint"]["nextGameIndex"] == 1
    assert first["checkpoint"]["terminalCount"] == 1
    assert module.terminal_writes == ["provider:game-0"]
    assert module.original_calls == 0

    second = module.run_cooperative_terminal_chunk(
        slate_date=SLATE,
        checkpoint=first["checkpoint"],
        context=BudgetContext(900_000),
    )
    assert second["ok"] is True
    assert second["complete"] is False
    assert second["checkpoint"]["nextGameIndex"] == 2
    assert second["checkpoint"]["terminalCount"] == 2
    assert module.terminal_writes == [
        "provider:game-0",
        "provider:game-1",
    ]
    assert module.original_calls == 0

    final = module.run_cooperative_terminal_chunk(
        slate_date=SLATE,
        checkpoint=second["checkpoint"],
        context=BudgetContext(900_000),
    )
    assert final["ok"] is True
    assert final["complete"] is True
    assert module.terminal_writes == [
        "provider:game-0",
        "provider:game-1",
    ]
    response = final["terminalReplayResponse"]
    assert response["lockStatusComplete"] is True
    assert response["missedGameCount"] == 0
    assert response["postStartPredictionCreationAllowed"] is False
    assert response["immutablePredictionRewriteAllowed"] is False
    assert response["productionAuthorityChanged"] is False
    reconciliation = response["missedLockTerminalReconciliation"]
    assert reconciliation["remainingMissedCount"] == 0
    assert reconciliation["unresolved"] == []
    assert reconciliation["progressAfter"]["dueMissingCount"] == 0
    assert reconciliation["progressAfter"]["missedCount"] == 0
    assert module.original_calls == 0


def test_decreasing_context_defers_before_terminal_write_and_keeps_cursor():
    module = _install(ChunkModule(game_count=1))

    result = module.run_cooperative_terminal_chunk(
        slate_date=SLATE,
        checkpoint=None,
        context=BudgetContext(
            900_000,
            250_000,
            250_000,
            119_000,
        ),
    )

    assert result["ok"] is True
    assert result["complete"] is False
    assert result["deferred"] is True
    assert result["stage"] == "WRITE_BUDGET"
    assert result["remainingSeconds"] == 119
    assert result["checkpoint"]["nextGameIndex"] == 0
    assert result["checkpoint"]["terminalCount"] == 0
    assert result["checkpoint"]["lastAttempt"]["status"] == (
        "DEFERRED_INSUFFICIENT_REMAINING_TIME"
    )
    assert module.terminal_writes == []
    assert module.outcomes == {}
    assert module.original_calls == 0


def test_candidate_integrity_problem_stays_unresolved_and_writes_nothing():
    class CandidatePatch(ChunkPatch):
        @staticmethod
        def _last_prelock_candidate(module, slate, game, scoring):
            del module, slate, game, scoring
            return (
                {"predictedWinner": "Home 0"},
                {"persisted": True},
                [],
                [],
            )

    module = _install(ChunkModule(game_count=1), CandidatePatch)

    result = module.run_cooperative_terminal_chunk(
        slate_date=SLATE,
        checkpoint=None,
        context=BudgetContext(900_000),
    )

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["stage"] == "PROVE_PRELOCK_ABSENCE"
    assert result["errorCode"] == "PRELOCK_CANDIDATE_REQUIRES_REVIEW"
    assert result["checkpoint"]["nextGameIndex"] == 0
    assert result["checkpoint"]["terminalCount"] == 0
    assert result["checkpoint"]["lastAttempt"]["status"] == "FAILED_CLOSED"
    assert module.terminal_writes == []
    assert module.outcomes == {}
    assert module.original_calls == 0


def test_existing_immutable_canonical_is_counted_without_any_terminal_write():
    class CanonicalPatch(ChunkPatch):
        @staticmethod
        def _get_stage(module, slate, game):
            del module
            assert slate == SLATE
            return {
                "data": {
                    "row": {
                        "gameIdentity": game["game_id"],
                        "slateDateEt": SLATE,
                    }
                }
            }

        @staticmethod
        def _validate_stage(
            module,
            stage,
            slate,
            game,
            manifest,
            scoring,
        ):
            del module, stage, game, manifest, scoring
            assert slate == SLATE
            return []

        @staticmethod
        def _canonical_readback(module, row):
            del module
            assert row["slateDateEt"] == SLATE
            return {
                "ok": True,
                "storageClass": "LOCKED_IMMUTABLE",
                "writeOnce": True,
                "selectionLockVerified": True,
            }

        @staticmethod
        def _put_no_prediction_outcome(*args, **kwargs):
            del args, kwargs
            raise AssertionError("canonical games must never write a terminal")

    module = _install(ChunkModule(game_count=1), CanonicalPatch)

    result = module.run_cooperative_terminal_chunk(
        slate_date=SLATE,
        checkpoint=None,
        context=BudgetContext(900_000),
    )

    assert result["ok"] is True
    assert result["complete"] is False
    assert result["terminalWrittenThisInvocation"] is False
    assert result["checkpoint"]["canonicalCount"] == 1
    assert result["checkpoint"]["noPredictionDataCount"] == 0
    assert result["checkpoint"]["processedGames"] == [
        {
            "gameIdentity": "provider:game-0",
            "terminalState": "LOCKED_CANONICAL",
            "reconciled": False,
        }
    ]
    assert module.terminal_writes == []
    assert module.original_calls == 0
