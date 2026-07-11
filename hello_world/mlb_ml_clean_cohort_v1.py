from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "MLB-ML-CLEAN-COHORT-v1-post-fix-immutable-feature-snapshot"
FEATURE_SNAPSHOT_VERSION = "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v1-home-away-outcome"
DEFAULT_MIN_LOCK_AT_UTC = os.environ.get("INQSI_MLB_ML_CLEAN_MIN_LOCK_AT_UTC", "2026-07-11T15:22:00+00:00")


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _sig(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    value = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return value if isinstance(value, dict) else {}


def _prob(signal: Dict[str, Any]) -> Optional[float]:
    for key in ("marketConsensusProbability", "probLatest", "fairProbability", "winProbability"):
        value = _f(signal.get(key))
        if value is not None:
            value = value / 100.0 if value > 1 else value
            if 0 < value < 1:
                return value
    return None


def _american_implied(value: Any) -> Optional[float]:
    price = _f(value)
    if price is None or price == 0:
        return None
    return abs(price) / (abs(price) + 100.0) if price < 0 else 100.0 / (price + 100.0)


def _tag(signal: Dict[str, Any], name: str) -> float:
    return 1.0 if name in {str(item) for item in (signal.get("tags") or [])} else 0.0


def _run_line(signal: Dict[str, Any]) -> float:
    value = _f(signal.get("runLineMovement"), 0.0)
    return float(value or 0.0)


def _fundamental_value(snapshot: Dict[str, Any], key: str, default: float = 0.0) -> float:
    values = snapshot.get("numericValues") or {}
    value = _f(values.get(key), default)
    return float(value if value is not None else default)


def _audit(row: Dict[str, Any]) -> Dict[str, Any]:
    value = row.get("lockedCardAudit") or {}
    return value if isinstance(value, dict) else {}


def _lock_at(row: Dict[str, Any]) -> Optional[datetime]:
    audit = _audit(row)
    for value in (
        audit.get("lockAtUtc"),
        (row.get("slatePredictionLock") or {}).get("lockAtUtc"),
        (row.get("lastPossiblePredictionGate") or {}).get("lockAtUtc"),
        row.get("lockedAtUtc"),
    ):
        parsed = _parse_dt(value)
        if parsed:
            return parsed
    return None


def _source_at(row: Dict[str, Any]) -> Optional[datetime]:
    audit = _audit(row)
    for value in (
        audit.get("explicitSourceAtUtc"),
        row.get("predictionSourcePullAt"),
        (row.get("slatePredictionLock") or {}).get("latestScoringPullAt"),
        (row.get("lastPossiblePredictionGate") or {}).get("latestScoringPullAt"),
    ):
        parsed = _parse_dt(value)
        if parsed:
            return parsed
    return None


def _game_id(row: Dict[str, Any]) -> str:
    provider_id = row.get("id") or row.get("gameId") or row.get("game_id")
    if provider_id:
        return str(provider_id)
    start = str(row.get("commenceTime") or row.get("commence_time") or "unknown")
    return f"{_norm(row.get('awayTeam') or row.get('away_team'))}|{_norm(row.get('homeTeam') or row.get('home_team'))}|{start}"


def _home_won(row: Dict[str, Any]) -> Optional[int]:
    winner = _norm(row.get("winner"))
    home = _norm(row.get("homeTeam") or row.get("home_team"))
    away = _norm(row.get("awayTeam") or row.get("away_team"))
    if not winner or winner not in {home, away}:
        return None
    return 1 if winner == home else 0


def _correct(row: Dict[str, Any]) -> Optional[int]:
    value = row.get("correct")
    if value is True:
        return 1
    if value is False:
        return 0
    success = row.get("success")
    if success is True:
        return 1
    if success is False:
        return 0
    return None


def _modern_probability_semantics(row: Dict[str, Any]) -> bool:
    version = str(row.get("predictionSemanticsVersion") or "")
    meaning = str(row.get("winProbabilityMeaning") or "")
    return bool(
        row.get("probabilitySemanticsFixed") is True
        or version.startswith("MLB-OFFICIAL-PREDICTION-SEMANTICS-")
        or meaning == "estimated_probability_selected_team_wins_game"
    )


def eligibility(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    audit = _audit(row)
    lock_at = _lock_at(row)
    source_at = _source_at(row)
    min_lock = _parse_dt(DEFAULT_MIN_LOCK_AT_UTC)

    if row.get("status") != "GRADED":
        reasons.append("not_graded")
    if _home_won(row) is None or _correct(row) is None:
        reasons.append("missing_outcome_label")
    if not bool(audit.get("lockedFlag") or row.get("lockedPrediction") or row.get("officialPredictionStatus") == "OFFICIAL_LOCKED_PREDICTION"):
        reasons.append("not_immutable_locked_prediction")
    if not lock_at:
        reasons.append("missing_lock_timestamp")
    if not source_at:
        reasons.append("missing_source_pull_timestamp")
    if lock_at and source_at and source_at > lock_at:
        reasons.append("post_lock_source_leakage")
    if min_lock and lock_at and lock_at < min_lock:
        reasons.append("pre_clean_cohort_cutoff")
    if not _modern_probability_semantics(row):
        reasons.append("legacy_probability_semantics")
    if _f(row.get("teamWinProbabilityPct")) is None:
        reasons.append("missing_team_win_probability")
    if not _game_id(row):
        reasons.append("missing_game_identity")
    if audit and audit.get("preventsLateRows") is not True:
        reasons.append("late_row_protection_not_proven")

    return not reasons, sorted(set(reasons))


def freeze_feature_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    home = _sig(row, "home")
    away = _sig(row, "away")
    fundamentals = row.get("fundamentalsSnapshot") or {}
    home_prob = _prob(home)
    away_prob = _prob(away)
    if home_prob is None and away_prob is not None:
        home_prob = 1.0 - away_prob
    if away_prob is None and home_prob is not None:
        away_prob = 1.0 - home_prob
    home_prob = float(home_prob if home_prob is not None else 0.5)
    away_prob = float(away_prob if away_prob is not None else 1.0 - home_prob)

    home_price = _f(home.get("americanOdds"), _f(row.get("homeAmericanOdds")))
    away_price = _f(away.get("americanOdds"), _f(row.get("awayAmericanOdds")))
    selected_side = str(row.get("predictedSide") or "home")
    selected_prob = home_prob if selected_side == "home" else away_prob
    selected_signal = home if selected_side == "home" else away
    opponent_prob = away_prob if selected_side == "home" else home_prob

    features = {
        "homeMarketProb": home_prob,
        "awayMarketProb": away_prob,
        "marketGapHome": home_prob - away_prob,
        "homeDelta": float(_f(home.get("delta"), 0.0) or 0.0),
        "awayDelta": float(_f(away.get("delta"), 0.0) or 0.0),
        "deltaGapHome": float((_f(home.get("delta"), 0.0) or 0.0) - (_f(away.get("delta"), 0.0) or 0.0)),
        "homeBookDivergence": float(_f(home.get("bookDivergence"), 0.0) or 0.0),
        "awayBookDivergence": float(_f(away.get("bookDivergence"), 0.0) or 0.0),
        "homeReversalCount": float(_f(home.get("reversalCount"), 0.0) or 0.0),
        "awayReversalCount": float(_f(away.get("reversalCount"), 0.0) or 0.0),
        "homeRunLineMove": _run_line(home),
        "awayRunLineMove": _run_line(away),
        "homeBookAgreement": _tag(home, "BOOK_AGREEMENT"),
        "awayBookAgreement": _tag(away, "BOOK_AGREEMENT"),
        "homeSteam": _tag(home, "STEAM"),
        "awaySteam": _tag(away, "STEAM"),
        "homeResistance": _tag(home, "RESISTANCE"),
        "awayResistance": _tag(away, "RESISTANCE"),
        "homePriceImpliedProb": float(_american_implied(home_price) or home_prob),
        "awayPriceImpliedProb": float(_american_implied(away_price) or away_prob),
        "selectedMarketProb": selected_prob,
        "selectedMarketEdge": selected_prob - opponent_prob,
        "selectedScore": float(_f(row.get("score"), 0.0) or 0.0),
        "selectedReversalCount": float(_f(selected_signal.get("reversalCount"), 0.0) or 0.0),
        "selectedBookDivergence": float(_f(selected_signal.get("bookDivergence"), 0.0) or 0.0),
        "selectedDelta": float(_f(selected_signal.get("delta"), 0.0) or 0.0),
        "selectedFavorite": 1.0 if selected_prob >= 0.5 else 0.0,
        "selectedHome": 1.0 if selected_side == "home" else 0.0,
        "fundamentalsCompleteness": float(_f(fundamentals.get("completenessRatio"), 0.0) or 0.0),
        "homeStarterFip": _fundamental_value(fundamentals, "homeStarterFip"),
        "awayStarterFip": _fundamental_value(fundamentals, "awayStarterFip"),
        "homeStarterXfip": _fundamental_value(fundamentals, "homeStarterXfip"),
        "awayStarterXfip": _fundamental_value(fundamentals, "awayStarterXfip"),
        "homeWrcPlus": _fundamental_value(fundamentals, "homeWrcPlus"),
        "awayWrcPlus": _fundamental_value(fundamentals, "awayWrcPlus"),
        "homeBullpenFatigue": _fundamental_value(fundamentals, "homeBullpenFatigue"),
        "awayBullpenFatigue": _fundamental_value(fundamentals, "awayBullpenFatigue"),
        "homeLineupStrengthDelta": _fundamental_value(fundamentals, "homeLineupStrengthDelta"),
        "awayLineupStrengthDelta": _fundamental_value(fundamentals, "awayLineupStrengthDelta"),
        "parkFactorRuns": _fundamental_value(fundamentals, "parkFactorRuns", 1.0),
        "windOutMph": _fundamental_value(fundamentals, "windOutMph"),
        "homeRestDays": _fundamental_value(fundamentals, "homeRestDays"),
        "awayRestDays": _fundamental_value(fundamentals, "awayRestDays"),
    }
    payload = {
        "version": FEATURE_SNAPSHOT_VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourcePullAtUtc": _source_at(row).isoformat() if _source_at(row) else None,
        "lockAtUtc": _lock_at(row).isoformat() if _lock_at(row) else None,
        "gameId": _game_id(row),
        "slateDateEt": row.get("slateDateEt") or row.get("slate_date"),
        "commenceTime": row.get("commenceTime") or row.get("commence_time"),
        "homeTeam": row.get("homeTeam") or row.get("home_team"),
        "awayTeam": row.get("awayTeam") or row.get("away_team"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": selected_side,
        "features": features,
        "labels": {"homeWon": _home_won(row), "pickCorrect": _correct(row)},
        "immutableSource": "locked_prediction_row_pre_game_features",
        "derivedOnceFromImmutableLockedRow": True,
    }
    fingerprint_source = json.dumps({"gameId": payload["gameId"], "lockAtUtc": payload["lockAtUtc"], "features": features}, sort_keys=True, default=str)
    payload["fingerprint"] = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    return payload


def build(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    clean: List[Dict[str, Any]] = []
    quarantined: List[Dict[str, Any]] = []
    seen = set()
    for row in rows or []:
        ok, reasons = eligibility(row)
        key = (_game_id(row), str(_lock_at(row) or ""))
        if key in seen:
            continue
        seen.add(key)
        if not ok:
            quarantined.append({"gameId": _game_id(row), "slateDateEt": row.get("slateDateEt"), "reasons": reasons})
            continue
        snapshot = row.get("frozenFeatureVector") or freeze_feature_snapshot(row)
        clean.append({
            "gameId": _game_id(row),
            "slateDateEt": row.get("slateDateEt") or row.get("slate_date"),
            "commenceTime": row.get("commenceTime") or row.get("commence_time"),
            "homeTeam": row.get("homeTeam") or row.get("home_team"),
            "awayTeam": row.get("awayTeam") or row.get("away_team"),
            "predictedWinner": row.get("predictedWinner"),
            "predictedSide": row.get("predictedSide"),
            "winner": row.get("winner"),
            "correct": bool(_correct(row)),
            "lockedAmericanOdds": _f(row.get("lockedAmericanOdds"), _f(row.get("americanOdds"))),
            "featureSnapshot": snapshot,
        })
    clean.sort(key=lambda item: str(item.get("commenceTime") or ""))
    reason_counts: Dict[str, int] = {}
    for item in quarantined:
        for reason in item.get("reasons") or []:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "ok": True,
        "version": VERSION,
        "featureSnapshotVersion": FEATURE_SNAPSHOT_VERSION,
        "minimumLockAtUtc": DEFAULT_MIN_LOCK_AT_UTC,
        "inputRows": len(list(rows)) if isinstance(rows, list) else len(clean) + len(quarantined),
        "cleanRowCount": len(clean),
        "quarantinedRowCount": len(quarantined),
        "quarantineReasonCounts": reason_counts,
        "cleanRows": clean,
        "quarantinedRows": quarantined,
        "policy": "Only post-fix immutable locked rows with explicit modern probability semantics and pre-lock source timestamps may train ML.",
    }
