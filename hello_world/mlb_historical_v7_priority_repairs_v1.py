"""Priority repairs for V7 historical learning.

This module is deliberately shadow-safe. It expands trainable missingness and
fundamental features, produces durable diagnostics, and creates a frozen,
fail-closed candidate-handoff contract. It never writes a champion, cutover,
prediction, lock, or wager and does not weaken the canonical 200-game untouched
audit or 80%-every-slate promotion gate.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence

VERSION = "MLB-HISTORICAL-V7-PRIORITY-REPAIRS-v2"
SHADOW_REFIT_INCREMENT_GAMES = 50
CANONICAL_PROMOTION_AUDIT_GAMES = 200
SELECTIVE_THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80)

EXTRA_FEATURES = (
    "starterDiff", "bullpenDiff", "lineupDiff",
    "starterAvailable", "bullpenAvailable", "lineupAvailable",
    "firstFiveAvailable", "spreadAvailable", "fullHistoryAvailable",
    "starterFirstFiveInteraction", "bullpenLateMarketInteraction",
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def strict_binary_label(value: Any) -> Optional[int]:
    """Return 0/1 only for explicit binary outcomes; never coerce missing to a loss."""
    if value is True or value == 1 or value == 1.0 or value == "1":
        return 1
    if value is False or value == 0 or value == 0.0 or value == "0":
        return 0
    return None


def _fundamental(learner: Any, signal: Mapping[str, Any], names: Sequence[str]):
    return learner._fundamental(signal, names)


def install_feature_repairs(learner: Any) -> None:
    """Extend the supervised learner before runtime install.

    Installation is idempotent and retains the legacy marker for compatibility
    with already-warm Lambda execution environments.
    """
    if getattr(learner, "_INQIS_V7_PRIORITY_FEATURES_INSTALLED", False) or getattr(
        learner, "_INQSI_V7_PRIORITY_FEATURES_INSTALLED", False
    ):
        return
    original_pair = learner.pair_features
    learner.FEATURES = tuple(dict.fromkeys(tuple(learner.FEATURES) + EXTRA_FEATURES))

    def pair_features(home: Mapping[str, Any], away: Mapping[str, Any], policy: Mapping[str, Any]):
        values = dict(original_pair(home, away, policy))
        hs = _fundamental(learner, home, ("starterQuality", "startingPitcherQuality"))
        away_starter = _fundamental(learner, away, ("starterQuality", "startingPitcherQuality"))
        hb = _fundamental(learner, home, ("bullpenQuality", "bullpenStrength"))
        away_bullpen = _fundamental(learner, away, ("bullpenQuality", "bullpenStrength"))
        hl = _fundamental(learner, home, ("lineupQuality", "lineupStrength"))
        away_lineup = _fundamental(learner, away, ("lineupQuality", "lineupStrength"))
        hf5 = learner._v8(home, "firstFiveH2HMedianImpliedProbability")
        af5 = learner._v8(away, "firstFiveH2HMedianImpliedProbability")
        hsp = learner._v8(home, "fullGameSpreadMedian")
        asp = learner._v8(away, "fullGameSpreadMedian")
        starter_diff = _f(hs) - _f(away_starter)
        bullpen_diff = _f(hb) - _f(away_bullpen)
        lineup_diff = _f(hl) - _f(away_lineup)
        values.update({
            "starterDiff": starter_diff,
            "bullpenDiff": bullpen_diff,
            "lineupDiff": lineup_diff,
            "starterAvailable": float(hs is not None and away_starter is not None),
            "bullpenAvailable": float(hb is not None and away_bullpen is not None),
            "lineupAvailable": float(hl is not None and away_lineup is not None),
            "firstFiveAvailable": float(hf5 is not None and af5 is not None),
            "spreadAvailable": float(hsp is not None and asp is not None),
            "fullHistoryAvailable": float(
                learner._temporal(home, "full", "coverageRatio") > 0
                and learner._temporal(away, "full", "coverageRatio") > 0
            ),
            "starterFirstFiveInteraction": starter_diff * _f(values.get("v8FirstFiveLogit")),
            "bullpenLateMarketInteraction": bullpen_diff * (
                _f(values.get("marketLogit")) - _f(values.get("v8FirstFiveLogit"))
            ),
        })
        return values

    learner.pair_features = pair_features
    learner.FEATURE_VERSION = "MLB-SUPERVISED-PAIR-FEATURES-v2-missingness-separated"
    learner.V7_PRIORITY_REPAIRS_VERSION = VERSION
    learner._INQIS_V7_PRIORITY_FEATURES_INSTALLED = True
    learner._INQSI_V7_PRIORITY_FEATURES_INSTALLED = True


def dataset_fingerprint(records: Sequence[Mapping[str, Any]]) -> str:
    """Order-independent fingerprint over identity, immutable feature hash, and label."""
    rows = []
    for row in records:
        rows.append({
            "date": str(row.get("slateDateEt") or ""),
            "game": str(row.get("gameId") or row.get("eventId") or row.get("id") or ""),
            "homeWon": strict_binary_label(row.get("homeWon")),
            "fingerprint": str(row.get("fingerprint") or row.get("featureVectorFingerprint") or ""),
        })
    rows.sort(key=lambda item: (item["date"], item["game"], item["fingerprint"], str(item["homeWon"])))
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def feature_population_report(records: Sequence[Mapping[str, Any]], learner: Any, policy: Mapping[str, Any]) -> Dict[str, Any]:
    stats = {
        name: {"count": 0, "nonzero": 0, "sum": 0.0, "sumSq": 0.0,
               "winnerSum": 0.0, "loserSum": 0.0, "winnerCount": 0, "loserCount": 0}
        for name in learner.FEATURES
    }
    by_season: Dict[str, Counter] = {}
    invalid_labels = 0
    eligible_records = 0
    for row in records:
        label = strict_binary_label(row.get("homeWon"))
        if label is None:
            invalid_labels += 1
            continue
        eligible_records += 1
        features = learner.pair_features(row.get("homeSignal") or {}, row.get("awaySignal") or {}, policy)
        season = str(row.get("slateDateEt") or "")[:4] or "unknown"
        by_season.setdefault(season, Counter())
        for name in learner.FEATURES:
            value = _f(features.get(name))
            item = stats[name]
            item["count"] += 1
            item["nonzero"] += int(abs(value) > 1e-12)
            item["sum"] += value
            item["sumSq"] += value * value
            bucket = "winner" if label == 1 else "loser"
            item[f"{bucket}Sum"] += value
            item[f"{bucket}Count"] += 1
            if abs(value) > 1e-12:
                by_season[season][name] += 1
    output = {}
    for name, item in stats.items():
        count = item["count"]
        divisor = max(1, count)
        mean = item["sum"] / divisor
        variance = max(0.0, item["sumSq"] / divisor - mean * mean)
        winner_mean = item["winnerSum"] / max(1, item["winnerCount"])
        loser_mean = item["loserSum"] / max(1, item["loserCount"])
        output[name] = {
            "count": count, "nonzeroCount": item["nonzero"],
            "nonzeroPct": round(item["nonzero"] / divisor, 6),
            "mean": mean, "variance": variance,
            "winnerMinusLoserMean": winner_mean - loser_mean,
            "degenerate": count == 0 or variance < 1e-12 or item["nonzero"] == 0,
        }
    return {
        "version": VERSION,
        "recordCount": len(records),
        "eligibleRecordCount": eligible_records,
        "invalidLabelCount": invalid_labels,
        "features": output,
        "degenerateFeatures": sorted(name for name, item in output.items() if item["degenerate"]),
        "nonzeroBySeason": {season: dict(counter) for season, counter in sorted(by_season.items())},
    }


def rejection_and_lease_report(state: Mapping[str, Any], handler: Any) -> Dict[str, Any]:
    reasons = Counter(str(row.get("reason") or "unknown") for row in state.get("rejectedSlates") or [] if isinstance(row, Mapping))
    try:
        lease = handler._get_item(handler.LEASE_SK)
    except Exception as exc:
        lease = {"readError": type(exc).__name__}
    return {
        "rejectedSlateCount": sum(reasons.values()), "rejectionReasons": dict(reasons.most_common()),
        "lease": lease,
        "skippedInvocationCount": state.get("leaseContentionCount") or state.get("skippedLeaseInvocationCount") or 0,
        "lastInvocationDurationMs": state.get("lastInvocationDurationMs"),
        "staleLeaseRecoveryCount": state.get("staleLeaseRecoveryCount") or 0,
    }


def selective_accuracy_report(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Diagnostic PICK/PASS view using only explicit settled labels and valid markets."""
    rows = []
    invalid_labels = 0
    invalid_markets = 0
    for row in records:
        label = strict_binary_label(row.get("homeWon"))
        if label is None:
            invalid_labels += 1
            continue
        home = row.get("homeSignal") or {}
        away = row.get("awaySignal") or {}
        hp_raw = home.get("marketConsensusProbability", home.get("probLatest"))
        ap_raw = away.get("marketConsensusProbability", away.get("probLatest"))
        try:
            hp, ap = float(hp_raw), float(ap_raw)
        except (TypeError, ValueError):
            invalid_markets += 1
            continue
        if not (math.isfinite(hp) and math.isfinite(ap) and hp > 0 and ap > 0):
            invalid_markets += 1
            continue
        p = hp / (hp + ap)
        prediction = int(p >= 0.5)
        rows.append((str(row.get("slateDateEt") or ""), max(p, 1.0 - p), int(prediction == label)))
    full_accuracy = (sum(r[2] for r in rows) / len(rows)) if rows else None
    thresholds = {}
    for threshold in SELECTIVE_THRESHOLDS:
        chosen = [r for r in rows if r[1] >= threshold]
        thresholds[f"{threshold:.2f}"] = {
            "pickCount": len(chosen),
            "coverage": len(chosen) / max(1, len(rows)),
            "accuracy": (sum(r[2] for r in chosen) / len(chosen)) if chosen else None,
            "dayCount": len({r[0] for r in chosen}),
        }
    return {
        "diagnosticOnly": True, "fullSlateGameCount": len(rows),
        "invalidLabelCount": invalid_labels, "invalidMarketCount": invalid_markets,
        "fullSlateAccuracy": full_accuracy, "selectiveThresholds": thresholds,
    }


def candidate_handoff(result: Mapping[str, Any], fingerprint: str) -> Dict[str, Any]:
    """Freeze a non-authoritative candidate, failing closed on incomplete evidence."""
    policy = copy.deepcopy(result.get("policy") or result.get("candidatePolicy") or result.get("bestPolicy") or {})
    search_version = result.get("searchVersion")
    completed = str(result.get("status") or result.get("outcome") or "").upper() in {"SUCCESS", "COMPLETED", "COMPLETE"}
    blockers = []
    if not policy: blockers.append("MISSING_POLICY")
    if not fingerprint: blockers.append("MISSING_DATASET_FINGERPRINT")
    if not search_version: blockers.append("MISSING_SEARCH_VERSION")
    if not completed: blockers.append("SEARCH_NOT_COMPLETED")
    payload = {
        "schemaVersion": "MLB-V7-SHADOW-CANDIDATE-HANDOFF-v2",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "datasetFingerprint": fingerprint,
        "searchVersion": search_version,
        "policy": policy,
        "promotionAuthority": False,
        "eligibleForCanonicalSeed": not blockers,
        "eligibilityBlockers": blockers,
        "requiresCanonicalChronologicalReevaluation": True,
        "requiresFresh200GameUntouchedAudit": True,
        "requiredUntouchedAuditGames": CANONICAL_PROMOTION_AUDIT_GAMES,
        "requiresEverySlateAtLeast80Pct": True,
    }
    payload["digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return payload
