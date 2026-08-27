from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[2]
HANDLER = ROOT / "hello_world" / "mlb_daily_pick_lock_protected.py"
COOPERATIVE_REPAIR_PATH = ROOT / "hello_world" / "mlb_prospective_row_repair.py"
COOPERATIVE_PRODUCER_FIXTURES_PATH = (
    ROOT / "tests" / "unit" / "test_mlb_cooperative_terminal_chunk.py"
)
_COOPERATIVE_REPAIR_SPEC = importlib.util.spec_from_file_location(
    "_test_mlb_prospective_row_repair", COOPERATIVE_REPAIR_PATH
)
assert _COOPERATIVE_REPAIR_SPEC is not None
assert _COOPERATIVE_REPAIR_SPEC.loader is not None
COOPERATIVE_REPAIR = importlib.util.module_from_spec(_COOPERATIVE_REPAIR_SPEC)
_COOPERATIVE_REPAIR_SPEC.loader.exec_module(COOPERATIVE_REPAIR)
RUNTIME_VERSION = "MLB-ML-RUNTIME-INSTALL-v3-test"
REQUIRED_RUNTIME_STEPS = {
    "accuracyTargetsSeparated",
    "legacyReliabilityOverlaySafety",
    "sourceHonestFundamentals",
    "sourceHonestFundamentalsV2",
    "legacyV1ChampionRuntimeInstalledForShadowDiagnostics",
    "legacyV1AuthorityDisabled",
    "v2ShadowManualFirst",
    "officialSemanticsFinalized",
    "exactCleanCohortVectorPatch",
    "officialFreezeBridge",
    "immutableFeatureFreeze",
    "immutableLockedStorageAuthority",
    "canonicalLockedStorageFinalizer",
    "lastPrelockPromotionAuthority",
    "canonicalProbabilityAndPersistedPrelockAuthority",
    "providerNeutralCalibrationAndActionability",
    "legacyFinalGateDisabled",
}
STUB_MODULE_NAMES = (
    "mlb_ml_runtime_install_v3",
    "mlb_daily_pick_lock",
    "mlb_daily_lock_coverage_patch",
    "mlb_daily_lock_ml_vector_preservation_patch",
    "mlb_daily_per_game_lock_patch",
)


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}},
        operation,
    )


class FakeLeaseTable:
    CONDITION = (
        "attribute_not_exists(PK) OR "
        "attribute_not_exists(lease_expires_at_epoch) OR "
        "lease_expires_at_epoch <= :now"
    )
    RELEASE_CONDITION = (
        "lease_owner = :owner AND record_type = :record_type AND "
        "lease_version = :lease_version"
    )

    def __init__(self) -> None:
        self.item = None
        self.queue_item = None
        self.put_calls = []
        self.delete_calls = []
        self.get_calls = []
        self.update_calls = []
        self.put_error = None
        self.put_commits_then_error = None
        self.delete_error = None
        self.get_error = None
        self.update_error = None
        self.update_commits_then_error = None
        self.update_after_commit = None

    def put_item(self, **kwargs):
        if kwargs["Item"].get("SK") == "COOPERATIVE_TERMINAL_REPLAY#V1":
            self.put_calls.append(copy.deepcopy(kwargs))
            condition = kwargs["ConditionExpression"]
            if condition == "attribute_not_exists(PK) AND attribute_not_exists(SK)":
                may_replace = self.queue_item is None
            else:
                values = kwargs.get("ExpressionAttributeValues") or {}
                may_replace = bool(
                    isinstance(self.queue_item, dict)
                    and self.queue_item.get("state") == values.get(":acknowledged")
                    and self.queue_item.get("slate_date_et")
                    == values.get(":previous_slate_date")
                    and self.queue_item.get("record_type")
                    == values.get(":record_type")
                    and self.queue_item.get("coordination_version")
                    == values.get(":version")
                )
            if not may_replace:
                raise _client_error("ConditionalCheckFailedException", "PutItem")
            if self.put_error is not None:
                raise self.put_error
            self.queue_item = copy.deepcopy(kwargs["Item"])
            if self.put_commits_then_error is not None:
                raise self.put_commits_then_error
            return {}
        assert kwargs["ConditionExpression"] == self.CONDITION
        self.put_calls.append(copy.deepcopy(kwargs))
        now_epoch = int(kwargs["ExpressionAttributeValues"][":now"])
        existing_expiry = (
            self.item.get("lease_expires_at_epoch")
            if isinstance(self.item, dict)
            else None
        )
        may_replace = (
            self.item is None
            or existing_expiry is None
            or int(existing_expiry) <= now_epoch
        )
        if not may_replace:
            raise _client_error(
                "ConditionalCheckFailedException",
                "PutItem",
            )
        if self.put_error is not None:
            raise self.put_error
        self.item = copy.deepcopy(kwargs["Item"])
        if self.put_commits_then_error is not None:
            raise self.put_commits_then_error
        return {}

    def delete_item(self, **kwargs):
        assert kwargs["ConditionExpression"] == self.RELEASE_CONDITION
        self.delete_calls.append(copy.deepcopy(kwargs))
        if self.delete_error is not None:
            raise self.delete_error
        values = kwargs["ExpressionAttributeValues"]
        owned = bool(
            isinstance(self.item, dict)
            and self.item.get("lease_owner") == values[":owner"]
            and self.item.get("record_type") == values[":record_type"]
            and self.item.get("lease_version") == values[":lease_version"]
        )
        if not owned:
            raise _client_error(
                "ConditionalCheckFailedException",
                "DeleteItem",
            )
        self.item = None
        return {}

    def get_item(self, **kwargs):
        self.get_calls.append(copy.deepcopy(kwargs))
        assert kwargs["ConsistentRead"] is True
        if self.get_error is not None:
            raise self.get_error
        if kwargs["Key"].get("SK") == "COOPERATIVE_TERMINAL_REPLAY#V1":
            return (
                {"Item": copy.deepcopy(self.queue_item)}
                if self.queue_item
                else {}
            )
        return {"Item": copy.deepcopy(self.item)} if self.item else {}

    def update_item(self, **kwargs):
        self.update_calls.append(copy.deepcopy(kwargs))
        if self.update_error is not None:
            raise self.update_error
        assert kwargs["Key"].get("SK") == "COOPERATIVE_TERMINAL_REPLAY#V1"
        values = kwargs.get("ExpressionAttributeValues") or {}
        current = self.queue_item
        request_matches = bool(
            ":request_id" not in values
            or (
                isinstance(current, dict)
                and (
                    current.get("request_id") == values.get(":request_id")
                    or current.get("request_id") is None
                )
                and current.get("requested_at_epoch")
                == values.get(":request_epoch")
            )
        )
        valid_identity = bool(
            isinstance(current, dict)
            and current.get("record_type") == values.get(":record_type")
            and current.get("coordination_version") == values.get(":version")
            and current.get("slate_date_et") == values.get(":slate_date")
            and request_matches
        )
        proof_write = ":proof" in values
        if proof_write:
            allowed = bool(
                valid_identity
                and current.get("requested_at_epoch")
                == values.get(":request_epoch")
                and (
                    current.get("state") == values.get(":queued")
                    or (
                        current.get("state") == values.get(":claimed")
                        and int(current.get("claim_expires_at_epoch") or 0)
                        <= int(values.get(":now_epoch") or 0)
                    )
                )
            )
            next_state = current.get("state")
        elif ":acknowledged" in values:
            allowed = valid_identity and current.get("state") == values.get(
                ":completed"
            )
            next_state = values[":acknowledged"]
        elif ":receipt" in values:
            allowed = bool(
                valid_identity
                and current.get("state") == values.get(":claimed")
                and current.get("claim_owner") == values.get(":owner")
                and current.get("terminal_replay_progress")
                == values.get(":expected_progress")
            )
            next_state = values[":completed"]
        elif ":expires_epoch" in values:
            allowed = bool(
                valid_identity
                and (
                    current.get("state") == values.get(":queued")
                    or (
                        current.get("state") == values.get(":claimed")
                        and int(current.get("claim_expires_at_epoch") or 0)
                        <= int(values.get(":now_epoch") or 0)
                    )
                )
            )
            next_state = values[":claimed"]
        else:
            prior_progress_matches = bool(
                ":progress" not in values
                or (
                    ":prior_progress" in values
                    and current.get("terminal_replay_progress")
                    == values.get(":prior_progress")
                )
                or (
                    ":prior_progress" not in values
                    and current.get("terminal_replay_progress") is None
                )
            )
            allowed = bool(
                valid_identity
                and prior_progress_matches
                and current.get("state") == values.get(":claimed")
                and current.get("claim_owner") == values.get(":owner")
            )
            next_state = values[":queued"]
        if not allowed:
            raise _client_error("ConditionalCheckFailedException", "UpdateItem")

        updated = copy.deepcopy(current)
        updated["state"] = next_state
        if ":request_id" in values:
            updated["request_id"] = values[":request_id"]
        if proof_write:
            updated["current_slate_success_proof"] = copy.deepcopy(
                values[":proof"]
            )
        elif next_state == values.get(":claimed"):
            updated.update(
                {
                    "claim_owner": values[":owner"],
                    "claim_acquired_at_utc": values[":now_utc"],
                    "claim_acquired_at_epoch": values[":now_epoch"],
                    "claim_expires_at_utc": values[":expires_utc"],
                    "claim_expires_at_epoch": values[":expires_epoch"],
                }
            )
        elif next_state == values.get(":completed"):
            updated.update(
                {
                    "completed_at_utc": values[":now_utc"],
                    "completed_at_epoch": values[":now_epoch"],
                    "replay_receipt": copy.deepcopy(values[":receipt"]),
                }
            )
        elif next_state == values.get(":acknowledged"):
            updated.update(
                {
                    "acknowledged_at_utc": values[":now_utc"],
                    "acknowledged_at_epoch": values[":now_epoch"],
                }
            )
        elif ":progress" in values:
            updated.update(
                {
                    "terminal_replay_progress": copy.deepcopy(
                        values[":progress"]
                    ),
                    "last_chunk_at_utc": values[":now_utc"],
                    "last_chunk_at_epoch": values[":now_epoch"],
                    "last_chunk_stage": values[":chunk_stage"],
                    "last_chunk_status": values[":chunk_status"],
                }
            )
            if values[":chunk_status"] == "FAILED_CLOSED":
                updated.update(
                    {
                        "last_failure_at_utc": values[":now_utc"],
                        "last_failure_at_epoch": values[":now_epoch"],
                    }
                )
        else:
            updated.update(
                {
                    "last_failure_at_utc": values[":now_utc"],
                    "last_failure_at_epoch": values[":now_epoch"],
                }
            )
        if (
            "REMOVE current_slate_success_proof"
            in str(kwargs.get("UpdateExpression") or "")
        ):
            updated.pop("current_slate_success_proof", None)
        if not proof_write and next_state != values.get(":claimed"):
            for field in (
                "claim_owner",
                "claim_acquired_at_utc",
                "claim_acquired_at_epoch",
                "claim_expires_at_utc",
                "claim_expires_at_epoch",
            ):
                updated.pop(field, None)
        self.queue_item = updated
        if callable(self.update_after_commit):
            self.update_after_commit(self, copy.deepcopy(kwargs))
        if self.update_commits_then_error is not None:
            raise self.update_commits_then_error
        result = {}
        if kwargs.get("ReturnValues") == "ALL_NEW":
            # Preserve an object alias only in this fixture so the injected
            # default chunk adapter can emulate a pre-existing COMPLETE_READY
            # checkpoint. Production DynamoDB still returns a detached value.
            result["Attributes"] = self.queue_item
        return result


class FakeContext:
    def __init__(
        self,
        request_id: str = "request-1",
        remaining_millis: int = 300_000,
    ) -> None:
        self.aws_request_id = request_id
        self.remaining_millis = remaining_millis

    def get_remaining_time_in_millis(self) -> int:
        return self.remaining_millis


def _scheduled_event() -> dict:
    return {
        "sport": "mlb",
        "run": "daily_lock_check",
        "auto_ingest": False,
        "date": "2026-07-21",
    }


def _manual_event(*, token: str = "test-admin-token") -> dict:
    return {
        "httpMethod": "POST",
        "requestContext": {"stage": "test"},
        "headers": {"x-inqsi-admin-token": token},
        "body": json.dumps({"date": "2026-07-21"}),
    }


def _cooperative_event(
    slate_date: str = "2026-07-20", *, acknowledge: bool = False
) -> dict:
    event = {
        "sport": "mlb",
        "run": "prospective_terminal_backlog_reconciliation_v5",
        "slateDateEt": slate_date,
        "force": True,
    }
    if acknowledge:
        event["acknowledgeCooperativeCompletion"] = True
    return event


def _fixture_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


_COOPERATIVE_PRODUCER_FIXTURES = None


def _cooperative_producer_fixtures():
    global _COOPERATIVE_PRODUCER_FIXTURES
    if _COOPERATIVE_PRODUCER_FIXTURES is not None:
        return _COOPERATIVE_PRODUCER_FIXTURES
    module_name = (
        "_test_real_cooperative_terminal_producer_"
        + uuid.uuid4().hex
    )
    spec = importlib.util.spec_from_file_location(
        module_name,
        COOPERATIVE_PRODUCER_FIXTURES_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _COOPERATIVE_PRODUCER_FIXTURES = module
    return module


def _complete_terminal_checkpoint(
    *,
    slate_date: str = "2026-07-20",
    request_epoch: int = 1_700_000_000,
    request_id: str = "fixture-request",
    game_count: int = 15,
) -> dict:
    manifest_item = {
        "tableRole": "PULLS_TABLE",
        "PK": f"MLB_PROVIDER_MANIFEST#{slate_date}",
        "SK": "FULL_SLATE#V1",
        "itemFingerprint": _fixture_hash(
            {"kind": "manifest", "slate": slate_date, "count": game_count}
        ),
    }
    manifest_authority = {
        "version": "fixture-provider-manifest-v1",
        "recordType": "fixture_provider_manifest",
        "pk": manifest_item["PK"],
        "sk": manifest_item["SK"],
        "fingerprint": _fixture_hash(
            {"kind": "manifest-semantic", "slate": slate_date}
        ),
        "gameCount": game_count,
        "immutable": True,
        "writeOnce": True,
        "consistentReadVerified": True,
        "officialScheduleAuthorityVersion": "fixture-schedule-v1",
        "officialScheduleAuthorityFingerprint": _fixture_hash(
            {"kind": "schedule", "slate": slate_date}
        ),
        "atomicItems": [copy.deepcopy(manifest_item)],
    }
    manifest_authority["authorityEvidenceFingerprint"] = _fixture_hash(
        manifest_authority
    )

    processed_games = []
    for index in range(game_count):
        identity = f"provider:game-{index + 1}"
        outcome_item = {
            "tableRole": "LOCK_TABLE",
            "PK": f"MLB_LOCK_OUTCOME#{slate_date}",
            "SK": identity,
            "itemFingerprint": _fixture_hash(
                {
                    "kind": "outcome",
                    "slate": slate_date,
                    "identity": identity,
                }
            ),
        }
        evidence = {
            "durableIdentity": identity,
            "terminalState": "LOCKED_NO_PREDICTION_DATA",
            "authorityItemCount": 1,
            "dependencyItemCount": 1,
            "manifestAuthorityEvidenceFingerprint": (
                manifest_authority["authorityEvidenceFingerprint"]
            ),
            "items": [outcome_item, copy.deepcopy(manifest_item)],
        }
        evidence["evidenceFingerprint"] = (
            COOPERATIVE_REPAIR._cooperative_terminal_evidence_fingerprint(
                evidence
            )
        )
        processed_games.append(
            {
                "gameIdentity": identity,
                "durableIdentity": identity,
                "terminalState": "LOCKED_NO_PREDICTION_DATA",
                "reconciled": True,
                "durableEvidence": evidence,
            }
        )

    checkpoint = {
        "version": COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "slateDateEt": slate_date,
        "requestEpoch": int(request_epoch),
        "requestId": str(request_id),
        "manifestFingerprint": _fixture_hash(
            {
                "slate": slate_date,
                "orderedIdentities": [
                    entry["gameIdentity"] for entry in processed_games
                ],
            }
        ),
        "manifestBindingVersion": (
            COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_MANIFEST_BINDING_VERSION
        ),
        "manifestAuthority": manifest_authority,
        "manifestGameCount": game_count,
        "phase": "VERIFY",
        "nextGameIndex": game_count,
        "processedGameCount": game_count,
        "terminalCount": game_count,
        "canonicalCount": 0,
        "noPredictionDataCount": game_count,
        "reconciledCount": game_count,
        "processedGames": processed_games,
        "verificationIndex": game_count,
        "verifiedGameCount": game_count,
        "verificationComplete": True,
        "attemptCount": 2 * game_count,
        "lastAttempt": {
            "status": "DURABLE_TERMINAL_VERIFIED",
            "stage": "COMPLETE_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": game_count - 1,
            "gameIdentity": f"provider:game-{game_count}",
            "durableIdentity": f"provider:game-{game_count}",
        },
        "identityResolutionVersion": (
            COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_IDENTITY_RESOLUTION_VERSION
        ),
        "identityAliasLimit": (
            COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_IDENTITY_ALIAS_LIMIT
        ),
        "candidateAliasQueryLimit": (
            COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_QUERY_LIMIT
        ),
        "writerLeaseVersion": (
            COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_WRITER_LEASE_VERSION
        ),
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "productionAuthorityChanged": False,
    }
    checkpoint["checkpointFingerprint"] = (
        COOPERATIVE_REPAIR._cooperative_terminal_checkpoint_fingerprint(
            checkpoint
        )
    )
    COOPERATIVE_REPAIR._validated_cooperative_terminal_complete_checkpoint(
        checkpoint
    )
    return checkpoint


def _successful_terminal_replay_payload(
    slate_date: str = "2026-07-20",
    game_count: int = 15,
    *,
    checkpoint: dict | None = None,
) -> dict:
    complete_checkpoint = (
        copy.deepcopy(checkpoint)
        if checkpoint is not None
        else _complete_terminal_checkpoint(
            slate_date=slate_date,
            game_count=game_count,
        )
    )
    return COOPERATIVE_REPAIR._cooperative_terminal_completion_response(
        complete_checkpoint
    )


def _fixture_completion_chunk_result(
    *,
    module,
    patch,
    slate_date: str,
    request_epoch: int,
    request_id: str,
    context,
    game_count: int = 15,
    checkpoint: dict | None = None,
) -> dict:
    complete_checkpoint = (
        copy.deepcopy(checkpoint)
        if checkpoint is not None
        else _complete_terminal_checkpoint(
            slate_date=slate_date,
            request_epoch=request_epoch,
            request_id=request_id,
            game_count=game_count,
        )
    )
    complete_checkpoint, atomic_requests, read_set_fingerprint = (
        COOPERATIVE_REPAIR._validated_cooperative_terminal_complete_checkpoint(
            complete_checkpoint
        )
    )
    owner = f"fixture-v2-owner:{context.aws_request_id}"
    lease = {
        "handoffVersion": (
            COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION
        ),
        "acquired": True,
        "slateDateEt": slate_date,
        "owner": owner,
        "expiresAtEpoch": 2_000_000_000,
        "expiresAtUtc": "2033-05-18T03:33:20+00:00",
        "ownedKeys": (
            COOPERATIVE_REPAIR._cooperative_terminal_expected_lease_specs(
                patch, slate_date
            )
        ),
    }
    replay_payload = (
        COOPERATIVE_REPAIR._cooperative_terminal_completion_response(
            complete_checkpoint
        )
    )
    return {
        "ok": True,
        "complete": True,
        "deferred": False,
        "stage": "COMPLETE",
        "remainingSeconds": 840,
        "checkpointWriteAllowed": False,
        "checkpoint": copy.deepcopy(complete_checkpoint),
        "terminalReplayResponse": replay_payload,
        "terminalChunkVersion": (
            COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_CHUNK_VERSION
        ),
        "_completionLease": lease,
        "_atomicCompletionProof": {
            "handoffVersion": (
                COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION
            ),
            "slateDateEt": slate_date,
            "requestEpoch": int(request_epoch),
            "requestId": str(request_id),
            "checkpointFingerprint": complete_checkpoint[
                "checkpointFingerprint"
            ],
            "leaseOwnerFingerprint": hashlib.sha256(
                owner.encode("utf-8")
            ).hexdigest(),
            "readSetFingerprint": read_set_fingerprint,
            "itemCount": len(atomic_requests),
            "verifiedAtEpoch": 1_785_000_000,
        },
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "productionAuthorityChanged": False,
    }

def _successful_current_slate_payload(slate_date: str = "2026-07-21") -> dict:
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": slate_date,
        "currentSlateProcessed": True,
    }


def _runtime_status() -> dict:
    return {
        "applied": True,
        "ok": True,
        "version": RUNTIME_VERSION,
        "steps": {name: True for name in REQUIRED_RUNTIME_STEPS},
        "errors": [],
    }


@contextmanager
def _load_handler(
    *,
    runtime_status=None,
    runtime_error=None,
    delegate_payload=None,
    delegate_error=None,
    lease_table=None,
    lease_seconds=960,
    installed_diagnostics_version="test-diagnostics-version",
    writer_payload_fingerprint_version="test-ddb-canonical-fingerprint-version",
):
    events = []
    delegate_calls = []

    runtime = ModuleType("mlb_ml_runtime_install_v3")
    runtime.VERSION = RUNTIME_VERSION

    def install():
        events.append("runtime_install")
        if runtime_error is not None:
            raise runtime_error
        return copy.deepcopy(runtime_status if runtime_status is not None else _runtime_status())

    runtime.install = install

    daily_lock = ModuleType("mlb_daily_pick_lock")
    daily_lock.mlb_game_winner_engine = ModuleType("mlb_game_winner_engine")
    daily_lock.mlb_game_winner_engine.PAYLOAD_FINGERPRINT_VERSION = (
        writer_payload_fingerprint_version
    )
    daily_lock.history = ModuleType("inqsi_pull_history")
    daily_lock.history.CANONICAL_PAYLOAD_FINGERPRINT_VERSION = (
        "test-ddb-canonical-fingerprint-version"
    )
    daily_lock.history.canonical_payload_fingerprint = lambda value: str(value)
    daily_lock.history.verified_full_slate_manifest = lambda pulls, slate: {}
    daily_lock.TABLE = lease_table if lease_table is not None else FakeLeaseTable()

    def payload(event):
        result = {}
        if not event.get("httpMethod") and not event.get("requestContext"):
            result.update(event)
        params = event.get("queryStringParameters") or {}
        if isinstance(params, dict):
            result.update(params)
        body = event.get("body")
        if body:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                result.update(parsed)
        return result

    daily_lock._payload = payload
    daily_lock._today_et = lambda: "2026-07-21"

    def delegate(event, context):
        delegate_calls.append((event, context))
        if delegate_error is not None:
            raise delegate_error
        selected_payload = (
            delegate_payload(event, context)
            if callable(delegate_payload)
            else delegate_payload
        )
        return {
            "statusCode": 200 if (selected_payload or {}).get("ok", True) else 500,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(
                selected_payload or {"ok": True, "delegated": True}
            ),
        }

    daily_lock.lambda_handler = delegate
    daily_lock.MLB_COOPERATIVE_TERMINAL_CHUNK_VERSION = (
        "MLB-COOPERATIVE-TERMINAL-CHUNK-v3-"
        "bounded-proof-lease-handoff"
    )

    def default_chunk_runner(
        *,
        slate_date,
        request_epoch,
        request_id,
        checkpoint,
        context,
    ):
        del checkpoint
        replay_event = {
            "sport": "mlb",
            "run": "prospective_terminal_backlog_reconciliation_v5",
            "slateDateEt": slate_date,
            "force": False,
            "cooperativeEventBridgeOwner": True,
        }
        replay_response = delegate(replay_event, context)
        replay_payload = json.loads(replay_response["body"])
        if (
            int(replay_response.get("statusCode") or 0) >= 400
            or replay_payload.get("ok") is not True
        ):
            raise RuntimeError(
                "MLB_SCHEDULED_LOCK_FAILED:"
                + str(replay_payload.get("reason") or "UNKNOWN")
            )
        result = _fixture_completion_chunk_result(
            module=daily_lock,
            patch=per_game,
            slate_date=slate_date,
            request_epoch=request_epoch,
            request_id=request_id,
            context=context,
        )
        if isinstance(daily_lock.TABLE.queue_item, dict):
            daily_lock.TABLE.queue_item[
                "terminal_replay_progress"
            ] = copy.deepcopy(result["checkpoint"])
        return result

    daily_lock.run_cooperative_terminal_chunk = default_chunk_runner

    coverage = ModuleType("mlb_daily_lock_coverage_patch")

    def apply_coverage(module):
        assert module is daily_lock
        events.append("coverage_patch")

    coverage.apply = apply_coverage

    preservation = ModuleType("mlb_daily_lock_ml_vector_preservation_patch")

    def apply_preservation(module):
        assert module is daily_lock
        events.append("vector_preservation_patch")
        return {
            "ok": True,
            "failClosed": True,
            "expectedVectorVersion": "test-vector-version",
            "selectionLockIndependentOfTrainingVector": True,
        }

    preservation.apply = apply_preservation

    per_game = ModuleType("mlb_daily_per_game_lock_patch")
    per_game.ATTEMPT_DIAGNOSTICS_VERSION = "test-diagnostics-version"
    per_game.PROMOTION_POLICY_VERSION = "test-promotion-version"
    per_game.PAYLOAD_FINGERPRINT_VERSION = "test-ddb-canonical-fingerprint-version"
    per_game.OFFICIAL_SCHEDULE_AUTHORITY_VERSION = (
        "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date"
    )
    per_game.READINESS_VERSION = "test-readiness-version"
    per_game.LOCK_OUTCOME_VERSION = "test-lock-outcome-version"
    per_game.RELEASE_ASSESSMENT_VERSION = "test-playability-version"
    per_game.LOCK_EXECUTION_LEASE_VERSION = (
        "MLB-LOCK-EXECUTION-LEASE-v2-global-all-mutating"
    )
    per_game.LOCK_EXECUTION_LEASE_RECORD_TYPE = (
        "mlb_lock_execution_lease_v2"
    )
    per_game.LEGACY_SCHEDULED_SINGLE_FLIGHT_VERSION = (
        "MLB-LOCK-RUNTIME-v4-legacy-rollout-bridge"
    )
    per_game.LEGACY_SCHEDULED_SINGLE_FLIGHT_RECORD_TYPE = (
        "mlb_lock_runtime_v4"
    )
    per_game._lock_execution_lease_key = lambda: {
        "PK": "MLB_LOCK_EXECUTION#V2",
        "SK": "LEASE",
    }

    def fixture_bridge_slates(slate):
        center = datetime.strptime(str(slate), "%Y-%m-%d")
        return [
            (center + timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in (-1, 0, 1)
        ]

    per_game._legacy_rollout_bridge_slates = fixture_bridge_slates
    per_game._legacy_scheduled_single_flight_key = lambda slate: {
        "PK": f"MLB_LOCK_RUNTIME#{slate}",
        "SK": "SCHEDULED_SINGLE_FLIGHT#V1",
    }
    daily_lock._now_utc = lambda: datetime(
        2026, 7, 21, 22, 10, tzinfo=timezone.utc
    )
    daily_lock.MLB_COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION = (
        COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION
    )
    daily_lock.validate_cooperative_terminal_completion_checkpoint = (
        COOPERATIVE_REPAIR._validated_cooperative_terminal_complete_checkpoint
    )
    daily_lock.validate_cooperative_terminal_completion_handoff = (
        lambda **kwargs: (
            COOPERATIVE_REPAIR._validate_cooperative_terminal_completion_handoff(
                daily_lock,
                per_game,
                **kwargs,
            )
        )
    )
    daily_lock._completion_release_calls = []

    def fixture_completion_release(*, slate_date, lease):
        validated = (
            COOPERATIVE_REPAIR._validated_cooperative_terminal_completion_lease(
                per_game,
                slate=str(slate_date),
                lease=lease,
                now_epoch=int(daily_lock._now_utc().timestamp()),
                require_live=False,
            )
        )
        daily_lock._completion_release_calls.append(
            {
                "slateDateEt": str(slate_date),
                "lease": copy.deepcopy(validated),
            }
        )
        return {
            "ok": True,
            "released": True,
            "ownedKeyCount": len(validated["ownedKeys"]),
            "ownerExposed": False,
        }

    daily_lock.release_cooperative_terminal_completion_lease = (
        fixture_completion_release
    )

    def apply_per_game(module):
        assert module is daily_lock
        events.append("per_game_patch")
        module._INQSI_MLB_DAILY_PER_GAME_LOCK_V1 = True
        module.MLB_DAILY_PER_GAME_LOCK_VERSION = "test-per-game-lock-version"
        module.MLB_PER_GAME_LOCK_ATTEMPT_DIAGNOSTICS_VERSION = installed_diagnostics_version
        module.MLB_LAST_PRELOCK_PROMOTION_VERSION = "test-promotion-version"
        module.MLB_LOCK_READINESS_VERSION = "test-readiness-version"
        module.MLB_LOCK_OUTCOME_VERSION = "test-lock-outcome-version"
        module.MLB_PLAYABILITY_ASSESSMENT_VERSION = "test-playability-version"
        module.MLB_LOCK_SOURCE_WINDOW_STABILIZATION_SECONDS = 0
        module.MLB_LOCK_EXECUTION_LEASE_VERSION = (
            per_game.LOCK_EXECUTION_LEASE_VERSION
        )
        module.MLB_LOCK_EXECUTION_LEASE_SECONDS = 960
        module.MLB_LOCK_EXECUTION_LEASE_SCOPE = (
            "global_all_mutating_lock_invocations"
        )
        module.MLB_LOCK_EXECUTION_LEGACY_ROLLOUT_BRIDGE = True
        module.LOCK_POLICY = "test-policy"

    per_game.apply = apply_per_game

    stubs = {
        "mlb_ml_runtime_install_v3": runtime,
        "mlb_daily_pick_lock": daily_lock,
        "mlb_daily_lock_coverage_patch": coverage,
        "mlb_daily_lock_ml_vector_preservation_patch": preservation,
        "mlb_daily_per_game_lock_patch": per_game,
    }
    previous = {name: sys.modules.get(name) for name in STUB_MODULE_NAMES}
    module_name = f"_test_mlb_daily_pick_lock_protected_{uuid.uuid4().hex}"
    previous_lease_seconds = os.environ.get("MLB_LOCK_EXECUTION_LEASE_SECONDS")
    try:
        os.environ["MLB_LOCK_EXECUTION_LEASE_SECONDS"] = str(lease_seconds)
        sys.modules.update(stubs)
        spec = importlib.util.spec_from_file_location(module_name, HANDLER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module, events, delegate_calls
    finally:
        if previous_lease_seconds is None:
            os.environ.pop("MLB_LOCK_EXECUTION_LEASE_SECONDS", None)
        else:
            os.environ["MLB_LOCK_EXECUTION_LEASE_SECONDS"] = (
                previous_lease_seconds
            )
        sys.modules.pop(module_name, None)
        for name, original in previous.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _body(response):
    return json.loads(response["body"])


def _assert_runtime_error(callable_, expected_text):
    try:
        callable_()
    except RuntimeError as exc:
        assert expected_text in str(exc)
        return
    raise AssertionError("expected RuntimeError")


def test_installs_exact_runtime_before_lock_patches_and_delegates():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, events, delegate_calls):
        assert events == [
            "runtime_install",
            "coverage_patch",
            "vector_preservation_patch",
            "per_game_patch",
        ]

        response = handler.lambda_handler(_scheduled_event(), FakeContext())
        payload = _body(response)

        assert response["statusCode"] == 200
        assert payload["delegated"] is True
        assert payload["mlRuntimeInstallation"]["ok"] is True
        assert payload["mlRuntimeInstallation"]["version"] == RUNTIME_VERSION
        assert payload["mlRuntimeInstallation"]["steps"] == {
            name: True for name in REQUIRED_RUNTIME_STEPS
        }
        assert payload["perGameLockInstallation"]["fixVersion"] == (
            "MLB-LOCK-RUNTIME-FIX-v5-official-schedule-lifecycle-vector-separation"
        )
        assert payload["perGameLockInstallation"]["lastPrelockAtCutoffBecomesFinal"] is True
        assert payload["perGameLockInstallation"]["modelOrSignalRecomputedAtLock"] is False
        assert payload["perGameLockInstallation"]["explicitMlRuntimeInstall"] is True
        assert payload["perGameLockInstallation"]["durableAttemptDiagnostics"] is True
        assert payload["perGameLockInstallation"]["candidatePayloadFingerprintVersion"] == (
            "test-ddb-canonical-fingerprint-version"
        )
        assert payload["perGameLockInstallation"]["writerPayloadFingerprintVersion"] == (
            "test-ddb-canonical-fingerprint-version"
        )
        assert payload["perGameLockInstallation"]["historyPayloadFingerprintVersion"] == (
            "test-ddb-canonical-fingerprint-version"
        )
        assert payload["perGameLockInstallation"]["candidatePayloadFingerprintDdbReadCanonical"] is True
        assert payload["perGameLockInstallation"]["readinessCheckpointsAtTMinus60AndTMinus50"] is True
        assert payload["perGameLockInstallation"]["lockOutcomeStatusSeparateFromPrediction"] is True
        assert payload["perGameLockInstallation"]["latePlayabilityAssessmentCannotRewriteSelection"] is True
        assert payload["perGameLockInstallation"]["sourceWindowStabilizationSeconds"] == 0
        assert payload["perGameLockInstallation"]["officialScheduleAuthorityRequired"] is True
        assert payload["perGameLockInstallation"]["officialScheduleAuthorityVersion"] == (
            "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date"
        )
        assert payload["perGameLockInstallation"]["selectionLockIndependentOfTrainingVector"] is True
        assert payload["perGameLockInstallation"]["lockExecutionConcurrency"] == {
            "version": "MLB-LOCK-EXECUTION-LEASE-v1",
            "strategy": "dynamodb_conditional_lease",
            "scope": "global_mlb_lock_execution",
            "sharedLeaseKey": True,
            "leaseSeconds": 960,
            "requiredLeaseSeconds": 960,
            "lambdaTimeoutSeconds": 900,
            "timeoutSafetyMarginSeconds": 60,
            "expiredLeaseReclaim": True,
            "ownerConditionalRelease": True,
            "reservedLambdaConcurrencyRequired": False,
        }
        assert payload["lockExecutionConcurrency"]["executionMode"] == "scheduled"
        assert payload["lockExecutionConcurrency"]["leaseAcquired"] is True
        assert payload["lockExecutionConcurrency"]["leaseReleased"] is True
        assert payload["lockExecutionConcurrency"]["overlapSkipped"] is False
        assert len(delegate_calls) == 1
        assert len(table.put_calls) == 1
        assert len(table.delete_calls) == 1
        assert table.item is None


def test_missing_required_runtime_step_fails_closed_without_delegating():
    status = _runtime_status()
    status["steps"]["officialFreezeBridge"] = False

    with _load_handler(runtime_status=status) as (handler, _, delegate_calls):
        response = handler.lambda_handler({"httpMethod": "POST", "requestContext": {"stage": "test"}}, None)
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_ML_LOCK_RUNTIME_NOT_READY"
        assert payload["status"]["ok"] is False
        assert payload["status"]["missingRequiredSteps"] == ["officialFreezeBridge"]
        assert delegate_calls == []


def test_runtime_installer_exception_fails_closed_and_remains_observable():
    with _load_handler(runtime_error=RuntimeError("injected install failure")) as (
        handler,
        _,
        delegate_calls,
    ):
        response = handler.lambda_handler({"httpMethod": "POST", "requestContext": {"stage": "test"}}, None)
        payload = _body(response)
        options = _body(handler.lambda_handler({"httpMethod": "OPTIONS"}, None))

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_ML_LOCK_RUNTIME_NOT_READY"
        assert payload["status"]["errors"] == ["injected install failure"]
        assert options["mlRuntimeInstallation"]["ok"] is False
        assert delegate_calls == []


def test_runtime_version_mismatch_fails_closed_without_delegating():
    status = _runtime_status()
    status["version"] = "unexpected-runtime-version"

    with _load_handler(runtime_status=status) as (handler, _, delegate_calls):
        response = handler.lambda_handler({"httpMethod": "POST", "requestContext": {"stage": "test"}}, None)
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["status"]["ok"] is False
        assert payload["status"]["expectedVersion"] == RUNTIME_VERSION
        assert delegate_calls == []


def test_scheduled_prerequisite_failure_raises_for_eventbridge_visibility():
    status = _runtime_status()
    status["steps"]["officialFreezeBridge"] = False

    with _load_handler(runtime_status=status) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler({"sport": "mlb", "run": "daily_lock_check"}, None),
            "MLB_SCHEDULED_LOCK_PREREQUISITE_FAILED",
        )
        assert delegate_calls == []


def test_scheduled_delegate_lock_failure_raises_after_diagnostics_return():
    payload = {
        "ok": False,
        "reason": "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL",
        "perGameLockAttemptDiagnostics": {"attemptedGameCount": 1},
    }
    with _load_handler(delegate_payload=payload) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler({"sport": "mlb", "run": "daily_lock_check"}, None),
            "MLB_SCHEDULED_LOCK_FAILED",
        )
        assert len(delegate_calls) == 1


def test_stale_attempt_diagnostics_version_fails_closed():
    with _load_handler(installed_diagnostics_version="stale-diagnostics-version") as (
        handler,
        _,
        delegate_calls,
    ):
        response = handler.lambda_handler(
            {"httpMethod": "POST", "requestContext": {"stage": "test"}},
            None,
        )
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_DAILY_PER_GAME_LOCK_NOT_INSTALLED"
        assert payload["status"]["durableAttemptDiagnostics"] is False
        assert payload["status"]["expectedAttemptDiagnosticsVersion"] == "test-diagnostics-version"
        assert delegate_calls == []


def test_writer_fingerprint_contract_mismatch_fails_closed():
    with _load_handler(writer_payload_fingerprint_version="stale-writer-fingerprint") as (
        handler,
        _,
        delegate_calls,
    ):
        response = handler.lambda_handler(
            {"httpMethod": "POST", "requestContext": {"stage": "test"}},
            None,
        )
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_DAILY_PER_GAME_LOCK_NOT_INSTALLED"
        assert payload["status"]["candidatePayloadFingerprintDdbReadCanonical"] is False
        assert payload["status"]["writerPayloadFingerprintVersion"] == (
            "stale-writer-fingerprint"
        )
        assert delegate_calls == []


def test_active_slow_run_causes_five_fresh_minute_ticks_to_skip_without_owner_leak():
    table = FakeLeaseTable()
    clock = {"now": datetime(2026, 7, 21, 22, 0, tzinfo=timezone.utc)}
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler._utc_now = lambda: clock["now"]
        handler._acquire_execution_lease(
            mode="scheduled",
            slate_date_et="2026-07-21",
            owner="slow-owner-must-not-leak",
        )

        for minute in range(1, 6):
            clock["now"] = datetime(
                2026, 7, 21, 22, minute, tzinfo=timezone.utc
            )
            response = handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id=f"tick-{minute}"),
            )
            payload = _body(response)

            assert response["statusCode"] == 200
            assert payload["status"] == "SKIPPED_OVERLAPPING_LOCK_EXECUTION"
            assert payload["skipped"] is True
            assert payload["mutatingRunAttempted"] is False
            assert payload["nextFreshScheduleIsRetry"] is True
            control = payload["lockExecutionConcurrency"]
            assert control["overlapSkipped"] is True
            assert control["activeLease"]["active"] is True
            assert control["activeLease"]["ownerPresent"] is True
            assert "slow-owner-must-not-leak" not in json.dumps(payload)

        assert delegate_calls == []
        assert len(table.put_calls) == 6
        assert len(table.delete_calls) == 0


def test_exact_historical_v5_request_queues_idempotently_without_touching_lease():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        first = handler.lambda_handler(
            _cooperative_event(),
            FakeContext(request_id="manual-recovery-1", remaining_millis=900_000),
        )
        second = handler.lambda_handler(
            _cooperative_event(),
            FakeContext(request_id="manual-recovery-2", remaining_millis=900_000),
        )

        first_payload = _body(first)
        second_payload = _body(second)
        assert first["statusCode"] == 200
        assert first_payload["status"] == "QUEUED_FOR_EVENTBRIDGE_LOCK_OWNER"
        assert first_payload["cooperativeTerminalReplay"]["state"] == "QUEUED"
        assert second_payload["cooperativeTerminalReplay"]["state"] == "QUEUED"
        assert first_payload["mutatingRunAttempted"] is False
        assert first_payload["activeLeaseMutationAllowed"] is False
        assert first_payload["postStartPredictionCreationAllowed"] is False
        assert first_payload["immutablePredictionRewriteAllowed"] is False
        assert delegate_calls == []
        assert table.item is None
        assert table.delete_calls == []
        assert len(table.put_calls) == 1
        assert table.put_calls[0]["Item"]["SK"] == (
            "COOPERATIVE_TERMINAL_REPLAY#V1"
        )


@pytest.mark.parametrize(
    "event",
    (
        _cooperative_event("2026-07-21"),
        _cooperative_event("2026-07-22"),
        _cooperative_event("not-a-date"),
        {
            **_cooperative_event("2026-07-20"),
            "sport": "soccer",
        },
        {
            **_cooperative_event("2026-07-20"),
            "force": False,
        },
    ),
)
def test_cooperative_queue_rejects_nonexact_or_nonhistorical_contract(event):
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(event, FakeContext()),
            "MLB_SCHEDULED_LOCK_PREREQUISITE_FAILED",
        )
        assert delegate_calls == []
        assert table.item is None
        assert table.queue_item is None
        assert table.put_calls == []
        assert table.delete_calls == []


def test_eventbridge_owner_runs_current_first_then_completes_redacted_handoff():
    table = FakeLeaseTable()

    def payload_for(event, context):
        del context
        if event.get("run") == "daily_lock_check":
            return {
                "ok": True,
                "sport": "mlb",
                "slateDateEt": "2026-07-21",
                "currentSlateProcessed": True,
            }
        payload = _successful_terminal_replay_payload()
        payload["secretToken"] = "must-not-enter-coordination-record"
        payload["failures"] = [{"authorization": "Bearer hidden"}]
        return payload

    with _load_handler(
        lease_table=table,
        delegate_payload=payload_for,
    ) as (handler, _, delegate_calls):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        response = handler.lambda_handler(
            _scheduled_event(),
            FakeContext(request_id="eventbridge-owner", remaining_millis=900_000),
        )
        payload = _body(response)

        assert [call[0]["run"] for call in delegate_calls] == [
            "daily_lock_check",
            "prospective_terminal_backlog_reconciliation_v5",
        ]
        assert delegate_calls[1][0]["cooperativeEventBridgeOwner"] is True
        assert delegate_calls[1][0]["force"] is False
        assert payload["currentSlateProcessed"] is True
        owner = payload["cooperativeTerminalReplayOwnerExecution"]
        assert owner["state"] == "COMPLETED"
        assert owner["currentSlateRanFirst"] is True
        assert owner["historicalReplayCompleted"] is True
        assert owner["claimOwnerIsCurrentLeaseOwner"] is True
        assert table.item is None
        assert len(table.delete_calls) == 1
        assert table.queue_item["state"] == "COMPLETED"
        stored = json.dumps(table.queue_item, sort_keys=True)
        assert "must-not-enter-coordination-record" not in stored
        assert "Bearer hidden" not in stored
        assert table.queue_item["replay_receipt"]["cooperativeReceiptRedacted"] is True

        # This is the backward-compatible contract consumed by an old v5
        # checkout: the identical retry receives canonical replay evidence and
        # still never acquires the lease or invokes the delegate itself.
        delegate_count = len(delegate_calls)
        poll = handler.lambda_handler(_cooperative_event(), FakeContext())
        poll_payload = _body(poll)
        assert poll_payload["cooperativeTerminalReplayCompleted"] is True
        assert poll_payload["cooperativeTerminalReplay"]["state"] == "ACKNOWLEDGED"
        assert poll_payload["missedLockTerminalReconciliation"]["ok"] is True
        assert poll_payload["mutatingRunAttemptedByPollingRequest"] is False
        assert len(delegate_calls) == delegate_count
        assert table.item is None
        assert table.queue_item["state"] == "ACKNOWLEDGED"
        legacy_v57_overlap = bool(
            str(poll_payload.get("reason") or poll_payload.get("status") or "")
            == "SKIPPED_OVERLAPPING_LOCK_EXECUTION"
            or str(poll_payload.get("error") or "")
            == "MLB_LOCK_EXECUTION_ALREADY_RUNNING"
            or (
                poll_payload.get("skipped") is True
                and poll_payload.get("mutatingRunAttempted") is False
            )
        )
        assert legacy_v57_overlap is False

        # v5.7 has no explicit ACK call.  The completed poll above performs the
        # ACK server-side, so the same old workflow can immediately enqueue its
        # next exact historical slate.
        next_date = handler.lambda_handler(
            _cooperative_event("2026-07-19"),
            FakeContext(),
        )
        assert _body(next_date)["cooperativeTerminalReplay"]["state"] == "QUEUED"
        assert table.queue_item["slate_date_et"] == "2026-07-19"


def test_completed_receipt_survives_acknowledgement_replacement_race():
    table = FakeLeaseTable()

    def payload_for(event, context):
        del context
        if event.get("run") == "daily_lock_check":
            return _successful_current_slate_payload()
        return _successful_terminal_replay_payload()

    with _load_handler(
        lease_table=table,
        delegate_payload=payload_for,
    ) as (handler, _, _):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        handler.lambda_handler(
            _scheduled_event(),
            FakeContext(request_id="completing-owner", remaining_millis=900_000),
        )
        assert table.queue_item["state"] == "COMPLETED"
        completed_receipt = copy.deepcopy(table.queue_item["replay_receipt"])

        original_update = table.update_item

        def acknowledge_then_replace(**kwargs):
            result = original_update(**kwargs)
            values = kwargs.get("ExpressionAttributeValues") or {}
            if ":acknowledged" in values:
                replacement = copy.deepcopy(table.queue_item)
                replacement.update(
                    {
                        "state": "QUEUED",
                        "slate_date_et": "2026-07-19",
                        "requested_at_epoch": 9_999_999_998,
                    }
                )
                replacement.pop("replay_receipt", None)
                table.queue_item = replacement
                raise _client_error("InternalServerError", "UpdateItem")
            return result

        table.update_item = acknowledge_then_replace
        poll = _body(handler.lambda_handler(_cooperative_event(), FakeContext()))

        assert poll["cooperativeTerminalReplayCompleted"] is True
        assert poll["cooperativeTerminalReplay"]["state"] == "ACKNOWLEDGED"
        assert poll["missedLockTerminalReconciliation"] == completed_receipt[
            "missedLockTerminalReconciliation"
        ]
        assert table.queue_item["state"] == "QUEUED"
        assert table.queue_item["slate_date_et"] == "2026-07-19"


def test_insufficient_time_persists_current_proof_then_next_owner_replays():
    table = FakeLeaseTable()
    now = datetime(2026, 7, 21, 22, 10, tzinfo=timezone.utc)

    def payload_for(event, context):
        del context
        if event.get("run") == "daily_lock_check":
            return _successful_current_slate_payload()
        return _successful_terminal_replay_payload()

    with _load_handler(
        lease_table=table,
        delegate_payload=payload_for,
    ) as (handler, _, delegate_calls):
        handler._utc_now = lambda: now
        handler.lambda_handler(_cooperative_event(), FakeContext())

        first = handler.lambda_handler(
            _scheduled_event(),
            FakeContext(request_id="short-owner", remaining_millis=659_000),
        )
        first_payload = _body(first)
        first_status = first_payload[
            "cooperativeTerminalReplayOwnerExecution"
        ]

        assert [call[0]["run"] for call in delegate_calls] == [
            "daily_lock_check"
        ]
        assert first_status["state"] == (
            "DEFERRED_INSUFFICIENT_REMAINING_TIME"
        )
        assert first_status["currentSlateRanFirst"] is True
        assert first_status["queueReadAttempted"] is True
        assert first_status["currentSlateSuccessProofPersisted"] is True
        assert table.queue_item["state"] == "QUEUED"
        proof = table.queue_item["current_slate_success_proof"]
        assert proof == {
            "version": handler.COOPERATIVE_CURRENT_SLATE_PROOF_VERSION,
            "currentSlateDateEt": "2026-07-21",
            "requestSlateDateEt": "2026-07-20",
            "requestEpoch": int(now.timestamp()),
            "requestId": table.queue_item["request_id"],
            "processedAtEpoch": int(now.timestamp()),
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
        }
        assert "owner" not in json.dumps(proof).lower()
        assert table.item is None

        second = handler.lambda_handler(
            _scheduled_event(),
            FakeContext(request_id="fresh-owner", remaining_millis=900_000),
        )
        second_payload = _body(second)
        second_status = second_payload[
            "cooperativeTerminalReplayOwnerExecution"
        ]

        # The second EventBridge owner consumes the fresh durable proof instead
        # of repeating the long current run, preserving a full replay budget.
        assert [call[0]["run"] for call in delegate_calls] == [
            "daily_lock_check",
            "prospective_terminal_backlog_reconciliation_v5",
        ]
        assert second_payload["status"] == (
            "CURRENT_SLATE_PROVEN_BY_PRIOR_EVENTBRIDGE_OWNER"
        )
        assert delegate_calls[1][0]["force"] is False
        assert second_status["state"] == "COMPLETED"
        assert second_status["currentSlateRanFirst"] is True
        assert second_status[
            "currentSlateSuccessProofCarriedAcrossInvocation"
        ] is True
        assert second_status["historicalReplayCompleted"] is True
        assert table.queue_item["state"] == "COMPLETED"
        assert "current_slate_success_proof" not in table.queue_item
        assert table.item is None


@pytest.mark.parametrize(
    "invalid_proof",
    (
        "wrong_current_slate",
        "wrong_request_epoch",
        "pre_request",
        "stale",
        "future",
        "wrong_version",
    ),
)
def test_invalid_carried_proof_never_substitutes_for_current_slate(
    invalid_proof,
):
    table = FakeLeaseTable()
    request_now = datetime(2026, 7, 21, 22, 0, tzinfo=timezone.utc)
    owner_now = request_now + timedelta(seconds=300)
    invalid_current = {
        "ok": True,
        "sport": "soccer",
        "slateDateEt": "2026-07-21",
    }

    with _load_handler(
        lease_table=table,
        delegate_payload=invalid_current,
    ) as (handler, _, delegate_calls):
        handler._utc_now = lambda: request_now
        handler.lambda_handler(_cooperative_event(), FakeContext())
        request_epoch = int(request_now.timestamp())
        proof = {
            "version": handler.COOPERATIVE_CURRENT_SLATE_PROOF_VERSION,
            "currentSlateDateEt": "2026-07-21",
            "requestSlateDateEt": "2026-07-20",
            "requestEpoch": request_epoch,
            "requestId": table.queue_item["request_id"],
            "processedAtEpoch": int(owner_now.timestamp()),
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
        }
        if invalid_proof == "wrong_current_slate":
            proof["currentSlateDateEt"] = "2026-07-20"
        elif invalid_proof == "wrong_request_epoch":
            proof["requestEpoch"] = request_epoch + 1
        elif invalid_proof == "pre_request":
            proof["processedAtEpoch"] = request_epoch - 1
        elif invalid_proof == "stale":
            proof["processedAtEpoch"] = (
                int(owner_now.timestamp())
                - handler.COOPERATIVE_CURRENT_SLATE_PROOF_MAX_AGE_SECONDS
                - 1
            )
        elif invalid_proof == "future":
            proof["processedAtEpoch"] = int(owner_now.timestamp()) + 1
        elif invalid_proof == "wrong_version":
            proof["version"] = "wrong-proof-version"
        table.queue_item["current_slate_success_proof"] = proof
        handler._utc_now = lambda: owner_now

        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id=f"invalid-proof-{invalid_proof}",
                    remaining_millis=900_000,
                ),
            ),
            "MLB_CURRENT_SLATE_LOCK_NOT_PROCESSED_BEFORE_COOPERATIVE_REPLAY",
        )

        # Invalid proof falls back to a fresh current run.  That deliberately
        # invalid current response then fails closed before historical work.
        assert [call[0]["run"] for call in delegate_calls] == [
            "daily_lock_check"
        ]
        assert table.queue_item["state"] == "QUEUED"
        assert table.item is None


def test_replay_failure_clears_carried_proof_before_retrying_current():
    table = FakeLeaseTable()
    now = datetime(2026, 7, 21, 22, 10, tzinfo=timezone.utc)
    state = {"current_ok": True}

    def payload_for(event, context):
        del context
        if event.get("run") == "daily_lock_check":
            if state["current_ok"]:
                return _successful_current_slate_payload()
            return {
                "ok": True,
                "sport": "soccer",
                "slateDateEt": "2026-07-21",
            }
        return {
            "ok": False,
            "sport": "mlb",
            "slateDateEt": "2026-07-20",
            "reason": "PROTECTED_REPLAY_FAILED_CLOSED",
        }

    with _load_handler(
        lease_table=table,
        delegate_payload=payload_for,
    ) as (handler, _, delegate_calls):
        handler._utc_now = lambda: now
        handler.lambda_handler(_cooperative_event(), FakeContext())
        handler.lambda_handler(
            _scheduled_event(),
            FakeContext(request_id="proof-owner", remaining_millis=659_000),
        )
        assert "current_slate_success_proof" in table.queue_item

        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id="failed-replay-owner",
                    remaining_millis=900_000,
                ),
            ),
            "MLB_SCHEDULED_LOCK_FAILED",
        )
        assert table.queue_item["state"] == "QUEUED"
        assert "current_slate_success_proof" not in table.queue_item

        state["current_ok"] = False
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id="must-refresh-current",
                    remaining_millis=900_000,
                ),
            ),
            "MLB_CURRENT_SLATE_LOCK_NOT_PROCESSED_BEFORE_COOPERATIVE_REPLAY",
        )
        assert [call[0]["run"] for call in delegate_calls] == [
            "daily_lock_check",
            "prospective_terminal_backlog_reconciliation_v5",
            "daily_lock_check",
        ]
        assert table.queue_item["state"] == "QUEUED"
        assert table.item is None


def test_live_cooperative_claim_is_not_reported_as_stale_or_reclaimed():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        table.queue_item.update(
            {
                "state": "CLAIMED",
                "claim_owner": "still-live-owner",
                "claim_expires_at_epoch": 9_999_999_999,
            }
        )

        response = handler.lambda_handler(
            _scheduled_event(),
            FakeContext(request_id="waiting-owner", remaining_millis=900_000),
        )
        payload = _body(response)

        assert [call[0]["run"] for call in delegate_calls] == ["daily_lock_check"]
        status = payload["cooperativeTerminalReplayOwnerExecution"]
        assert status["state"] == "CLAIMED"
        assert status["staleClaimReclaimable"] is False
        assert table.queue_item["claim_owner"] == "still-live-owner"
        assert table.update_calls == []
        assert table.item is None


def test_inner_overlap_without_pending_handoff_retains_normal_skip_behavior():
    table = FakeLeaseTable()

    def overlap_payload(event, context):
        del event, context
        return {
            "ok": True,
            "sport": "mlb",
            "slateDateEt": "2026-07-21",
            "status": "SKIPPED_OVERLAPPING_LOCK_EXECUTION",
            "skipped": True,
            "mutatingRunAttempted": False,
        }

    with _load_handler(
        lease_table=table,
        delegate_payload=overlap_payload,
    ) as (handler, _, delegate_calls):
        response = handler.lambda_handler(
            _scheduled_event(),
            FakeContext(request_id="normal-overlap", remaining_millis=900_000),
        )
        payload = _body(response)

        assert payload["status"] == "SKIPPED_OVERLAPPING_LOCK_EXECUTION"
        assert [call[0]["run"] for call in delegate_calls] == ["daily_lock_check"]
        assert table.queue_item is None
        assert table.update_calls == []
        assert table.item is None


@pytest.mark.parametrize(
    "current_payload",
    (
        {"sport": "mlb", "slateDateEt": "2026-07-21"},
        {"ok": True, "sport": "soccer", "slateDateEt": "2026-07-21"},
        {"ok": True, "sport": "mlb", "slateDateEt": "2026-07-20"},
    ),
)
def test_pending_handoff_requires_exact_successful_current_slate_proof(
    current_payload,
):
    table = FakeLeaseTable()
    with _load_handler(
        lease_table=table,
        delegate_payload=current_payload,
    ) as (handler, _, delegate_calls):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="unproven-current", remaining_millis=900_000),
            ),
            "MLB_CURRENT_SLATE_LOCK_NOT_PROCESSED_BEFORE_COOPERATIVE_REPLAY",
        )

        assert [call[0]["run"] for call in delegate_calls] == ["daily_lock_check"]
        assert table.queue_item["state"] == "QUEUED"
        assert table.update_calls == []
        assert table.item is None


def test_noncanonical_methodless_daily_shape_cannot_claim_handoff():
    table = FakeLeaseTable()
    with _load_handler(
        lease_table=table,
        delegate_payload=_successful_current_slate_payload(),
    ) as (handler, _, delegate_calls):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        noncanonical = _scheduled_event()
        noncanonical.pop("auto_ingest")
        response = handler.lambda_handler(
            noncanonical,
            FakeContext(request_id="noncanonical-owner", remaining_millis=900_000),
        )

        assert _body(response)["ok"] is True
        assert [call[0]["run"] for call in delegate_calls] == ["daily_lock_check"]
        assert table.queue_item["state"] == "QUEUED"
        assert table.update_calls == []
        assert table.item is None


def test_current_slate_failure_prevents_historical_claim_or_replay():
    table = FakeLeaseTable()

    def payload_for(event, context):
        del context
        if event.get("run") == "daily_lock_check":
            return {"ok": False, "reason": "CURRENT_SLATE_FAILED_CLOSED"}
        return _successful_terminal_replay_payload()

    with _load_handler(
        lease_table=table,
        delegate_payload=payload_for,
    ) as (handler, _, delegate_calls):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id="current-failure-owner",
                    remaining_millis=900_000,
                ),
            ),
            "MLB_SCHEDULED_LOCK_FAILED",
        )
        assert [call[0]["run"] for call in delegate_calls] == ["daily_lock_check"]
        assert table.queue_item["state"] == "QUEUED"
        assert table.update_calls == []
        assert table.item is None
        assert len(table.delete_calls) == 1


def test_inner_current_slate_overlap_cannot_be_treated_as_current_first():
    table = FakeLeaseTable()

    def payload_for(event, context):
        del context
        if event.get("run") == "daily_lock_check":
            return {
                "ok": True,
                "sport": "mlb",
                "status": "SKIPPED_OVERLAPPING_LOCK_EXECUTION",
                "reason": "SKIPPED_OVERLAPPING_LOCK_EXECUTION",
                "skipped": True,
                "mutatingRunAttempted": False,
            }
        return _successful_terminal_replay_payload()

    with _load_handler(
        lease_table=table,
        delegate_payload=payload_for,
    ) as (handler, _, delegate_calls):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="inner-overlap", remaining_millis=900_000),
            ),
            "MLB_CURRENT_SLATE_LOCK_NOT_PROCESSED_BEFORE_COOPERATIVE_REPLAY",
        )
        assert [call[0]["run"] for call in delegate_calls] == ["daily_lock_check"]
        assert table.queue_item["state"] == "QUEUED"
        assert table.update_calls == []
        assert table.item is None
        assert len(table.delete_calls) == 1


def test_budget_depletion_after_claim_requeues_before_historical_delegate():
    table = FakeLeaseTable()

    class DepletingContext(FakeContext):
        def __init__(self):
            super().__init__(
                request_id="budget-depleted-owner",
                remaining_millis=900_000,
            )
            self.remaining_values = iter(
                (900_000, 900_000, 900_000, 659_000)
            )

        def get_remaining_time_in_millis(self) -> int:
            return next(self.remaining_values)

    with _load_handler(
        lease_table=table,
        delegate_payload=_successful_current_slate_payload(),
    ) as (handler, _, delegate_calls):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                DepletingContext(),
            ),
            "MLB_COOPERATIVE_REPLAY_BUDGET_DEPLETED_BEFORE_DELEGATE",
        )

        assert [call[0]["run"] for call in delegate_calls] == [
            "daily_lock_check"
        ]
        assert table.queue_item["state"] == "QUEUED"
        assert table.queue_item["force"] is True
        assert table.queue_item.get("last_failure_at_epoch") is not None
        assert "claim_owner" not in table.queue_item
        assert table.item is None
        assert len(table.delete_calls) == 1


def test_historical_failure_is_requeued_fail_closed_without_lease_bypass():
    table = FakeLeaseTable()

    def payload_for(event, context):
        del context
        if event.get("run") == "daily_lock_check":
            return _successful_current_slate_payload()
        return {
            "ok": False,
            "sport": "mlb",
            "slateDateEt": "2026-07-20",
            "reason": "PROTECTED_REPLAY_FAILED_CLOSED",
        }

    with _load_handler(
        lease_table=table,
        delegate_payload=payload_for,
    ) as (handler, _, delegate_calls):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="replay-failure-owner", remaining_millis=900_000),
            ),
            "MLB_SCHEDULED_LOCK_FAILED",
        )

        assert [call[0]["run"] for call in delegate_calls] == [
            "daily_lock_check",
            "prospective_terminal_backlog_reconciliation_v5",
        ]
        assert table.queue_item["state"] == "QUEUED"
        assert "claim_owner" not in table.queue_item
        assert table.item is None
        assert len(table.delete_calls) == 1


def test_stale_claim_is_owner_fenced_reclaimed_and_ack_allows_next_date():
    table = FakeLeaseTable()
    with _load_handler(
        lease_table=table,
        delegate_payload=lambda event, context: (
            _successful_current_slate_payload()
            if event.get("run") == "daily_lock_check"
            else _successful_terminal_replay_payload()
        ),
    ) as (handler, _, _):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        table.queue_item.update(
            {
                "state": "CLAIMED",
                "claim_owner": "expired-owner",
                "claim_expires_at_epoch": 1,
            }
        )
        response = handler.lambda_handler(
            _scheduled_event(),
            FakeContext(request_id="reclaiming-owner", remaining_millis=900_000),
        )
        assert _body(response)["cooperativeTerminalReplayOwnerExecution"][
            "state"
        ] == "COMPLETED"
        assert table.queue_item["state"] == "COMPLETED"

        ack = handler.lambda_handler(
            _cooperative_event(acknowledge=True),
            FakeContext(),
        )
        assert _body(ack)["cooperativeTerminalReplayAcknowledged"] is True
        assert table.queue_item["state"] == "ACKNOWLEDGED"

        next_request = handler.lambda_handler(
            _cooperative_event("2026-07-19"),
            FakeContext(),
        )
        assert _body(next_request)["cooperativeTerminalReplay"]["state"] == "QUEUED"
        assert table.queue_item["slate_date_et"] == "2026-07-19"


def test_completion_is_conditioned_on_the_current_claim_owner():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        claimed, _ = handler._claim_cooperative_replay(
            owner="actual-outer-lease-owner",
            context=FakeContext(remaining_millis=900_000),
            current_slate_response={
                "statusCode": 200,
                "body": json.dumps(_successful_current_slate_payload()),
            },
            expected_slate_date="2026-07-21",
        )
        assert claimed is not None
        replay_response = {
            "statusCode": 200,
            "body": json.dumps(_successful_terminal_replay_payload()),
        }
        with pytest.raises(
            RuntimeError,
            match="MLB_COOPERATIVE_REPLAY_COMPLETE_FAILED",
        ):
            handler._complete_cooperative_replay(
                item=claimed,
                owner="different-owner",
                response=replay_response,
            )

        assert delegate_calls == []
        assert table.queue_item["state"] == "CLAIMED"
        assert table.queue_item["claim_owner"] == "actual-outer-lease-owner"
        assert table.item is None
        assert table.delete_calls == []


def test_expired_execution_lease_is_reclaimed_and_released():
    table = FakeLeaseTable()
    now = datetime(2026, 7, 21, 22, 10, tzinfo=timezone.utc)
    table.item = {
        "PK": "MLB_LOCK_EXECUTION#V1",
        "SK": "LEASE",
        "record_type": "mlb_lock_execution_lease_v1",
        "lease_version": "MLB-LOCK-EXECUTION-LEASE-v1",
        "lease_owner": "expired-owner",
        "lease_expires_at_epoch": int(now.timestamp()) - 1,
    }
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler._utc_now = lambda: now

        response = handler.lambda_handler(
            _scheduled_event(),
            FakeContext(request_id="replacement-owner"),
        )

        assert response["statusCode"] == 200
        assert len(delegate_calls) == 1
        assert table.put_calls[0]["Item"]["lease_owner"] == (
            "scheduled:replacement-owner"
        )
        assert len(table.delete_calls) == 1
        assert table.item is None


def test_only_current_owner_can_release_or_delete_reclaimed_lease():
    table = FakeLeaseTable()
    clock = {"now": datetime(2026, 7, 21, 22, 0, tzinfo=timezone.utc)}
    with _load_handler(lease_table=table) as (handler, _, _):
        handler._utc_now = lambda: clock["now"]
        handler._acquire_execution_lease(
            mode="scheduled",
            slate_date_et="2026-07-21",
            owner="old-owner",
        )

        with pytest.raises(handler.LockExecutionLeaseOwnershipConflict):
            handler._release_execution_lease("wrong-owner")
        assert table.item["lease_owner"] == "old-owner"

        clock["now"] += timedelta(seconds=961)
        handler._acquire_execution_lease(
            mode="manual",
            slate_date_et="2026-07-21",
            owner="replacement-owner",
        )
        with pytest.raises(handler.LockExecutionLeaseOwnershipConflict):
            handler._release_execution_lease("old-owner")
        assert table.item["lease_owner"] == "replacement-owner"

        handler._release_execution_lease("replacement-owner")
        assert table.item is None


def test_authenticated_manual_and_scheduled_runs_share_one_lease_after_auth():
    table = FakeLeaseTable()
    now = datetime(2026, 7, 21, 22, 0, tzinfo=timezone.utc)
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler._utc_now = lambda: now
        handler.ADMIN_TOKEN = "test-admin-token"
        handler._acquire_execution_lease(
            mode="scheduled",
            slate_date_et="2026-07-21",
            owner="scheduled-owner",
        )
        put_count = len(table.put_calls)

        unauthorized = handler.lambda_handler(
            _manual_event(token="wrong-token"),
            FakeContext(request_id="unauthorized-manual"),
        )
        assert unauthorized["statusCode"] == 401
        assert len(table.put_calls) == put_count

        response = handler.lambda_handler(
            _manual_event(),
            FakeContext(request_id="authenticated-manual"),
        )
        payload = _body(response)

        assert response["statusCode"] == 409
        assert payload["ok"] is False
        assert payload["error"] == "MLB_LOCK_EXECUTION_ALREADY_RUNNING"
        assert payload["skipped"] is True
        assert payload["retryable"] is True
        assert payload["mutatingRunAttempted"] is False
        assert payload["lockExecutionConcurrency"]["executionMode"] == "manual"
        assert payload["lockExecutionConcurrency"]["overlapSkipped"] is True
        assert (
            payload["lockExecutionConcurrency"]["nextFreshScheduleIsRetry"]
            is False
        )
        assert "scheduled-owner" not in json.dumps(payload)
        assert delegate_calls == []
        assert len(table.put_calls) == put_count + 1
        handler._release_execution_lease("scheduled-owner")


def test_http_api_v2_methods_are_normalized_before_auth_lease_and_delegate():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler.ADMIN_TOKEN = "test-admin-token"

        unauthorized = handler.lambda_handler(
            {
                "requestContext": {"http": {"method": "POST"}},
                "headers": {"x-inqsi-admin-token": "wrong-token"},
                "body": json.dumps({"date": "2026-07-21"}),
            },
            FakeContext(request_id="v2-unauthorized"),
        )
        get_response = handler.lambda_handler(
            {
                "requestContext": {"http": {"method": "GET"}},
                "rawPath": "/v1/mlb/locks/status",
                "queryStringParameters": {"date": "2026-07-21"},
            },
            FakeContext(request_id="v2-get"),
        )
        options_response = handler.lambda_handler(
            {"requestContext": {"http": {"method": "OPTIONS"}}},
            FakeContext(request_id="v2-options"),
        )

        assert unauthorized["statusCode"] == 401
        assert get_response["statusCode"] == 200
        assert options_response["statusCode"] == 200
        assert len(delegate_calls) == 1
        assert delegate_calls[0][0]["httpMethod"] == "GET"
        assert table.put_calls == []
        assert table.delete_calls == []

        post_response = handler.lambda_handler(
            {
                "requestContext": {"http": {"method": "POST"}},
                "headers": {"authorization": "Bearer test-admin-token"},
                "body": json.dumps({"date": "2026-07-21"}),
            },
            FakeContext(request_id="v2-authorized"),
        )

        assert post_response["statusCode"] == 200
        assert len(delegate_calls) == 2
        assert delegate_calls[1][0]["httpMethod"] == "POST"
        assert len(table.put_calls) == 1
        assert len(table.delete_calls) == 1


@pytest.mark.parametrize(
    "event",
    (
        {"requestContext": {"stage": "test"}},
        {
            "httpMethod": "GET",
            "requestContext": {"http": {"method": "POST"}},
        },
    ),
)
def test_malformed_or_conflicting_http_method_fails_closed_without_writes(event):
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        response = handler.lambda_handler(
            event,
            FakeContext(request_id="invalid-http-method"),
        )

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "MLB_LOCK_HTTP_METHOD_INVALID"
        assert delegate_calls == []
        assert table.put_calls == []
        assert table.delete_calls == []


@pytest.mark.parametrize("method", ("PUT", "PATCH", "DELETE"))
@pytest.mark.parametrize("event_version", ("v1", "v2"))
def test_unsupported_authenticated_http_methods_never_delegate_or_lease(
    method, event_version
):
    table = FakeLeaseTable()
    event = {
        "headers": {"authorization": "Bearer test-admin-token"},
        "body": json.dumps({"date": "2026-07-21"}),
    }
    if event_version == "v1":
        event.update(
            {
                "httpMethod": method,
                "requestContext": {"stage": "test"},
            }
        )
    else:
        event["requestContext"] = {"http": {"method": method}}

    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler.ADMIN_TOKEN = "test-admin-token"

        response = handler.lambda_handler(
            event,
            FakeContext(request_id=f"unsupported-{event_version}-{method}"),
        )
        payload = _body(response)

        assert response["statusCode"] == 405
        assert payload["error"] == "MLB_LOCK_HTTP_METHOD_NOT_ALLOWED"
        assert payload["method"] == method
        assert delegate_calls == []
        assert table.put_calls == []
        assert table.delete_calls == []
        assert table.get_calls == []


def test_ambiguous_acquire_commit_then_error_never_runs_unlocked():
    table = FakeLeaseTable()
    table.put_commits_then_error = _client_error(
        "InternalServerError",
        "PutItem",
    )
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="ambiguous-acquire"),
            ),
            "MLB_SCHEDULED_LOCK_PREREQUISITE_FAILED",
        )

        assert delegate_calls == []
        assert table.item is not None
        assert table.item["lease_owner"] == "scheduled:ambiguous-acquire"
        assert table.delete_calls == []


def test_nonconditional_acquire_failure_never_delegates():
    table = FakeLeaseTable()
    table.put_error = _client_error("InternalServerError", "PutItem")
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="failed-acquire"),
            ),
            "MLB_SCHEDULED_LOCK_PREREQUISITE_FAILED",
        )

        assert delegate_calls == []
        assert table.item is None
        assert table.delete_calls == []


def test_delegate_exception_releases_lease_before_reraising_primary():
    table = FakeLeaseTable()
    with _load_handler(
        lease_table=table,
        delegate_error=RuntimeError("delegate exploded"),
    ) as (handler, _, delegate_calls):
        with pytest.raises(RuntimeError, match="delegate exploded"):
            handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="delegate-error"),
            )

        assert len(delegate_calls) == 1
        assert len(table.delete_calls) == 1
        assert table.item is None


def test_delegate_and_release_failure_preserves_primary_error(capsys):
    table = FakeLeaseTable()
    table.delete_error = _client_error("InternalServerError", "DeleteItem")
    with _load_handler(
        lease_table=table,
        delegate_error=RuntimeError("primary delegate error"),
    ) as (handler, _, delegate_calls):
        with pytest.raises(RuntimeError, match="primary delegate error"):
            handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="double-error"),
            )

        assert len(delegate_calls) == 1
        assert table.item is not None
        assert "RELEASE_FAILED_AFTER_PRIMARY_ERROR" in capsys.readouterr().out


def test_successful_manual_delegate_with_release_failure_fails_closed():
    table = FakeLeaseTable()
    table.delete_error = _client_error("InternalServerError", "DeleteItem")
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler.ADMIN_TOKEN = "test-admin-token"

        response = handler.lambda_handler(
            _manual_event(),
            FakeContext(request_id="release-error"),
        )
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_LOCK_EXECUTION_LEASE_RELEASE_FAILED"
        assert payload["errorCode"] == "InternalServerError"
        assert len(delegate_calls) == 1
        assert table.item is not None


def test_failed_manual_response_and_release_failure_preserves_primary(capsys):
    table = FakeLeaseTable()
    table.delete_error = _client_error("InternalServerError", "DeleteItem")
    with _load_handler(
        lease_table=table,
        delegate_payload={
            "ok": False,
            "error": "PRIMARY_LOCK_FAILURE",
            "reason": "stale source",
        },
    ) as (handler, _, delegate_calls):
        handler.ADMIN_TOKEN = "test-admin-token"

        response = handler.lambda_handler(
            _manual_event(),
            FakeContext(request_id="failed-response-double-error"),
        )
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "PRIMARY_LOCK_FAILURE"
        assert payload["reason"] == "stale source"
        assert payload["lockExecutionConcurrency"]["leaseAcquired"] is True
        assert payload["lockExecutionConcurrency"]["leaseReleased"] is False
        assert len(delegate_calls) == 1
        assert table.item is not None
        assert "RELEASE_FAILED_AFTER_FAILED_RESPONSE" in capsys.readouterr().out


def test_get_and_options_are_read_only_and_bypass_execution_lease():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        get_response = handler.lambda_handler(
            {
                "httpMethod": "GET",
                "requestContext": {"stage": "test"},
                "queryStringParameters": {"date": "2026-07-21"},
            },
            FakeContext(request_id="read-only"),
        )
        options_response = handler.lambda_handler(
            {"httpMethod": "OPTIONS", "requestContext": {"stage": "test"}},
            FakeContext(request_id="options"),
        )

        assert get_response["statusCode"] == 200
        assert _body(get_response)["lockExecutionConcurrency"][
            "leaseAcquired"
        ] is False
        assert options_response["statusCode"] == 200
        assert len(delegate_calls) == 1
        assert table.put_calls == []
        assert table.delete_calls == []
        assert table.get_calls == []


def test_runtime_requires_exact_360_second_lease_and_timeout_margin():
    table = FakeLeaseTable()
    with _load_handler(
        lease_table=table,
        lease_seconds=959,
    ) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="short-lease"),
            ),
            "MLB_SCHEDULED_LOCK_PREREQUISITE_FAILED",
        )
        assert handler.PER_GAME_LOCK_STATUS["ok"] is False
        assert delegate_calls == []
        assert table.put_calls == []

    with _load_handler(lease_table=FakeLeaseTable()) as (handler, _, _):
        with pytest.raises(
            RuntimeError,
            match="MLB_LOCK_EXECUTION_LEASE_TIMEOUT_BOUND_FAILED",
        ):
            handler._validate_lease_duration(
                FakeContext(remaining_millis=900_001)
            )


def test_fractional_acquisition_rounds_expiry_up_without_shortening_lease():
    table = FakeLeaseTable()
    clock = {
        "now": datetime(
            2026,
            7,
            21,
            22,
            0,
            0,
            999_999,
            tzinfo=timezone.utc,
        )
    }
    with _load_handler(lease_table=table) as (handler, _, _):
        handler._utc_now = lambda: clock["now"]
        item = handler._acquire_execution_lease(
            mode="scheduled",
            slate_date_et="2026-07-21",
            owner="fractional-owner",
        )
        expires_at = clock["now"] + timedelta(seconds=960)
        expires_epoch = item["lease_expires_at_epoch"]

        assert expires_epoch == math.ceil(expires_at.timestamp())
        assert expires_epoch - clock["now"].timestamp() >= 360

        clock["now"] = datetime.fromtimestamp(
            expires_epoch,
            tz=timezone.utc,
        ) - timedelta(microseconds=1)
        with pytest.raises(handler.LockExecutionLeaseUnavailable):
            handler._acquire_execution_lease(
                mode="manual",
                slate_date_et="2026-07-21",
                owner="too-early-owner",
            )
        assert table.item["lease_owner"] == "fractional-owner"

        clock["now"] = datetime.fromtimestamp(expires_epoch, tz=timezone.utc)
        replacement = handler._acquire_execution_lease(
            mode="manual",
            slate_date_et="2026-07-21",
            owner="on-time-owner",
        )
        assert replacement["lease_owner"] == "on-time-owner"



def test_eventbridge_owner_checkpoints_process_then_verify_then_completes():
    table = FakeLeaseTable()
    runner_calls = []
    chunk_version = (
        COOPERATIVE_REPAIR.COOPERATIVE_TERMINAL_CHUNK_VERSION
    )

    def payload_for(event, context):
        del context
        assert event.get("run") == "daily_lock_check"
        return _successful_current_slate_payload()

    with _load_handler(
        lease_table=table,
        delegate_payload=payload_for,
    ) as (handler, _, delegate_calls):
        def checkpoint_for(
            *,
            slate_date,
            request_epoch,
            request_id,
            verification_complete,
        ):
            progress = _complete_terminal_checkpoint(
                slate_date=slate_date,
                request_epoch=request_epoch,
                request_id=request_id,
                game_count=1,
            )
            if not verification_complete:
                progress["verificationIndex"] = 0
                progress["verifiedGameCount"] = 0
                progress["verificationComplete"] = False
                progress["attemptCount"] = 1
                progress["lastAttempt"] = {
                    "status": "TERMINAL_CHECKPOINT_READY",
                    "stage": "PROCESS_CHECKPOINT_READY",
                    "atUtc": "2026-07-21T22:10:00+00:00",
                    "phase": "VERIFY",
                    "gameIndex": 0,
                    "gameIdentity": "provider:game-1",
                    "durableIdentity": "provider:game-1",
                }
                progress["checkpointFingerprint"] = (
                    COOPERATIVE_REPAIR._cooperative_terminal_checkpoint_fingerprint(
                        progress
                    )
                )
            return progress

        def chunk_runner(
            *,
            slate_date,
            request_epoch,
            request_id,
            checkpoint,
            context,
        ):
            runner_calls.append(
                (
                    slate_date,
                    request_epoch,
                    request_id,
                    copy.deepcopy(checkpoint),
                    context.aws_request_id,
                )
            )
            if checkpoint is None:
                next_progress = checkpoint_for(
                    slate_date=slate_date,
                    request_epoch=request_epoch,
                    request_id=request_id,
                    verification_complete=False,
                )
                return {
                    "ok": True,
                    "complete": False,
                    "deferred": False,
                    "stage": "PROCESS_CHECKPOINT_READY",
                    "remainingSeconds": 820,
                    "checkpointWriteAllowed": True,
                    "checkpoint": next_progress,
                    "terminalChunkVersion": chunk_version,
                    "terminalWrittenThisInvocation": True,
                    "postStartPredictionCreationAllowed": False,
                    "immutablePredictionRewriteAllowed": False,
                    "productionAuthorityChanged": False,
                }
            if checkpoint["verificationComplete"] is not True:
                next_progress = checkpoint_for(
                    slate_date=slate_date,
                    request_epoch=request_epoch,
                    request_id=request_id,
                    verification_complete=True,
                )
                return {
                    "ok": True,
                    "complete": False,
                    "deferred": False,
                    "stage": "COMPLETE_READY",
                    "remainingSeconds": 830,
                    "checkpointWriteAllowed": True,
                    "checkpoint": next_progress,
                    "terminalChunkVersion": chunk_version,
                    "terminalWrittenThisInvocation": False,
                    "postStartPredictionCreationAllowed": False,
                    "immutablePredictionRewriteAllowed": False,
                    "productionAuthorityChanged": False,
                }
            return _fixture_completion_chunk_result(
                module=handler.mlb_daily_pick_lock,
                patch=handler.mlb_daily_per_game_lock_patch,
                slate_date=slate_date,
                request_epoch=request_epoch,
                request_id=request_id,
                context=context,
                game_count=1,
                checkpoint=checkpoint,
            )

        handler.mlb_daily_pick_lock.run_cooperative_terminal_chunk = (
            chunk_runner
        )
        handler.lambda_handler(_cooperative_event(), FakeContext())

        first = _body(
            handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id="process-owner",
                    remaining_millis=900_000,
                ),
            )
        )
        first_status = first["cooperativeTerminalReplayOwnerExecution"]
        assert first_status["state"] == "QUEUED"
        assert first_status["historicalReplayCompleted"] is False
        assert first_status["terminalWrittenThisInvocation"] is True
        assert table.queue_item["terminal_replay_progress"][
            "verificationIndex"
        ] == 0

        second = _body(
            handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id="verify-owner",
                    remaining_millis=900_000,
                ),
            )
        )
        second_status = second["cooperativeTerminalReplayOwnerExecution"]
        assert second_status["state"] == "QUEUED"
        assert second_status["historicalReplayCompleted"] is False
        assert second_status["terminalWrittenThisInvocation"] is False
        assert table.queue_item["terminal_replay_progress"][
            "verificationComplete"
        ] is True

        third = _body(
            handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id="atomic-completer",
                    remaining_millis=900_000,
                ),
            )
        )
        third_status = third["cooperativeTerminalReplayOwnerExecution"]
        assert third_status["state"] == "COMPLETED"
        assert third_status["historicalReplayChunked"] is True
        assert third_status["historicalReplayCompleted"] is True
        assert third_status[
            "completionMutationLeaseHeldThroughQueueCas"
        ] is True
        assert table.queue_item["state"] == "COMPLETED"
        receipt = table.queue_item["replay_receipt"]
        assert receipt["durableTerminalVerificationComplete"] is True
        assert receipt["atomicDurableProofRequired"] is True
        assert receipt["atomicDurableItemCount"] == 2
        assert len(receipt["checkpointFingerprint"]) == 64
        assert len(receipt["manifestFingerprint"]) == 64
        reconciliation = receipt["missedLockTerminalReconciliation"]
        assert reconciliation["verifiedGameCount"] == 1
        assert reconciliation["remainingMissedCount"] == 0
        assert reconciliation["unresolved"] == []
        assert [call[0]["run"] for call in delegate_calls] == [
            "daily_lock_check",
            "daily_lock_check",
            "daily_lock_check",
        ]
        assert len(runner_calls) == 3
        assert runner_calls[0][3] is None
        assert runner_calls[1][3]["verificationIndex"] == 0
        assert runner_calls[2][3]["verificationComplete"] is True
        release_calls = (
            handler.mlb_daily_pick_lock._completion_release_calls
        )
        assert len(release_calls) == 1
        assert release_calls[0]["slateDateEt"] == "2026-07-20"
        serialized = json.dumps(third, sort_keys=True)
        assert "_completionLease" not in serialized
        assert "_atomicCompletionProof" not in serialized
        assert "fixture-v2-owner:atomic-completer" not in serialized


def test_failed_chunk_persists_redacted_stage_and_requeues_before_raising(capsys):
    table = FakeLeaseTable()
    chunk_version = (
        "MLB-COOPERATIVE-TERMINAL-CHUNK-v3-"
        "bounded-proof-lease-handoff"
    )
    checkpoint = {
        "version": chunk_version,
        "requestEpoch": None,
        "requestId": None,
        "slateDateEt": "2026-07-20",
        "manifestFingerprint": "manifest-fingerprint",
        "manifestGameCount": 1,
        "phase": "PROCESS",
        "nextGameIndex": 0,
        "processedGameCount": 0,
        "terminalCount": 0,
        "canonicalCount": 0,
        "noPredictionDataCount": 0,
        "reconciledCount": 0,
        "processedGames": [],
        "verificationIndex": 0,
        "verifiedGameCount": 0,
        "verificationComplete": False,
        "attemptCount": 1,
        "lastAttempt": {
            "status": "FAILED_CLOSED",
            "stage": "PROVE_PRELOCK_ABSENCE",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "gameIndex": 0,
            "gameIdentity": "provider:game-1",
            "errorCode": "PRELOCK_CANDIDATE_REQUIRES_REVIEW",
        },
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "productionAuthorityChanged": False,
    }

    with _load_handler(
        lease_table=table,
        delegate_payload=_successful_current_slate_payload(),
    ) as (handler, _, delegate_calls):
        def failed_runner(**kwargs):
            bound_checkpoint = copy.deepcopy(checkpoint)
            bound_checkpoint["requestEpoch"] = int(
                kwargs["request_epoch"]
            )
            bound_checkpoint["requestId"] = str(kwargs["request_id"])
            checkpoint.update(copy.deepcopy(bound_checkpoint))
            return {
                "ok": False,
                "complete": False,
                "deferred": False,
                "stage": "PROVE_PRELOCK_ABSENCE",
                "errorCode": "PRELOCK_CANDIDATE_REQUIRES_REVIEW",
                "remainingSeconds": 700,
                "checkpointWriteAllowed": True,
                "checkpoint": bound_checkpoint,
                "terminalChunkVersion": chunk_version,
                "postStartPredictionCreationAllowed": False,
                "immutablePredictionRewriteAllowed": False,
                "productionAuthorityChanged": False,
            }

        handler.mlb_daily_pick_lock.run_cooperative_terminal_chunk = (
            failed_runner
        )
        handler.lambda_handler(_cooperative_event(), FakeContext())
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id="failed-chunk-owner",
                    remaining_millis=900_000,
                ),
            ),
            "MLB_COOPERATIVE_TERMINAL_CHUNK_FAILED_CLOSED",
        )

        assert [call[0]["run"] for call in delegate_calls] == [
            "daily_lock_check"
        ]
        assert table.queue_item["state"] == "QUEUED"
        assert table.queue_item["terminal_replay_progress"] == checkpoint
        assert "claim_owner" not in table.queue_item
        assert table.item is None
        logs = capsys.readouterr().out
        assert "PROVE_PRELOCK_ABSENCE" in logs
        assert "PRELOCK_CANDIDATE_REQUIRES_REVIEW" in logs
        assert "claim_owner" not in logs


@pytest.mark.parametrize("runner_mode", ["missing", "noncallable", "wrong_version"])
def test_chunk_runner_prerequisite_failure_requeues_without_legacy_delegate(
    runner_mode,
):
    table = FakeLeaseTable()
    with _load_handler(
        lease_table=table,
        delegate_payload=_successful_current_slate_payload(),
    ) as (handler, _, delegate_calls):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        if runner_mode == "missing":
            delattr(
                handler.mlb_daily_pick_lock,
                "run_cooperative_terminal_chunk",
            )
        elif runner_mode == "noncallable":
            handler.mlb_daily_pick_lock.run_cooperative_terminal_chunk = None
        else:
            handler.mlb_daily_pick_lock.MLB_COOPERATIVE_TERMINAL_CHUNK_VERSION = (
                "wrong-chunk-version"
            )

        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id=f"runner-{runner_mode}",
                    remaining_millis=900_000,
                ),
            ),
            "MLB_COOPERATIVE_TERMINAL_CHUNK_RUNNER_NOT_READY",
        )

        assert [call[0]["run"] for call in delegate_calls] == [
            "daily_lock_check"
        ]
        assert table.queue_item["state"] == "QUEUED"
        assert "claim_owner" not in table.queue_item
        assert table.queue_item.get("terminal_replay_progress") is None
        assert table.item is None


def test_production_source_has_no_whole_slate_chunk_fallback():
    source = HANDLER.read_text()
    assert "Compatibility fallback for injected/test adapters" not in source
    assert "mlb_daily_pick_lock.lambda_handler(\n                        replay_event" not in source
    assert "MLB_COOPERATIVE_TERMINAL_CHUNK_RUNNER_NOT_READY" in source


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["missedLockTerminalReconciliation"][
            "progressAfter"
        ].__setitem__("verifiedGameCount", 14),
        lambda payload: payload["missedLockTerminalReconciliation"][
            "progressAfter"
        ].__setitem__("canonicalCount", 1),
        lambda payload: payload.__setitem__(
            "durableTerminalVerificationComplete", False
        ),
        lambda payload: payload.__setitem__(
            "atomicDurableProofRequired", False
        ),
        lambda payload: payload.__setitem__(
            "completionMutationLeaseRequired", False
        ),
        lambda payload: payload[
            "missedLockTerminalReconciliation"
        ].__setitem__("completionMutationLeaseRequired", False),
        lambda payload: payload.__setitem__(
            "checkpointFingerprint", "forged"
        ),
    ],
)
def test_terminal_receipt_rejects_inconsistent_verification_contract(
    mutate,
):
    with _load_handler() as (handler, _, _):
        payload = _successful_terminal_replay_payload()
        mutate(payload)
        with pytest.raises(
            RuntimeError,
            match="MLB_COOPERATIVE_REPLAY_",
        ):
            handler._terminal_replay_receipt(
                {
                    "statusCode": 200,
                    "body": json.dumps(payload),
                },
                "2026-07-20",
            )


def test_checkpoint_cas_rejects_same_slate_replacement_request():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, _):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        claimed, _ = handler._claim_cooperative_replay(
            owner="checkpoint-owner",
            context=FakeContext(remaining_millis=900_000),
            current_slate_response={
                "statusCode": 200,
                "body": json.dumps(_successful_current_slate_payload()),
            },
            expected_slate_date="2026-07-21",
        )
        assert claimed is not None
        progress = {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "slateDateEt": "2026-07-20",
            "requestEpoch": claimed["requested_at_epoch"],
            "requestId": claimed["request_id"],
            "manifestGameCount": 1,
            "phase": "PROCESS",
            "nextGameIndex": 0,
            "processedGameCount": 0,
            "terminalCount": 0,
            "canonicalCount": 0,
            "noPredictionDataCount": 0,
            "reconciledCount": 0,
            "verificationIndex": 0,
            "verifiedGameCount": 0,
            "verificationComplete": False,
            "attemptCount": 1,
            "lastAttempt": {
                "status": "FAILED_CLOSED",
                "stage": "TEST",
                "atUtc": "2026-07-21T22:10:00+00:00",
            },
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "productionAuthorityChanged": False,
        }
        table.queue_item["requested_at_epoch"] += 1
        table.queue_item["request_id"] = "replacement-request"

        with pytest.raises(
            RuntimeError,
            match="MLB_COOPERATIVE_TERMINAL_CHECKPOINT_FAILED",
        ):
            handler._checkpoint_cooperative_replay(
                item=claimed,
                owner="checkpoint-owner",
                progress=progress,
                failed=True,
            )


def test_requeue_cas_rejects_same_slate_replacement_request():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, _):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        claimed, _ = handler._claim_cooperative_replay(
            owner="stale-requeue-owner",
            context=FakeContext(remaining_millis=900_000),
            current_slate_response={
                "statusCode": 200,
                "body": json.dumps(_successful_current_slate_payload()),
            },
            expected_slate_date="2026-07-21",
        )
        assert claimed is not None
        table.queue_item["requested_at_epoch"] += 1
        table.queue_item["request_id"] = "replacement-request"

        assert handler._requeue_cooperative_replay(
            claimed, "stale-requeue-owner"
        ) is False
        assert table.queue_item["state"] == "CLAIMED"
        assert table.queue_item["request_id"] == "replacement-request"


def _claimed_completion_fixture(handler, table, *, owner="completion-owner"):
    handler.lambda_handler(_cooperative_event(), FakeContext())
    claimed, _ = handler._claim_cooperative_replay(
        owner=owner,
        context=FakeContext(remaining_millis=900_000),
        current_slate_response={
            "statusCode": 200,
            "body": json.dumps(_successful_current_slate_payload()),
        },
        expected_slate_date="2026-07-21",
    )
    assert claimed is not None
    checkpoint = _complete_terminal_checkpoint(
        slate_date="2026-07-20",
        request_epoch=claimed["requested_at_epoch"],
        request_id=claimed["request_id"],
        game_count=1,
    )
    claimed["terminal_replay_progress"] = copy.deepcopy(checkpoint)
    table.queue_item["terminal_replay_progress"] = copy.deepcopy(
        checkpoint
    )
    response = {
        "statusCode": 200,
        "body": json.dumps(
            _successful_terminal_replay_payload(
                "2026-07-20",
                game_count=1,
                checkpoint=checkpoint,
            )
        ),
    }
    return claimed, checkpoint, response


def test_completion_ambiguity_accepts_only_exact_committed_ack():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, _):
        claimed, checkpoint, response = _claimed_completion_fixture(
            handler, table
        )

        def committed_then_error(table_, kwargs):
            del table_
            values = kwargs.get("ExpressionAttributeValues") or {}
            if ":receipt" in values:
                raise _client_error("InternalServerError", "UpdateItem")

        table.update_after_commit = committed_then_error
        completed = handler._complete_cooperative_replay(
            item=claimed,
            owner="completion-owner",
            response=response,
        )

        assert completed["state"] == "COMPLETED"
        assert table.queue_item["terminal_replay_progress"] == checkpoint
        assert table.queue_item["request_id"] == claimed["request_id"]
        receipt = table.queue_item["replay_receipt"]
        assert receipt["ok"] is True
        assert receipt["checkpointFingerprint"] == (
            checkpoint["checkpointFingerprint"]
        )
        assert receipt["manifestFingerprint"] == (
            checkpoint["manifestFingerprint"]
        )


def test_completion_ambiguity_rejects_same_slate_replacement_completed_row():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, _):
        claimed, _, response = _claimed_completion_fixture(
            handler, table
        )

        def replace_after_commit(table_, kwargs):
            values = kwargs.get("ExpressionAttributeValues") or {}
            if ":receipt" not in values:
                return
            replacement = copy.deepcopy(table_.queue_item)
            replacement["requested_at_epoch"] = (
                int(replacement["requested_at_epoch"]) + 1
            )
            replacement["request_id"] = "replacement-request"
            replacement["terminal_replay_progress"] = {
                "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
                "replacement": True,
            }
            replacement["replay_receipt"] = {
                "ok": True,
                "replacement": True,
            }
            table_.queue_item = replacement
            raise _client_error("InternalServerError", "UpdateItem")

        table.update_after_commit = replace_after_commit
        with pytest.raises(
            RuntimeError,
            match="MLB_COOPERATIVE_REPLAY_COMPLETE_FAILED",
        ):
            handler._complete_cooperative_replay(
                item=claimed,
                owner="completion-owner",
                response=response,
            )

        assert table.queue_item["state"] == "COMPLETED"
        assert table.queue_item["request_id"] == "replacement-request"
        assert table.queue_item["replay_receipt"]["replacement"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda progress: progress.__setitem__("phase", "PROCESS"),
        lambda progress: progress.__setitem__("processedGames", []),
        lambda progress: progress["processedGames"][0].__setitem__(
            "durableIdentity", "official:1"
        ),
        lambda progress: progress.__setitem__("verifiedGameCount", 0),
    ],
)
def test_completion_boundary_rejects_structurally_forged_checkpoint(mutate):
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, _):
        claimed, checkpoint, response = _claimed_completion_fixture(
            handler, table
        )
        mutate(checkpoint)
        checkpoint["checkpointFingerprint"] = (
            COOPERATIVE_REPAIR._cooperative_terminal_checkpoint_fingerprint(checkpoint)
        )
        claimed["terminal_replay_progress"] = copy.deepcopy(checkpoint)
        table.queue_item["terminal_replay_progress"] = copy.deepcopy(
            checkpoint
        )

        with pytest.raises(
            RuntimeError,
            match="MLB_COOPERATIVE_REPLAY_COMPLETE_PROGRESS_INVALID",
        ):
            handler._complete_cooperative_replay(
                item=claimed,
                owner="completion-owner",
                response=response,
            )

        assert table.queue_item["state"] == "CLAIMED"
        assert "replay_receipt" not in table.queue_item


def test_completion_cas_failure_releases_handoff_before_outer_lease():
    table = FakeLeaseTable()
    with _load_handler(
        lease_table=table,
        delegate_payload=_successful_current_slate_payload(),
    ) as (handler, _, _):
        handler.lambda_handler(_cooperative_event(), FakeContext())

        def undo_completion_and_raise(table_, kwargs):
            values = kwargs.get("ExpressionAttributeValues") or {}
            if ":receipt" not in values:
                return
            queue = table_.queue_item
            queue["state"] = values[":claimed"]
            queue["claim_owner"] = values[":owner"]
            queue["terminal_replay_progress"] = copy.deepcopy(
                values[":expected_progress"]
            )
            queue.pop("replay_receipt", None)
            raise _client_error("InternalServerError", "UpdateItem")

        table.update_after_commit = undo_completion_and_raise
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id="completion-cas-failure",
                    remaining_millis=900_000,
                ),
            ),
            "MLB_COOPERATIVE_REPLAY_COMPLETE_FAILED",
        )

        assert table.queue_item["state"] == "QUEUED"
        assert (
            len(
                handler.mlb_daily_pick_lock._completion_release_calls
            )
            == 1
        )
        assert table.item is None


@pytest.mark.parametrize(
    ("mode", "release_count"),
    [("missing", 0), ("expired", 1)],
)
def test_invalid_completion_handoff_blocks_queue_completion_and_cleans_up(
    mode,
    release_count,
):
    table = FakeLeaseTable()
    with _load_handler(
        lease_table=table,
        delegate_payload=_successful_current_slate_payload(),
    ) as (handler, _, _):
        handler.lambda_handler(_cooperative_event(), FakeContext())
        original_runner = (
            handler.mlb_daily_pick_lock.run_cooperative_terminal_chunk
        )

        def invalid_runner(**kwargs):
            result = original_runner(**kwargs)
            if mode == "missing":
                result.pop("_completionLease", None)
            else:
                result["_completionLease"]["expiresAtEpoch"] = 1
            return result

        handler.mlb_daily_pick_lock.run_cooperative_terminal_chunk = (
            invalid_runner
        )
        expected = (
            "MLB_COOPERATIVE_TERMINAL_CHUNK_COMPLETE_UNHEALTHY"
            if mode == "missing"
            else "COOPERATIVE_TERMINAL_COMPLETION_LEASE_INVALID"
        )
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id=f"invalid-handoff-{mode}",
                    remaining_millis=900_000,
                ),
            ),
            expected,
        )

        assert table.queue_item["state"] == "QUEUED"
        assert len(
            handler.mlb_daily_pick_lock._completion_release_calls
        ) == release_count
        assert table.item is None

def test_dynamodb_decimal_game_index_serializes_after_execution_lease_release():
    table = FakeLeaseTable()

    with _load_handler(
        lease_table=table,
        delegate_payload=_successful_current_slate_payload(),
    ) as (handler, _, _):
        checkpoint = _complete_terminal_checkpoint(
            slate_date="2026-07-20",
            request_epoch=1,
            request_id="placeholder",
            game_count=1,
        )
        checkpoint.update(
            {
                "phase": "VERIFY",
                "verificationIndex": 0,
                "verifiedGameCount": 0,
                "verificationComplete": False,
                "attemptCount": 1,
                "lastAttempt": {
                    "status": "TERMINAL_CHECKPOINT_READY",
                    "stage": "PROCESS_CHECKPOINT_READY",
                    "atUtc": "2026-07-21T22:10:00+00:00",
                    "phase": "VERIFY",
                    "gameIndex": 0,
                    "gameIdentity": "provider:game-1",
                    "durableIdentity": "provider:game-1",
                },
            }
        )

        def ddb_numbers(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, int):
                return Decimal(value)
            if isinstance(value, dict):
                return {
                    key: ddb_numbers(nested)
                    for key, nested in value.items()
                }
            if isinstance(value, list):
                return [ddb_numbers(nested) for nested in value]
            return value

        original_update_item = table.update_item

        def update_item_with_ddb_return_values(**kwargs):
            result = original_update_item(**kwargs)
            values = kwargs.get("ExpressionAttributeValues") or {}
            if ":progress" in values and "Attributes" in result:
                result["Attributes"] = ddb_numbers(result["Attributes"])
            return result

        table.update_item = update_item_with_ddb_return_values

        def chunk_runner(
            *,
            slate_date,
            request_epoch,
            request_id,
            checkpoint,
            context,
        ):
            del checkpoint, context
            bound = copy.deepcopy(checkpoint_template)
            bound["slateDateEt"] = slate_date
            bound["requestEpoch"] = int(request_epoch)
            bound["requestId"] = str(request_id)
            bound["checkpointFingerprint"] = (
                COOPERATIVE_REPAIR._cooperative_terminal_checkpoint_fingerprint(
                    bound
                )
            )
            return {
                "ok": True,
                "complete": False,
                "deferred": False,
                "stage": "PROCESS_CHECKPOINT_READY",
                "remainingSeconds": 820,
                "checkpointWriteAllowed": True,
                "checkpoint": bound,
                "terminalChunkVersion": (
                    handler.COOPERATIVE_TERMINAL_CHUNK_VERSION
                ),
                "terminalWrittenThisInvocation": True,
                "postStartPredictionCreationAllowed": False,
                "immutablePredictionRewriteAllowed": False,
                "productionAuthorityChanged": False,
            }

        checkpoint_template = checkpoint
        handler.mlb_daily_pick_lock.run_cooperative_terminal_chunk = (
            chunk_runner
        )
        handler.lambda_handler(_cooperative_event(), FakeContext())

        response = handler.lambda_handler(
            _scheduled_event(),
            FakeContext(
                request_id="decimal-status-owner",
                remaining_millis=900_000,
            ),
        )
        payload = _body(response)
        owner = payload["cooperativeTerminalReplayOwnerExecution"]
        last_attempt = owner["terminalChunkProgress"]["lastAttempt"]

        assert last_attempt["gameIndex"] == 0
        assert type(last_attempt["gameIndex"]) is int
        assert payload["lockExecutionConcurrency"]["leaseReleased"] is True
        assert table.queue_item["state"] == "QUEUED"
        assert table.item is None
        assert len(table.delete_calls) == 1
        json.dumps(response, sort_keys=True)


@pytest.mark.parametrize(
    "last_attempt",
    (
        None,
        [],
        {},
        ["not-an-object"],
        {
            "status": {"nested": "FAILED_CLOSED"},
            "stage": "READ_DURABLE_TERMINAL",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "errorCode": "NESTED_STATUS",
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": Decimal("0.5"),
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": True,
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": -1,
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": Decimal("NaN"),
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": Decimal("Infinity"),
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": 0.0,
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": "0",
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": Decimal("1"),
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": {"nested": 0},
        },
        {
            "status": "ARBITRARY_STATUS",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "ARBITRARY_STAGE",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "PROCESS",
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00",
            "phase": "VERIFY",
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T23:10:00+01:00",
            "phase": "VERIFY",
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00Z",
            "phase": "VERIFY",
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIdentity": "x" * 201,
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "unexpected": {"nested": True},
        },
        {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "phase": "VERIFY",
        },
    ),
)
def test_cooperative_public_progress_fails_closed_on_corrupt_last_attempt(
    last_attempt,
):
    with _load_handler() as (handler, _, _):
        progress = _complete_terminal_checkpoint(
            slate_date="2026-07-20",
            game_count=1,
        )
        progress["lastAttempt"] = copy.deepcopy(last_attempt)
        item = {
            "slate_date_et": "2026-07-20",
            "terminal_replay_progress": progress,
        }

        public = handler._cooperative_terminal_progress_public(item)

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }
        json.dumps(public, sort_keys=True)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("manifestGameCount", Decimal("1.5")),
        ("manifestGameCount", True),
        ("manifestGameCount", -1),
        ("manifestGameCount", 1.0),
        ("manifestGameCount", "1"),
        ("manifestGameCount", Decimal("16")),
        ("nextGameIndex", Decimal("0.5")),
        ("attemptCount", Decimal("NaN")),
        ("attemptCount", Decimal("Infinity")),
        ("attemptCount", Decimal("1e100000")),
    ),
)
def test_cooperative_public_progress_rejects_non_integral_or_unbounded_counts(
    field,
    value,
):
    with _load_handler() as (handler, _, _):
        progress = _complete_terminal_checkpoint(
            slate_date="2026-07-20",
            game_count=1,
        )
        progress[field] = value

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("manifestGameCount", 0),
        ("nextGameIndex", 0),
        ("processedGameCount", 0),
        ("terminalCount", 0),
        ("canonicalCount", 1),
        ("reconciledCount", 2),
        ("verificationIndex", 0),
        ("verifiedGameCount", 0),
        ("attemptCount", 0),
        ("attemptCount", 1),
        ("phase", "PROCESS"),
        ("verificationComplete", False),
    ),
)
def test_cooperative_public_progress_rejects_impossible_relational_state(
    field,
    value,
):
    with _load_handler() as (handler, _, _):
        progress = _complete_terminal_checkpoint(
            slate_date="2026-07-20",
            game_count=1,
        )
        progress[field] = value

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }


def _producer_attempt_public_progress(state):
    progress = _complete_terminal_checkpoint(
        slate_date="2026-07-20",
        game_count=2,
    )
    if state == "PROCESS_CURRENT":
        progress.update(
            {
                "phase": "PROCESS",
                "nextGameIndex": 0,
                "processedGameCount": 0,
                "terminalCount": 0,
                "canonicalCount": 0,
                "noPredictionDataCount": 0,
                "reconciledCount": 0,
                "verificationIndex": 0,
                "verifiedGameCount": 0,
                "verificationComplete": False,
            }
        )
        game_index = 0
    elif state == "PROCESS_PREVIOUS":
        progress.update(
            {
                "phase": "PROCESS",
                "nextGameIndex": 1,
                "processedGameCount": 1,
                "terminalCount": 1,
                "canonicalCount": 0,
                "noPredictionDataCount": 1,
                "reconciledCount": 1,
                "verificationIndex": 0,
                "verifiedGameCount": 0,
                "verificationComplete": False,
            }
        )
        game_index = 0
    elif state == "VERIFY_TRANSITION":
        progress.update(
            {
                "verificationIndex": 0,
                "verifiedGameCount": 0,
                "verificationComplete": False,
            }
        )
        game_index = 1
    elif state == "VERIFY_CURRENT":
        progress.update(
            {
                "verificationIndex": 0,
                "verifiedGameCount": 0,
                "verificationComplete": False,
            }
        )
        game_index = 0
    elif state == "VERIFY_PREVIOUS":
        progress.update(
            {
                "verificationIndex": 1,
                "verifiedGameCount": 1,
                "verificationComplete": False,
            }
        )
        game_index = 0
    elif state == "VERIFY_COMPLETE_PREVIOUS":
        game_index = 1
    elif state == "VERIFY_COMPLETION":
        game_index = 2
    else:
        raise AssertionError(f"unknown producer attempt state: {state}")
    progress["attemptCount"] = max(
        1,
        progress["nextGameIndex"] + progress["verificationIndex"],
    )
    return progress, game_index


@pytest.mark.parametrize(
    (
        "status",
        "stage",
        "state",
        "identity_present",
        "durable_present",
        "error_code",
    ),
    (
        (
            "TERMINAL_CHECKPOINT_READY",
            "PROCESS_CHECKPOINT_READY",
            "PROCESS_PREVIOUS",
            True,
            True,
            None,
        ),
        (
            "TERMINAL_CHECKPOINT_READY",
            "PROCESS_CHECKPOINT_READY",
            "VERIFY_TRANSITION",
            True,
            True,
            None,
        ),
        (
            "DURABLE_TERMINAL_VERIFIED",
            "VERIFICATION_CHECKPOINT_READY",
            "VERIFY_PREVIOUS",
            True,
            True,
            None,
        ),
        (
            "DURABLE_TERMINAL_VERIFIED",
            "COMPLETE_READY",
            "VERIFY_COMPLETE_PREVIOUS",
            True,
            True,
            None,
        ),
        (
            "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "WRITE_BUDGET",
            "PROCESS_CURRENT",
            True,
            False,
            None,
        ),
        (
            "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "GAME_BUDGET",
            "PROCESS_CURRENT",
            True,
            False,
            None,
        ),
        (
            "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "GAME_BUDGET",
            "VERIFY_CURRENT",
            True,
            False,
            None,
        ),
        (
            "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "ATOMIC_COMPLETION_PROOF",
            "VERIFY_COMPLETION",
            False,
            False,
            None,
        ),
        (
            "DEFERRED_MUTATION_LEASE_CONTENDED",
            "MUTATION_LEASE_CONTENDED",
            "PROCESS_CURRENT",
            True,
            False,
            "WRITER_LEASE_CONTENDED",
        ),
        (
            "DEFERRED_MUTATION_LEASE_CONTENDED",
            "MUTATION_LEASE_CONTENDED",
            "VERIFY_CURRENT",
            True,
            False,
            "WRITER_LEASE_CONTENDED",
        ),
        (
            "DEFERRED_MUTATION_LEASE_CONTENDED",
            "MUTATION_LEASE_CONTENDED",
            "VERIFY_COMPLETION",
            False,
            False,
            "WRITER_LEASE_CONTENDED",
        ),
        (
            "FAILED_CLOSED",
            "READ_DURABLE_TERMINAL",
            "PROCESS_CURRENT",
            True,
            True,
            "DURABLE_READ_INVALID",
        ),
        (
            "FAILED_CLOSED",
            "VERIFY_DURABLE_TERMINAL",
            "VERIFY_CURRENT",
            True,
            False,
            "DURABLE_VERIFICATION_MISMATCH",
        ),
        (
            "FAILED_CLOSED",
            "PROVE_PRELOCK_ABSENCE",
            "PROCESS_CURRENT",
            True,
            False,
            "PRELOCK_CANDIDATE_REQUIRES_REVIEW",
        ),
        (
            "FAILED_CLOSED",
            "BIND_MANIFEST_AUTHORITY",
            "PROCESS_CURRENT",
            True,
            False,
            "MANIFEST_AUTHORITY_INVALID",
        ),
        (
            "FAILED_CLOSED",
            "BIND_MANIFEST_AUTHORITY",
            "VERIFY_CURRENT",
            True,
            False,
            "COOPERATIVE_TERMINAL_CHUNK_WRITER_LEASE_NOT_READY",
        ),
        (
            "FAILED_CLOSED",
            "WRITE_NO_PREDICTION_TERMINAL",
            "PROCESS_CURRENT",
            True,
            True,
            "TERMINAL_WRITE_FAILED",
        ),
        (
            "FAILED_CLOSED",
            "READBACK_NO_PREDICTION_TERMINAL",
            "PROCESS_CURRENT",
            True,
            True,
            "OUTCOME_READBACK_INVALID",
        ),
        (
            "FAILED_CLOSED",
            "VERIFY_GAME_STARTED",
            "PROCESS_CURRENT",
            True,
            False,
            "GAME_NOT_STARTED",
        ),
        (
            "FAILED_CLOSED",
            "ACQUIRE_MUTATION_LEASE",
            "PROCESS_CURRENT",
            True,
            False,
            "WRITER_LEASE_NOT_READY",
        ),
        (
            "FAILED_CLOSED",
            "ACQUIRE_MUTATION_LEASE",
            "VERIFY_CURRENT",
            True,
            False,
            "WRITER_LEASE_NOT_READY",
        ),
        (
            "FAILED_CLOSED",
            "ATOMIC_COMPLETION_PROOF",
            "VERIFY_COMPLETION",
            False,
            False,
            "ATOMIC_DURABLE_PROOF_INVALID",
        ),
        (
            "FAILED_CLOSED",
            "RELEASE_MUTATION_LEASE",
            "PROCESS_CURRENT",
            True,
            False,
            "MUTATION_LEASE_RELEASE_FAILED",
        ),
        (
            "FAILED_CLOSED",
            "RELEASE_MUTATION_LEASE",
            "VERIFY_CURRENT",
            True,
            False,
            "MUTATION_LEASE_RELEASE_FAILED",
        ),
        (
            "FAILED_CLOSED",
            "RELEASE_MUTATION_LEASE",
            "VERIFY_COMPLETION",
            False,
            False,
            "MUTATION_LEASE_RELEASE_FAILED",
        ),
    ),
)
def test_cooperative_public_progress_accepts_every_producer_attempt_shape(
    status,
    stage,
    state,
    identity_present,
    durable_present,
    error_code,
):
    with _load_handler() as (handler, _, _):
        progress, game_index = _producer_attempt_public_progress(state)
        attempt = {
            "status": status,
            "stage": stage,
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": progress["phase"],
            "gameIndex": Decimal(game_index),
        }
        if identity_present:
            attempt["gameIdentity"] = f"provider:game-{game_index + 1}"
        if durable_present:
            attempt["durableIdentity"] = f"provider:game-{game_index + 1}"
        if error_code is not None:
            attempt["errorCode"] = error_code
        progress["lastAttempt"] = attempt

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public["valid"] is True
        assert public["lastAttempt"]["gameIndex"] == game_index
        assert type(public["lastAttempt"]["gameIndex"]) is int
        json.dumps(public, sort_keys=True)


@pytest.mark.parametrize(
    "missing_field",
    ("gameIndex", "gameIdentity", "durableIdentity"),
)
def test_cooperative_public_progress_rejects_missing_success_attempt_field(
    missing_field,
):
    with _load_handler() as (handler, _, _):
        progress, game_index = _producer_attempt_public_progress(
            "VERIFY_TRANSITION"
        )
        progress["lastAttempt"] = {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": game_index,
            "gameIdentity": "provider:game-2",
            "durableIdentity": "provider:game-2",
        }
        progress["lastAttempt"].pop(missing_field)

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }


@pytest.mark.parametrize("identity_field", ("gameIdentity", "durableIdentity"))
def test_cooperative_public_progress_rejects_completion_identity(
    identity_field,
):
    with _load_handler() as (handler, _, _):
        progress, game_index = _producer_attempt_public_progress(
            "VERIFY_COMPLETION"
        )
        progress["lastAttempt"] = {
            "status": "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "stage": "ATOMIC_COMPLETION_PROOF",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": game_index,
            identity_field: "provider:game-2",
        }

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }


@pytest.mark.parametrize(
    ("stage", "state"),
    (
        ("COMPLETE_READY", "VERIFY_PREVIOUS"),
        ("VERIFICATION_CHECKPOINT_READY", "VERIFY_COMPLETE_PREVIOUS"),
    ),
)
def test_cooperative_public_progress_binds_verification_stage_to_completion(
    stage,
    state,
):
    with _load_handler() as (handler, _, _):
        progress, game_index = _producer_attempt_public_progress(state)
        progress["lastAttempt"] = {
            "status": "DURABLE_TERMINAL_VERIFIED",
            "stage": stage,
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": game_index,
            "gameIdentity": f"provider:game-{game_index + 1}",
            "durableIdentity": f"provider:game-{game_index + 1}",
        }

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_stage"),
    (
        (
            "budget",
            "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "ATOMIC_COMPLETION_PROOF",
        ),
        (
            "contention",
            "DEFERRED_MUTATION_LEASE_CONTENDED",
            "MUTATION_LEASE_CONTENDED",
        ),
    ),
)
def test_real_completion_producer_persists_and_projects_exact_end_cursor(
    mode,
    expected_status,
    expected_stage,
):
    producer_fixtures = _cooperative_producer_fixtures()
    producer_module = producer_fixtures._install(
        producer_fixtures.ChunkModule(game_count=1)
    )
    table = FakeLeaseTable()
    delegate_payload = _successful_current_slate_payload(
        producer_fixtures.TODAY
    )

    with _load_handler(
        lease_table=table,
        delegate_payload=delegate_payload,
    ) as (handler, _, _):
        handler.mlb_daily_pick_lock._today_et = (
            lambda: producer_fixtures.TODAY
        )
        handler.lambda_handler(
            _cooperative_event(producer_fixtures.SLATE),
            FakeContext(),
        )
        claimed, _ = handler._claim_cooperative_replay(
            owner=f"seed-real-producer-{mode}",
            context=FakeContext(remaining_millis=900_000),
            current_slate_response={
                "statusCode": 200,
                "body": json.dumps(delegate_payload),
            },
            expected_slate_date=producer_fixtures.TODAY,
        )
        assert claimed is not None

        checkpoint = None
        for _ in range(2):
            produced = producer_fixtures._invoke(
                producer_module,
                checkpoint,
                request_epoch=claimed["requested_at_epoch"],
                request_id=claimed["request_id"],
            )
            assert produced["ok"] is True
            checkpoint = produced["checkpoint"]
        assert checkpoint["verificationComplete"] is True

        seed_public = handler._checkpoint_cooperative_replay(
            item=claimed,
            owner=f"seed-real-producer-{mode}",
            progress=checkpoint,
            failed=False,
        )
        assert seed_public["terminalChunkProgress"]["valid"] is True

        if mode == "contention":
            producer_module.lease_contended = True

        def real_completion_runner(
            *,
            slate_date,
            request_epoch,
            request_id,
            checkpoint,
            context,
        ):
            del context
            assert slate_date == producer_fixtures.SLATE
            producer_context = (
                producer_fixtures.BudgetContext(900_000, 89_000)
                if mode == "budget"
                else producer_fixtures.BudgetContext(900_000)
            )
            return producer_fixtures._invoke(
                producer_module,
                checkpoint,
                request_epoch=request_epoch,
                request_id=request_id,
                context=producer_context,
            )

        handler.mlb_daily_pick_lock.run_cooperative_terminal_chunk = (
            real_completion_runner
        )
        scheduled_event = _scheduled_event()
        scheduled_event["date"] = producer_fixtures.TODAY
        response = handler.lambda_handler(
            scheduled_event,
            FakeContext(
                request_id=f"real-completion-{mode}",
                remaining_millis=900_000,
            ),
        )
        payload = _body(response)
        public = payload[
            "cooperativeTerminalReplayOwnerExecution"
        ]["terminalChunkProgress"]
        stored_attempt = table.queue_item[
            "terminal_replay_progress"
        ]["lastAttempt"]

        assert public["valid"] is True
        assert public["lastAttempt"]["status"] == expected_status
        assert public["lastAttempt"]["stage"] == expected_stage
        assert public["lastAttempt"]["gameIndex"] == 1
        assert type(public["lastAttempt"]["gameIndex"]) is int
        assert stored_attempt["gameIndex"] == 1
        assert "gameIdentity" not in stored_attempt
        assert "durableIdentity" not in stored_attempt
        assert table.queue_item["state"] == "QUEUED"
        assert payload["lockExecutionConcurrency"]["leaseReleased"] is True
        json.dumps(response, sort_keys=True)


def test_cooperative_public_progress_rejects_zero_attempt_initial_shape():
    with _load_handler() as (handler, _, _):
        progress, _ = _producer_attempt_public_progress("PROCESS_CURRENT")
        progress["attemptCount"] = 0
        progress.pop("lastAttempt", None)

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }


def test_real_verify_bind_failure_persists_and_projects_producer_shape():
    producer_fixtures = _cooperative_producer_fixtures()
    producer_module = producer_fixtures._install(
        producer_fixtures.ChunkModule(game_count=1)
    )
    table = FakeLeaseTable()
    delegate_payload = _successful_current_slate_payload(
        producer_fixtures.TODAY
    )

    with _load_handler(
        lease_table=table,
        delegate_payload=delegate_payload,
    ) as (handler, _, _):
        handler.mlb_daily_pick_lock._today_et = (
            lambda: producer_fixtures.TODAY
        )
        handler.lambda_handler(
            _cooperative_event(producer_fixtures.SLATE),
            FakeContext(),
        )
        claimed, _ = handler._claim_cooperative_replay(
            owner="seed-real-bind-verify",
            context=FakeContext(remaining_millis=900_000),
            current_slate_response={
                "statusCode": 200,
                "body": json.dumps(delegate_payload),
            },
            expected_slate_date=producer_fixtures.TODAY,
        )
        assert claimed is not None

        processed = producer_fixtures._invoke(
            producer_module,
            None,
            request_epoch=claimed["requested_at_epoch"],
            request_id=claimed["request_id"],
        )
        checkpoint = processed["checkpoint"]
        assert checkpoint["phase"] == "VERIFY"
        assert checkpoint["verificationComplete"] is False

        producer_module.patch._acquire_lock_execution_lease = None
        failed = producer_fixtures._invoke(
            producer_module,
            checkpoint,
            request_epoch=claimed["requested_at_epoch"],
            request_id=claimed["request_id"],
        )
        attempt = failed["checkpoint"]["lastAttempt"]

        assert failed["ok"] is False
        assert failed["stage"] == "BIND_MANIFEST_AUTHORITY"
        assert attempt["phase"] == "VERIFY"
        assert attempt["gameIndex"] == 0
        assert attempt["gameIdentity"] == "provider:game-0"
        assert "durableIdentity" not in attempt

        public_state = handler._checkpoint_cooperative_replay(
            item=claimed,
            owner="seed-real-bind-verify",
            progress=failed["checkpoint"],
            failed=True,
        )
        public = public_state["terminalChunkProgress"]

        assert public["valid"] is True
        assert public["lastAttempt"]["stage"] == (
            "BIND_MANIFEST_AUTHORITY"
        )
        assert public["lastAttempt"]["phase"] == "VERIFY"
        assert table.queue_item["state"] == "QUEUED"
        json.dumps(public_state, sort_keys=True)


@pytest.mark.parametrize(
    ("identity_field", "identity_value"),
    (
        ("gameIdentity", "official:824805"),
        ("gameIdentity", "arbitrary-identity"),
        ("gameIdentity", "provider:"),
        ("durableIdentity", "provider:different-game"),
    ),
)
def test_cooperative_public_progress_rejects_noncanonical_identity(
    identity_field,
    identity_value,
):
    with _load_handler() as (handler, _, _):
        progress, game_index = _producer_attempt_public_progress(
            "VERIFY_TRANSITION"
        )
        progress["lastAttempt"] = {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": game_index,
            "gameIdentity": "provider:game-2",
            "durableIdentity": "provider:game-2",
        }
        progress["lastAttempt"][identity_field] = identity_value

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }


@pytest.mark.parametrize(
    "error_code",
    (
        "arbitrary_error",
        "BAD\nCODE",
        "wrongStage_RuntimeError",
    ),
)
def test_cooperative_public_progress_rejects_nonproducer_error_code(
    error_code,
):
    with _load_handler() as (handler, _, _):
        progress, game_index = _producer_attempt_public_progress(
            "PROCESS_CURRENT"
        )
        progress["lastAttempt"] = {
            "status": "FAILED_CLOSED",
            "stage": "BIND_MANIFEST_AUTHORITY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "PROCESS",
            "gameIndex": game_index,
            "gameIdentity": "provider:game-1",
            "errorCode": error_code,
        }

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }


def test_cooperative_public_progress_preserves_compatible_identity_suffix():
    with _load_handler() as (handler, _, _):
        progress, game_index = _producer_attempt_public_progress(
            "VERIFY_TRANSITION"
        )
        compatible_identity = "provider:producer suffix\nvalue"
        progress["lastAttempt"] = {
            "status": "TERMINAL_CHECKPOINT_READY",
            "stage": "PROCESS_CHECKPOINT_READY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": game_index,
            "gameIdentity": compatible_identity,
            "durableIdentity": compatible_identity,
        }

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public["valid"] is True
        assert public["lastAttempt"]["gameIdentity"] == compatible_identity
        json.dumps(public, sort_keys=True)


@pytest.mark.parametrize(
    "fallback_suffix",
    ("RuntimeError", "custom_error"),
)
def test_cooperative_public_progress_accepts_stage_exception_error_fallback(
    fallback_suffix,
):
    with _load_handler() as (handler, _, _):
        progress, game_index = _producer_attempt_public_progress(
            "PROCESS_CURRENT"
        )
        progress["lastAttempt"] = {
            "status": "FAILED_CLOSED",
            "stage": "BIND_MANIFEST_AUTHORITY",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "PROCESS",
            "gameIndex": game_index,
            "gameIdentity": "provider:game-1",
            "errorCode": (
                "BIND_MANIFEST_AUTHORITY_" + fallback_suffix
            ),
        }

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public["valid"] is True
        assert public["lastAttempt"]["errorCode"] == (
            "BIND_MANIFEST_AUTHORITY_" + fallback_suffix
        )
        json.dumps(public, sort_keys=True)


def test_cooperative_public_progress_rejects_reconciled_canonical_count():
    with _load_handler() as (handler, _, _):
        progress = _complete_terminal_checkpoint(
            slate_date="2026-07-20",
            game_count=1,
        )
        progress["canonicalCount"] = 1
        progress["noPredictionDataCount"] = 0
        progress["reconciledCount"] = 1

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }


@pytest.mark.parametrize(
    ("status", "stage", "error_code"),
    (
        (
            "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "ATOMIC_COMPLETION_PROOF",
            None,
        ),
        (
            "DEFERRED_MUTATION_LEASE_CONTENDED",
            "MUTATION_LEASE_CONTENDED",
            "WRITER_LEASE_CONTENDED",
        ),
        (
            "FAILED_CLOSED",
            "ATOMIC_COMPLETION_PROOF",
            "ATOMIC_DURABLE_PROOF_INVALID",
        ),
        (
            "FAILED_CLOSED",
            "RELEASE_MUTATION_LEASE",
            "MUTATION_LEASE_RELEASE_FAILED",
        ),
    ),
)
def test_cooperative_public_progress_accepts_exact_completion_cursor(
    status,
    stage,
    error_code,
):
    with _load_handler() as (handler, _, _):
        progress = _complete_terminal_checkpoint(
            slate_date="2026-07-20",
            game_count=1,
        )
        last_attempt = {
            "status": status,
            "stage": stage,
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": Decimal("1"),
        }
        if error_code is not None:
            last_attempt["errorCode"] = error_code
        progress["lastAttempt"] = last_attempt

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public["valid"] is True
        assert public["lastAttempt"]["gameIndex"] == 1
        assert type(public["lastAttempt"]["gameIndex"]) is int
        json.dumps(public, sort_keys=True)


@pytest.mark.parametrize(
    ("verification_index", "verification_complete", "game_index"),
    (
        (0, False, Decimal("1")),
        (1, True, Decimal("0")),
    ),
)
def test_cooperative_public_progress_rejects_inexact_completion_cursor(
    verification_index,
    verification_complete,
    game_index,
):
    with _load_handler() as (handler, _, _):
        progress = _complete_terminal_checkpoint(
            slate_date="2026-07-20",
            game_count=1,
        )
        progress["verificationIndex"] = verification_index
        progress["verifiedGameCount"] = verification_index
        progress["verificationComplete"] = verification_complete
        progress["lastAttempt"] = {
            "status": "DEFERRED_INSUFFICIENT_REMAINING_TIME",
            "stage": "ATOMIC_COMPLETION_PROOF",
            "atUtc": "2026-07-21T22:10:00+00:00",
            "phase": "VERIFY",
            "gameIndex": game_index,
        }

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public == {
            "version": handler.COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "valid": False,
            "failClosed": True,
        }


def test_cooperative_public_progress_accepts_bind_authority_failure_stage():
    with _load_handler() as (handler, _, _):
        progress = _complete_terminal_checkpoint(
            slate_date="2026-07-20",
            game_count=1,
        )
        progress.update(
            {
                "phase": "PROCESS",
                "nextGameIndex": 0,
                "processedGameCount": 0,
                "terminalCount": 0,
                "canonicalCount": 0,
                "noPredictionDataCount": 0,
                "reconciledCount": 0,
                "verificationIndex": 0,
                "verifiedGameCount": 0,
                "verificationComplete": False,
                "attemptCount": 1,
                "lastAttempt": {
                    "status": "FAILED_CLOSED",
                    "stage": "BIND_MANIFEST_AUTHORITY",
                    "atUtc": "2026-07-21T22:10:00+00:00",
                    "phase": "PROCESS",
                    "gameIndex": Decimal("0"),
                    "gameIdentity": "provider:game-1",
                    "errorCode": "MANIFEST_AUTHORITY_EVIDENCE_INVALID",
                },
            }
        )

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public["valid"] is True
        assert public["lastAttempt"]["stage"] == "BIND_MANIFEST_AUTHORITY"
        json.dumps(public, sort_keys=True)


def test_cooperative_public_progress_projects_only_integral_ddb_decimals():
    with _load_handler() as (handler, _, _):
        progress = _complete_terminal_checkpoint(
            slate_date="2026-07-20",
            game_count=1,
        )
        for field in (
            "manifestGameCount",
            "nextGameIndex",
            "processedGameCount",
            "terminalCount",
            "canonicalCount",
            "noPredictionDataCount",
            "reconciledCount",
            "verificationIndex",
            "verifiedGameCount",
            "attemptCount",
        ):
            progress[field] = Decimal(progress[field])
        progress["lastAttempt"]["gameIndex"] = Decimal("0")

        public = handler._cooperative_terminal_progress_public(
            {
                "slate_date_et": "2026-07-20",
                "terminal_replay_progress": progress,
            }
        )

        assert public["valid"] is True
        assert type(public["manifestGameCount"]) is int
        assert type(public["attemptCount"]) is int
        assert type(public["lastAttempt"]["gameIndex"]) is int
        json.dumps(public, sort_keys=True)


def test_newly_due_mid_run_failure_keeps_cooperative_replay_queued():
    table = FakeLeaseTable()
    chunk_calls = []
    current_slate_failure = {
        "ok": False,
        "sport": "mlb",
        "slateDateEt": "2026-07-21",
        "reason": "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL",
        "perGameLockProgress": {
            "manifestGameCount": 7,
            "lockOutcomeCount": 3,
            "pendingCount": 3,
            "dueMissingCount": 1,
            "games": [
                {"gameIdentity": "provider:newly-due", "state": "DUE_NOT_STAGED"}
            ],
        },
    }

    with _load_handler(
        lease_table=table,
        delegate_payload=current_slate_failure,
    ) as (handler, _, delegate_calls):
        handler.mlb_daily_pick_lock.run_cooperative_terminal_chunk = (
            lambda **kwargs: chunk_calls.append(kwargs)
        )
        handler.lambda_handler(_cooperative_event(), FakeContext())

        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(
                    request_id="mid-run-cutoff-owner",
                    remaining_millis=900_000,
                ),
            ),
            "MLB_SCHEDULED_LOCK_FAILED",
        )

        assert len(delegate_calls) == 1
        assert chunk_calls == []
        assert table.queue_item["state"] == "QUEUED"
        assert "current_slate_success_proof" not in table.queue_item
        assert table.item is None
        assert len(table.delete_calls) == 1
