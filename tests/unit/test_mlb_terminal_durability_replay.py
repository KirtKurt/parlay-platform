from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
SCRIPTS = ROOT / "scripts"
for value in (HELLO_WORLD, SCRIPTS):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

import mlb_prospective_row_repair as prospective
import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v4 as v4


SLATE = "2026-08-04"
GAME = {
    "game_id": "provider:missed-game",
    "id": "provider:missed-game",
    "commence_time": "2026-08-04T18:00:00+00:00",
}


class CachedModule:
    def __init__(self):
        self.outcome = None

    def _today_et(self):
        return SLATE

    def _now_utc(self):
        return datetime(2026, 8, 5, 2, 0, tzinfo=timezone.utc)

    def _pulls_for_date(self, slate):
        assert slate == SLATE
        return [{"pulled_at": "2026-08-04T17:00:00+00:00"}]

    def _latest_games_for_date(self, slate, pulls):
        assert slate == SLATE and pulls
        return [copy.deepcopy(GAME)]

    def run_lock(self, slate_date=None, force=False, *, scheduled=False):
        return {
            "ok": True,
            "sport": "mlb",
            "slateDateEt": slate_date or SLATE,
            "reason": "POST_WINDOW_TERMINAL_STATUS_ALREADY_RECONCILED",
            "lockStatusComplete": True,
            "perGameLockProgress": {
                "games": [
                    {
                        "gameIdentity": GAME["game_id"],
                        "state": "MISSED_NOT_BACKFILLED",
                    }
                ],
                "missedCount": 1,
                "dueMissingCount": 0,
                "lockOutcomeCount": 0,
                "canonicalCount": 0,
                "noPredictionDataCount": 0,
            },
        }


class Patch:
    @staticmethod
    def game_identity(game):
        return game["game_id"]

    @staticmethod
    def _pull_at(module, pull):
        return datetime.fromisoformat(pull["pulled_at"])

    @staticmethod
    def _progress(module, slate, pulls, manifest, now, *, ensure_canonical):
        del slate, pulls, manifest, now, ensure_canonical
        if module.outcome is None:
            return {
                "games": [
                    {
                        "gameIdentity": GAME["game_id"],
                        "state": "MISSED_NOT_BACKFILLED",
                    }
                ],
                "missedCount": 1,
                "dueMissingCount": 0,
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
            "dueMissingCount": 0,
            "lockOutcomeCount": 1,
            "canonicalCount": 0,
            "noPredictionDataCount": 1,
        }

    @staticmethod
    def _select_provider_manifest_authority(module, pulls, slate, manifest):
        del module, pulls, slate, manifest
        return {"fingerprint": "manifest", "gameCount": 1}

    @staticmethod
    def _start(module, game):
        del module
        return datetime.fromisoformat(game["commence_time"])

    @staticmethod
    def _get_stage(module, slate, game):
        del module, slate, game
        return None

    @staticmethod
    def _get_lock_outcome(module, slate, game):
        del slate, game
        return copy.deepcopy(module.outcome)

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
    def _put_no_prediction_outcome(module, slate, game, now, reasons, authority):
        del slate, game, now, authority
        module.outcome = {
            "lock_status": "LOCKED_NO_PREDICTION_DATA",
            "lock_outcome_recorded": True,
            "locked_prediction": False,
            "training_eligible": False,
            "reasons": list(reasons),
        }
        return copy.deepcopy(module.outcome)


def test_cached_post_window_state_still_writes_durable_terminal_outcome():
    module = CachedModule()
    prospective.install_prospective_row_repair(module, Patch)

    result = module.run_lock(slate_date=SLATE, force=True)

    assert module.outcome is not None
    assert module.outcome["lock_status"] == "LOCKED_NO_PREDICTION_DATA"
    assert module.outcome["locked_prediction"] is False
    assert module.outcome["training_eligible"] is False
    assert result["durableNoPredictionTerminalReconciled"] is True
    assert result["perGameLockProgress"]["missedCount"] == 0
    assert result["perGameLockProgress"]["lockOutcomeCount"] == 1
    assert result["postStartPredictionCreationAllowed"] is False


class CloudFormation:
    def describe_stack_resource(self, *, StackName, LogicalResourceId):
        assert StackName == "stack"
        return {
            "StackResourceDetail": {
                "PhysicalResourceId": f"physical-{LogicalResourceId}"
            }
        }


class Lambda:
    def get_function_configuration(self, *, FunctionName):
        assert FunctionName == "physical-MLBMLTrainingFunction"
        return {
            "Environment": {
                "Variables": {
                    "MLB_ML_RELEASE_CUTOFF_UTC": "2026-08-04T04:00:00+00:00"
                }
            }
        }


def _status(*, missed):
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": SLATE,
        "gameCount": 1,
        "officialScheduleBacked": True,
        "officialScheduleAuthorityVersion": base.OFFICIAL_SCHEDULE_AUTHORITY_VERSION,
        "officialScheduleAuthoritativeStartTimes": True,
        "officialScheduleGameCount": 1,
        "lockedPredictionCount": 0,
        "noPredictionDataCount": 1,
        "lockedStatusCount": 1,
        "lockStatusComplete": True,
        "missedGameCount": missed,
    }


def test_complete_projection_with_missed_rows_forces_protected_durability_replay():
    calls = []
    statuses = [_status(missed=1), _status(missed=0)]

    def invoke(client, function, event):
        del client, function
        calls.append(copy.deepcopy(event))
        if event.get("httpMethod") == "GET":
            return statuses.pop(0)
        if event.get("force") is True:
            return {
                "ok": True,
                "sport": "mlb",
                "slateDateEt": SLATE,
                "perGameLockProgress": {
                    "manifestGameCount": 1,
                    "canonicalCount": 0,
                    "noPredictionDataCount": 1,
                    "lockOutcomeCount": 1,
                    "missedCount": 0,
                    "dueMissingCount": 0,
                },
            }
        if event.get("run") == "prospective_backlog_settlement_v4":
            return {
                "ok": True,
                "slateDateEt": SLATE,
                "slateFinalized": True,
                "settledLabelCount": 0,
            }
        raise AssertionError(event)

    result = v4.reconcile(
        CloudFormation(),
        Lambda(),
        stack_name="stack",
        now_utc=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        invoke=invoke,
    )

    assert result["ok"] is True
    assert result["reconciledSlateCount"] == 1
    assert result["slates"][0]["protectedLockReplay"] is True
    assert [event.get("force") for event in calls].count(True) == 1
    assert [event.get("httpMethod") for event in calls].count("GET") == 2
