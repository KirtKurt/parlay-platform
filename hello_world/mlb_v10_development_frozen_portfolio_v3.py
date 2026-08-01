"""Development-frozen portfolio gate and prospective shadow evaluator for MLB V10.

The V2 engine discovers leakage-safe pregame rules, but it gated each individual rule
against the maximum shuffled-rule accuracy in both validation partitions. That creates
an empty-registry failure mode even when several modest, complementary rules combine
into a useful policy. It also used the nominally untouched holdout in signal selection.

This module fixes both defects without granting production authority:

* candidates and portfolio construction use the development partition only;
* the frozen portfolio is tested as one fixed policy on walk-forward data;
* the untouched holdout is audit-only and never changes the frozen policy;
* a statistically unpromoted portfolio may still run in prospective shadow so V10
  continues accumulating evidence instead of freezing an empty registry.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

VERSION = "MLB-V10-AUTONOMOUS-SIGNAL-DISCOVERY-v3.0-development-frozen-portfolio"
CONTROL_VERSION = "MLB-V10-PORTFOLIO-PERMUTATION-v1-fixed-pregame-policy"
MIN_SELECTED_SIGNALS = 3
MAX_SELECTED_SIGNALS = 24
MAX_SIGNATURE_JACCARD = 0.95
MIN_DEVELOPMENT_PORTFOLIO_PICKS = 80
MIN_WALK_FORWARD_PORTFOLIO_PICKS = 80
MIN_UNTOUCHED_PORTFOLIO_PICKS = 80
MIN_PORTFOLIO_SLATE_DAYS = 20
PORTFOLIO_PERMUTATION_ROUNDS = 1000


def _f(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _definition(row: Mapping[str, Any]) -> str:
    return str(row.get("definition") or "")


def _development_rank(row: Mapping[str, Any]) -> tuple[float, float, int, int, str]:
    """Rank only with development labels; never inspect validation outcomes."""
    accuracy = _f(row.get("developmentAccuracy"), 0.5)
    count = max(0, int(row.get("developmentPickCount") or 0))
    days = max(0, int(row.get("developmentSlateDayCount") or 0))
    lower = _f((row.get("developmentWilson95") or [0.0])[0], 0.0)
    evidence = max(0.0, accuracy - 0.5) * math.sqrt(max(1, count))
    return (lower, evidence, count, days, _definition(row))


def _development_candidates(subject: Any, development_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild the candidate registry without consulting either validation partition."""
    from collections import Counter

    occurrence: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    days: dict[str, set[str]] = defaultdict(set)
    families: dict[str, str] = {}
    for row in development_records:
        for rule in subject._side_applicable_rules(row):
            definition = str(rule.get("definition") or "")
            if not definition:
                continue
            occurrence[definition] += 1
            correct[definition] += int(subject._correct(rule, row))
            days[definition].add(subject._date(row))
            families[definition] = str(rule.get("family") or "unknown")

    candidates: list[dict[str, Any]] = []
    minimum_occurrences = int(getattr(subject, "MIN_PATTERN_OCCURRENCES", 20))
    minimum_days = int(getattr(subject, "MIN_PATTERN_DAYS", 8))
    for definition, count in occurrence.items():
        if count < minimum_occurrences or len(days[definition]) < minimum_days:
            continue
        wins = correct[definition]
        lower, upper = subject._wilson(wins, count)
        candidates.append({
            "signalId": hashlib.sha256(definition.encode()).hexdigest()[:20],
            "definition": definition,
            "family": families.get(definition, "unknown"),
            "developmentPickCount": count,
            "developmentCorrect": wins,
            "developmentAccuracy": wins / count,
            "developmentWilson95": [lower, upper],
            "developmentSlateDayCount": len(days[definition]),
            "pValue": subject._two_sided_binomial_pvalue(wins, count),
            "researchOnly": True,
            "productionEligible": False,
        })
    subject._bh_qvalues(candidates)
    alpha = _f(getattr(subject, "FDR_ALPHA", 0.10), 0.10)
    candidates = [
        row for row in candidates
        if _f(row.get("qValue"), 1.0) <= alpha and _f(row.get("developmentAccuracy"), 0.0) > 0.5
    ]
    candidates.sort(key=_development_rank, reverse=True)
    maximum = int(getattr(subject, "TOP_REGISTRY_PATTERNS", 1000))
    return candidates[:maximum]


def _attach_validation_diagnostics(
    subject: Any,
    candidates: Sequence[Mapping[str, Any]],
    walk_forward_records: Sequence[Mapping[str, Any]],
    holdout_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    definitions = {_definition(row) for row in candidates}
    wf_dates = sorted({subject._date(row) for row in walk_forward_records})
    holdout_dates = sorted({subject._date(row) for row in holdout_records})
    wf = subject._evaluate(walk_forward_records, wf_dates, definitions)
    holdout = subject._evaluate(holdout_records, holdout_dates, definitions)
    rows: list[dict[str, Any]] = []
    for raw in candidates:
        row = dict(raw)
        definition = _definition(row)
        row["walkForward"] = wf.get(definition, {"pickCount": 0, "correct": 0, "accuracy": 0.0, "slateDayCount": 0, "family": row.get("family")})
        row["untouchedHoldout"] = holdout.get(definition, {"pickCount": 0, "correct": 0, "accuracy": 0.0, "slateDayCount": 0, "family": row.get("family")})
        rows.append(row)
    return rows


def _applicable_predictions(subject: Any, records: Sequence[Mapping[str, Any]], definitions: set[str]) -> dict[str, set[tuple[str, str]]]:
    predictions: dict[str, set[tuple[str, str]]] = {definition: set() for definition in definitions}
    for row in records:
        game_key = f"{subject._date(row)}|{subject._game_id(row)}"
        for rule in subject._side_applicable_rules(row):
            definition = str(rule.get("definition") or "")
            if definition in predictions:
                predictions[definition].add((game_key, str(rule.get("selectedSide") or "")))
    return predictions


def _jaccard(left: set[Any], right: set[Any]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _select_diverse_signals(subject: Any, development_records: Sequence[Mapping[str, Any]], signal_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    eligible = [
        dict(row)
        for row in signal_rows
        if _definition(row)
        and _f(row.get("developmentAccuracy"), 0.0) > 0.5
        and _f(row.get("qValue"), 1.0) <= _f(getattr(subject, "FDR_ALPHA", 0.10), 0.10)
        and int(row.get("developmentPickCount") or 0) >= int(getattr(subject, "MIN_PATTERN_OCCURRENCES", 20))
        and int(row.get("developmentSlateDayCount") or 0) >= int(getattr(subject, "MIN_PATTERN_DAYS", 8))
    ]
    eligible.sort(key=_development_rank, reverse=True)
    signatures = _applicable_predictions(subject, development_records, {_definition(row) for row in eligible})
    selected: list[dict[str, Any]] = []
    for row in eligible:
        definition = _definition(row)
        signature = signatures.get(definition, set())
        if selected and any(
            _jaccard(signature, signatures.get(_definition(existing), set())) >= MAX_SIGNATURE_JACCARD
            for existing in selected
        ):
            continue
        selected.append(row)
        if len(selected) >= MAX_SELECTED_SIGNALS:
            break
    if len(selected) < MIN_SELECTED_SIGNALS:
        seen = {_definition(row) for row in selected}
        for row in eligible:
            if _definition(row) in seen:
                continue
            selected.append(row)
            seen.add(_definition(row))
            if len(selected) >= min(MIN_SELECTED_SIGNALS, len(eligible)):
                break
    return selected


def _weights(selected: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    raw: dict[str, float] = {}
    for row in selected:
        definition = _definition(row)
        count = max(1, int(row.get("developmentPickCount") or 0))
        correct = max(0, int(row.get("developmentCorrect") or 0))
        smoothed_accuracy = (correct + 1.0) / (count + 2.0)
        edge = max(1e-6, smoothed_accuracy - 0.5)
        reliability = min(3.0, math.sqrt(count / 20.0))
        raw[definition] = edge * reliability
    total = sum(raw.values())
    return {definition: value / total for definition, value in raw.items()} if total > 0 else {}


def _policy_predictions(
    subject: Any,
    records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    *,
    include_rows: bool = True,
    prediction_limit: int | None = 500,
) -> dict[str, Any]:
    weights = {str(key): _f(value) for key, value in (policy.get("weights") or {}).items() if _f(value) > 0.0}
    minimum_margin = max(0.0, _f(policy.get("minimumNormalizedVoteMargin"), 0.0))
    predictions: list[dict[str, Any]] = []
    for record in records:
        votes = {"home": 0.0, "away": 0.0}
        support = {"home": [], "away": []}
        for rule in subject._side_applicable_rules(record):
            definition = str(rule.get("definition") or "")
            weight = weights.get(definition, 0.0)
            side = str(rule.get("selectedSide") or "")
            if weight <= 0.0 or side not in votes:
                continue
            votes[side] += weight
            support[side].append(definition)
        total_vote = votes["home"] + votes["away"]
        if total_vote <= 0.0 or abs(votes["home"] - votes["away"]) <= 1e-15:
            continue
        margin = abs(votes["home"] - votes["away"]) / total_vote
        if margin + 1e-15 < minimum_margin:
            continue
        selected = "home" if votes["home"] > votes["away"] else "away"
        label = subject._label(record)
        correct = None if label not in (0, 1) else ((selected == "home") == bool(label))
        predictions.append({
            "slateDateEt": subject._date(record),
            "gameId": subject._game_id(record),
            "selectedSide": selected,
            "selectedHome": selected == "home",
            "correct": correct,
            "normalizedVoteMargin": margin,
            "homeVote": votes["home"],
            "awayVote": votes["away"],
            "supportingSignalCount": len(support[selected]),
            "supportingSignals": support[selected][:20],
        })
    settled = [row for row in predictions if row["correct"] is not None]
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in settled:
        by_day[str(row["slateDateEt"])].append(row)
    daily = []
    for day in sorted(by_day):
        rows = by_day[day]
        correct = sum(bool(row["correct"]) for row in rows)
        daily.append({"slateDateEt": day, "pickCount": len(rows), "correct": correct, "accuracy": correct / len(rows)})
    correct = sum(bool(row["correct"]) for row in settled)
    lower, upper = subject._wilson(correct, len(settled)) if settled else (0.0, 1.0)
    result = {
        "policyType": "DEVELOPMENT_FROZEN_WEIGHTED_PORTFOLIO",
        "selectionUsesWalkForwardLabels": False,
        "selectionUsesHoldoutLabels": False,
        "pickCount": len(settled),
        "correct": correct,
        "losses": len(settled) - correct,
        "accuracy": correct / len(settled) if settled else None,
        "wilson95": [lower, upper],
        "selectionDayCount": len(daily),
        "meanDailyAccuracy": sum(row["accuracy"] for row in daily) / len(daily) if daily else None,
        "minimumDailyAccuracy": min((row["accuracy"] for row in daily), default=None),
        "daily": daily,
    }
    if include_rows:
        result["predictions"] = predictions if prediction_limit is None else predictions[: max(0, prediction_limit)]
    return result


def _candidate_margins(subject: Any, records: Sequence[Mapping[str, Any]], weights: Mapping[str, float]) -> list[float]:
    policy = {"weights": weights, "minimumNormalizedVoteMargin": 0.0}
    scored = _policy_predictions(subject, records, policy, prediction_limit=None)
    return sorted(_f(row.get("normalizedVoteMargin")) for row in scored.get("predictions") or [])


def _quantile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * fraction))))
    return float(values[index])


def _freeze_development_policy(subject: Any, development_records: Sequence[Mapping[str, Any]], signal_rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    selected = _select_diverse_signals(subject, development_records, signal_rows)
    weights = _weights(selected)
    if len(weights) < MIN_SELECTED_SIGNALS:
        return None, {"reason": "insufficient_diverse_development_signals", "selectedSignalCount": len(weights)}

    margins = _candidate_margins(subject, development_records, weights)
    thresholds = sorted({0.0, _quantile(margins, 0.25), _quantile(margins, 0.50), _quantile(margins, 0.75)})
    options = []
    minimum_picks = min(
        max(MIN_DEVELOPMENT_PORTFOLIO_PICKS, int(len(development_records) * 0.20)),
        max(1, len(development_records)),
    )
    for threshold in thresholds:
        policy = {
            "policyVersion": VERSION,
            "policyType": "DEVELOPMENT_FROZEN_WEIGHTED_PORTFOLIO",
            "weights": weights,
            "signalDefinitions": sorted(weights),
            "minimumNormalizedVoteMargin": threshold,
            "selectionPartition": "development",
            "selectionUsesWalkForwardLabels": False,
            "selectionUsesHoldoutLabels": False,
        }
        metrics = _policy_predictions(subject, development_records, policy, include_rows=False)
        eligible = metrics["pickCount"] >= minimum_picks and metrics["selectionDayCount"] >= MIN_PORTFOLIO_SLATE_DAYS
        options.append({"policy": policy, "metrics": metrics, "eligible": eligible})
    eligible_options = [row for row in options if row["eligible"]]
    if not eligible_options:
        return None, {
            "reason": "development_portfolio_coverage_gate_failed",
            "selectedSignalCount": len(weights),
            "minimumPicks": minimum_picks,
            "options": [{"threshold": row["policy"]["minimumNormalizedVoteMargin"], "metrics": row["metrics"]} for row in options],
        }
    chosen = max(
        eligible_options,
        key=lambda row: (
            _f((row["metrics"].get("wilson95") or [0.0])[0]),
            _f(row["metrics"].get("accuracy")),
            int(row["metrics"].get("pickCount") or 0),
            -_f(row["policy"].get("minimumNormalizedVoteMargin")),
        ),
    )
    policy = dict(chosen["policy"])
    policy["developmentMetrics"] = chosen["metrics"]
    policy["developmentSelectionOptions"] = [
        {
            "minimumNormalizedVoteMargin": row["policy"]["minimumNormalizedVoteMargin"],
            "eligible": row["eligible"],
            "pickCount": row["metrics"]["pickCount"],
            "accuracy": row["metrics"]["accuracy"],
            "wilson95": row["metrics"]["wilson95"],
        }
        for row in options
    ]
    policy["fingerprint"] = hashlib.sha256(
        json.dumps({
            "weights": policy["weights"],
            "minimumNormalizedVoteMargin": policy["minimumNormalizedVoteMargin"],
            "version": VERSION,
        }, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return policy, {"reason": None, "selectedSignalCount": len(weights)}


def _fixed_policy_permutation_control(subject: Any, records: Sequence[Mapping[str, Any]], policy: Mapping[str, Any], *, minimum_picks: int, seed: int) -> dict[str, Any]:
    metrics = _policy_predictions(subject, records, policy, prediction_limit=None)
    predictions = metrics.get("predictions") or []
    selected_by_game = {
        (str(row.get("slateDateEt")), str(row.get("gameId"))): bool(row.get("selectedHome"))
        for row in predictions
    }
    selected: list[bool] = []
    labels: list[int] = []
    for record in records:
        key = (str(subject._date(record)), str(subject._game_id(record)))
        if key not in selected_by_game:
            continue
        label = subject._label(record)
        if label not in (0, 1):
            continue
        selected.append(selected_by_game[key])
        labels.append(int(label))
    round_count = PORTFOLIO_PERMUTATION_ROUNDS
    if len(labels) < minimum_picks:
        return {
            "implementation": CONTROL_VERSION,
            "rounds": 0,
            "seed": seed,
            "minimumPicks": minimum_picks,
            "pickCount": len(labels),
            "accuracy95thPercentile": 1.0,
            "passed": False,
            "reason": "minimum_pick_count_not_met",
        }
    rng = random.Random(seed)
    accuracies = []
    for _ in range(round_count):
        shuffled = list(labels)
        rng.shuffle(shuffled)
        correct = sum(selected_home == bool(shuffled[index]) for index, selected_home in enumerate(selected))
        accuracies.append(correct / len(selected))
    accuracies.sort()
    index = min(len(accuracies) - 1, math.ceil(len(accuracies) * 0.95) - 1)
    threshold = accuracies[index]
    observed = _f(metrics.get("accuracy"), 0.0)
    wilson_lower = _f((metrics.get("wilson95") or [0.0])[0], 0.0)
    passed = (
        len(labels) >= minimum_picks
        and int(metrics.get("selectionDayCount") or 0) >= MIN_PORTFOLIO_SLATE_DAYS
        and observed > threshold
        and wilson_lower > 0.5
    )
    return {
        "implementation": CONTROL_VERSION,
        "rounds": round_count,
        "seed": seed,
        "minimumPicks": minimum_picks,
        "minimumSlateDays": MIN_PORTFOLIO_SLATE_DAYS,
        "pickCount": len(labels),
        "observedAccuracy": observed,
        "observedWilson95": metrics.get("wilson95"),
        "accuracy95thPercentile": threshold,
        "passed": passed,
        "pregamePolicyFrozen": True,
        "selectionUsesPartitionLabels": False,
    }


def _partition_records(subject: Any, records: Sequence[Mapping[str, Any]], dates: Iterable[str]) -> list[Mapping[str, Any]]:
    allowed = set(dates)
    return [row for row in records if subject._date(row) in allowed]


def evaluate_frozen_registry(subject: Any, records: Sequence[Mapping[str, Any]], previous: Mapping[str, Any]) -> dict[str, Any]:
    freeze = previous.get("registryFreeze") or {}
    policy = freeze.get("portfolio") or {}
    frozen_through = str(freeze.get("frozenThroughDate") or "")
    future = [row for row in records if subject._date(row) > frozen_through] if frozen_through and policy else []
    dates = sorted({subject._date(row) for row in future})
    portfolio = _policy_predictions(subject, future, policy) if future else _policy_predictions(subject, [], policy)
    status = "EVALUATED" if portfolio["pickCount"] else "AWAITING_FUTURE_GAMES"
    return {
        "status": status,
        "registryVersion": previous.get("version"),
        "registryFingerprint": freeze.get("registryFingerprint"),
        "frozenThroughDate": frozen_through or None,
        "futureCanonicalGameCount": len(future),
        "futureSlateCount": len(dates),
        "policyChangedDuringEvaluation": False,
        "selectionUsesFutureLabels": False,
        "productionAuthority": False,
        "researchGatePassedAtFreeze": bool(freeze.get("researchGatePassed")),
        "shadowOnly": True,
        "portfolio": portfolio,
    }


def upgrade_report(subject: Any, records: Sequence[Mapping[str, Any]], report: Mapping[str, Any], *, previous_report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = dict(report)
    partitions = value.get("partitions") or {}
    development_records = _partition_records(subject, records, partitions.get("development") or [])
    walk_forward_records = _partition_records(subject, records, partitions.get("walkForward") or [])
    holdout_records = _partition_records(subject, records, partitions.get("untouchedHoldout") or [])
    development_candidates = _development_candidates(subject, development_records)
    diagnostic_signals = _attach_validation_diagnostics(
        subject, development_candidates, walk_forward_records, holdout_records
    )
    policy, selection = _freeze_development_policy(subject, development_records, diagnostic_signals)

    prospective = (
        evaluate_frozen_registry(subject, records, previous_report)
        if previous_report
        and previous_report.get("version") == VERSION
        and (previous_report.get("registryFreeze") or {}).get("portfolio")
        else {
            "status": "NOT_STARTED_NO_PRIOR_FROZEN_PORTFOLIO",
            "futureCanonicalGameCount": 0,
            "futureSlateCount": 0,
            "policyChangedDuringEvaluation": False,
            "selectionUsesFutureLabels": False,
            "productionAuthority": False,
            "shadowOnly": True,
            "portfolio": _policy_predictions(subject, [], {}),
        }
    )

    research_blockers: list[str] = []
    if policy is None:
        research_blockers.append(str(selection.get("reason") or "development_portfolio_not_constructed"))
        wf_metrics = _policy_predictions(subject, [], {})
        holdout_metrics = _policy_predictions(subject, [], {})
        wf_control = {"implementation": CONTROL_VERSION, "passed": False, "reason": "no_frozen_policy"}
        holdout_control = {"implementation": CONTROL_VERSION, "passed": False, "reason": "no_frozen_policy"}
        frozen_through = max(partitions.get("untouchedHoldout") or [], default=max((subject._date(row) for row in records), default=""))
        registry_fingerprint = hashlib.sha256(b"no-policy").hexdigest()
        selected_definitions: set[str] = set()
        walk_forward_passed = False
        audit_passed = False
    else:
        wf_metrics = _policy_predictions(subject, walk_forward_records, policy)
        holdout_metrics = _policy_predictions(subject, holdout_records, policy)
        wf_control = _fixed_policy_permutation_control(
            subject,
            walk_forward_records,
            policy,
            minimum_picks=MIN_WALK_FORWARD_PORTFOLIO_PICKS,
            seed=27101,
        )
        holdout_control = _fixed_policy_permutation_control(
            subject,
            holdout_records,
            policy,
            minimum_picks=MIN_UNTOUCHED_PORTFOLIO_PICKS,
            seed=27102,
        )
        walk_forward_passed = bool(wf_control.get("passed"))
        audit_passed = bool(holdout_control.get("passed"))
        if not walk_forward_passed:
            research_blockers.append("portfolio_walk_forward_gate_failed")
        if not audit_passed:
            research_blockers.append("untouched_holdout_audit_failed")
        frozen_through = max(partitions.get("untouchedHoldout") or [], default=max((subject._date(row) for row in records), default=""))
        registry_fingerprint = str(policy.get("fingerprint") or "")
        selected_definitions = set(policy.get("signalDefinitions") or [])

    signals = []
    for raw in diagnostic_signals:
        row = dict(raw)
        selected = _definition(row) in selected_definitions
        row["selectedForFrozenPortfolio"] = selected
        row["predictiveResearchGatePassed"] = bool(selected and walk_forward_passed)
        row["gateBasis"] = "DEVELOPMENT_FROZEN_PORTFOLIO_WALK_FORWARD_CONTROL"
        row["selectionUsedUntouchedHoldoutLabels"] = False
        row["productionEligible"] = False
        signals.append(row)

    value.update({
        "version": VERSION,
        "portfolioControlImplementation": CONTROL_VERSION,
        "individualSignalMaxControlRetainedForDiagnosticsOnly": True,
        "holdoutSelectionDefectFixed": True,
        "emptyRegistryStallFixed": policy is not None,
        "learningActive": policy is not None,
        "learningStatus": (
            "VALIDATED_SHADOW_PORTFOLIO_ACTIVE" if walk_forward_passed
            else "RESEARCH_SHADOW_PORTFOLIO_ACTIVE" if policy is not None
            else "STALLED_NO_DEVELOPMENT_PORTFOLIO"
        ),
        "researchBlockers": research_blockers,
        "fdrRetainedPatternCount": len(development_candidates),
        "retainedPatternCount": len(development_candidates),
        "signals": signals,
        "shadowSignalCount": len(selected_definitions),
        "predictiveSignalCount": len(selected_definitions) if walk_forward_passed else 0,
        "aggregateResearchPolicy": {
            "development": policy.get("developmentMetrics") if policy else _policy_predictions(subject, [], {}),
            "walkForward": wf_metrics,
            "untouchedHoldout": holdout_metrics,
        },
        "portfolioValidation": {
            "selectionPartition": "development",
            "selectionUsesWalkForwardLabels": False,
            "selectionUsesUntouchedHoldoutLabels": False,
            "walkForwardControl": wf_control,
            "walkForwardPassed": walk_forward_passed,
            "untouchedHoldoutAuditControl": holdout_control,
            "untouchedHoldoutAuditPassed": audit_passed,
            "untouchedHoldoutUsedForSelection": False,
        },
        "registryFreeze": {
            "frozenThroughDate": frozen_through or None,
            "registryFingerprint": registry_fingerprint,
            "frozenSignalCount": len(selected_definitions),
            "predictiveSignalCount": len(selected_definitions) if walk_forward_passed else 0,
            "selectionUsedWalkForwardLabels": False,
            "selectionUsedUntouchedHoldoutLabels": False,
            "researchGatePassed": walk_forward_passed,
            "untouchedAuditPassed": audit_passed,
            "shadowOnly": True,
            "portfolio": policy or {},
        },
        "prospectiveShadow": prospective,
        "productionAuthority": False,
        "mayWriteChampion": False,
        "mayPublishPicks": False,
        "warning": "Research-only shadow portfolio; no production authority and no public picks.",
    })
    value["blockers"] = list(value.get("blockers") or [])
    return value


def refresh_unchanged_report(subject: Any, records: Sequence[Mapping[str, Any]], previous: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(previous)
    value["version"] = VERSION
    value["incrementalNoChange"] = True
    value["lastCheckedAtUtc"] = datetime.now(timezone.utc).isoformat()
    value["prospectiveShadow"] = evaluate_frozen_registry(subject, records, previous)
    value["learningActive"] = bool((previous.get("registryFreeze") or {}).get("portfolio"))
    if value["learningActive"]:
        value["learningStatus"] = (
            "PROSPECTIVE_SHADOW_EVALUATED"
            if (value["prospectiveShadow"] or {}).get("status") == "EVALUATED"
            else value.get("learningStatus") or "RESEARCH_SHADOW_PORTFOLIO_ACTIVE"
        )
    return value
