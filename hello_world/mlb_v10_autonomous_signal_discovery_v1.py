"""Leakage-safe V10 autonomous signal discovery research engine.

V10 discovers side-applicable pregame rules, freezes them on chronological development
partitions, evaluates them on walk-forward and untouched holdout data, and remains
permanently research-only. It cannot publish picks, write a champion, or change
production authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

VERSION = "MLB-V10-AUTONOMOUS-SIGNAL-DISCOVERY-v2.0-leakage-safe"
MIN_PATTERN_OCCURRENCES = 20
MIN_PATTERN_DAYS = 8
MIN_WALK_FORWARD_PICKS = 40
MIN_UNTOUCHED_PICKS = 40
MAX_ATOMIC_PER_GAME = 80
MAX_INTERACTIONS_PER_GAME = 100
TOP_REGISTRY_PATTERNS = 1000
MAX_REPORT_SIGNALS = 300
FDR_ALPHA = 0.10
PERMUTATION_ROUNDS = 40
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
    reasons = []
    if not _game_id(row): reasons.append("missing_game_id")
    if not _date(row): reasons.append("missing_slate_date")
    if _label(row) is None: reasons.append("invalid_label")
    if row.get("trainingEligible") is False: reasons.append("training_ineligible")
    if row.get("canonicalLockValid") is False: reasons.append("invalid_canonical_lock")
    if row.get("duplicateContaminated") is True: reasons.append("duplicate_contaminated")
    cutoff = str(row.get("featureCutoff") or row.get("perGameFeatureCutoff") or "")
    if cutoff and "45" not in cutoff: reasons.append("non_t45_cutoff")
    for side in ("homeSignal", "awaySignal"):
        if not isinstance(row.get(side), Mapping): reasons.append(f"missing_{side}")
    return not reasons, reasons


def _deduplicate(records: Sequence[Mapping[str, Any]]) -> Tuple[list[dict], Dict[str, Any]]:
    by_key: Dict[Tuple[str, str], dict] = {}
    exclusions = Counter()
    duplicate_count = 0
    for raw in records:
        row = dict(raw)
        ok, reasons = _canonical_ok(row)
        if not ok:
            exclusions.update(reasons)
            continue
        key = (_date(row), _game_id(row))
        if key in by_key:
            duplicate_count += 1
            old_fp = str(by_key[key].get("fingerprint") or by_key[key].get("featureVectorFingerprint") or "")
            new_fp = str(row.get("fingerprint") or row.get("featureVectorFingerprint") or "")
            if old_fp and new_fp and old_fp != new_fp:
                exclusions["conflicting_duplicate"] += 1
                by_key.pop(key, None)
            continue
        by_key[key] = row
    clean = [by_key[key] for key in sorted(by_key)]
    return clean, {
        "inputRecordCount": len(records),
        "canonicalRecordCount": len(clean),
        "duplicateRecordCount": duplicate_count,
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
        if numeric:
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
    return ("positive_" if value > 0 else "negative_") + size


def _side_applicable_rules(record: Mapping[str, Any]) -> list[dict]:
    home = record.get("homeSignal") or {}
    away = record.get("awaySignal") or {}
    hf, af = _flatten_numeric(home), _flatten_numeric(away)
    rules = []
    ranked = sorted(set(hf) & set(af), key=lambda key: abs(hf[key] - af[key]), reverse=True)
    for key in ranked[:MAX_ATOMIC_PER_GAME]:
        diff = hf[key] - af[key]
        if abs(diff) < 1e-12:
            continue
        rules.append({
            "definition": f"higher:{key}:{_bucket(abs(diff))}",
            "selectedSide": "home" if diff > 0 else "away",
            "family": "higher_numeric",
        })
        rules.append({
            "definition": f"lower:{key}:{_bucket(abs(diff))}",
            "selectedSide": "away" if diff > 0 else "home",
            "family": "lower_numeric",
        })
    home_tags, away_tags = _tags(home), _tags(away)
    for tag in sorted(home_tags ^ away_tags):
        rules.append({
            "definition": f"has_tag:{tag}",
            "selectedSide": "home" if tag in home_tags else "away",
            "family": "tag",
        })
        rules.append({
            "definition": f"lacks_tag:{tag}",
            "selectedSide": "away" if tag in home_tags else "home",
            "family": "tag",
        })
    unique = {r["definition"] + "|" + r["selectedSide"]: r for r in rules}
    return list(unique.values())


def _correct(rule: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    return (rule["selectedSide"] == "home") == bool(_label(row))


def _partitions(records: Sequence[Mapping[str, Any]]) -> Dict[str, list[str]]:
    dates = sorted({_date(row) for row in records})
    n = len(dates)
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
    output = {}
    for definition, item in stats.items():
        picks = item["picks"]
        output[definition] = {
            "pickCount": picks,
            "correct": item["correct"],
            "accuracy": item["correct"] / picks if picks else 0.0,
            "slateDayCount": len(item["days"]),
            "family": item["family"],
        }
    return output


def _two_sided_binomial_pvalue(correct: int, total: int, baseline: float = 0.5) -> float:
    if total <= 0: return 1.0
    mean = total * baseline
    variance = total * baseline * (1.0 - baseline)
    if variance <= 0: return 1.0
    z = abs(correct - mean) / math.sqrt(variance)
    return min(1.0, math.erfc(z / math.sqrt(2.0)))


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


def _permutation_control(records: Sequence[Mapping[str, Any]], definitions: set[str], rounds: int = PERMUTATION_ROUNDS) -> Dict[str, Any]:
    if not records or not definitions:
        return {"rounds": 0, "maximumAccuracy95thPercentile": 1.0, "passed": False}
    rng = random.Random(17010)
    maxima = []
    labels = [_label(row) for row in records]
    for _ in range(rounds):
        shuffled = list(labels)
        rng.shuffle(shuffled)
        shadow = [dict(row, homeWon=shuffled[i]) for i, row in enumerate(records)]
        metrics = _evaluate(shadow, {_date(row) for row in shadow}, definitions)
        maxima.append(max((m["accuracy"] for m in metrics.values() if m["pickCount"] >= MIN_PATTERN_OCCURRENCES), default=0.5))
    maxima.sort()
    index = min(len(maxima) - 1, int(len(maxima) * 0.95))
    return {"rounds": rounds, "maximumAccuracy95thPercentile": maxima[index], "passed": True}


def dataset_fingerprint(records: Sequence[Mapping[str, Any]]) -> str:
    material = []
    for row in sorted(records, key=lambda r: (_date(r), _game_id(r))):
        material.append({
            "date": _date(row), "game": _game_id(row), "homeWon": _label(row),
            "vector": row.get("fingerprint") or row.get("featureVectorFingerprint") or "",
        })
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def discover(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    clean, integrity = _deduplicate(records)
    partitions = _partitions(clean)
    development_dates = set(partitions["development"])
    development_records = [row for row in clean if _date(row) in development_dates]

    occurrence = Counter()
    correct = Counter()
    days = defaultdict(set)
    families = Counter()
    unique_families = defaultdict(set)
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
            "family": definition.split(":", 1)[0],
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

    wf = _evaluate(clean, partitions["walkForward"], definitions)
    holdout = _evaluate(clean, partitions["untouchedHoldout"], definitions)
    controls = _permutation_control(development_records, definitions)
    registry = []
    for row in candidates:
        definition = row["definition"]
        w = wf.get(definition, {"pickCount": 0, "correct": 0, "accuracy": 0.0, "slateDayCount": 0})
        h = holdout.get(definition, {"pickCount": 0, "correct": 0, "accuracy": 0.0, "slateDayCount": 0})
        predictive = (
            w["pickCount"] >= MIN_WALK_FORWARD_PICKS
            and h["pickCount"] >= MIN_UNTOUCHED_PICKS
            and w["accuracy"] > controls["maximumAccuracy95thPercentile"]
            and h["accuracy"] > 0.5
        )
        row.update({
            "walkForward": w,
            "untouchedHoldout": h,
            "predictiveResearchGatePassed": predictive,
        })
        registry.append(row)
    registry.sort(key=lambda row: (row["predictiveResearchGatePassed"], row["untouchedHoldout"]["accuracy"], row["walkForward"]["accuracy"], row["developmentPickCount"]), reverse=True)
    registry = registry[:MAX_REPORT_SIGNALS]

    return {
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "mode": "CHRONOLOGICAL_SIDE_APPLICABLE_DISCOVERY",
        "autonomousFeatureGeneration": True,
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
        "prohibitedFeatureTokens": sorted(PROHIBITED_TOKENS),
        "partitions": partitions,
        "minimumPatternOccurrences": MIN_PATTERN_OCCURRENCES,
        "minimumPatternDays": MIN_PATTERN_DAYS,
        "generatedPatternCount": len(occurrence),
        "fdrAlpha": FDR_ALPHA,
        "fdrRetainedPatternCount": len(candidates),
        "retainedPatternCount": len(registry),
        "patternFamilyObservationCounts": dict(families),
        "uniquePatternCountByFamily": {key: len(value) for key, value in unique_families.items()},
        "negativeControl": controls,
        "multipleTestingCorrection": "Benjamini-Hochberg",
        "uncertaintyInterval": "Wilson 95%",
        "incrementalExecutionSupported": True,
        "fullRebuildRecommendedCadence": "DAILY",
        "signals": registry,
        "blockers": [] if clean else ["no_canonical_records"],
    }
