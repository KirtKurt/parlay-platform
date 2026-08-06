from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_prospective_row_repair as prospective


SLATE = "2026-08-05"
GAME = {
    "game_id": "provider:missed-game",
    "id": "provider:missed-game",
    "commence_time": "2026-08-05T18:00:00+00:00",
}


class FakeModule:
    def __init__(self):
        self.outcome = None
        self.original_calls = 0

    def _today_et(self):
        return SLATE

    def _now_utc(self):
        return datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)

    def _pulls_for_date(self, slate):
        assert slate == SLATE
        return [
            {
                "pull_id": "pull-1",
                "pulled_at": "2026-08-05T17:00:00+00:00",
            }
        ]

    def _latest_games_for_date(self, slate, pulls):
        assert slate == SLATE
        assert pulls
        return [copy.deepcopy(GAME)]

    def run_lock(self, slate_date=None, force=False, *, scheduled=False):
        self.original_calls += 1
        return {
            "ok": False,
            "sport": "mlb",
            "slateDateEt": slate_date or SLATE,
            "reason": "MISSED_PER_GAME_LOCK_NOT_BACKFILLED",
            "failClosed": True,
            "perGameLockProgress": {
                "missedCount": 1,
                "lockOutcomeCount": 0,
                "canonicalCount": 0,
                "noPredictionDataCount": 0,
            },
        }


class FakePatch:
    @staticmethod
    def game_identity(game):
        return game["game_id"]

    @staticmethod
    def _pull_at(module, pull):
        return datetime.fromisoformat(pull["pulled_at"])

    @staticmethod
    def _progress(module, slate, pulls, manifest, now, *, ensure_canonical):
        if module.outcome is None:
            return {
                "games": [
                    {
                        "gameIdentity": GAME["game_id"],
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
                    "gameIdentity": GAME["game_id"],
                    "state": "LOCKED_NO_PREDICTION_DATA",
                }
            ],
            "missedCount": 0,
            "lockOutcomeCount": 1,
            "canonicalCount": 0,
            "noPredictionDataCount": 1,
        }

    @staticmethod
    def _select_provider_manifest_authority(module, pulls, slate, manifest):
        return {"fingerprint": "manifest", "gameCount": 1}

    @staticmethod
    def _start(module, game):
        return datetime.fromisoformat(game["commence_time"])

    @staticmethod
    def _get_stage(module, slate, game):
        return None

    @staticmethod
    def _get_lock_outcome(module, slate, game):
        return copy.deepcopy(module.outcome)

    @staticmethod
    def _scoring_pulls(module, pulls, game):
        return []

    @staticmethod
    def _last_prelock_candidate(module, slate, game, scoring):
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
    def _put_no_prediction_outcome(
        module,
        slate,
        game,
        now,
        reasons,
        authority,
    ):
        module.outcome = {
            "lock_status": "LOCKED_NO_PREDICTION_DATA",
            "lock_outcome_recorded": True,
            "locked_prediction": False,
            "training_eligible": False,
            "reasons": list(reasons),
        }
        return copy.deepcopy(module.outcome)


def test_proven_absence_is_terminalized_without_creating_a_prediction():
    module = FakeModule()
    prospective.install_prospective_row_repair(module, FakePatch)

    result = module.run_lock(slate_date=SLATE, scheduled=True)

    assert module.original_calls == 1
    assert module.outcome["lock_status"] == "LOCKED_NO_PREDICTION_DATA"
    assert module.outcome["locked_prediction"] is False
    assert module.outcome["training_eligible"] is False
    assert result["ok"] is True
    assert result["lockStatusComplete"] is True
    assert result["canonicalPredictionComplete"] is False
    assert result["noPredictionDataCount"] == 1
    assert result["durableNoPredictionTerminalReconciled"] is True
    repair = result["missedLockTerminalReconciliation"]
    assert repair["reconciledCount"] == 1
    assert repair["remainingMissedCount"] == 0
    assert repair["postStartPredictionCreationAllowed"] is False
    assert repair["candidateIntegrityFailuresRelabeled"] is False


def test_existing_candidate_is_not_relabelled_as_no_data():
    class CandidatePatch(FakePatch):
        @staticmethod
        def _last_prelock_candidate(module, slate, game, scoring):
            return ({"predictedWinner": "Team A"}, {"proof": True}, [], [])

    module = FakeModule()
    prospective.install_prospective_row_repair(module, CandidatePatch)

    result = module.run_lock(slate_date=SLATE, scheduled=True)

    assert module.outcome is None
    assert result["ok"] is False
    assert result["reason"] == "MISSED_PER_GAME_LOCK_NOT_BACKFILLED"
    repair = result["missedLockTerminalReconciliation"]
    assert repair["reconciledCount"] == 0
    assert repair["remainingMissedCount"] == 1
    assert repair["unresolved"][0]["candidatePresent"] is True
    assert repair["candidateIntegrityFailuresRelabeled"] is False


def test_existing_post_window_success_contract_is_preserved():
    class PostWindowModule(FakeModule):
        def run_lock(self, slate_date=None, force=False, *, scheduled=False):
            result = super().run_lock(
                slate_date=slate_date,
                force=force,
                scheduled=scheduled,
            )
            result.update(
                {
                    "ok": True,
                    "reason": "POST_WINDOW_TERMINAL_STATUS_RECONCILED",
                    "lockStatusComplete": True,
                }
            )
            return result

    module = PostWindowModule()
    prospective.install_prospective_row_repair(module, FakePatch)

    result = module.run_lock(slate_date=SLATE, scheduled=True)

    assert module.outcome["lock_status"] == "LOCKED_NO_PREDICTION_DATA"
    assert result["ok"] is True
    assert result["reason"] == "POST_WINDOW_TERMINAL_STATUS_RECONCILED"
    assert result["lockStatusComplete"] is True
    assert result["perGameLockProgress"]["missedCount"] == 1
    assert result["perGameLockProgress"]["lockOutcomeCount"] == 0
    assert result["durableNoPredictionTerminalReconciled"] is True


def _locked_row(*reasons, verified=True):
    return {
        "lockedPrediction": True,
        "immutablePerGameStage": True,
        "exactVectorVerified": verified,
        "exactVectorValidationErrors": (
            [] if verified else ["frozen_vector_fingerprint_mismatch"]
        ),
        "trainingEligible": False,
        "trainingEligibilityStatus": "INELIGIBLE",
        "trainingExclusionReasons": list(reasons),
        "mlFeatureFreeze": {
            "trainingEligible": False,
            "trainingExclusionReasons": list(reasons),
            "exactVectorVerified": verified,
            "exactVectorValidationErrors": (
                [] if verified else ["frozen_vector_fingerprint_mismatch"]
            ),
        },
    }


def test_verified_promoted_lock_clears_only_expired_prelock_exclusion():
    row = _locked_row("immutable_tminus45_prediction_not_available")

    result = prospective._cleanup_promoted_lock_training_eligibility(row)

    assert result["trainingEligible"] is True
    assert result["trainingEligibilityStatus"] == "ELIGIBLE"
    assert result["trainingExclusionReasons"] == []
    assert result["mlFeatureFreeze"]["trainingEligible"] is True
    assert result["mlFeatureFreeze"]["trainingExclusionReasons"] == []
    assert result["expiredPrelockTrainingExclusionsCleared"] == [
        "immutable_tminus45_prediction_not_available"
    ]
    assert result["promotedLockTrainingEligibilityVersion"] == (
        prospective.PROMOTED_LOCK_TRAINING_ELIGIBILITY_VERSION
    )
    assert row["trainingEligible"] is False


def test_verified_promoted_lock_retains_real_reliability_exclusion():
    row = _locked_row(
        "immutable_tminus45_prediction_not_available",
        "lock_reliability:stale_or_missing_source_at_lock",
    )

    result = prospective._cleanup_promoted_lock_training_eligibility(row)

    expected = ["lock_reliability:stale_or_missing_source_at_lock"]
    assert result["trainingEligible"] is False
    assert result["trainingEligibilityStatus"] == "INELIGIBLE"
    assert result["trainingExclusionReasons"] == expected
    assert result["mlFeatureFreeze"]["trainingExclusionReasons"] == expected


def test_unverified_lock_never_has_exclusions_cleared():
    row = _locked_row(
        "immutable_tminus45_prediction_not_available",
        "exact_lock_vector_validation:frozen_vector_fingerprint_mismatch",
        verified=False,
    )

    result = prospective._cleanup_promoted_lock_training_eligibility(row)

    assert result == row


def test_install_hooks_prepare_row_before_per_game_apply(monkeypatch):
    stale_row = _locked_row("immutable_tminus45_prediction_not_available")
    patch = SimpleNamespace(
        _prepare_row=lambda: copy.deepcopy(stale_row),
        apply=lambda module: module,
    )
    monkeypatch.setitem(sys.modules, "mlb_daily_per_game_lock_patch", patch)

    prospective.install()

    prepared = patch._prepare_row()
    assert prepared["trainingEligible"] is True
    assert prepared["trainingExclusionReasons"] == []
    module = SimpleNamespace(run_lock=lambda **kwargs: {"ok": True})
    installed = patch.apply(module)
    assert installed is module
    assert installed.MLB_PROMOTED_LOCK_TRAINING_ELIGIBILITY_VERSION == (
        prospective.PROMOTED_LOCK_TRAINING_ELIGIBILITY_VERSION
    )
