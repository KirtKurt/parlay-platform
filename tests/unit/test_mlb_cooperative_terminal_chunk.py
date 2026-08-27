from __future__ import annotations

import copy
import hashlib
import json
import sys
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
        self.stages = {}
        self.canonicals = {}
        self.outcome_reads = []
        self.stage_reads = []
        self.terminal_writes = []
        self.lease_acquires = []
        self.lease_releases = []
        self.atomic_calls = []
        self.lease_contended = False
        self.release_mode = "success"
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
        del slate_date, force, scheduled
        self.original_calls += 1
        raise AssertionError(
            "the cooperative chunk must not call the whole-slate run_lock"
        )


class ChunkPatch:
    LOCK_EXECUTION_LEASE_VERSION = (
        "MLB-LOCK-EXECUTION-LEASE-v2-global-all-mutating"
    )

    def __init__(self):
        self._STATUS_READ_CACHE = ContextVar(
            f"test_cooperative_terminal_chunk_cache_{id(self)}",
            default=None,
        )
        self._COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT = ContextVar(
            f"test_cooperative_terminal_alias_limit_{id(self)}",
            default=None,
        )

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

    def _get_lock_outcome(self, module, slate, game):
        assert slate == SLATE
        request_cache = self._STATUS_READ_CACHE.get()
        assert isinstance(request_cache, dict)
        assert set(request_cache) == {"canonicalPulls"}
        identity = self.game_identity(game)
        module.outcome_reads.append(identity)
        return copy.deepcopy(module.outcomes.get(identity))

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
        return {
            "version": "test-provider-manifest-v1",
            "recordType": "test-immutable-provider-manifest",
            "pk": "PULL#manifest",
            "sk": "FULL#2026-08-05",
            "fingerprint": "verified-manifest",
            "gameCount": len(manifest),
            "canonicalGameIdentities": [
                game["game_id"] for game in manifest
            ],
            "immutable": True,
            "writeOnce": True,
            "consistentReadVerified": True,
            "officialScheduleAuthorityVersion": "test-official-v1",
            "officialScheduleAuthorityFingerprint": "official-fingerprint",
        }

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
            "official_prediction": False,
            "playable": False,
            "blocked": True,
            "training_eligible": False,
            "training_exclusion_reasons": [
                "missing_immutable_prediction"
            ],
            "reasons": list(reasons) or ["proven_absence"],
            "provider_manifest_fingerprint": "verified-manifest",
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
        assert authority["fingerprint"] == "verified-manifest"
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
                "providerManifestFingerprint": "verified-manifest",
                "vectorFingerprint": "vector-fingerprint",
                "promotionPolicyVersion": "test-promotion",
                "lockPolicy": "test-lock-policy",
                "modelVersion": "test-model",
                "items": items,
            }
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
        return {
            "acquired": True,
            "owner": f"lease-owner-{len(module.lease_acquires)}",
            "ownedKeys": [
                {"scope": "global", "key": "MLB_LOCK_EXECUTION#V2"},
                *[
                    {
                        "scope": "legacy_rollout_bridge",
                        "key": f"MLB_LOCK_RUNTIME#{offset}",
                    }
                    for offset in (-1, 0, 1)
                ],
            ],
        }

    def _release_lock_execution_lease(self, module, lease):
        module.lease_releases.append(copy.deepcopy(lease))
        if module.release_mode == "raise":
            raise RuntimeError("simulated release transport error")
        if module.release_mode == "ambiguous":
            return False
        return True

    def _cooperative_terminal_atomic_verify(
        self, module, processed_games
    ):
        module.atomic_calls.append(copy.deepcopy(processed_games))
        item_count = 0
        for entry in processed_games:
            for expected in entry["durableEvidence"]["items"]:
                item_count += 1
                observed = None
                if expected["tableRole"] == "LOCK_TABLE":
                    values = [
                        *module.outcomes.values(),
                        *module.stages.values(),
                    ]
                else:
                    values = list(module.canonicals.values())
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
            "itemCount": item_count,
            "maxItemCount": 100,
            "postStartPredictionCreationAllowed": False,
        }


def _canonical_rows(identity: str) -> tuple[dict, dict]:
    stage = {
        "PK": f"MLB_LOCK#{SLATE}",
        "SK": f"STAGE#{_fingerprint(identity)[:16]}",
        "stage_fingerprint": f"stage-fingerprint-{identity}",
        "candidate_proof": {"selectionFingerprint": "candidate"},
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
    assert final["atomicCompletionProof"] == {
        "atomicSnapshot": True,
        "itemCount": 2,
        "maxItemCount": 100,
        "applicationAppendOnlyAuthorityRequired": True,
    }
    assert len(module.atomic_calls) == 1
    assert len(module.lease_acquires) == 5
    assert len(module.lease_releases) == 5
    response = final["terminalReplayResponse"]
    assert response["durableTerminalVerificationComplete"] is True
    assert response["atomicDurableProofRequired"] is True
    assert response["atomicDurableItemCount"] == 2
    assert response["verificationIndex"] == 2
    assert response["processedGameCount"] == 2
    assert response["missedGameCount"] == 0
    assert response["postStartPredictionCreationAllowed"] is False
    assert response["immutablePredictionRewriteAllowed"] is False
    assert response["productionAuthorityChanged"] is False


def test_deleted_prefix_after_first_verification_fails_atomic_completion():
    module = _install(ChunkModule(game_count=2))

    first = _invoke(module)
    second = _invoke(module, first["checkpoint"])
    verify_first = _invoke(module, second["checkpoint"])
    assert verify_first["checkpoint"]["verificationIndex"] == 1

    del module.outcomes["official:822865"]

    verify_second = _invoke(module, verify_first["checkpoint"])
    assert verify_second["ok"] is True
    assert verify_second["checkpoint"]["verificationComplete"] is True

    final = _invoke(module, verify_second["checkpoint"])
    assert final["ok"] is False
    assert final["complete"] is False
    assert final["stage"] == "ATOMIC_COMPLETION_PROOF"
    assert final["checkpoint"]["verificationComplete"] is True
    assert len(module.atomic_calls) == 1


def test_mutated_verified_row_fails_atomic_completion():
    module = _install(ChunkModule(game_count=1))
    checkpoint, _ = _process_then_verify_all(module)
    module.outcomes["official:822865"]["reasons"].append("corruption")

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
    assert result["checkpoint"]["processedGames"][0][
        "durableIdentity"
    ] == "provider-real-validator"
    outcomes = real_lock_fixtures.lock_outcome_items(module)
    assert len(outcomes) == 1
    assert outcomes[0]["game_identity"] == "provider-real-validator"

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


def test_existing_official_keyed_outcome_is_used_without_duplicate_write():
    module = ChunkModule(game_count=1)
    patch = ChunkPatch()
    module.outcomes["official:822865"] = patch._strict_outcome(
        "official:822865", ["existing"]
    )
    module = _install(module, patch)

    result = _invoke(module)

    assert result["ok"] is True
    assert result["checkpoint"]["processedGames"][0][
        "durableIdentity"
    ] == "official:822865"
    assert result["checkpoint"]["processedGames"][0][
        "terminalState"
    ] == "LOCKED_NO_PREDICTION_DATA"
    assert module.terminal_writes == []


def test_existing_official_keyed_canonical_is_used_without_terminal_write():
    module = ChunkModule(game_count=1)
    stage, canonical = _canonical_rows("official:822865")
    module.stages["official:822865"] = stage
    module.canonicals["official:822865"] = canonical
    module = _install(module)

    result = _invoke(module)

    entry = result["checkpoint"]["processedGames"][0]
    assert result["ok"] is True
    assert entry["durableIdentity"] == "official:822865"
    assert entry["terminalState"] == "LOCKED_CANONICAL"
    assert len(entry["durableEvidence"]["items"]) == 2
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
    assert retry["checkpoint"]["nextGameIndex"] == 1
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
