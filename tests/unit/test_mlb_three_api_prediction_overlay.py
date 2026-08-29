import copy

from hello_world import mlb_fundamentals_snapshot_v2 as snapshot_v2
from hello_world import mlb_r7_source_honest_training_repair as r7_training
from hello_world import mlb_three_api_prediction_overlay as overlay
from hello_world import mlb_winner_stack_v2 as winner_stack


PERSISTED_AT = "2026-08-24T16:00:00+00:00"
LOCK_AT = "2026-08-24T16:15:00+00:00"


def _row():
    return {
        "official_game_pk": "123",
        "official_commence_time": "2026-08-24T23:10:00+00:00",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "predictedWinner": "Home Team",
        "winProbability": 0.60,
        "bookmakers": [
            {
                "key": "book1",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home Team", "price": -110},
                            {"name": "Away Team", "price": -110},
                        ],
                    }
                ],
            },
            {
                "key": "book2",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home Team", "price": 105},
                            {"name": "Away Team", "price": -125},
                        ],
                    }
                ],
            },
        ],
        "advancedContext": {
            "official_game_pk": "123",
            "schedule_authority": "MLB Stats API exact-date schedule",
        },
    }


def _signed_snapshot():
    source_row = {
        "gameId": "123",
        "officialGamePk": "123",
        "slateDateEt": "2026-08-24",
        "homeTeam": "Home Team",
        "awayTeam": "Away Team",
        "predictionSourcePullAt": "2026-08-24T15:55:00+00:00",
        "advanced_context": {},
    }
    for _output_name, context_name, _fields in snapshot_v2.GROUP_SPECS:
        source_row["advanced_context"][context_name] = {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "reason": "fixture source unavailable before lock",
        }
    return snapshot_v2.build(source_row, captured_at_utc=PERSISTED_AT)


def _snapshot_ref(snapshot):
    return {
        "version": snapshot["version"],
        "schemaCohort": snapshot["schemaCohort"],
        "gameId": snapshot["game"]["gameId"],
        "sourcePullId": snapshot["sourcePullId"],
        "evidenceCutoffUtc": snapshot["evidenceCutoffUtc"],
        "fingerprintVersion": snapshot["fingerprintVersion"],
        "fingerprint": snapshot["fingerprint"],
    }


def test_final_decision_materially_blends_existing_model_market_and_llm(monkeypatch):
    monkeypatch.setenv("MLB_THREE_API_ENABLED", "true")
    monkeypatch.setenv("MLB_THREE_API_REQUIRE_ALL_SOURCES", "true")

    bbd_context = {
        "sourceStatus": "CONNECTED",
        "operationsSucceeded": 4,
        "contextFingerprint": "bbd-fingerprint",
        "categories": {},
    }
    monkeypatch.setattr(
        overlay.bbd,
        "collect_game_context",
        lambda game, as_of_utc=None: bbd_context,
    )
    monkeypatch.setattr(
        overlay.bbd,
        "merge_into_advanced_context",
        lambda context, bbd: {
            **context,
            "big_balls_data_pro": bbd,
            "three_api_source_status": {
                "mlbStatsApi": "PRESENT",
                "theOddsApi": "PRESENT",
                "bigBallsDataPro": "CONNECTED",
            },
        },
    )
    monkeypatch.setattr(
        overlay.llm,
        "analyze_game",
        lambda game, context, as_of_utc=None: {
            "status": "CONNECTED",
            "modelId": "test-model",
            "predictedWinner": "Away Team",
            "predictedLoser": "Home Team",
            "winProbability": 0.70,
            "sourceCompleteness": 1.0,
            "evidenceFingerprint": "llm-fingerprint",
        },
    )

    result = overlay.apply_prediction_overlay(_row(), as_of_utc="2026-08-24T16:00:00+00:00")
    decision = result["threeApiDecision"]
    components = {row["component"]: row for row in decision["components"]}

    assert set(components) == {
        "existingAutonomousMLModel",
        "theOddsApiNoVigMarketConsensus",
        "bedrockThreeSourceAnalyst",
    }
    assert components["bedrockThreeSourceAnalyst"]["weight"] > 0
    assert components["theOddsApiNoVigMarketConsensus"]["weight"] > 0
    assert all(decision["sourceReady"].values())
    assert result["predictedWinner"] in {"Home Team", "Away Team"}
    assert result["predictedLoser"] in {"Home Team", "Away Team"}
    assert result["predictedWinner"] != result["predictedLoser"]
    assert decision["llmEvidenceFingerprint"] == "llm-fingerprint"
    assert decision["bbdContextFingerprint"] == "bbd-fingerprint"
    assert decision["noPass"] is True
    assert decision["accuracyGuarantee"] is False


def test_overlay_and_winner_stack_preserve_signed_v2_evidence_and_verdicts(
    monkeypatch,
):
    monkeypatch.setenv("MLB_THREE_API_ENABLED", "true")
    monkeypatch.setenv("MLB_THREE_API_REQUIRE_ALL_SOURCES", "true")

    snapshot = _signed_snapshot()
    snapshot_ref = _snapshot_ref(snapshot)
    v2_verdict_before = snapshot_v2.validate(snapshot)
    r7_verdict_before = r7_training.validate_snapshot_for_r7_training(
        snapshot,
        PERSISTED_AT,
        LOCK_AT,
    )
    assert v2_verdict_before == []
    assert r7_verdict_before == (True, [])

    row = _row()
    row.pop("advancedContext")
    row.update(
        {
            "predictionPersistedAtUtc": PERSISTED_AT,
            "lockedAtUtc": LOCK_AT,
            "threeApiContext": {"contextRole": "three-api-only"},
            "context": {"genericContextSentinel": "must-not-be-read"},
            "fundamentalsSnapshotV2": copy.deepcopy(snapshot),
            "fundamentalsSnapshotV2Ref": copy.deepcopy(snapshot_ref),
            "frozenFeatureVector": {
                "fingerprint": "signed-vector-fixture",
                "homeTeam": "Home Team",
                "awayTeam": "Away Team",
                "predictedWinner": "Home Team",
                "fundamentalsSnapshotV2": copy.deepcopy(snapshot),
                "fundamentalsSnapshotV2Ref": copy.deepcopy(snapshot_ref),
            },
        }
    )
    row["featureSnapshot"] = copy.deepcopy(row["frozenFeatureVector"])
    row["mlFeatureFreeze"] = {
        "homeTeam": "Home Team",
        "awayTeam": "Away Team",
        "predictedWinner": "Home Team",
        "vectorFingerprint": "signed-vector-fixture",
    }
    expected_snapshot = copy.deepcopy(row["fundamentalsSnapshotV2"])
    expected_ref = copy.deepcopy(row["fundamentalsSnapshotV2Ref"])
    expected_vector = copy.deepcopy(row["frozenFeatureVector"])
    expected_feature_snapshot = copy.deepcopy(row["featureSnapshot"])
    expected_freeze = copy.deepcopy(row["mlFeatureFreeze"])

    observed_contexts = []
    bbd_context = {
        "sourceStatus": "CONNECTED",
        "operationsSucceeded": 4,
        "contextFingerprint": "bbd-fingerprint",
        "categories": {},
    }
    monkeypatch.setattr(
        overlay.bbd,
        "collect_game_context",
        lambda game, as_of_utc=None: bbd_context,
    )

    def merge_context(context, collected):
        observed_contexts.append(copy.deepcopy(context))
        return {**context, "big_balls_data_pro": collected}

    monkeypatch.setattr(overlay.bbd, "merge_into_advanced_context", merge_context)

    def analyze_game(game, context, as_of_utc=None):
        observed_contexts.append(copy.deepcopy(context))
        return {
            "status": "CONNECTED",
            "modelId": "test-model",
            "predictedWinner": "Home Team",
            "predictedLoser": "Away Team",
            "winProbability": 0.62,
            "sourceCompleteness": 1.0,
            "evidenceFingerprint": "llm-fingerprint",
        }

    monkeypatch.setattr(overlay.llm, "analyze_game", analyze_game)

    transformed = winner_stack.enhance_result({"predictions": [row]})
    result = overlay.apply_to_value(transformed, as_of_utc=PERSISTED_AT)
    overlaid = result["predictions"][0]

    assert observed_contexts
    assert all(context.get("contextRole") == "three-api-only" for context in observed_contexts)
    assert all("groups" not in context for context in observed_contexts)
    assert all("genericContextSentinel" not in context for context in observed_contexts)
    assert overlaid["fundamentalsSnapshotV2"] == expected_snapshot
    assert overlaid["fundamentalsSnapshotV2Ref"] == expected_ref
    assert overlaid["frozenFeatureVector"] == expected_vector
    assert overlaid["featureSnapshot"] == expected_feature_snapshot
    assert overlaid["mlFeatureFreeze"] == expected_freeze
    assert "threeApiDecision" not in overlaid["frozenFeatureVector"]
    assert "threeApiDecision" not in overlaid["featureSnapshot"]
    assert "threeApiDecision" not in overlaid["mlFeatureFreeze"]
    assert overlaid["threeApiContext"]["big_balls_data_pro"] == bbd_context
    assert snapshot_v2.validate(overlaid["fundamentalsSnapshotV2"]) == v2_verdict_before
    assert r7_training.validate_snapshot_for_r7_training(
        overlaid["fundamentalsSnapshotV2"],
        PERSISTED_AT,
        LOCK_AT,
    ) == r7_verdict_before


def test_strict_mode_rejects_missing_bbd_or_llm(monkeypatch):
    monkeypatch.setenv("MLB_THREE_API_ENABLED", "true")
    monkeypatch.setenv("MLB_THREE_API_REQUIRE_ALL_SOURCES", "true")
    monkeypatch.setattr(
        overlay.bbd,
        "collect_game_context",
        lambda game, as_of_utc=None: {
            "sourceStatus": "ERROR",
            "operationsSucceeded": 0,
            "errors": ["provider unavailable"],
        },
    )
    monkeypatch.setattr(
        overlay.bbd,
        "merge_into_advanced_context",
        lambda context, bbd: {**context, "big_balls_data_pro": bbd},
    )
    monkeypatch.setattr(
        overlay.llm,
        "analyze_game",
        lambda game, context, as_of_utc=None: {"status": "UNAVAILABLE"},
    )

    try:
        overlay.apply_prediction_overlay(_row())
    except overlay.ThreeApiPredictionError as exc:
        assert "THREE_API_SOURCE_NOT_READY" in str(exc)
    else:
        raise AssertionError("Strict mode must fail closed when BBD/LLM evidence is unavailable")


def test_overlay_installer_wraps_prediction_function(monkeypatch):
    monkeypatch.setenv("MLB_THREE_API_ENABLED", "true")
    namespace = {}

    def make_prediction():
        return _row()

    namespace["make_prediction"] = make_prediction
    monkeypatch.setattr(
        overlay,
        "apply_to_value",
        lambda value, as_of_utc=None, depth=0: {**value, "overlayApplied": True},
    )
    installed = overlay.install_named_overlays(namespace, "unit_test_module", ["make_prediction"])
    assert installed == ["make_prediction"]
    assert namespace["make_prediction"]()["overlayApplied"] is True
