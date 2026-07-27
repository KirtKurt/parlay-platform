"""V10 autonomous outcome-anchored signal discovery laboratory.

This research-only engine deliberately starts from settled winners, compares each
winner's pregame signal state with the loser, autonomously creates bucketed atomic
and interaction patterns, and measures recurrence across all settled games.

It is intentionally retrospective and is permanently non-authoritative: no output
from this module may become a production prediction, champion, lock, or wager.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

VERSION = "MLB-V10-AUTONOMOUS-SIGNAL-DISCOVERY-v1"
MIN_PATTERN_OCCURRENCES = 8
MAX_ATOMIC_PER_GAME = 80
MAX_INTERACTIONS_PER_GAME = 120
TOP_REGISTRY_PATTERNS = 2000


def _f(value: Any):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _flatten_numeric(value: Any, prefix: str = "") -> Dict[str, float]:
    out: Dict[str, float] = {}
    if isinstance(value, Mapping):
        for key in sorted(value):
            name = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten_numeric(value[key], name))
    elif isinstance(value, (list, tuple)):
        # Lists are represented by length and numeric summary; raw indices are avoided
        # so provider ordering cannot manufacture false patterns.
        numeric = [_f(item) for item in value]
        numeric = [item for item in numeric if item is not None]
        if numeric:
            out[f"{prefix}.__count"] = float(len(numeric))
            out[f"{prefix}.__mean"] = sum(numeric) / len(numeric)
            out[f"{prefix}.__max"] = max(numeric)
            out[f"{prefix}.__min"] = min(numeric)
    else:
        parsed = _f(value)
        if parsed is not None:
            out[prefix] = parsed
    return out


def _tags(signal: Mapping[str, Any]) -> set[str]:
    values = signal.get("tags") or []
    return {str(value).strip().upper() for value in values if str(value).strip()}


def _bucket(value: float) -> str:
    magnitude = abs(value)
    if magnitude < 1e-12:
        return "zero"
    if magnitude < 0.0025:
        size = "tiny"
    elif magnitude < 0.01:
        size = "small"
    elif magnitude < 0.03:
        size = "medium"
    elif magnitude < 0.10:
        size = "large"
    else:
        size = "extreme"
    return ("positive_" if value > 0 else "negative_") + size


def _winner_loser(record: Mapping[str, Any]) -> Tuple[Mapping[str, Any], Mapping[str, Any], str]:
    home_won = int(record.get("homeWon") or 0) == 1
    if home_won:
        return record.get("homeSignal") or {}, record.get("awaySignal") or {}, "home"
    return record.get("awaySignal") or {}, record.get("homeSignal") or {}, "away"


def _atomic_patterns(record: Mapping[str, Any]) -> list[str]:
    winner, loser, winner_side = _winner_loser(record)
    wf = _flatten_numeric(winner)
    lf = _flatten_numeric(loser)
    patterns = [f"winner_side={winner_side}"]
    common = sorted(set(wf) & set(lf))
    ranked = sorted(common, key=lambda key: abs(wf[key] - lf[key]), reverse=True)
    for key in ranked[:MAX_ATOMIC_PER_GAME]:
        diff = wf[key] - lf[key]
        patterns.append(f"diff:{key}:{_bucket(diff)}")
        if diff > 0:
            patterns.append(f"winner_gt_loser:{key}")
        elif diff < 0:
            patterns.append(f"winner_lt_loser:{key}")
        else:
            patterns.append(f"winner_eq_loser:{key}")
    winner_tags = _tags(winner)
    loser_tags = _tags(loser)
    patterns.extend(f"winner_tag:{tag}" for tag in sorted(winner_tags))
    patterns.extend(f"loser_tag:{tag}" for tag in sorted(loser_tags))
    patterns.extend(f"winner_only_tag:{tag}" for tag in sorted(winner_tags - loser_tags))
    return list(dict.fromkeys(patterns))


def _interactions(atomic: Sequence[str]) -> list[str]:
    # Autonomous bounded interaction creation. Prefer the strongest directional and
    # tag patterns, then create deterministic pairs without manual signal selection.
    eligible = [
        item for item in atomic
        if item.startswith(("diff:", "winner_gt_loser:", "winner_lt_loser:", "winner_only_tag:"))
    ][:24]
    pairs = []
    for i, left in enumerate(eligible):
        for right in eligible[i + 1 :]:
            pairs.append("interaction:" + " && ".join(sorted((left, right))))
            if len(pairs) >= MAX_INTERACTIONS_PER_GAME:
                return pairs
    return pairs


def _comparison_occurrences(record: Mapping[str, Any], patterns: Iterable[str]) -> set[str]:
    """Return patterns that would also describe the actual winner in this record."""
    observed = set(_atomic_patterns(record))
    observed.update(_interactions(sorted(observed)))
    return set(patterns) & observed


def dataset_fingerprint(records: Sequence[Mapping[str, Any]]) -> str:
    material = [
        {
            "date": row.get("slateDateEt"),
            "game": row.get("officialGamePk") or row.get("gameId") or row.get("eventId"),
            "homeWon": row.get("homeWon"),
            "vector": row.get("fingerprint") or row.get("featureVectorFingerprint"),
        }
        for row in records
    ]
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def discover(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    settled = [dict(row) for row in records if row.get("homeWon") in {0, 1, False, True}]
    pattern_games: Dict[str, set[int]] = defaultdict(set)
    dates_by_pattern: Dict[str, set[str]] = defaultdict(set)
    family_counts = Counter()

    for index, record in enumerate(settled):
        atomic = _atomic_patterns(record)
        interactions = _interactions(atomic)
        for pattern in set(atomic + interactions):
            pattern_games[pattern].add(index)
            dates_by_pattern[pattern].add(str(record.get("slateDateEt") or ""))
            family_counts[pattern.split(":", 1)[0]] += 1

    registry = []
    total_games = len(settled)
    for pattern, game_indexes in pattern_games.items():
        occurrences = len(game_indexes)
        if occurrences < MIN_PATTERN_OCCURRENCES:
            continue
        # Because patterns are winner-anchored, recurrence probability is the share
        # of all settled games in which the same winner-associated state reappears.
        # Beta(1,1) smoothing avoids 0%/100% claims in small samples.
        posterior_recurrence = (occurrences + 1.0) / (total_games + 2.0) if total_games else 0.0
        # Cross-game persistence: compare the pattern against every other game's winner.
        matched_other = 0
        compared_other = 0
        for idx, record in enumerate(settled):
            if idx in game_indexes:
                continue
            compared_other += 1
            if pattern in _comparison_occurrences(record, (pattern,)):
                matched_other += 1
        posterior_other_game = (matched_other + 1.0) / (compared_other + 2.0) if compared_other else 0.0
        registry.append(
            {
                "signalId": hashlib.sha256(pattern.encode()).hexdigest()[:20],
                "definition": pattern,
                "family": pattern.split(":", 1)[0],
                "occurrenceCount": occurrences,
                "occurrenceRate": occurrences / total_games if total_games else 0.0,
                "slateDayCount": len(dates_by_pattern[pattern]),
                "otherGamesCompared": compared_other,
                "otherGameWinnerMatchCount": matched_other,
                "posteriorProbabilityOfRecurring": posterior_recurrence,
                "posteriorProbabilityInOtherGameWinner": posterior_other_game,
                "researchOnly": True,
                "productionEligible": False,
            }
        )

    registry.sort(
        key=lambda row: (
            row["posteriorProbabilityInOtherGameWinner"],
            row["occurrenceCount"],
            row["slateDayCount"],
        ),
        reverse=True,
    )
    registry = registry[:TOP_REGISTRY_PATTERNS]
    return {
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "mode": "OUTCOME_ANCHORED_RETROSPECTIVE_DISCOVERY",
        "autonomousFeatureGeneration": True,
        "winnerKnownBeforeSignalConstruction": True,
        "warning": "Probabilities are retrospective recurrence estimates, not validated predictive probabilities.",
        "productionAuthority": False,
        "mayWriteChampion": False,
        "mayPublishPicks": False,
        "settledGameCount": total_games,
        "datasetFingerprint": dataset_fingerprint(settled),
        "minimumPatternOccurrences": MIN_PATTERN_OCCURRENCES,
        "generatedPatternCount": len(pattern_games),
        "retainedPatternCount": len(registry),
        "patternFamilyCounts": dict(family_counts),
        "signals": registry,
    }
