"""Priority repairs for V7 historical learning.

This module is deliberately shadow-safe.  It expands trainable missingness and
fundamental features, produces durable diagnostics, and creates a frozen
candidate-handoff contract.  It never writes a champion, cutover, prediction,
lock, or wager and does not weaken the canonical 200-game untouched audit or
80%-every-slate promotion gate.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Sequence

VERSION = "MLB-HISTORICAL-V7-PRIORITY-REPAIRS-v1"
SHADOW_REFIT_INCREMENT_GAMES = 50
CANONICAL_PROMOTION_AUDIT_GAMES = 200
SELECTIVE_THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80)

EXTRA_FEATURES = (
    "starterDiff",
    "bullpenDiff",
    "lineupDiff",
    "starterAvailable",
    "bullpenAvailable",
    "lineupAvailable",
    "firstFiveAvailable",
    "spreadAvailable",
    "fullHistoryAvailable",
    "starterFirstFiveInteraction",
    "bullpenLateMarketInteraction",
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _fundamental(learner: Any, signal: Mapping[str, Any], names: Sequence[str]):
    return learner._fundamental(signal, names)


def install_feature_repairs(learner: Any) -> None:
    """Extend V9 before its runtime install so policy defaults/bounds include fields."""
    if getattr(learner, "_INQSI_V7_PRIORITY_FEATURES_INSTALLED", False):
        return
    original_pair = learner.pair_features
    learner.FEATURES = tuple(dict.fromkeys(tuple(learner.FEATURES) + EXTRA_FEATURES))

    def pair_features(home: Mapping[str, Any], away: Mapping[str, Any], policy: Mapping[str, Any]):
        values = dict(original_pair(home, away, policy))
        hs = _fundamental(learner, home, ("starterQuality", "startingPitcherQuality"))
        as_ = _fundamental(learner, away, ("starterQuality", "startingPitcherQuality"))
        hb = _fundamental(learner, home, ("bullpenQuality", "bullpenStrength"))
        ab = _fundamental(learner, away, ("bullpenQuality", "bullpenStrength"))
        hl = _fundamental(learner, home, ("lineupQuality", "lineupStrength"))
        al = _fundamental(learner, away, ("lineupQuality", "lineupStrength"))
        hf5 = learner._v8(home, "firstFiveH2HMedianImpliedProbability")
        af5 = learner._v8(away, "firstFiveH2HMedianImpliedProbability")
        hsp = learner._v8(home, "fullGameSpreadMedian")
        asp = learner._v8(away, "fullGameSpreadMedian")
        starter_diff = _f(hs) - _f(as_)
        bullpen_diff = _f(hb) - _f(ab)
        lineup_diff = _f(hl) - _f(al)
        first_five_available = float(hf5 is not None and af5 is not None)
        spread_available = float(hsp is not None and asp is not None)
        full_history_available = float(
            learner._temporal(home, "full", "coverageRatio") > 0
            and learner._temporal(away, "full", "coverageRatio") > 0
        )
        values.update(
            {
                "starterDiff": starter_diff,
                "bullpenDiff": bullpen_diff,
                "lineupDiff": lineup_diff,
                "starterAvailable": float(hs is not None and as_ is not None),
                "bullpenAvailable": float(hb is not None and ab is not None),
                "lineupAvailable": float(hl is not None and al is not None),
                "firstFiveAvailable": first_five_available,
                "spreadAvailable": spread_available,
                "fullHistoryAvailable": full_history_available,
                "starterFirstFiveInteraction": starter_diff * _f(values.get("v8FirstFiveLogit")),
                "bullpenLateMarketInteraction": bullpen_diff * (
                    _f(values.get("marketLogit")) - _f(values.get("v8FirstFiveLogit"))
                ),
            }
        )
        return values

    learner.pair_features = pair_features
    learner.FEATURE_VERSION = "MLB-SUPERVISED-PAIR-FEATURES-v2-missingness-separated"
    learner.V7_PRIORITY_REPAIRS_VERSION = VERSION
    learner._INQSI_V7_PRIORITY_FEATURES_INSTALLED = True


def dataset_fingerprint(records: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for row in records:
        rows.append(
            {
                "date": row.get("slateDateEt"),
                "game": row.get("gameId") or row.get("eventId") or row.get("id"),
                "homeWon": row.get("homeWon"),
                "fingerprint": row.get("fingerprint") or row.get("featureVectorFingerprint"),
            }
        )
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def feature_population_report(
    records: Sequence[Mapping[str, Any]], learner: Any, policy: Mapping[str, Any]
) -> Dict[str, Any]:
    stats = {
        name: {"count": 0, "nonzero": 0, "sum": 0.0, "sumSq": 0.0, "winnerSum": 0.0, "loserSum": 0.0, "winnerCount": 0, "loserCount": 0}
        for name in learner.FEATURES
    }
    by_season: Dict[str, Counter] = {}
    for row in records:
        features = learner.pair_features(row.get("homeSignal") or {}, row.get("awaySignal") or {}, policy)
        label = int(row.get("homeWon") or 0)
        season = str(row.get("slateDateEt") or "")[:4] or "unknown"
        by_season.setdefault(season, Counter())
        for name in learner.FEATURES:
            value = _f(features.get(name))
            item = stats[name]
            item["count"] += 1
            item["nonzero"] += int(abs(value) > 1e-12)
            item["sum"] += value
            item["sumSq"] += value * value
            if label:
                item["winnerSum"] += value
                item["winnerCount"] += 1
            else:
                item["loserSum"] += value
                item["loserCount"] += 1
            if abs(value) > 1e-12:
                by_season[season][name] += 1
    output = {}
    for name, item in stats.items():
        count = max(1, item["count"])
        mean = item["sum"] / count
        variance = max(0.0, item["sumSq"] / count - mean * mean)
        winner_mean = item["winnerSum"] / max(1, item["winnerCount"])
        loser_mean = item["loserSum"] / max(1, item["loserCount"])
        output[name] = {
            "count": item["count"],
            "nonzeroCount": item["nonzero"],
            "nonzeroPct": round(item["nonzero"] / count, 6),
            "mean": mean,
            "variance": variance,
            "winnerMinusLoserMean": winner_mean - loser_mean,
            "degenerate": variance < 1e-12 or item["nonzero"] == 0,
        }
    return {
        "version": VERSION,
        "recordCount": len(records),
        "features": output,
        "degenerateFeatures": sorted(name for name, item in output.items() if item["degenerate"]),
        "nonzeroBySeason": {season: dict(counter) for season, counter in sorted(by_season.items())},
    }


def rejection_and_lease_report(state: Mapping[str, Any], handler: Any) -> Dict[str, Any]:
    reasons = Counter(str(row.get("reason") or "unknown") for row in state.get("rejectedSlates") or [] if isinstance(row, Mapping))
    lease = None
    try:
        lease = handler._get_item(handler.LEASE_SK)
    except Exception as exc:
        lease = {"readError": type(exc).__name__}
    return {
        "rejectedSlateCount": sum(reasons.values()),
        "rejectionReasons": dict(reasons.most_common()),
        "lease": lease,
        "skippedInvocationCount": state.get("leaseContentionCount") or state.get("skippedLeaseInvocationCount") or 0,
        "lastInvocationDurationMs": state.get("lastInvocationDurationMs"),
        "staleLeaseRecoveryCount": state.get("staleLeaseRecoveryCount") or 0,
    }


def selective_accuracy_report(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Separate full-slate market baseline from confidence-filtered published picks.

    This is diagnostic only and does not change selections.  It uses the immutable
    pre-lock market consensus already present in each record.
    """
    rows = []
    for row in records:
        home = row.get("homeSignal") or {}
        away = row.get("awaySignal") or {}
        hp = _f(home.get("marketConsensusProbability", home.get("probLatest")), 0.5)
        ap = _f(away.get("marketConsensusProbability", away.get("probLatest")), 0.5)
        p = hp / max(1e-12, hp + ap)
        prediction = 1 if p >= 0.5 else 0
        correct = int(prediction == int(row.get("homeWon") or 0))
        rows.append((str(row.get("slateDateEt") or ""), max(p, 1.0 - p), correct))
    full_accuracy = sum(r[2] for r in rows) / max(1, len(rows))
    thresholds = {}
    for threshold in SELECTIVE_THRESHOLDS:
        chosen = [r for r in rows if r[1] >= threshold]
        thresholds[f"{threshold:.2f}"] = {
            "pickCount": len(chosen),
            "coverage": len(chosen) / max(1, len(rows)),
            "accuracy": sum(r[2] for r in chosen) / max(1, len(chosen)),
            "dayCount": len({r[0] for r in chosen}),
        }
    return {
        "diagnosticOnly": True,
        "fullSlateGameCount": len(rows),
        "fullSlateAccuracy": full_accuracy,
        "selectiveThresholds": thresholds,
    }


def candidate_handoff(result: Mapping[str, Any], fingerprint: str) -> Dict[str, Any]:
    policy = copy.deepcopy(
        result.get("policy")
        or result.get("candidatePolicy")
        or result.get("bestPolicy")
        or {}
    )
    payload = {
        "schemaVersion": "MLB-V7-SHADOW-CANDIDATE-HANDOFF-v1",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "datasetFingerprint": fingerprint,
        "searchVersion": result.get("searchVersion"),
        "policy": policy,
        "promotionAuthority": False,
        "eligibleForCanonicalSeed": bool(policy),
        "requiresCanonicalChronologicalReevaluation": True,
        "requiresFresh200GameUntouchedAudit": True,
    }
    payload["digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return payload
