from __future__ import annotations

import copy
import hashlib
import json
import sys
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
UNIT_TESTS = ROOT / "tests" / "unit"
for path in (HELLO_WORLD, UNIT_TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import mlb_daily_per_game_lock_patch as real_patch
import mlb_prospective_row_repair as repair
import test_mlb_daily_per_game_lock as real_lock_fixtures


SLATE = "2026-08-05"
TODAY = "2026-08-06"
REQUEST_EPOCH = 1_786_000_000
REQUEST_ID = "request-terminal-chunk-test"


def _fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _game(index: int, *, official_pk: str | None = None) -> dict:
    official = official_pk or str(822_865 + index)
    return {
        "game_id": f"provider:game-{index}",
        "id": f"provider:game-{index}",
        "providerEventId": f"game-{index}",
        "officialGamePk": official,
        "home_team": f"Home {index}",
        "away_team": f"Away {index}",
        "commence_time": f"2026-08-05T{17 + index:02d}:00:00+00:00",
    }


def _manifest_authority(game_count: int) -> dict:
    return {
        "version": "test-provider-manifest-v1",
        "recordType": "test-immutable-provider-manifest",
        "pk": "PULL#manifest",
        "sk": "FULL#2026-08-05",
        "fingerprint": "a" * 64,
        "gameCount": game_count,
        "canonicalGameIdentities": [
            f"provider:game-{index}" for index in range(game_count)
        ],
        "immutable": True,
        "writeOnce": True,
        "fullProviderSchedule": True,
        "consistentReadVerified": True,
        "officialScheduleAuthorityVersion": "test-official-v1",
        "officialScheduleAuthorityFingerprint": "b" * 64,
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


class EvidenceTable:
    name = "test-lock-table"

    def __init__(self):
        self.items = {}
        self.meta = SimpleNamespace(
            client=SimpleNamespace(region_name="us-east-1")
        )

    def get_item(self, Key, ConsistentRead=False):
        del ConsistentRead
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item is not None else {}


class ChunkModule:
    def __init__(self, game_count: int = 2):
        self.games = [_game(index) for index in range(game_count)]
        self.TABLE = EvidenceTable()
        self.manifest_item = {
            "PK": "PULL#manifest",
            "SK": "FULL#2026-08-05",
            "record_type": "test-immutable-provider-manifest",
            "manifest_fingerprint": "a" * 64,
            "write_once": True,
            "data": {
                "slateDate": SLATE,
                "fingerprint": "a" * 64,
                "games": copy.deepcopy(self.games),
            },
        }
        self.TABLE.items[
            (self.manifest_item["PK"], self.manifest_item["SK"])
        ] = copy.deepcopy(self.manifest_item)
        self.history = SimpleNamespace(
            PULLS=self.TABLE,
            ddb_safe=copy.deepcopy,
        )
        self.outcomes = {}
        self.stages = {}
        self.canonicals = {}
        self.outcome_reads = []
        self.stage_reads = []
        self.terminal_writes = []
        self.writer_authorities = []
        self.lease_acquires = []
        self.lease_releases = []
        self.atomic_calls = []
        self.lease_contended = False
        self.release_mode = "success"
        self.original_calls = 0
        self.now = datetime(
            2026,
            8,
            6,
            12,
            0,
            tzinfo=timezone.utc,
        )

    def _today_et(self):
        return TODAY

    def _now_utc(self):
        return self.now

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
        del slate_date, force, scheduled
        self.original_calls += 1
        raise AssertionError(
            "the cooperative chunk must not call the whole-slate run_lock"
        )


class ChunkPatch:
    LOCK_EXECUTION_LEASE_VERSION = (
        "MLB-LOCK-EXECUTION-LEASE-v2-global-all-mutating"
    )
    LOCK_EXECUTION_LEASE_RECORD_TYPE = "test-global-lease"
    LEGACY_SCHEDULED_SINGLE_FLIGHT_VERSION = "test-bridge-v1"
    LEGACY_SCHEDULED_SINGLE_FLIGHT_RECORD_TYPE = "test-bridge-lease"

    @staticmethod
    def _lock_execution_lease_key():
        return {"PK": "MLB_LOCK_EXECUTION#V2", "SK": "LEASE"}

    @staticmethod
    def _legacy_rollout_bridge_slates(slate):
        base = datetime.fromisoformat(f"{slate}T00:00:00+00:00")
        return [
            (base + timedelta(days=offset)).date().isoformat()
            for offset in (-1, 0, 1)
        ]

    @staticmethod
    def _legacy_scheduled_single_flight_key(slate):
        return {"PK": f"MLB_LOCK_RUNTIME#{slate}", "SK": "SCHEDULED"}

    def __init__(self):
        self._STATUS_READ_CACHE = ContextVar(
            f"test_cooperative_terminal_chunk_cache_{id(self)}",
            default=None,
        )
        self._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT = ContextVar(
            f"test_cooperative_terminal_alias_limit_{id(self)}",
            default=None,
        )

    @staticmethod
    def _cooperative_terminal_item_fingerprint(item):
        return _fingerprint(item)

    def game_identity(self, game):
        return game["game_id"]

    def _pull_at(self, module, pull):
        del module
        return datetime.fromisoformat(pull["pulled_at"])

    def _start(self, module, game):
        del module
        return datetime.fromisoformat(game["commence_time"])

    def _lock_at(self, module, game):
        return self._start(module, game) - timedelta(minutes=45)

    def _get_stage(self, module, slate, game):
        assert slate == SLATE
        request_cache = self._STATUS_READ_CACHE.get()
        assert isinstance(request_cache, dict)
        assert set(request_cache) == {"canonicalPulls"}
        identity = self.game_identity(game)
        module.stage_reads.append(identity)
        return copy.deepcopy(module.stages.get(identity))

    def _cooperative_terminal_lock_outcome_observation(
        self, module, slate, game
    ):
        assert slate == SLATE
        request_cache = self._STATUS_READ_CACHE.get()
        assert isinstance(request_cache, dict)
        assert set(request_cache) == {"canonicalPulls"}
        identity = self.game_identity(game)
        module.outcome_reads.append(identity)
        item = copy.deepcopy(module.outcomes.get(identity))
        if item is None:
            return {
                "exists": False,
                "valid": False,
                "item": None,
                "errors": [],
            }
        valid = bool(
            item.get("game_identity") == identity
            and item.get("lock_status") == "LOCKED_NO_PREDICTION_DATA"
            and item.get("lock_outcome_recorded") is True
            and item.get("write_once") is True
            and item.get("lock_outcome_fingerprint")
            == _fingerprint({
                key: value
                for key, value in item.items()
                if key != "lock_outcome_fingerprint"
            })
        )
        return {
            "exists": True,
            "valid": valid,
            "item": item,
            "errors": [] if valid else ["injected_invalid_outcome"],
        }

    def _get_lock_outcome(self, module, slate, game):
        observation = self._cooperative_terminal_lock_outcome_observation(
            module, slate, game
        )
        return (
            copy.deepcopy(observation["item"])
            if observation["valid"] is True
            else None
        )

    def _scoring_pulls(self, module, pulls, game):
        del module, pulls, game
        return []

    def _last_prelock_candidate(self, module, slate, game, scoring):
        del module, slate, game, scoring
        assert (
            self._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT.get()
            == repair.COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_QUERY_LIMIT
        )
        return (
            None,
            None,
            [],
            [
                "no_persisted_user_visible_platform_prelock_prediction_"
                "at_or_before_cutoff"
            ],
        )

    def _is_no_prediction_candidate_failure(self, errors):
        return errors == [
            "no_persisted_user_visible_platform_prelock_prediction_"
            "at_or_before_cutoff"
        ]

    def _select_provider_manifest_authority(
        self, module, pulls, slate, manifest
    ):
        del module
        assert pulls
        assert slate == SLATE
        self._manifest_game_count = len(manifest)
        return _manifest_authority(len(manifest))

    def _strict_outcome(self, identity, reasons):
        item = {
            "PK": f"MLB_LOCK#{SLATE}",
            "SK": f"OUTCOME#{_fingerprint(identity)[:16]}",
            "record_type": "mlb_immutable_per_game_lock_outcome",
            "version": "MLB-LOCK-OUTCOME-v1-explicit-terminal-status",
            "game_identity": identity,
            "lock_status": "LOCKED_NO_PREDICTION_DATA",
            "lock_outcome_recorded": True,
            "locked_prediction": False,
            "canonical": False,
            "canonical_prediction": False,
            "official_prediction": False,
            "playable": False,
            "blocked": True,
            "training_eligible": False,
            "accuracy_eligible": False,
            "wager_allowed": False,
            "prediction_adopted": False,
            "operational_defect": False,
            "canonical_prediction_complete": False,
            "post_start_prediction_creation_allowed": False,
            "immutable_prediction_rewrite_allowed": False,
            "training_exclusion_reasons": [
                "missing_immutable_prediction"
            ],
            "reasons": list(reasons) or ["proven_absence"],
            "provider_manifest_fingerprint": "a" * 64,
            "provider_manifest_authority": _manifest_authority(
                getattr(self, "_manifest_game_count", 1)
            ),
            "write_once": True,
        }
        material = {
            key: value
            for key, value in item.items()
            if key != "lock_outcome_fingerprint"
        }
        item["lock_outcome_fingerprint"] = _fingerprint(material)
        return item

    def _put_no_prediction_outcome(
        self,
        module,
        slate,
        game,
        now,
        reasons,
        authority,
    ):
        assert slate == SLATE
        assert now >= datetime.fromisoformat(game["commence_time"])
        assert authority["fingerprint"] == "a" * 64
        module.writer_authorities.append(copy.deepcopy(authority))
        identity = self.game_identity(game)
        if identity not in module.outcomes:
            module.terminal_writes.append(identity)
            module.outcomes[identity] = self._strict_outcome(
                identity, reasons
            )
        return copy.deepcopy(module.outcomes[identity])

    def _validate_stage(
        self, module, stored_stage, slate, game, manifest, scoring
    ):
        del module, stored_stage, slate, game, manifest, scoring
        return []

    def _canonical_readback(self, module, row):
        identity = str(row.get("gameIdentity") or "")
        item = module.canonicals.get(identity)
        if not item:
            return None
        return {
            "ok": True,
            "pk": item["PK"],
            "sk": item["SK"],
            "storageClass": "LOCKED_IMMUTABLE",
        }

    def _cooperative_terminal_authority_evidence(
        self,
        module,
        *,
        durable_identity,
        terminal_state,
        outcome,
        stored_stage,
        canonical,
    ):
        del canonical
        if terminal_state == "LOCKED_NO_PREDICTION_DATA":
            items = [
                {
                    "tableRole": "LOCK_TABLE",
                    "PK": outcome["PK"],
                    "SK": outcome["SK"],
                    "itemFingerprint": _fingerprint(outcome),
                }
            ]
            evidence = {
                "durableIdentity": durable_identity,
                "terminalState": terminal_state,
                "lockOutcomeFingerprint": outcome[
                    "lock_outcome_fingerprint"
                ],
                "providerManifestFingerprint": outcome[
                    "provider_manifest_fingerprint"
                ],
                "items": items,
            }
        else:
            canonical_item = module.canonicals[durable_identity]
            items = [
                {
                    "tableRole": "LOCK_TABLE",
                    "PK": stored_stage["PK"],
                    "SK": stored_stage["SK"],
                    "itemFingerprint": _fingerprint(stored_stage),
                },
                {
                    "tableRole": "PULLS_TABLE",
                    "PK": canonical_item["PK"],
                    "SK": canonical_item["SK"],
                    "itemFingerprint": _fingerprint(canonical_item),
                },
            ]
            evidence = {
                "durableIdentity": durable_identity,
                "terminalState": terminal_state,
                "stageFingerprint": stored_stage["stage_fingerprint"],
                "canonicalPayloadFingerprint": _fingerprint(
                    canonical_item["data"]
                ),
                "canonicalPayloadFingerprintVersion": "test",
                "candidateSelectionFingerprint": _fingerprint(
                    stored_stage.get("candidate_proof") or {}
                ),
                "providerManifestFingerprint": "a" * 64,
                "vectorFingerprint": "vector-fingerprint",
                "promotionPolicyVersion": "test-promotion",
                "lockPolicy": "test-lock-policy",
                "modelVersion": "test-model",
                "items": items,
            }
        manifest_dependency = {
            "tableRole": "PULLS_TABLE",
            "PK": module.manifest_item["PK"],
            "SK": module.manifest_item["SK"],
            "itemFingerprint": _fingerprint(module.manifest_item),
        }
        if manifest_dependency not in items:
            items.append(manifest_dependency)
        evidence["authorityItemCount"] = (
            1 if terminal_state == "LOCKED_NO_PREDICTION_DATA" else 2
        )
        evidence["dependencyItemCount"] = (
            len(items) - evidence["authorityItemCount"]
        )
        evidence["providerManifestGameCount"] = len(module.games)
        evidence["providerManifestPk"] = module.manifest_item["PK"]
        evidence["providerManifestSk"] = module.manifest_item["SK"]
        evidence["evidenceFingerprint"] = (
            repair._cooperative_terminal_evidence_fingerprint(evidence)
        )
        return evidence

    def _acquire_lock_execution_lease(self, module, slate, now):
        assert slate == SLATE
        module.lease_acquires.append(now)
        if module.lease_contended:
            return {
                "acquired": False,
                "contentionScope": "legacy_rollout_bridge",
            }
        owner = f"lease-owner-{len(module.lease_acquires)}"
        expires_epoch = int(now.timestamp()) + 960
        specs = [
            {
                "key": self._lock_execution_lease_key(),
                "recordType": self.LOCK_EXECUTION_LEASE_RECORD_TYPE,
                "version": self.LOCK_EXECUTION_LEASE_VERSION,
            },
            *[
                {
                    "key": self._legacy_scheduled_single_flight_key(
                        bridge_slate
                    ),
                    "recordType": (
                        self.LEGACY_SCHEDULED_SINGLE_FLIGHT_RECORD_TYPE
                    ),
                    "version": (
                        self.LEGACY_SCHEDULED_SINGLE_FLIGHT_VERSION
                    ),
                }
                for bridge_slate in self._legacy_rollout_bridge_slates(slate)
            ],
        ]
        now_epoch = int(now.timestamp())
        existing = [
            module.TABLE.items.get(
                (spec["key"]["PK"], spec["key"]["SK"])
            )
            for spec in specs
        ]
        if any(
            item is not None
            and int(item.get("lease_expires_at_epoch") or 0) > now_epoch
            for item in existing
        ):
            return {
                "acquired": False,
                "contentionScope": "global",
            }
        # Model production's owner-fenced stale-lease takeover: after the TTL,
        # every exact V2/bridge key may be replaced by the next owner.
        for spec, item in zip(specs, existing):
            if item is not None:
                module.TABLE.items.pop(
                    (spec["key"]["PK"], spec["key"]["SK"]),
                    None,
                )
        for spec in specs:
            module.TABLE.items[
                (spec["key"]["PK"], spec["key"]["SK"])
            ] = {
                **spec["key"],
                "record_type": spec["recordType"],
                "version": spec["version"],
                "lease_owner": owner,
                "lease_expires_at_epoch": expires_epoch,
            }
        return {
            "acquired": True,
            "owner": owner,
            "expiresAtUtc": datetime.fromtimestamp(
                expires_epoch,
                tz=timezone.utc,
            ).isoformat(),
            "expiresAtEpoch": expires_epoch,
            "ownedKeys": specs,
        }

    def _release_lock_execution_lease(self, module, lease):
        module.lease_releases.append(copy.deepcopy(lease))
        if module.release_mode == "raise":
            raise RuntimeError("simulated release transport error")
        if module.release_mode == "ambiguous":
            return False
        for owned in reversed(lease.get("ownedKeys") or []):
            key = owned["key"]
            item = module.TABLE.items.get((key["PK"], key["SK"]))
            if item and item.get("lease_owner") == lease.get("owner"):
                module.TABLE.items.pop((key["PK"], key["SK"]), None)
        return True

    def _cooperative_terminal_atomic_verify(
        self, module, processed_games, manifest_authority=None
    ):
        module.atomic_calls.append(copy.deepcopy(processed_games))
        by_key = {}
        for expected in (manifest_authority or {}).get("atomicItems") or []:
            by_key[(
                expected["tableRole"],
                expected["PK"],
                expected["SK"],
            )] = copy.deepcopy(expected)
        for entry in processed_games:
            for expected in entry["durableEvidence"]["items"]:
                key = (
                    expected["tableRole"],
                    expected["PK"],
                    expected["SK"],
                )
                prior = by_key.get(key)
                if (
                    prior is not None
                    and prior["itemFingerprint"]
                    != expected["itemFingerprint"]
                ):
                    raise RuntimeError(
                        "COOPERATIVE_TERMINAL_ATOMIC_EVIDENCE_CONFLICT"
                    )
                by_key[key] = copy.deepcopy(expected)
        requests = [by_key[key] for key in sorted(by_key)]
        for expected in requests:
                observed = None
                if expected["tableRole"] == "LOCK_TABLE":
                    values = [
                        *module.outcomes.values(),
                        *module.stages.values(),
                    ]
                else:
                    values = [
                        *module.canonicals.values(),
                        *[
                            item
                            for item in module.TABLE.items.values()
                            if item.get("record_type")
                            == "test-immutable-provider-manifest"
                        ],
                    ]
                for candidate in values:
                    if (
                        candidate.get("PK") == expected["PK"]
                        and candidate.get("SK") == expected["SK"]
                    ):
                        observed = candidate
                        break
                if (
                    observed is None
                    or _fingerprint(observed)
                    != expected["itemFingerprint"]
                ):
                    raise RuntimeError(
                        "COOPERATIVE_TERMINAL_ATOMIC_ITEM_MISMATCH"
                    )
        return {
            "ok": True,
            "atomicSnapshot": True,
            "itemCount": len(requests),
            "maxItemCount": 100,
            "readSetFingerprint": _fingerprint(requests),
            "postStartPredictionCreationAllowed": False,
        }


def _canonical_rows(identity: str) -> tuple[dict, dict]:
    stage = {
        "PK": f"MLB_LOCK#{SLATE}",
        "SK": f"STAGE#{_fingerprint(identity)[:16]}",
        "stage_fingerprint": f"stage-fingerprint-{identity}",
        "candidate_proof": {"selectionFingerprint": "candidate"},
        "provider_manifest_authority": _manifest_authority(1),
        "data": {"row": {"gameIdentity": identity}},
    }
    canonical = {
        "PK": f"GAME_WINNERS#mlb#{SLATE}",
        "SK": f"LOCKED#GAME#2026-08-05T17:00:00+00:00#{identity}",
        "data": {
            "gameIdentity": identity,
            "frozenFeatureVector": {"fingerprint": "vector-fingerprint"},
        },
    }
    return stage, canonical


def _install(module, patch=None):
    selected = patch or ChunkPatch()
    repair.install_prospective_row_repair(module, selected)
    assert callable(module.run_cooperative_terminal_chunk)
    module.patch = selected
    return module


def _invoke(
    module,
    checkpoint=None,
    *,
    request_epoch=REQUEST_EPOCH,
    request_id=REQUEST_ID,
    context=None,
):
    return module.run_cooperative_terminal_chunk(
        slate_date=SLATE,
        request_epoch=request_epoch,
        request_id=request_id,
        checkpoint=checkpoint,
        context=context or BudgetContext(900_000),
    )


def _process_then_verify_all(module):
    checkpoint = None
    results = []
    game_count = len(module.games)
    for _ in range(game_count):
        result = _invoke(module, checkpoint)
        results.append(result)
        assert result["ok"] is True
        assert result["complete"] is False
        checkpoint = result["checkpoint"]
    assert checkpoint["phase"] == "VERIFY"
    assert checkpoint["verificationIndex"] == 0
    for index in range(game_count):
        result = _invoke(module, checkpoint)
        results.append(result)
        assert result["ok"] is True
        assert result["complete"] is False
        checkpoint = result["checkpoint"]
        assert checkpoint["verificationIndex"] == index + 1
    assert checkpoint["verificationComplete"] is True
    return checkpoint, results


def test_chunk_processes_and_verifies_one_target_per_owner_then_atomic_completes():
    module = _install(ChunkModule(game_count=2))

    checkpoint, results = _process_then_verify_all(module)

    assert len(results) == 4
    assert module.terminal_writes == ["provider:game-0", "provider:game-1"]
    assert len(module.writer_authorities) == 2
    assert all(
        "atomicItems" not in authority
        and "authorityEvidenceFingerprint" not in authority
        for authority in module.writer_authorities
    )
    manifest_authority_fingerprint = checkpoint["manifestAuthority"][
        "authorityEvidenceFingerprint"
    ]
    assert len(manifest_authority_fingerprint) == 64
    assert checkpoint["manifestAuthority"]["atomicItems"]
    assert all(
        entry["durableEvidence"][
            "manifestAuthorityEvidenceFingerprint"
        ]
        == manifest_authority_fingerprint
        for entry in checkpoint["processedGames"]
    )
    assert len(module.lease_acquires) == 4
    assert len(module.lease_releases) == 4
    assert all(
        len(lease["ownedKeys"]) == 4
        for lease in module.lease_releases
    )
    assert module.original_calls == 0

    final = _invoke(module, checkpoint)
    assert final["ok"] is True
    assert final["complete"] is True
    assert final["stage"] == "COMPLETE"
    assert final["atomicCompletionProof"]["atomicSnapshot"] is True
    assert final["atomicCompletionProof"]["itemCount"] == 3
    assert final["atomicCompletionProof"]["maxItemCount"] == 100
    assert final["atomicCompletionProof"][
        "completionMutationLeaseHeld"
    ] is True
    assert final["atomicCompletionProof"]["ownerExposed"] is False
    assert len(final["atomicCompletionProof"]["readSetFingerprint"]) == 64
    assert "_completionLease" in final
    assert "_atomicCompletionProof" in final
    assert len(module.atomic_calls) == 1
    assert len(module.lease_acquires) == 5
    # The final V2 + bridge lease is intentionally retained until the queue
    # COMPLETE CAS, rather than released after the atomic read.
    assert len(module.lease_releases) == 4
    response = final["terminalReplayResponse"]
    assert response["durableTerminalVerificationComplete"] is True
    assert response["atomicDurableProofRequired"] is True
    assert response["atomicDurableItemCount"] == 3
    assert response["verificationIndex"] == 2
    assert response["processedGameCount"] == 2
    assert response["missedGameCount"] == 0
    assert response["postStartPredictionCreationAllowed"] is False
    assert response["immutablePredictionRewriteAllowed"] is False
    released = module.release_cooperative_terminal_completion_lease(
        slate_date=SLATE,
        lease=final["_completionLease"],
    )
    assert released["released"] is True
    assert len(module.lease_releases) == 5
    assert response["productionAuthorityChanged"] is False


def test_deleted_prefix_after_first_verification_fails_atomic_completion():
    module = _install(ChunkModule(game_count=2))

    first = _invoke(module)
    second = _invoke(module, first["checkpoint"])
    verify_first = _invoke(module, second["checkpoint"])
    assert verify_first["checkpoint"]["verificationIndex"] == 1

    del module.outcomes["provider:game-0"]

    verify_second = _invoke(module, verify_first["checkpoint"])
    assert verify_second["ok"] is True
    assert verify_second["checkpoint"]["verificationComplete"] is True

    final = _invoke(module, verify_second["checkpoint"])
    assert final["ok"] is False
    assert final["complete"] is False
    assert final["stage"] == "ATOMIC_COMPLETION_PROOF"
    assert final["checkpoint"]["verificationComplete"] is True
    assert len(module.atomic_calls) == 1


def test_deleted_manifest_dependency_after_verify_blocks_completion():
    module = _install(ChunkModule(game_count=1))
    checkpoint, _ = _process_then_verify_all(module)
    module.TABLE.items.pop(
        (module.manifest_item["PK"], module.manifest_item["SK"])
    )

    final = _invoke(module, checkpoint)

    assert final["ok"] is False
    assert final["complete"] is False
    assert final["stage"] == "BIND_MANIFEST_AUTHORITY"


def test_final_atomic_proof_retains_writer_barrier_for_completion_cas():
    class BarrierPatch(ChunkPatch):
        def __init__(self):
            super().__init__()
            self.overlap_result = None

        def _cooperative_terminal_atomic_verify(
            self, module, processed_games, manifest_authority=None
        ):
            self.overlap_result = self._acquire_lock_execution_lease(
                module,
                SLATE,
                module._now_utc(),
            )
            return super()._cooperative_terminal_atomic_verify(
                module,
                processed_games,
                manifest_authority,
            )

    patch = BarrierPatch()
    module = _install(ChunkModule(game_count=1), patch)
    checkpoint, _ = _process_then_verify_all(module)

    final = _invoke(module, checkpoint)

    assert final["ok"] is True
    assert final["complete"] is True
    assert patch.overlap_result["acquired"] is False
    assert final["_completionLease"]["acquired"] is True
    module.release_cooperative_terminal_completion_lease(
        slate_date=SLATE,
        lease=final["_completionLease"],
    )


def test_mutated_verified_row_fails_atomic_completion():
    module = _install(ChunkModule(game_count=1))
    checkpoint, _ = _process_then_verify_all(module)
    module.outcomes["provider:game-0"]["reasons"].append("corruption")

    final = _invoke(module, checkpoint)

    assert final["ok"] is False
    assert final["stage"] == "ATOMIC_COMPLETION_PROOF"
    assert final["errorCode"] == (
        "COOPERATIVE_TERMINAL_ATOMIC_ITEM_MISMATCH"
    )


def test_decreasing_context_defers_before_terminal_write_and_releases_lease():
    module = _install(ChunkModule(game_count=1))

    result = _invoke(
        module,
        context=BudgetContext(
            900_000,
            900_000,
            900_000,
            900_000,
            119_000,
        ),
    )

    assert result["ok"] is True
    assert result["complete"] is False
    assert result["deferred"] is True
    assert result["stage"] == "WRITE_BUDGET"
    assert result["checkpoint"]["nextGameIndex"] == 0
    assert result["checkpoint"]["lastAttempt"]["status"] == (
        "DEFERRED_INSUFFICIENT_REMAINING_TIME"
    )
    assert module.terminal_writes == []
    assert len(module.lease_acquires) == 1
    assert len(module.lease_releases) == 1


def test_candidate_integrity_problem_stays_unresolved_and_writes_nothing():
    class CandidatePatch(ChunkPatch):
        def _last_prelock_candidate(
            self, module, slate, game, scoring
        ):
            del module, slate, game, scoring
            return (
                {"predictedWinner": "Home 0"},
                {"persisted": True},
                [],
                [],
            )

    module = _install(ChunkModule(game_count=1), CandidatePatch())

    result = _invoke(module)

    assert result["ok"] is False
    assert result["stage"] == "PROVE_PRELOCK_ABSENCE"
    assert result["errorCode"] == "PRELOCK_CANDIDATE_REQUIRES_REVIEW"
    assert result["checkpoint"]["nextGameIndex"] == 0
    assert module.terminal_writes == []
    assert len(module.lease_releases) == 1


def test_new_outcome_uses_manifest_primary_and_passes_real_manifest_validator():
    game = real_lock_fixtures.game(
        "provider-real-validator",
        "2026-07-13T18:00:00+00:00",
    )
    game.update({
        "provider_event_id": "provider-real-validator",
        "official_game_pk": "991777",
        "official_game_id": "mlb_statsapi:991777",
        "official_commence_time": game["commence_time"],
        "provider_commence_time": game["commence_time"],
        "provider_start_drift_seconds": 0,
        "canonical_start_time_source": "MLB_STATS_API_EXACT_DATE",
        "official_game_type": "R",
        "official_game_number": 1,
        "official_double_header": "N",
        "official_status": {"abstractGameState": "Final"},
    })
    source = real_lock_fixtures.pull(
        "2026-07-13T16:00:00+00:00",
        [game],
        "cooperative-real-validator",
    )
    assert (
        real_lock_fixtures.history_contract.validate_provider_schedule_manifest(
            source,
            real_lock_fixtures.SLATE,
        )
        == []
    )
    module = real_lock_fixtures.build_module(
        [source],
        "2026-07-14T12:00:00+00:00",
        seed=False,
    )
    module._today_et = lambda: "2026-07-14"
    repair.install_prospective_row_repair(module, real_patch)

    result = module.run_cooperative_terminal_chunk(
        slate_date=real_lock_fixtures.SLATE,
        request_epoch=REQUEST_EPOCH,
        request_id=REQUEST_ID,
        checkpoint=None,
        context=BudgetContext(900_000),
    )

    assert result["ok"] is True
    expected_identity = real_patch.game_identity(game)
    assert result["checkpoint"]["processedGames"][0][
        "durableIdentity"
    ] == expected_identity
    outcomes = real_lock_fixtures.lock_outcome_items(module)
    assert len(outcomes) == 1
    assert outcomes[0]["game_identity"] == expected_identity

    pulls = module._pulls_for_date(real_lock_fixtures.SLATE)
    manifest_game = module._latest_games_for_date(
        real_lock_fixtures.SLATE,
        pulls,
    )[0]
    # This is the production readback and provider-manifest authority validator,
    # not the chunk test double.
    assert real_patch._get_lock_outcome(
        module,
        real_lock_fixtures.SLATE,
        manifest_game,
    ) == outcomes[0]
    progress = real_patch._progress(
        module,
        real_lock_fixtures.SLATE,
        pulls,
        [manifest_game],
        module._now_utc(),
        ensure_canonical=False,
    )
    assert progress["lockOutcomeCount"] == 1
    assert progress["noPredictionDataCount"] == 1
    assert progress["missedCount"] == 0


@pytest.mark.parametrize(
    ("corrupt_identity", "valid_identity"),
    [
        ("provider:game-0", "official:822865"),
        ("official:822865", "provider:game-0"),
    ],
)
def test_present_invalid_outcome_alias_blocks_valid_counterpart_and_write(
    corrupt_identity,
    valid_identity,
):
    module = ChunkModule(game_count=1)
    patch = ChunkPatch()
    corrupt = patch._strict_outcome(corrupt_identity, ["corrupt"])
    corrupt["lock_outcome_fingerprint"] = "0" * 64
    module.outcomes[corrupt_identity] = corrupt
    module.outcomes[valid_identity] = patch._strict_outcome(
        valid_identity, ["valid-counterpart"]
    )
    module = _install(module, patch)

    result = _invoke(module)

    assert result["ok"] is False
    assert result["errorCode"] == (
        "IMMUTABLE_LOCK_OUTCOME_AUTHORITY_INVALID"
    )
    assert module.terminal_writes == []


def test_present_invalid_official_alias_blocks_new_provider_write():
    module = ChunkModule(game_count=1)
    patch = ChunkPatch()
    corrupt = patch._strict_outcome("official:822865", ["corrupt"])
    corrupt["write_once"] = False
    module.outcomes["official:822865"] = corrupt
    module = _install(module, patch)

    result = _invoke(module)

    assert result["ok"] is False
    assert result["errorCode"] == (
        "IMMUTABLE_LOCK_OUTCOME_AUTHORITY_INVALID"
    )
    assert module.terminal_writes == []
    assert "provider:game-0" not in module.outcomes


def test_existing_official_keyed_outcome_blocks_duplicate_and_requires_review():
    module = ChunkModule(game_count=1)
    patch = ChunkPatch()
    module.outcomes["official:822865"] = patch._strict_outcome(
        "official:822865", ["existing"]
    )
    module = _install(module, patch)

    result = _invoke(module)

    assert result["ok"] is False
    assert result["errorCode"] == (
        "NONCANONICAL_TERMINAL_ALIAS_REQUIRES_REVIEW"
    )
    assert module.terminal_writes == []


def test_existing_official_keyed_canonical_requires_review_without_terminal_write():
    module = ChunkModule(game_count=1)
    stage, canonical = _canonical_rows("official:822865")
    module.stages["official:822865"] = stage
    module.canonicals["official:822865"] = canonical
    module = _install(module)

    result = _invoke(module)

    assert result["ok"] is False
    assert result["errorCode"] == (
        "NONCANONICAL_TERMINAL_ALIAS_REQUIRES_REVIEW"
    )
    assert module.terminal_writes == []


def test_immutable_outcome_from_different_manifest_revision_fails_closed():
    module = ChunkModule(game_count=1)
    patch = ChunkPatch()
    outcome = patch._strict_outcome(
        "provider:game-0", ["existing-other-revision"]
    )
    outcome["provider_manifest_authority"]["fingerprint"] = (
        "different-manifest-revision"
    )
    outcome["provider_manifest_fingerprint"] = (
        "different-manifest-revision"
    )
    outcome["lock_outcome_fingerprint"] = _fingerprint({
        key: value
        for key, value in outcome.items()
        if key != "lock_outcome_fingerprint"
    })
    module.outcomes["provider:game-0"] = outcome
    module = _install(module, patch)

    result = _invoke(module)

    assert result["ok"] is False
    assert result["errorCode"] == (
        "DURABLE_TERMINAL_MANIFEST_AUTHORITY_MISMATCH"
    )
    assert module.terminal_writes == []


def test_duplicate_provider_and_official_durable_authority_fails_closed():
    module = ChunkModule(game_count=1)
    patch = ChunkPatch()
    module.outcomes["provider:game-0"] = patch._strict_outcome(
        "provider:game-0", ["existing-provider"]
    )
    module.outcomes["official:822865"] = patch._strict_outcome(
        "official:822865", ["existing-official"]
    )
    module = _install(module, patch)

    result = _invoke(module)

    assert result["ok"] is False
    assert result["errorCode"] == (
        "AMBIGUOUS_DURABLE_TERMINAL_IDENTITY"
    )
    assert module.terminal_writes == []


def test_dual_stage_and_outcome_authority_fails_closed():
    module = ChunkModule(game_count=1)
    patch = ChunkPatch()
    identity = "official:822865"
    module.outcomes[identity] = patch._strict_outcome(
        identity, ["existing"]
    )
    stage, canonical = _canonical_rows(identity)
    module.stages[identity] = stage
    module.canonicals[identity] = canonical
    module = _install(module, patch)

    result = _invoke(module)

    assert result["ok"] is False
    assert result["errorCode"] == "AMBIGUOUS_DUAL_TERMINAL_AUTHORITY"
    assert module.terminal_writes == []


def test_ambiguous_manifest_official_crosswalk_fails_before_lease_or_write():
    module = ChunkModule(game_count=2)
    module.games = [
        _game(0, official_pk="822865"),
        _game(1, official_pk="822865"),
    ]
    module = _install(module)

    result = _invoke(module)

    assert result["ok"] is False
    assert result["stage"] == "RESOLVE_MANIFEST"
    assert result["errorCode"] == (
        "COOPERATIVE_TERMINAL_CHUNK_AMBIGUOUS_MANIFEST_IDENTITY"
    )
    assert module.lease_acquires == []
    assert module.terminal_writes == []


def test_writer_lease_contention_defers_without_terminal_reads_or_writes():
    module = _install(ChunkModule(game_count=1))
    module.lease_contended = True

    result = _invoke(module)

    assert result["ok"] is True
    assert result["deferred"] is True
    assert result["stage"] == "MUTATION_LEASE_CONTENDED"
    assert result["errorCode"] == "WRITER_LEASE_CONTENDED"
    assert module.outcome_reads == []
    assert module.stage_reads == []
    assert module.terminal_writes == []
    assert module.lease_releases == []


@pytest.mark.parametrize("release_mode", ["raise", "ambiguous"])
def test_writer_lease_release_ambiguity_fails_closed_after_durable_write(
    release_mode,
):
    module = _install(ChunkModule(game_count=1))
    module.release_mode = release_mode

    result = _invoke(module)

    assert result["ok"] is False
    assert result["stage"] == "RELEASE_MUTATION_LEASE"
    assert result["checkpoint"]["nextGameIndex"] == 0
    assert module.terminal_writes == ["provider:game-0"]
    assert "provider:game-0" in module.outcomes

    module.release_mode = "success"
    retry = _invoke(module, result["checkpoint"])
    assert retry["ok"] is True
    assert retry["complete"] is False
    assert retry["deferred"] is True
    assert retry["stage"] == "MUTATION_LEASE_CONTENDED"
    assert retry["errorCode"] == "WRITER_LEASE_CONTENDED"
    assert retry["checkpoint"]["nextGameIndex"] == 0
    assert module.terminal_writes == ["provider:game-0"]

    module.now += timedelta(seconds=961)
    resumed = _invoke(module, retry["checkpoint"])
    assert resumed["ok"] is True
    assert resumed["deferred"] is False
    assert resumed["checkpoint"]["nextGameIndex"] == 1
    assert module.terminal_writes == ["provider:game-0"]


def test_checkpoint_is_bound_to_request_and_full_manifest_authority():
    module = _install(ChunkModule(game_count=1))
    first = _invoke(module)

    wrong_request = _invoke(
        module,
        first["checkpoint"],
        request_epoch=REQUEST_EPOCH + 1,
    )
    assert wrong_request["ok"] is False
    assert wrong_request["checkpointWriteAllowed"] is False

    module.games[0]["officialGamePk"] = "999999"
    changed_manifest = _invoke(module, first["checkpoint"])
    assert changed_manifest["ok"] is False
    assert changed_manifest["checkpointWriteAllowed"] is False


def test_checkpoint_rejects_changed_schedule_detail():
    module = _install(ChunkModule(game_count=2))
    first = _invoke(module)
    module.games[0]["commence_time"] = (
        "2026-08-05T17:30:00+00:00"
    )

    result = _invoke(module, first["checkpoint"])

    assert result["ok"] is False
    assert result["checkpointWriteAllowed"] is False
    assert result["stage"] in {
        "RESOLVE_MANIFEST",
        "BIND_MANIFEST_AUTHORITY",
    }


def test_raw_manifest_list_order_is_nonsemantic():
    module = _install(ChunkModule(game_count=2))
    first = _invoke(module)
    assert first["ok"] is True
    module.games = list(reversed(module.games))

    second = _invoke(module, first["checkpoint"])

    assert second["ok"] is True
    assert second["checkpoint"]["nextGameIndex"] == 2
    assert [
        entry["gameIdentity"]
        for entry in second["checkpoint"]["processedGames"]
    ] == ["provider:game-0", "provider:game-1"]


def test_checkpoint_rejects_forged_processed_game_order():
    module = _install(ChunkModule(game_count=2))
    first = _invoke(module)
    assert first["ok"] is True
    second = _invoke(module, first["checkpoint"])
    assert second["ok"] is True
    forged = copy.deepcopy(second["checkpoint"])
    forged["processedGames"] = list(
        reversed(forged["processedGames"])
    )
    forged["checkpointFingerprint"] = (
        repair._cooperative_terminal_checkpoint_fingerprint(forged)
    )

    result = _invoke(module, forged)

    assert result["ok"] is False
    assert result["checkpointWriteAllowed"] is False
    assert result["stage"] == "BIND_MANIFEST_AUTHORITY"



def test_forged_counts_fail_even_with_recomputed_checkpoint_fingerprint():
    module = _install(ChunkModule(game_count=1))
    first = _invoke(module)
    forged = copy.deepcopy(first["checkpoint"])
    forged["terminalCount"] = 99
    forged["checkpointFingerprint"] = (
        repair._cooperative_terminal_checkpoint_fingerprint(forged)
    )

    result = _invoke(module, forged)

    assert result["ok"] is False
    assert result["checkpointWriteAllowed"] is False
    assert result["stage"] == "BIND_MANIFEST_AUTHORITY"


def test_candidate_alias_queries_are_capped_before_any_query(monkeypatch):
    calls = []

    def query(module, slate, prefix):
        del module, slate
        calls.append(prefix)
        return []

    monkeypatch.setattr(real_patch, "_query_prediction_items", query)
    game = {
        "game_id": "root",
        "officialGamePk": "822865",
        "commence_time": "2026-08-05T17:00:00+00:00",
    }
    scoring = [
        {
            "games": [
                {
                    "game_id": f"alias-{index}",
                    "officialGamePk": "822865",
                    "commence_time": (
                        "2026-08-05T17:00:00+00:00"
                    ),
                }
            ]
        }
        for index in range(5)
    ]

    token = real_patch._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT.set(4)
    try:
        with pytest.raises(
            RuntimeError,
            match="COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT_EXCEEDED",
        ):
            real_patch._candidate_items(object(), SLATE, game, scoring)
    finally:
        real_patch._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT.reset(
            token
        )
    assert calls == []

    token = real_patch._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT.set(4)
    try:
        real_patch._candidate_items(object(), SLATE, game, scoring[:3])
    finally:
        real_patch._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT.reset(
            token
        )
    assert len(calls) == 4


def test_alias_and_canonical_only_contexts_are_always_reset():
    module = _install(ChunkModule(game_count=1))
    result = _invoke(module)

    assert result["ok"] is True
    assert module.patch._STATUS_READ_CACHE.get() is None
    assert (
        module.patch._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT.get()
        is None
    )


@pytest.mark.parametrize("candidate_failure", [False, True])
def test_outer_context_values_are_restored_on_success_and_failure(
    candidate_failure,
):
    class MaybeFailPatch(ChunkPatch):
        def _last_prelock_candidate(
            self, module, slate, game, scoring
        ):
            if candidate_failure:
                return (
                    {"predictedWinner": "Home 0"},
                    {"persisted": True},
                    [],
                    [],
                )
            return super()._last_prelock_candidate(
                module, slate, game, scoring
            )

    patch = MaybeFailPatch()
    module = _install(ChunkModule(game_count=1), patch)
    outer_cache = {"canonicalPulls": {"outer": ["sentinel"]}}
    cache_token = patch._STATUS_READ_CACHE.set(outer_cache)
    alias_token = (
        patch._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT.set(3)
    )
    try:
        result = _invoke(module)
        assert result["ok"] is (not candidate_failure)
        assert patch._STATUS_READ_CACHE.get() is outer_cache
        assert (
            patch._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT.get()
            == 3
        )
    finally:
        patch._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT.reset(
            alias_token
        )
        patch._STATUS_READ_CACHE.reset(cache_token)



class AtomicReadClient:
    def __init__(self, items):
        self.items = copy.deepcopy(items)
        self.calls = []
        self.mode = "ok"

    def transact_get_items(self, *, TransactItems, ReturnConsumedCapacity):
        assert ReturnConsumedCapacity == "NONE"
        self.calls.append(copy.deepcopy(TransactItems))
        deserializer = real_patch.TypeDeserializer()
        serializer = real_patch.TypeSerializer()
        responses = []
        for index, request in enumerate(TransactItems):
            get = request["Get"]
            key = {
                name: deserializer.deserialize(value)
                for name, value in get["Key"].items()
            }
            item = copy.deepcopy(
                self.items.get(
                    (get["TableName"], key["PK"], key["SK"])
                )
            )
            if self.mode == "missing" and index == 0:
                item = None
            if self.mode == "mutated" and index == 0 and item:
                item["tampered"] = True
            responses.append(
                {
                    "Item": {
                        name: serializer.serialize(value)
                        for name, value in item.items()
                    }
                }
                if item
                else {}
            )
        if self.mode == "short":
            responses = responses[:-1]
        return {"Responses": responses}


def _real_atomic_fixture():
    lock_name = "lock-authority-table"
    pulls_name = "pull-authority-table"
    outcome = {
        "PK": "LOCK#2026-08-05",
        "SK": "OUTCOME#provider-0",
        "record_type": "outcome",
        "write_once": True,
    }
    stage = {
        "PK": "LOCK#2026-08-05",
        "SK": "STAGE#provider-1",
        "record_type": "stage",
        "write_once": True,
    }
    canonical = {
        "PK": "PULLS#mlb#2026-08-05",
        "SK": "LOCKED#provider-1",
        "record_type": "canonical",
        "write_once": True,
    }
    manifest = {
        "PK": "PULLS#mlb#2026-08-05",
        "SK": "MANIFEST#membership",
        "record_type": "manifest",
        "write_once": True,
    }
    items = {
        (lock_name, outcome["PK"], outcome["SK"]): outcome,
        (lock_name, stage["PK"], stage["SK"]): stage,
        (pulls_name, canonical["PK"], canonical["SK"]): canonical,
        (pulls_name, manifest["PK"], manifest["SK"]): manifest,
    }
    client = AtomicReadClient(items)
    lock_table = SimpleNamespace(
        name=lock_name,
        meta=SimpleNamespace(client=client),
    )
    pulls_table = SimpleNamespace(
        name=pulls_name,
        meta=SimpleNamespace(client=client),
    )
    module = SimpleNamespace(
        TABLE=lock_table,
        history=SimpleNamespace(PULLS=pulls_table),
    )
    manifest_item = {
        "tableRole": "PULLS_TABLE",
        "PK": manifest["PK"],
        "SK": manifest["SK"],
        "itemFingerprint": (
            real_patch._cooperative_terminal_item_fingerprint(manifest)
        ),
    }

    def evidence(identity, state, primary_items):
        value = {
            "durableIdentity": identity,
            "terminalState": state,
            "items": [*primary_items, manifest_item],
        }
        value["evidenceFingerprint"] = (
            real_patch._cooperative_terminal_evidence_fingerprint(value)
        )
        return value

    outcome_item = {
        "tableRole": "LOCK_TABLE",
        "PK": outcome["PK"],
        "SK": outcome["SK"],
        "itemFingerprint": (
            real_patch._cooperative_terminal_item_fingerprint(outcome)
        ),
    }
    stage_item = {
        "tableRole": "LOCK_TABLE",
        "PK": stage["PK"],
        "SK": stage["SK"],
        "itemFingerprint": (
            real_patch._cooperative_terminal_item_fingerprint(stage)
        ),
    }
    canonical_item = {
        "tableRole": "PULLS_TABLE",
        "PK": canonical["PK"],
        "SK": canonical["SK"],
        "itemFingerprint": (
            real_patch._cooperative_terminal_item_fingerprint(canonical)
        ),
    }
    processed = [
        {
            "durableEvidence": evidence(
                "provider:0",
                "LOCKED_NO_PREDICTION_DATA",
                [outcome_item],
            )
        },
        {
            "durableEvidence": evidence(
                "provider:1",
                "LOCKED_CANONICAL",
                [stage_item, canonical_item],
            )
        },
    ]
    authority = {"atomicItems": [manifest_item]}
    return module, client, processed, authority


def test_real_atomic_verify_dedupes_mixed_authority_and_uses_exact_tables():
    module, client, processed, authority = _real_atomic_fixture()

    result = real_patch._cooperative_terminal_atomic_verify(
        module,
        processed,
        authority,
    )

    assert result["ok"] is True
    assert result["atomicSnapshot"] is True
    assert result["itemCount"] == 4
    assert result["maxItemCount"] == 100
    assert len(result["readSetFingerprint"]) == 64
    assert len(client.calls) == 1
    table_names = [
        request["Get"]["TableName"]
        for request in client.calls[0]
    ]
    assert table_names.count("lock-authority-table") == 2
    assert table_names.count("pull-authority-table") == 2


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("missing", "COOPERATIVE_TERMINAL_ATOMIC_ITEM_MISSING"),
        ("mutated", "COOPERATIVE_TERMINAL_ATOMIC_ITEM_MISMATCH"),
        ("short", "COOPERATIVE_TERMINAL_ATOMIC_RESPONSE_COUNT_MISMATCH"),
    ],
)
def test_real_atomic_verify_fails_closed_on_snapshot_anomaly(mode, error):
    module, client, processed, authority = _real_atomic_fixture()
    client.mode = mode

    with pytest.raises(RuntimeError, match=error):
        real_patch._cooperative_terminal_atomic_verify(
            module,
            processed,
            authority,
        )



def _real_all_quarantine_atomic_fixture(
    game_count=15,
    manifest_root_count=1,
):
    lock_name = "lock-authority-table"
    pulls_name = "pull-authority-table"
    manifests = [
        {
            "PK": "PULLS#mlb#2026-08-05",
            "SK": f"MANIFEST#membership#{root}",
            "record_type": "manifest",
            "write_once": True,
        }
        for root in range(manifest_root_count)
    ]
    items = {
        (pulls_name, manifest["PK"], manifest["SK"]): manifest
        for manifest in manifests
    }
    manifest_items = [
        {
            "tableRole": "PULLS_TABLE",
            "PK": manifest["PK"],
            "SK": manifest["SK"],
            "itemFingerprint": (
                real_patch._cooperative_terminal_item_fingerprint(manifest)
            ),
        }
        for manifest in manifests
    ]
    processed = []
    for index in range(game_count):
        identity = f"provider:quarantine-{index}"
        outcome = {
            "PK": "LOCK#2026-08-05",
            "SK": f"OUTCOME#{index}",
            "record_type": "outcome",
            "write_once": True,
        }
        candidate = {
            "PK": "GAME_WINNERS#mlb#2026-08-05",
            "SK": f"PREGAME#{index}",
            "record_type": "candidate",
            "write_once": True,
        }
        source = {
            "PK": "PULLS#mlb#2026-08-05",
            "SK": f"PULL#SLOT#{index:02d}",
            "record_type": "pull_run",
            "write_once": True,
        }
        items.update(
            {
                (lock_name, outcome["PK"], outcome["SK"]): outcome,
                (pulls_name, candidate["PK"], candidate["SK"]): candidate,
                (pulls_name, source["PK"], source["SK"]): source,
            }
        )
        primary = [
            {
                "tableRole": "LOCK_TABLE",
                "PK": outcome["PK"],
                "SK": outcome["SK"],
                "itemFingerprint": (
                    real_patch._cooperative_terminal_item_fingerprint(
                        outcome
                    )
                ),
            },
            {
                "tableRole": "PULLS_TABLE",
                "PK": candidate["PK"],
                "SK": candidate["SK"],
                "itemFingerprint": (
                    real_patch._cooperative_terminal_item_fingerprint(
                        candidate
                    )
                ),
            },
            {
                "tableRole": "PULLS_TABLE",
                "PK": source["PK"],
                "SK": source["SK"],
                "itemFingerprint": (
                    real_patch._cooperative_terminal_item_fingerprint(
                        source
                    )
                ),
            },
        ]
        evidence = {
            "durableIdentity": identity,
            "terminalState": (
                "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
            ),
            "authorityItemCount": 3,
            "dependencyItemCount": len(manifest_items),
            "manifestAuthorityEvidenceFingerprint": "a" * 64,
            "items": [
                *primary,
                *copy.deepcopy(manifest_items),
            ],
        }
        evidence["evidenceFingerprint"] = (
            real_patch._cooperative_terminal_evidence_fingerprint(
                evidence
            )
        )
        processed.append({"durableEvidence": evidence})

    client = AtomicReadClient(items)
    module = SimpleNamespace(
        TABLE=SimpleNamespace(
            name=lock_name,
            meta=SimpleNamespace(client=client),
        ),
        history=SimpleNamespace(
            PULLS=SimpleNamespace(
                name=pulls_name,
                meta=SimpleNamespace(client=client),
            )
        ),
    )
    return module, client, processed, {
        "atomicItems": manifest_items,
    }


def test_real_atomic_verify_accepts_all_fifteen_quarantines_with_46_reads():
    module, client, processed, authority = (
        _real_all_quarantine_atomic_fixture()
    )

    proof = real_patch._cooperative_terminal_atomic_verify(
        module,
        processed,
        authority,
    )

    assert proof["ok"] is True
    assert proof["atomicSnapshot"] is True
    assert proof["itemCount"] == 46
    assert proof["maxItemCount"] == 100
    assert len(proof["readSetFingerprint"]) == 64
    assert len(client.calls) == 1
    assert len(client.calls[0]) == 46


def test_real_atomic_verify_accepts_two_manifest_roots_and_47_reads():
    module, client, processed, authority = (
        _real_all_quarantine_atomic_fixture(
            manifest_root_count=2,
        )
    )

    proof = real_patch._cooperative_terminal_atomic_verify(
        module,
        processed,
        authority,
    )

    assert proof["ok"] is True
    assert proof["itemCount"] == 47
    assert proof["maxItemCount"] == 100
    assert len(client.calls[0]) == 47


@pytest.mark.parametrize(
    "storage_key",
    [
        ("pull-authority-table", "GAME_WINNERS#mlb#2026-08-05", "PREGAME#0"),
        ("pull-authority-table", "PULLS#mlb#2026-08-05", "PULL#SLOT#00"),
    ],
)
def test_quarantine_candidate_and_source_atomic_mutations_fail_closed(
    storage_key,
):
    module, client, processed, authority = (
        _real_all_quarantine_atomic_fixture()
    )
    client.items[storage_key]["tamperedAfterTerminalWrite"] = True

    with pytest.raises(
        RuntimeError,
        match="COOPERATIVE_TERMINAL_ATOMIC_ITEM_MISMATCH",
    ):
        real_patch._cooperative_terminal_atomic_verify(
            module,
            processed,
            authority,
        )


def _synthetic_terminal_evidence(
    index,
    state,
    manifest_items,
):
    identity = f"provider:synthetic-{index}"
    if state == "LOCKED_NO_PREDICTION_DATA":
        primary = [
            {
                "tableRole": "LOCK_TABLE",
                "PK": "LOCK#synthetic",
                "SK": f"OUTCOME#{index}",
                "itemFingerprint": _fingerprint(["outcome", index]),
            }
        ]
    else:
        primary = [
            {
                "tableRole": "LOCK_TABLE",
                "PK": "LOCK#synthetic",
                "SK": f"STAGE#{index}",
                "itemFingerprint": _fingerprint(["stage", index]),
            },
            {
                "tableRole": "PULLS_TABLE",
                "PK": "PULLS#synthetic",
                "SK": f"CANONICAL#{index}",
                "itemFingerprint": _fingerprint(["canonical", index]),
            },
        ]
    evidence = {
        "durableIdentity": identity,
        "terminalState": state,
        "authorityItemCount": len(primary),
        "dependencyItemCount": len(manifest_items),
        "manifestAuthorityEvidenceFingerprint": "a" * 64,
        "items": [*primary, *copy.deepcopy(manifest_items)],
    }
    evidence["evidenceFingerprint"] = (
        repair._cooperative_terminal_evidence_fingerprint(evidence)
    )
    return {
        "gameIdentity": identity,
        "durableIdentity": identity,
        "terminalState": state,
        "reconciled": False,
        "durableEvidence": evidence,
    }


@pytest.mark.parametrize(("manifest_root_count", "expected"), [(1, 16), (2, 17)])
def test_fifteen_no_prediction_roots_dedupe_to_bounded_read_set(
    manifest_root_count,
    expected,
):
    roots = [
        {
            "tableRole": "PULLS_TABLE",
            "PK": "PULLS#manifest",
            "SK": f"MANIFEST#{index}",
            "itemFingerprint": _fingerprint(["manifest", index]),
        }
        for index in range(manifest_root_count)
    ]
    processed = [
        _synthetic_terminal_evidence(
            index,
            "LOCKED_NO_PREDICTION_DATA",
            roots,
        )
        for index in range(15)
    ]

    requests, fingerprint = repair._cooperative_terminal_atomic_read_set(
        processed,
        {"atomicItems": roots},
    )

    assert len(requests) == expected
    assert len(fingerprint) == 64


def test_fifteen_canonical_games_plus_two_manifest_roots_fit_under_max100():
    roots = [
        {
            "tableRole": "PULLS_TABLE",
            "PK": "PULLS#manifest",
            "SK": f"MANIFEST#{index}",
            "itemFingerprint": _fingerprint(["manifest", index]),
        }
        for index in range(2)
    ]
    processed = [
        _synthetic_terminal_evidence(
            index,
            "LOCKED_CANONICAL",
            roots,
        )
        for index in range(15)
    ]

    requests, _ = repair._cooperative_terminal_atomic_read_set(
        processed,
        {"atomicItems": roots},
    )

    assert len(requests) == 32
    assert len(requests) < repair.COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS == 100
