#!/usr/bin/env python3
"""Read-only V7 odds-selective and V9 supervised evaluation against AWS evidence."""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts import run_mlb_historical_supervised_v9_shadow_cadence as cadence_state
except ImportError:  # Direct execution from the scripts directory.
    import run_mlb_historical_supervised_v9_shadow_cadence as cadence_state

V9_STRONG_MIN_PROBABILITY = 0.65
V9_LEAN_MIN_PROBABILITY = 0.55
V9_DIAGNOSTIC_MAX_ROWS = 5000


def _pct(value):
    return round(float(value or 0.0) * 100.0, 4)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else default
    except Exception:
        return default


def _team_name(row: Mapping[str, Any], side: str) -> str:
    signal = row.get(f"{side}Signal") or {}
    keys = (
        f"{side}Team",
        f"{side}TeamName",
        "teamName",
        "team",
        "name",
    )
    for key in keys:
        value = row.get(key) if key.startswith(side) else signal.get(key)
        if value not in (None, ""):
            return str(value)
    return side.upper()


def _game_id(row: Mapping[str, Any]) -> str:
    return str(row.get("officialGamePk") or row.get("gameId") or row.get("eventId") or "")


def _band(probability: float) -> str:
    if probability >= V9_STRONG_MIN_PROBABILITY:
        return "MLB_STRONG"
    if probability >= V9_LEAN_MIN_PROBABILITY:
        return "MLB_LEAN"
    return "PASS"


def _diagnostic_pick_rows(
    records: Iterable[Mapping[str, Any]],
    policy: Mapping[str, Any],
    policy_runtime: Any,
    dates: Iterable[str] | None = None,
) -> dict:
    allowed_dates = {str(value) for value in dates} if dates is not None else None
    rows = []
    counts = Counter()
    selected_correct = Counter()
    selected_total = Counter()
    daily = defaultdict(lambda: {"selected": 0, "correct": 0, "strong": 0, "lean": 0, "pass": 0})

    for record in records:
        day = str(record.get("slateDateEt") or "")
        if allowed_dates is not None and day not in allowed_dates:
            continue
        home_signal = record.get("homeSignal") or {}
        away_signal = record.get("awaySignal") or {}
        selected, scored_home, scored_away = policy_runtime.select_winner(home_signal, away_signal, policy)
        home_probability = _f(scored_home.get("winProbability"), 0.5)
        away_probability = _f(scored_away.get("winProbability"), 1.0 - home_probability)
        selected_side = "home" if selected is scored_home or home_probability >= away_probability else "away"
        selected_probability = max(home_probability, away_probability)
        band = _band(selected_probability)
        counts[band] += 1
        daily[day]["pass" if band == "PASS" else "strong" if band == "MLB_STRONG" else "lean"] += 1
        actual_home_won = record.get("homeWon") is True or record.get("homeWon") == 1
        actual_side = "home" if actual_home_won else "away"
        correct = selected_side == actual_side
        if band != "PASS":
            selected_total[band] += 1
            selected_correct[band] += int(correct)
            daily[day]["selected"] += 1
            daily[day]["correct"] += int(correct)
        rows.append({
            "slateDateEt": day,
            "gameId": _game_id(record),
            "homeTeam": _team_name(record, "home"),
            "awayTeam": _team_name(record, "away"),
            "predictedWinner": _team_name(record, selected_side),
            "selectedSide": selected_side,
            "selected": band != "PASS",
            "selectedPickBand": band,
            "selectedProbability": round(selected_probability, 8),
            "selectedProbabilityPct": round(selected_probability * 100.0, 4),
            "homeProbabilityPct": round(home_probability * 100.0, 4),
            "awayProbabilityPct": round(away_probability * 100.0, 4),
            "signalScore": round(_f(selected.get("score"), _f(selected.get("optimizedWinnerScore"))), 4),
            "actualWinner": _team_name(record, actual_side),
            "correct": correct if band != "PASS" else None,
            "canonicalLockValid": record.get("canonicalLockValid"),
            "trainingEligible": record.get("trainingEligible"),
            "duplicateContaminated": record.get("duplicateContaminated"),
            "featureVectorFingerprint": record.get("featureVectorFingerprint") or record.get("fingerprint"),
            "featureCutoff": record.get("featureCutoff") or record.get("perGameFeatureCutoff"),
        })

    rows.sort(key=lambda row: (row["slateDateEt"], row["gameId"], row["selectedPickBand"]))
    selected_count = sum(selected_total.values())
    correct_count = sum(selected_correct.values())
    by_band = {}
    for name in ("MLB_STRONG", "MLB_LEAN", "PASS"):
        total = counts[name]
        selected_n = selected_total[name]
        by_band[name] = {
            "gameCount": total,
            "selectedGameCount": selected_n,
            "correct": selected_correct[name],
            "accuracy": (selected_correct[name] / selected_n) if selected_n else None,
            "accuracyPct": round(selected_correct[name] * 100.0 / selected_n, 4) if selected_n else None,
        }
    daily_rows = []
    for day in sorted(daily):
        item = daily[day]
        daily_rows.append({
            "slateDateEt": day,
            "selectedGameCount": item["selected"],
            "correct": item["correct"],
            "accuracy": item["correct"] / item["selected"] if item["selected"] else None,
            "accuracyPct": round(item["correct"] * 100.0 / item["selected"], 4) if item["selected"] else None,
            "MLB_STRONG": item["strong"],
            "MLB_LEAN": item["lean"],
            "PASS": item["pass"],
        })
    return {
        "version": "MLB-V9-DIAGNOSTIC-SELECTED-PICK-BANDS-v1",
        "diagnosticOnly": True,
        "productionAuthority": False,
        "bandThresholds": {
            "MLB_STRONG": {"minimumSelectedProbability": V9_STRONG_MIN_PROBABILITY},
            "MLB_LEAN": {"minimumSelectedProbability": V9_LEAN_MIN_PROBABILITY, "maximumExclusive": V9_STRONG_MIN_PROBABILITY},
            "PASS": {"maximumExclusive": V9_LEAN_MIN_PROBABILITY},
        },
        "gameCount": len(rows),
        "selectedGameCount": selected_count,
        "correct": correct_count,
        "selectedAccuracy": correct_count / selected_count if selected_count else None,
        "selectedAccuracyPct": round(correct_count * 100.0 / selected_count, 4) if selected_count else None,
        "bandCounts": dict(counts),
        "byBand": by_band,
        "dailySelectedPickBands": daily_rows,
        "rowsTruncated": len(rows) > V9_DIAGNOSTIC_MAX_ROWS,
        "untruncatedRowCount": len(rows),
        "games": rows[:V9_DIAGNOSTIC_MAX_ROWS],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--previous-report")
    parser.add_argument("--handoff-output")
    args = parser.parse_args()

    import mlb_historical_optimizer_v7_recovery_entrypoint as runtime
    import mlb_historical_supervised_v9 as supervised_v9
    import mlb_historical_supervised_v9_integrity_v2 as integrity_v2
    import mlb_historical_v7_priority_repairs_v1 as repairs
    import mlb_historical_v7_selective_search_v2 as selective_v2

    handler = runtime.base.optimizer_handler
    integrity_v2.install(supervised_v9)
    supervised_v9.install(handler.optimizer, handler.policy_runtime)
    selective_v2.install(handler.optimizer)
    state = handler._load_state()
    if not isinstance(state, dict):
        raise RuntimeError("historical optimizer state is missing")
    records = handler._load_training_records(state)
    fingerprint = repairs.dataset_fingerprint(records)
    previous = _load_json(Path(args.previous_report)) if args.previous_report else {}
    current_count = int(state.get("eligibleGameCount") or len(records))
    full_increment = int(os.environ.get("MLB_V7_SHADOW_REFIT_INCREMENT_GAMES", "50"))
    light_increment = int(os.environ.get("MLB_V7_LIGHTWEIGHT_INCREMENT_GAMES", "25"))
    force = os.environ.get("MLB_V7_FORCE_SHADOW_REFIT", "false").lower() == "true"
    cadence = cadence_state.decide_cadence(
        previous,
        current_count=current_count,
        fingerprint=fingerprint,
        full_increment=full_increment,
        lightweight_increment=light_increment,
        force=force,
    )
    should_refit = bool(cadence["shouldRefit"])
    should_lightweight = bool(cadence["shouldLightweight"])

    base_report = {
        "proofType": "MLB_HISTORICAL_V7_V9_SHADOW_EVALUATION",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "readOnly": True,
        "retrospectiveShadowOnly": True,
        "prospectiveAuditRequiredBeforePromotion": True,
        "providerCallsMade": 0,
        "productionAuthorityChanged": False,
        "historicalChampionWritten": False,
        "productionCutoverWritten": False,
        "datasetFingerprint": fingerprint,
        "v7LearningCadenceStateVersion": cadence_state.VERSION,
        "shadowRefitIncrementGames": full_increment,
        "lightweightSelectiveEvaluationIncrementGames": light_increment,
        "canonicalFreshAuditIncrementGames": handler.FRESH_AUDIT_INCREMENT_GAMES,
        "newEligibleGamesSinceLastShadowFit": cadence["newEligibleGamesSinceLastShadowFit"],
        "newEligibleGamesSinceLastLightweightEvaluation": cadence[
            "newEligibleGamesSinceLastLightweightEvaluation"
        ],
        "remainingEligibleGamesUntilShadowRefit": cadence[
            "remainingEligibleGamesUntilShadowRefit"
        ],
        "remainingEligibleGamesUntilLightweightEvaluation": cadence[
            "remainingEligibleGamesUntilLightweightEvaluation"
        ],
        "state": {
            "phase": state.get("phase"), "currentDate": state.get("currentDate"),
            "currentSlotIndex": state.get("currentSlotIndex"), "eligibleGameCount": current_count,
            "completeSlateCount": state.get("completeSlateCount"), "optimizationRound": state.get("optimizationRound"),
            "featureDatasetVersion": state.get("featureDatasetVersion"),
            "rematerializationComplete": state.get("featureRematerializationComplete"),
            "rematerializationErrors": state.get("featureRematerializationErrors") or [],
        },
        "featurePopulation": repairs.feature_population_report(records, supervised_v9, handler.policy_runtime.BASELINE_POLICY),
        "operationsDiagnostics": repairs.rejection_and_lease_report(state, handler),
        "accuracyViews": repairs.selective_accuracy_report(records),
    }

    config = handler.optimizer.SearchConfig(
        minimum_training_games=handler.policy_runtime.MIN_TRAINING_GAMES,
        minimum_walk_forward_games=handler.policy_runtime.MIN_WALK_FORWARD_GAMES,
        minimum_untouched_holdout_games=handler.policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES,
        minimum_settled_games=handler.policy_runtime.MIN_TOTAL_SETTLED_GAMES,
        maximum_candidates=100,
        random_seed=1541,
    )

    if should_lightweight:
        try:
            base_report["v7SelectiveSearch"] = handler.optimizer.v7_selective_search(records, config)
        except Exception as exc:
            base_report["v7SelectiveSearch"] = {"ok": False, "version": selective_v2.VERSION, "status": "V7_SELECTIVE_SEARCH_ERROR", "errorType": type(exc).__name__, "error": str(exc), "promotionAuthority": False}

    if not should_refit:
        selective = base_report.get("v7SelectiveSearch") or {}
        blockers = []
        if selective and selective.get("ok") is not True:
            blockers.append("v7_selective_search_failed")
        prior_diagnostics = previous.get("diagnosticSelectedPickBands")
        if prior_diagnostics:
            base_report["diagnosticSelectedPickBands"] = prior_diagnostics
            base_report["diagnosticSelectedPickBandsReusedFromPreviousReport"] = True
        else:
            blockers.append("v9_diagnostic_selected_pick_bands_missing_until_refit")
        base_report.update(
            cadence_state.report_anchor_fields(
                cadence,
                current_count=current_count,
                fingerprint=fingerprint,
                shadow_refit_performed=False,
                lightweight_performed=should_lightweight,
            )
        )
        base_report.update({
            "ok": not blockers,
            "shadowRefitPerformed": False,
            "lightweightSelectiveEvaluationPerformed": should_lightweight,
            "stalledStage": "WAITING_FOR_50_NEW_ELIGIBLE_GAMES",
            "blockers": blockers,
        })
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(base_report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(base_report, indent=2, sort_keys=True))
        return 0 if base_report["ok"] else 1

    result = handler.optimizer.search(records, config)
    gate = result.get("promotionGate") or {}
    diagnostics = result.get("supervisedDiagnostics") or {}
    integrity = result.get("trainingIntegrity") or {}
    handoff = repairs.candidate_handoff(result, fingerprint)
    if args.handoff_output:
        handoff_path = Path(args.handoff_output)
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n")

    blockers = []
    if result.get("ok") is not True: blockers.append("supervised_search_failed")
    if integrity.get("rejected"): blockers.append("training_integrity_rejected_rows")
    if integrity.get("acceptedCount") != integrity.get("inputCount"): blockers.append("training_integrity_count_mismatch")
    if diagnostics.get("strictBinaryLabels") is not True: blockers.append("strict_binary_label_contract_missing")
    if diagnostics.get("holdoutEvaluatedAfterFreeze") is not True: blockers.append("holdout_not_proven_post_freeze")
    if diagnostics.get("holdoutLabelsUsedForFitOrSelection") is not False: blockers.append("holdout_used_for_fit_or_selection")
    if state.get("featureRematerializationErrors"): blockers.append("feature_rematerialization_errors")
    selective = base_report.get("v7SelectiveSearch") or {}
    if selective.get("ok") is not True: blockers.append("v7_selective_search_failed")

    candidate = result.get("candidate") or {}
    policy = candidate.get("policy") or handler.policy_runtime.BASELINE_POLICY
    partitions = result.get("partitions") or {}
    diagnostic_views = {
        "allCanonicalGames": _diagnostic_pick_rows(records, policy, handler.policy_runtime),
        "training": _diagnostic_pick_rows(records, policy, handler.policy_runtime, partitions.get("train") or []),
        "walkForward": _diagnostic_pick_rows(records, policy, handler.policy_runtime, partitions.get("walkForward") or []),
        "untouchedHoldout": _diagnostic_pick_rows(records, policy, handler.policy_runtime, partitions.get("untouchedHoldout") or []),
    }

    base_report.update(
        cadence_state.report_anchor_fields(
            cadence,
            current_count=current_count,
            fingerprint=fingerprint,
            shadow_refit_performed=True,
            lightweight_performed=True,
        )
    )
    base_report.update({
        "ok": not blockers,
        "shadowRefitPerformed": True,
        "lightweightSelectiveEvaluationPerformed": True,
        "blockers": blockers,
        "trainingIntegrity": integrity,
        "runtimeInstall": {"modelVersion": supervised_v9.VERSION, "featureVersion": supervised_v9.FEATURE_VERSION, "featureCount": len(supervised_v9.FEATURES), "priorityRepairsVersion": repairs.VERSION, "v7SelectiveSearchVersion": selective_v2.VERSION},
        "supervisedCandidate": {
            "status": result.get("status"), "searchVersion": result.get("searchVersion"), "settledGameCount": result.get("settledGameCount"),
            "walkForwardMeanDailyAccuracyPct": _pct(gate.get("walkForwardMeanDailyAccuracy")),
            "walkForwardMinimumDailyAccuracyPct": _pct(gate.get("walkForwardMinimumDailyAccuracy")),
            "untouchedHoldoutMeanDailyAccuracyPct": _pct(gate.get("untouchedHoldoutMeanDailyAccuracy")),
            "untouchedHoldoutMinimumDailyAccuracyPct": _pct(gate.get("untouchedHoldoutMinimumDailyAccuracy")),
            "brierScore": gate.get("brierScore") or diagnostics.get("brierScore"),
            "logLoss": gate.get("logLoss") or diagnostics.get("logLoss"),
            "promotionPassed": gate.get("passed") is True, "errors": gate.get("errors") or [], "diagnostics": diagnostics,
        },
        "diagnosticSelectedPickBands": diagnostic_views,
        "canonicalCandidateHandoff": handoff,
    })
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base_report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(base_report, indent=2, sort_keys=True))
    return 0 if base_report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
