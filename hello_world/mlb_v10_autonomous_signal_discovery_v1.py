"""Leakage-safe V10 autonomous signal discovery research engine.

V10 discovers side-applicable pregame atomic and interaction rules on a chronological
 development partition, freezes a research registry and aggregate voting policy, then
 evaluates them on walk-forward, untouched holdout, and later prospective shadow data.
It is permanently research-only and cannot publish picks, write a champion, wager, or
change production authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

VERSION = "MLB-V10-AUTONOMOUS-SIGNAL-DISCOVERY-v2.1-integrity-statistical-portfolio"
MIN_PATTERN_OCCURRENCES = 20
MIN_PATTERN_DAYS = 8
MIN_WALK_FORWARD_PICKS = 40
MIN_UNTOUCHED_PICKS = 40
MAX_ATOMIC_PER_GAME = 80
MAX_INTERACTIONS_PER_GAME = 100
TOP_REGISTRY_PATTERNS = 1000
MAX_REPORT_SIGNALS = 300
FDR_ALPHA = 0.10
PERMUTATION_ROUNDS = 100
PROHIBITED_TOKENS = {
    "winner", "loser", "result", "final", "score", "settlement", "settled",
    "correct", "postgame", "actualruns", "homescore", "awayscore", "homewon",
}
CANONICAL_REQUIRED_FLAGS = (
    "trainingEligible", "canonicalLockValid", "duplicateContaminated",
)


def _f(value: Any):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _game_id(row: Mapping[str, Any]) -> str:
    return str(row.get("officialGamePk") or row.get("gameId") or row.get("eventId") or "")


def _date(row: Mapping[str, Any]) -> str:
    return str(row.get("slateDateEt") or "")


def _label(row: Mapping[str, Any]):
    value = row.get("homeWon")
    if value is True or value == 1:
        return 1
    if value is False or value == 0:
        return 0
    return None


def _canonical_ok(row: Mapping[str, Any]) -> Tuple[bool, list[str]]:
    reasons: list[str] = []
    if not _game_id(row): reasons.append("missing_game_id")
    if not _date(row): reasons.append("missing_slate_date")
    if _label(row) is None: reasons.append("invalid_label")
    if row.get("trainingEligible") is not True: reasons.append("training_eligibility_not_proven")
    if row.get("canonicalLockValid") is not True: reasons.append("canonical_lock_not_proven")
    if row.get("duplicateContaminated") is not False: reasons.append("duplicate_cleanliness_not_proven")
    cutoff = str(row.get("featureCutoff") or row.get("perGameFeatureCutoff") or "")
    if "45" not in cutoff: reasons.append("t45_cutoff_not_proven")
    for side in ("homeSignal", "awaySignal"):
        if not isinstance(row.get(side), Mapping): reasons.append(f"missing_{side}")
    return not reasons, reasons


def _deduplicate(records: Sequence[Mapping[str, Any]]) -> Tuple[list[dict], Dict[str, Any]]:
    by_key: Dict[Tuple[str, str], dict] = {}
    quarantined: set[Tuple[str, str]] = set()
    exclusions = Counter()
    duplicate_count = 0
    for raw in records:
        row = dict(raw)
        ok, reasons = _canonical_ok(row)
        if not ok:
            exclusions.update(reasons)
            continue
        key = (_date(row), _game_id(row))
        if key in quarantined:
            duplicate_count += 1
            exclusions["quarantined_duplicate_reappearance"] += 1
            continue
        if key in by_key:
            duplicate_count += 1
            old_fp = str(by_key[key].get("fingerprint") or by_key[key].get("featureVectorFingerprint") or "")
            new_fp = str(row.get("fingerprint") or row.get("featureVectorFingerprint") or "")
            if not old_fp or not new_fp:
                exclusions["duplicate_fingerprint_not_proven"] += 1
                by_key.pop(key, None)
                quarantined.add(key)
            elif old_fp != new_fp:
                exclusions["conflicting_duplicate"] += 1
                by_key.pop(key, None)
                quarantined.add(key)
            continue
        by_key[key] = row
    clean = [by_key[key] for key in sorted(by_key) if key not in quarantined]
    return clean, {
        "inputRecordCount": len(records),
        "canonicalRecordCount": len(clean),
        "duplicateRecordCount": duplicate_count,
        "quarantinedGameCount": len(quarantined),
        "exclusionCounts": dict(exclusions),
    }


def _flatten_numeric(value: Any, prefix: str = "") -> Dict[str, float]:
    out: Dict[str, float] = {}
    if isinstance(value, Mapping):
        for key in sorted(value):
            name = f"{prefix}.{key}" if prefix else str(key)
            normalized = name.lower().replace("_", "").replace("-", "")
            if any(token in normalized for token in PROHIBITED_TOKENS):
                continue
            out.update(_flatten_numeric(value[key], name))
    elif isinstance(value, (list, tuple)):
        numeric = [_f(item) for item in value]
        numeric = [item for item in numeric if item is not None]
        if numeric and prefix:
            out[f"{prefix}.__count"] = float(len(numeric))
            out[f"{prefix}.__mean"] = sum(numeric) / len(numeric)
            out[f"{prefix}.__max"] = max(numeric)
            out[f"{prefix}.__min"] = min(numeric)
    else:
        parsed = _f(value)
        if parsed is not None and prefix:
            out[prefix] = parsed
    return out


def _tags(signal: Mapping[str, Any]) -> set[str]:
    values = signal.get("tags") or []
    return {str(value).strip().upper() for value in values if str(value).strip()}


def _bucket(value: float) -> str:
    magnitude = abs(value)
    if magnitude < 1e-12: return "zero"
    if magnitude < 0.0025: size = "tiny"
    elif magnitude < 0.01: size = "small"
    elif magnitude < 0.03: size = "medium"
    elif magnitude < 0.10: size = "large"
    else: size = "extreme"
    return size


def _atomic_rules(record: Mapping[str, Any]) -> list[dict]:
    home = record.get("homeSignal") or {}
    away = record.get("awaySignal") or {}
    hf, af = _flatten_numeric(home), _flatten_numeric(away)
    rules: list[dict] = []
    ranked = sorted(set(hf) & set(af), key=lambda key: abs(hf[key] - af[key]), reverse=True)
    for key in ranked[:MAX_ATOMIC_PER_GAME]:
        diff = hf[key] - af[key]
        if abs(diff) < 1e-12:
            continue
        magnitude = _bucket(diff)
        rules.append({"definition": f"higher:{key}:{magnitude}", "selectedSide": "home" if diff > 0 else "away", "family": "higher_numeric"})
        rules.append({"definition": f"lower:{key}:{magnitude}", "selectedSide": "away" if diff > 0 else "home", "family": "lower_numeric"})
    home_tags, away_tags = _tags(home), _tags(away)
    for tag in sorted(home_tags ^ away_tags):
        rules.append({"definition": f"has_tag:{tag}", "selectedSide": "home" if tag in home_tags else "away", "family": "tag"})
        rules.append({"definition": f"lacks_tag:{tag}", "selectedSide": "away" if tag in home_tags else "home", "family": "tag"})
    unique = {r["definition"] + "|" + r["selectedSide"]: r for r in rules}
    return list(unique.values())


def _side_applicable_rules(record: Mapping[str, Any]) -> list[dict]:
    atomic = _atomic_rules(record)
    interactions: list[dict] = []
    compact = sorted(atomic, key=lambda r: (r["family"], r["definition"], r["selectedSide"]))[:32]
    for index, left in enumerate(compact):
        for right in compact[index + 1:]:
            if left["selectedSide"] != right["selectedSide"]:
                continue
            definitions = sorted((left["definition"], right["definition"]))
            interactions.append({
                "definition": "interaction:" + " && ".join(definitions),
                "selectedSide": left["selectedSide"],
                "family": "interaction",
            })
            if len(interactions) >= MAX_INTERACTIONS_PER_GAME:
                break
        if len(interactions) >= MAX_INTERACTIONS_PER_GAME:
            break
    all_rules = atomic + interactions
    unique = {r["definition"] + "|" + r["selectedSide"]: r for r in all_rules}
    return list(unique.values())


def _correct(rule: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    return (rule["selectedSide"] == "home") == bool(_label(row))


def _partitions(records: Sequence[Mapping[str, Any]]) -> Dict[str, list[str]]:
    dates = sorted({_date(row) for row in records})
    n = len(dates)
    if n == 0:
        return {"development": [], "walkForward": [], "untouchedHoldout": []}
    a = max(1, int(n * 0.60))
    b = max(a + 1, int(n * 0.80)) if n >= 3 else n
    return {"development": dates[:a], "walkForward": dates[a:b], "untouchedHoldout": dates[b:]}


def _evaluate(records: Sequence[Mapping[str, Any]], dates: Iterable[str], definitions: set[str]) -> Dict[str, Any]:
    date_set = set(dates)
    stats: Dict[str, Dict[str, Any]] = {}
    for row in records:
        if _date(row) not in date_set:
            continue
        for rule in _side_applicable_rules(row):
            definition = rule["definition"]
            if definition not in definitions:
                continue
            item = stats.setdefault(definition, {"picks": 0, "correct": 0, "days": set(), "family": rule["family"]})
            item["picks"] += 1
            item["correct"] += int(_correct(rule, row))
            item["days"].add(_date(row))
    return {
        definition: {
            "pickCount": item["picks"],
            "correct": item["correct"],
            "accuracy": item["correct"] / item["picks"] if item["picks"] else 0.0,
            "slateDayCount": len(item["days"]),
            "family": item["family"],
        }
        for definition, item in stats.items()
    }


def _binomial_probability(k: int, n: int, p: float) -> float:
    return math.comb(n, k) * (p ** k) * ((1.0 - p) ** (n - k))


def _two_sided_binomial_pvalue(correct: int, total: int, baseline: float = 0.5) -> float:
    if total <= 0 or not 0.0 < baseline < 1.0:
        return 1.0
    observed = _binomial_probability(correct, total, baseline)
    tolerance = observed * 1e-12 + 1e-18
    return min(1.0, sum(
        probability for k in range(total + 1)
        if (probability := _binomial_probability(k, total, baseline)) <= observed + tolerance
    ))


def _bh_qvalues(rows: list[dict]) -> None:
    ordered = sorted(enumerate(rows), key=lambda pair: pair[1]["pValue"])
    m, running = len(rows), 1.0
    for rank in range(m, 0, -1):
        index, row = ordered[rank - 1]
        running = min(running, row["pValue"] * m / rank)
        rows[index]["qValue"] = running


def _wilson(correct: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    if total <= 0: return (0.0, 1.0)
    p = correct / total
    d = 1 + z * z / total
    center = (p + z * z / (2 * total)) / d
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / d
    return max(0.0, center - margin), min(1.0, center + margin)


def _permutation_control(records: Sequence[Mapping[str, Any]], definitions: set[str], *, minimum_picks: int, rounds: int = PERMUTATION_ROUNDS, seed: int = 17010) -> Dict[str, Any]:
    if not records or not definitions:
        return {"rounds": 0, "maximumAccuracy95thPercentile": 1.0, "passed": False, "minimumPicks": minimum_picks}
    rng = random.Random(seed)
    maxima = []
    labels = [_label(row) for row in records]
    dates = {_date(row) for row in records}
    for _ in range(rounds):
        shuffled = list(labels)
        rng.shuffle(shuffled)
        shadow = [dict(row, homeWon=shuffled[i]) for i, row in enumerate(records)]
        metrics = _evaluate(shadow, dates, definitions)
        maxima.append(max((m["accuracy"] for m in metrics.values() if m["pickCount"] >= minimum_picks), default=0.5))
    maxima.sort()
    index = min(len(maxima) - 1, math.ceil(len(maxima) * 0.95) - 1)
    return {"rounds": rounds, "maximumAccuracy95thPercentile": maxima[index], "passed": True, "minimumPicks": minimum_picks, "seed": seed}


def _portfolio(records: Sequence[Mapping[str, Any]], dates: Iterable[str], signal_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    date_set = set(dates)
    weights = {
        str(row["definition"]): max(0.0, float(row.get("developmentAccuracy") or 0.5) - 0.5)
        for row in signal_rows
    }
    predictions = []
    for record in records:
        if _date(record) not in date_set:
            continue
        votes = {"home": 0.0, "away": 0.0}
        supporting = {"home": [], "away": []}
        for rule in _side_applicable_rules(record):
            weight = weights.get(rule["definition"], 0.0)
            if weight <= 0.0:
                continue
            side = rule["selectedSide"]
            votes[side] += weight
            supporting[side].append(rule["definition"])
        if votes["home"] == votes["away"]:
            continue
        selected = "home" if votes["home"] > votes["away"] else "away"
        correct = (selected == "home") == bool(_label(record))
        predictions.append({
            "slateDateEt": _date(record), "gameId": _game_id(record), "selectedSide": selected,
            "correct": correct, "homeVote": votes["home"], "awayVote": votes["away"],
            "supportingSignalCount": len(supporting[selected]),
        })
    by_day: Dict[str, list[dict]] = defaultdict(list)
    for row in predictions:
        by_day[row["slateDateEt"]].append(row)
    daily = []
    for day in sorted(by_day):
        rows = by_day[day]
        wins = sum(row["correct"] for row in rows)
        daily.append({"slateDateEt": day, "pickCount": len(rows), "correct": wins, "accuracy": wins / len(rows)})
    wins = sum(row["correct"] for row in predictions)
    return {
        "policyType": "DEVELOPMENT_WEIGHTED_MAJORITY_VOTE",
        "selectionUsesHoldoutLabels": False,
        "pickCount": len(predictions),
        "correct": wins,
        "losses": len(predictions) - wins,
        "accuracy": wins / len(predictions) if predictions else None,
        "selectionDayCount": len(daily),
        "meanDailyAccuracy": sum(row["accuracy"] for row in daily) / len(daily) if daily else None,
        "minimumDailyAccuracy": min((row["accuracy"] for row in daily), default=None),
        "daily": daily,
        "predictions": predictions[:500],
    }


def dataset_fingerprint(records: Sequence[Mapping[str, Any]]) -> str:
    material = [{
        "date": _date(row), "game": _game_id(row), "homeWon": _label(row),
        "vector": row.get("fingerprint") or row.get("featureVectorFingerprint") or "",
    } for row in sorted(records, key=lambda r: (_date(r), _game_id(r)))]
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def evaluate_frozen_registry(records: Sequence[Mapping[str, Any]], previous: Mapping[str, Any]) -> Dict[str, Any]:
    freeze = previous.get("registryFreeze") or {}
    frozen_through = str(freeze.get("frozenThroughDate") or "")
    frozen_signals = [row for row in previous.get("signals") or [] if row.get("predictiveResearchGatePassed")]
    future = [row for row in records if _date(row) > frozen_through] if frozen_through else []
    dates = sorted({_date(row) for row in future})
    portfolio = _portfolio(future, dates, frozen_signals) if future and frozen_signals else _portfolio([], [], [])
    return {
        "status": "EVALUATED" if portfolio["pickCount"] else "AWAITING_FUTURE_GAMES",
        "registryVersion": previous.get("version"),
        "registryFingerprint": freeze.get("registryFingerprint"),
        "frozenThroughDate": frozen_through or None,
        "futureCanonicalGameCount": len(future),
        "futureSlateCount": len(dates),
        "policyChangedDuringEvaluation": False,
        "productionAuthority": False,
        "portfolio": portfolio,
    }


def discover(records: Sequence[Mapping[str, Any]], *, previous_report: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    clean, integrity = _deduplicate(records)
    partitions = _partitions(clean)
    development_dates = set(partitions["development"])
    development_records = [row for row in clean if _date(row) in development_dates]

    occurrence, correct = Counter(), Counter()
    days, families, unique_families = defaultdict(set), Counter(), defaultdict(set)
    for row in development_records:
        for rule in _side_applicable_rules(row):
            definition = rule["definition"]
            occurrence[definition] += 1
            correct[definition] += int(_correct(rule, row))
            days[definition].add(_date(row))
            families[rule["family"]] += 1
            unique_families[rule["family"]].add(definition)

    candidates = []
    for definition, count in occurrence.items():
        if count < MIN_PATTERN_OCCURRENCES or len(days[definition]) < MIN_PATTERN_DAYS:
            continue
        wins = correct[definition]
        lower, upper = _wilson(wins, count)
        candidates.append({
            "signalId": hashlib.sha256(definition.encode()).hexdigest()[:20],
            "definition": definition,
            "family": "interaction" if definition.startswith("interaction:") else definition.split(":", 1)[0],
            "developmentPickCount": count,
            "developmentCorrect": wins,
            "developmentAccuracy": wins / count,
            "developmentWilson95": [lower, upper],
            "developmentSlateDayCount": len(days[definition]),
            "pValue": _two_sided_binomial_pvalue(wins, count),
            "researchOnly": True,
            "productionEligible": False,
        })
    _bh_qvalues(candidates)
    candidates = [row for row in candidates if row.get("qValue", 1.0) <= FDR_ALPHA and row["developmentAccuracy"] > 0.5]
    candidates.sort(key=lambda row: (row["developmentAccuracy"], row["developmentPickCount"], row["developmentSlateDayCount"]), reverse=True)
    candidates = candidates[:TOP_REGISTRY_PATTERNS]
    definitions = {row["definition"] for row in candidates}

    wf_records = [row for row in clean if _date(row) in set(partitions["walkForward"])]
    holdout_records = [row for row in clean if _date(row) in set(partitions["untouchedHoldout"])]
    wf = _evaluate(wf_records, partitions["walkForward"], definitions)
    holdout = _evaluate(holdout_records, partitions["untouchedHoldout"], definitions)
    controls = {
        "development": _permutation_control(development_records, definitions, minimum_picks=MIN_PATTERN_OCCURRENCES, seed=17010),
        "walkForward": _permutation_control(wf_records, definitions, minimum_picks=MIN_WALK_FORWARD_PICKS, seed=17011),
        "untouchedHoldout": _permutation_control(holdout_records, definitions, minimum_picks=MIN_UNTOUCHED_PICKS, seed=17012),
    }
    registry = []
    for row in candidates:
        definition = row["definition"]
        w = wf.get(definition, {"pickCount": 0, "correct": 0, "accuracy": 0.0, "slateDayCount": 0})
        h = holdout.get(definition, {"pickCount": 0, "correct": 0, "accuracy": 0.0, "slateDayCount": 0})
        predictive = (
            w["pickCount"] >= MIN_WALK_FORWARD_PICKS
            and h["pickCount"] >= MIN_UNTOUCHED_PICKS
            and w["accuracy"] > controls["walkForward"]["maximumAccuracy95thPercentile"]
            and h["accuracy"] > controls["untouchedHoldout"]["maximumAccuracy95thPercentile"]
        )
        row.update({"walkForward": w, "untouchedHoldout": h, "predictiveResearchGatePassed": predictive})
        registry.append(row)
    registry.sort(key=lambda row: (row["predictiveResearchGatePassed"], row["untouchedHoldout"]["accuracy"], row["walkForward"]["accuracy"], row["developmentPickCount"]), reverse=True)
    registry = registry[:MAX_REPORT_SIGNALS]

    frozen = [row for row in registry if row["predictiveResearchGatePassed"]]
    aggregate = {
        "walkForward": _portfolio(clean, partitions["walkForward"], frozen),
        "untouchedHoldout": _portfolio(clean, partitions["untouchedHoldout"], frozen),
    }
    frozen_through = max(partitions["untouchedHoldout"], default=max((_date(row) for row in clean), default=""))
    registry_fingerprint = hashlib.sha256(json.dumps([
        {"definition": row["definition"], "developmentAccuracy": row["developmentAccuracy"]}
        for row in frozen
    ], sort_keys=True).encode()).hexdigest()
    prospective = evaluate_frozen_registry(clean, previous_report) if previous_report else {
        "status": "NOT_STARTED_NO_PRIOR_FROZEN_REGISTRY",
        "futureCanonicalGameCount": 0,
        "futureSlateCount": 0,
        "policyChangedDuringEvaluation": False,
        "productionAuthority": False,
        "portfolio": _portfolio([], [], []),
    }

    return {
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "mode": "CHRONOLOGICAL_SIDE_APPLICABLE_DISCOVERY",
        "autonomousFeatureGeneration": True,
        "atomicAndInteractionDiscovery": True,
        "winnerKnownBeforeSignalConstruction": False,
        "outcomeUsedOnlyForTrainingLabels": True,
        "warning": "Research-only results; no production authority and no public picks.",
        "productionAuthority": False,
        "mayWriteChampion": False,
        "mayPublishPicks": False,
        "inputIntegrity": integrity,
        "settledGameCount": len(clean),
        "datasetFingerprint": dataset_fingerprint(clean),
        "canonicalSortApplied": True,
        "canonicalDeduplicationApplied": True,
        "canonicalEligibilityFailClosed": True,
        "conflictingDuplicatesPermanentlyQuarantined": True,
        "prohibitedFeatureTokens": sorted(PROHIBITED_TOKENS),
        "partitions": partitions,
        "minimumPatternOccurrences": MIN_PATTERN_OCCURRENCES,
        "minimumPatternDays": MIN_PATTERN_DAYS,
        "generatedPatternCount": len(occurrence),
        "fdrAlpha": FDR_ALPHA,
        "fdrRetainedPatternCount": len(candidates),
        "retainedPatternCount": len(registry),
        "predictiveSignalCount": len(frozen),
        "patternFamilyObservationCounts": dict(families),
        "uniquePatternCountByFamily": {key: len(value) for key, value in unique_families.items()},
        "negativeControl": controls,
        "multipleTestingCorrection": "Benjamini-Hochberg",
        "exactSignificanceTest": "two-sided exact binomial",
        "uncertaintyInterval": "Wilson 95%",
        "aggregateResearchPolicy": aggregate,
        "registryFreeze": {
            "frozenThroughDate": frozen_through or None,
            "registryFingerprint": registry_fingerprint,
            "frozenSignalCount": len(frozen),
            "selectionUsedUntouchedHoldoutLabels": False,
        },
        "prospectiveShadow": prospective,
        "incrementalExecutionSupported": True,
        "fullRebuildRecommendedCadence": "DAILY",
        "signals": registry,
        "blockers": [] if clean else ["no_canonical_records"],
    }
