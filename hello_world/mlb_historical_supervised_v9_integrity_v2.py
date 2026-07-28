"""Integrity and feature-completeness patch for the historical supervised learner.

The patch is deterministic and pre-lock only. It rejects malformed/duplicate
training rows instead of coercing missing labels to away wins, derives V8 values
from the immutable expansion payload when old records lack the convenience
``v8TrainableFeatures`` envelope, and adds bounded market-pattern interactions.
Promotion remains controlled by the existing chronological every-slate 80% gate.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, Mapping, Sequence

import mlb_v7_integrity_pattern_v1 as integrity

VERSION = "MLB-HISTORICAL-SUPERVISED-INTEGRITY-v2.1-finite-fallback-empty-input-guard"
MODEL_VERSION = "MLB-HISTORICAL-SUPERVISED-v9.1-integrity-pattern-complete"
FEATURE_VERSION = "MLB-SUPERVISED-PAIR-FEATURES-v2-integrity-pattern-complete"
EXTRA_FEATURES = (
    "patternCurveDiff",
    "patternBookLeadershipDiff",
    "patternShockPersistenceDiff",
    "patternCompressionBreakoutDiff",
    "patternConsensusPersistenceDiff",
    "patternEntropySum",
    "lateVelocityDiff",
    "reversalInstabilityDiff",
    "coverageConsensusDiff",
)


def _finite(value: Any):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _team_key(signal: Mapping[str, Any]) -> str:
    team = (
        signal.get("team")
        or signal.get("teamName")
        or signal.get("name")
        or signal.get("marketTeam")
        or ""
    )
    return str(team).strip().replace(" ", "_")


def _expansion_value(signal: Mapping[str, Any], market: str, suffix: str):
    expansion = signal.get("oddsMarketExpansionFeatures")
    if not isinstance(expansion, Mapping):
        return None
    team = _team_key(signal)
    if not team:
        return None
    return _finite(expansion.get(f"{market}_{team}{suffix}"))


def _fallback_v8(signal: Mapping[str, Any], name: str):
    lookup = {
        "h2hMedianImpliedProbability": ("h2h", "MedianImpliedProbability"),
        "firstFiveH2HMedianImpliedProbability": (
            "h2h_1st_5_innings",
            "MedianImpliedProbability",
        ),
        "fullGameSpreadMedian": ("spreads", "MedianPoint"),
        "firstFiveSpreadMedian": ("spreads_1st_5_innings", "MedianPoint"),
    }
    if name in lookup:
        market, suffix = lookup[name]
        return _expansion_value(signal, market, suffix)
    expansion = signal.get("oddsMarketExpansionFeatures")
    if not isinstance(expansion, Mapping):
        return None
    if name == "impliedLateInningRunEnvironment":
        value = _finite(expansion.get("impliedLateInningRunEnvironment"))
    elif name == "starterBullpenSpreadDivergence":
        value = _finite(expansion.get("homeStarterBullpenSpreadDivergence"))
        side = str(signal.get("side") or signal.get("marketSide") or "").lower()
        if side == "away" and value is not None:
            value = -value
    else:
        return None
    return value


def _extra_pair_features(learner: Any, home: Mapping[str, Any], away: Mapping[str, Any]) -> Dict[str, float]:
    pattern = learner._pattern
    temporal = learner._temporal
    f = learner._f
    home_reversal_instability = f(home.get("reversalCount")) * temporal(
        home, "180m", "volatilityPpPerPull"
    )
    away_reversal_instability = f(away.get("reversalCount")) * temporal(
        away, "180m", "volatilityPpPerPull"
    )
    home_coverage = temporal(home, "full", "coverageRatio")
    away_coverage = temporal(away, "full", "coverageRatio")
    home_consensus = max(0.0, 1.0 - min(1.0, f(home.get("bookDivergence")) / 0.075))
    away_consensus = max(0.0, 1.0 - min(1.0, f(away.get("bookDivergence")) / 0.075))
    return {
        "patternCurveDiff": pattern(home, "curveScore") - pattern(away, "curveScore"),
        "patternBookLeadershipDiff": pattern(home, "bookLeadershipScore")
        - pattern(away, "bookLeadershipScore"),
        "patternShockPersistenceDiff": pattern(home, "shockPersistence")
        - pattern(away, "shockPersistence"),
        "patternCompressionBreakoutDiff": pattern(home, "compressionBreakout")
        - pattern(away, "compressionBreakout"),
        "patternConsensusPersistenceDiff": pattern(home, "consensusPersistence")
        - pattern(away, "consensusPersistence"),
        "patternEntropySum": pattern(home, "pathEntropy") + pattern(away, "pathEntropy"),
        "lateVelocityDiff": temporal(home, "15m", "velocityPpHr")
        - temporal(away, "15m", "velocityPpHr"),
        "reversalInstabilityDiff": home_reversal_instability - away_reversal_instability,
        "coverageConsensusDiff": home_coverage * home_consensus
        - away_coverage * away_consensus,
    }


def install(learner: Any) -> Any:
    if getattr(learner, "_INQSI_MLB_SUPERVISED_INTEGRITY_V2_INSTALLED", False):
        return learner

    original_features = tuple(learner.FEATURES)
    learner.FEATURES = tuple(dict.fromkeys(original_features + EXTRA_FEATURES))
    learner.FEATURE_VERSION = FEATURE_VERSION
    learner.VERSION = MODEL_VERSION

    original_v8 = learner._v8

    def v8(signal: Mapping[str, Any], name: str):
        value = original_v8(signal, name)
        value = _finite(value) if value is not None else None
        return value if value is not None else _fallback_v8(signal, name)

    learner._v8 = v8

    original_pair_features = learner.pair_features

    def pair_features(
        home: Mapping[str, Any], away: Mapping[str, Any], policy: Mapping[str, Any]
    ) -> Dict[str, float]:
        values = dict(original_pair_features(home, away, policy))
        values.update(_extra_pair_features(learner, home, away))
        return values

    learner.pair_features = pair_features

    def strict_examples(
        records: Sequence[Mapping[str, Any]],
        dates: Sequence[str],
        policy: Mapping[str, Any],
    ):
        allowed = {str(day) for day in dates}
        out = []
        for row in records:
            day = str(row.get("slateDateEt") or "")
            if day not in allowed:
                continue
            label = integrity.strict_binary_label(row)
            values = learner.pair_features(
                row.get("homeSignal") or {}, row.get("awaySignal") or {}, policy
            )
            out.append((day, [learner._f(values.get(name)) for name in learner.FEATURES], label))
        return out

    learner._examples = strict_examples

    original_fit = learner.fit_supervised_policy

    def fit_supervised_policy(optimizer, records, train_dates, base_policy):
        policy, diagnostics = original_fit(optimizer, records, train_dates, base_policy)
        diagnostics = copy.deepcopy(dict(diagnostics))
        diagnostics.update(
            {
                "integrityPatchVersion": VERSION,
                "strictBinaryLabels": True,
                "missingLabelsCoercedToAwayWin": False,
                "finiteV8FallbackRequired": True,
                "v8ExpansionFallbackEnabled": True,
                "extraPatternFeatures": list(EXTRA_FEATURES),
                "selectionObjective": [
                    "dailyPassRate",
                    "minimumDailyAccuracy",
                    "meanDailyAccuracy",
                    "overallAccuracy",
                    "brierScore",
                    "logLoss",
                ],
            }
        )
        return policy, diagnostics

    learner.fit_supervised_policy = fit_supervised_policy

    original_install = learner.install

    def install_optimizer(optimizer: Any, policy_runtime: Any) -> None:
        original_install(optimizer, policy_runtime)
        if getattr(optimizer, "_INQSI_MLB_SUPERVISED_INTEGRITY_V2_SEARCH_INSTALLED", False):
            return
        original_search = optimizer.search

        def strict_search(records, config=None, *, untouched_holdout_dates=None):
            source_records = list(records or [])
            verdict = integrity.validate_training_rows(source_records)
            rejected = copy.deepcopy(verdict.get("rejected") or {})
            accepted = list(verdict.get("accepted") or [])
            evidence = {
                "version": VERSION,
                "inputCount": len(source_records),
                "acceptedCount": len(accepted),
                "rejected": rejected,
                "strictBinaryLabels": True,
                "duplicateGamesRejected": True,
                "postLockProofRequired": True,
                "gameSpecificLockProofRequired": True,
            }
            errors = []
            if not source_records:
                errors.append("training_data_empty")
            if rejected:
                errors.append("training_data_integrity_rejected_rows")
            if not accepted:
                errors.append("no_integrity_eligible_training_rows")
            if errors:
                return {
                    "ok": False,
                    "version": getattr(optimizer, "VERSION", None),
                    "searchVersion": MODEL_VERSION,
                    "status": "DATA_INTEGRITY_BLOCKED",
                    "trainingIntegrity": evidence,
                    "promotionGate": {"passed": False, "errors": errors},
                }
            result = original_search(
                accepted,
                config,
                untouched_holdout_dates=untouched_holdout_dates,
            )
            result["trainingIntegrity"] = evidence
            diagnostics = result.setdefault("supervisedDiagnostics", {})
            if isinstance(diagnostics, dict):
                diagnostics.update(
                    {
                        "integrityPatchVersion": VERSION,
                        "strictBinaryLabels": True,
                        "finiteV8FallbackRequired": True,
                        "v8ExpansionFallbackEnabled": True,
                        "extraPatternFeatures": list(EXTRA_FEATURES),
                    }
                )
            return result

        optimizer.search = strict_search
        optimizer.SUPERVISED_INTEGRITY_PATCH_VERSION = VERSION
        optimizer._INQSI_MLB_SUPERVISED_INTEGRITY_V2_SEARCH_INSTALLED = True

    learner.install = install_optimizer
    learner.INTEGRITY_PATCH_VERSION = VERSION
    learner._INQSI_MLB_SUPERVISED_INTEGRITY_V2_INSTALLED = True
    return learner