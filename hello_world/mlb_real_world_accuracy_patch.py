from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

VERSION = "MLB-REAL-WORLD-ACCURACY-v2-canonical-lock-authority-ledger"
LEDGER_PK = "MLB_CANONICAL_LOCK_ACCURACY#LEDGER#v2"
CANONICAL_LOCK_AUTHORITY_VERSION = "MLB-ROLLING-AUDIT-CANONICAL-LOCK-AUTHORITY-v1"
EXACT_PROVIDER_MATCH_METHOD = "exact_provider_game_id_and_teams"
VERIFIED_PROVIDER_ALIAS_MATCH_METHOD = (
    "verified_immutable_pull_official_game_pk_provider_alias_and_teams"
)
OFFICIAL_CREDIBLE_TARGET = int(os.environ.get("INQSI_MLB_OFFICIAL_CREDIBLE_SAMPLE", "500"))
PLAYABLE_CREDIBLE_TARGET = int(os.environ.get("INQSI_MLB_PLAYABLE_CREDIBLE_SAMPLE", "200"))
PROVISIONAL_TARGET = int(os.environ.get("INQSI_MLB_PROVISIONAL_SAMPLE", "300"))
DIAGNOSTIC_TARGET = int(os.environ.get("INQSI_MLB_DIAGNOSTIC_SAMPLE", "100"))


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
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _normalize_team(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _provider_game_id(row: Dict[str, Any]) -> str:
    for key in (
        "providerEventId",
        "provider_event_id",
        "providerGameId",
        "provider_game_id",
        "gameId",
        "game_id",
        "id",
    ):
        value = row.get(key)
        if value not in (None, ""):
            text = str(value).strip()
            return text[len("provider:"):] if text.startswith("provider:") else text
    return ""


def _provider_identity_authorized(row: Dict[str, Any], authority: Dict[str, Any]) -> bool:
    method = authority.get("providerIdentityMatchMethod") or authority.get("matchMethod")
    if method == EXACT_PROVIDER_MATCH_METHOD:
        return bool(
            authority.get("exactProviderIdentityMatched") is True
            and authority.get("matchMethod") == EXACT_PROVIDER_MATCH_METHOD
            and authority.get("verifiedProviderAliasCrosswalkMatched") is not True
        )
    if method != VERIFIED_PROVIDER_ALIAS_MATCH_METHOD:
        return False
    proof = authority.get("providerAliasCrosswalk") or {}
    fingerprints = proof.get("manifestFingerprints") or []
    official_pk = str(authority.get("officialGamePk") or "")
    provider_id = _provider_game_id(row)
    row_teams = (
        _normalize_team(row.get("awayTeam") or row.get("away_team")),
        _normalize_team(row.get("homeTeam") or row.get("home_team")),
    )
    return bool(
        official_pk
        and provider_id
        and authority.get("exactProviderIdentityMatched") is False
        and authority.get("verifiedProviderAliasCrosswalkMatched") is True
        and authority.get("matchMethod") == VERIFIED_PROVIDER_ALIAS_MATCH_METHOD
        and isinstance(proof, dict)
        and proof.get("immutableManifestValidated") is True
        and proof.get("uniqueBidirectionalCrosswalk") is True
        and str(proof.get("officialGamePk") or "") == official_pk
        and str(proof.get("providerEventId") or "") == provider_id
        and str(authority.get("providerGameId") or "") == provider_id
        and str(authority.get("canonicalLockedGameId") or "")
        == f"mlb_statsapi:{official_pk}"
        and isinstance(fingerprints, list)
        and bool(fingerprints)
        and all(str(value).strip() for value in fingerprints)
        and len({str(value) for value in fingerprints}) == len(fingerprints)
        and proof.get("evidenceCount") == len(fingerprints)
        and (
            str(proof.get("awayTeamNormalized") or ""),
            str(proof.get("homeTeamNormalized") or ""),
        )
        == row_teams
        and all(row_teams)
    )


def _tags(row: Dict[str, Any]) -> set[str]:
    return {str(value) for value in (row.get("tags") or [])}


def _is_locked(row: Dict[str, Any]) -> bool:
    tags = _tags(row)
    audit = row.get("lockedCardAudit") or {}
    lock = row.get("slatePredictionLock") or {}
    return bool(
        row.get("lockedPrediction") is True
        or row.get("officialPredictionStatus") == "OFFICIAL_LOCKED_PREDICTION"
        or (isinstance(audit, dict) and audit.get("lockedFlag") is True)
        or (isinstance(lock, dict) and lock.get("locked") is True)
        or "SLATE_LOCKED" in tags
        or "FINAL_LOCKED" in tags
        or "OFFICIAL_LOCKED_PREDICTION" in tags
    )


def _has_canonical_lock_authority(row: Dict[str, Any]) -> bool:
    authority = row.get("canonicalLockAuthority") or {}
    slate = str(row.get("slateDateEt") or "")
    return bool(
        isinstance(authority, dict)
        and authority.get("version") == CANONICAL_LOCK_AUTHORITY_VERSION
        and authority.get("verified") is True
        and authority.get("consistentRead") is True
        and authority.get("immutableLocked") is True
        and authority.get("stageAuthorityVerified") is True
        and authority.get("persistedStageAuthorityValidated") is True
        and authority.get(
            "officialAuditEligible",
            authority.get("exactLockVectorValidated"),
        ) is True
        and _provider_identity_authorized(row, authority)
        and authority.get("legacyOrDailyCardFallbackUsed") is False
        and authority.get("sourcePk") == f"GAME_WINNERS#mlb#{slate}"
        and str(authority.get("sourceSk") or "").startswith("LOCKED#GAME#")
        and authority.get("recordType") == "mlb_immutable_locked_single_game_prediction"
    )


def _is_official(row: Dict[str, Any]) -> bool:
    return bool(row.get("predictedWinner") and _has_canonical_lock_authority(row))


def _is_playable(row: Dict[str, Any]) -> bool:
    """Return wagering playability without using official-prediction status."""
    tags = _tags(row)
    recommendation = str(row.get("recommendationStatus") or "").upper()
    actionability = str(row.get("actionability") or "").upper()
    blocked = bool(
        "NOT_PLAYABLE" in tags
        or "ML_REJECTED" in tags
        or "NOT_PLAYABLE" in recommendation
        or "LOW_CONFIDENCE" in recommendation
        or "NOT_PLAYABLE" in actionability
        or "LOW_CONFIDENCE" in actionability
    )
    if blocked:
        return False
    return bool(
        row.get("playable") is True
        or row.get("playablePick") is True
        or row.get("actionablePick") is True
        or row.get("accuracyTargetEligible") is True
        or recommendation == "PLAYABLE_PREDICTION"
        or "PLAYABLE_PREDICTION" in tags
        or "ACTIONABLE_PICK" in tags
        or "ML_CONFIRMED" in tags
    )


def _selected_signal(row: Dict[str, Any]) -> Dict[str, Any]:
    signal = row.get("homeSignal") if str(row.get("predictedSide") or "").lower() == "home" else row.get("awaySignal")
    return signal if isinstance(signal, dict) else {}


def _market_probability(signal: Dict[str, Any]) -> Optional[float]:
    for key in ("marketConsensusProbability", "fairProbability", "probLatest", "winProbability"):
        value = _f(signal.get(key))
        if value is None:
            continue
        if value > 1.0:
            value /= 100.0
        if 0.0 < value < 1.0:
            return value
    return None


def _team_probability(row: Dict[str, Any]) -> Optional[float]:
    for key in ("teamWinProbabilityPct", "winProbabilityPct"):
        value = _f(row.get(key))
        if value is None:
            continue
        value = value / 100.0 if value > 1.0 else value
        if 0.0 < value < 1.0:
            return value
    return _market_probability(_selected_signal(row))


def _ml_reliability_pct(row: Dict[str, Any]) -> Optional[float]:
    value = _f(row.get("mlPickReliabilityPct"))
    if value is not None:
        return round(value * 100.0 if 0.0 < value <= 1.0 else value, 2)
    overlay = row.get("mlOverlay") or {}
    value = _f(overlay.get("probabilityPickCorrect")) if isinstance(overlay, dict) else None
    return round(value * 100.0, 2) if value is not None and 0.0 <= value <= 1.0 else None


def _american_odds(value: Any) -> Optional[float]:
    odds = _f(value)
    if odds is None or odds == 0 or -99.0 < odds < 99.0:
        return None
    return round(odds, 2)


def _selected_odds(row: Dict[str, Any]) -> Optional[float]:
    for key in ("lockedAmericanOdds", "selectedAmericanOdds", "americanOdds", "moneyline", "selectedMoneyline"):
        odds = _american_odds(row.get(key))
        if odds is not None:
            return odds
    signal = _selected_signal(row)
    for key in ("americanOdds", "moneyline", "selectedMoneyline", "price"):
        odds = _american_odds(signal.get(key))
        if odds is not None:
            return odds
    return None


def _unit_profit(correct: bool, american_odds: Optional[float]) -> Optional[float]:
    if american_odds is None:
        return None
    if not correct:
        return -1.0
    return round(american_odds / 100.0 if american_odds > 0 else 100.0 / abs(american_odds), 6)


def _market_baseline(row: Dict[str, Any]) -> Dict[str, Any]:
    home = row.get("homeSignal") or {}
    away = row.get("awaySignal") or {}
    hp, ap = _market_probability(home), _market_probability(away)
    if hp is None or ap is None or abs(hp - ap) < 1e-9:
        return {"available": False}
    side = "home" if hp > ap else "away"
    signal = home if side == "home" else away
    team = row.get("homeTeam") if side == "home" else row.get("awayTeam")
    probability = hp if side == "home" else ap
    odds = _american_odds(signal.get("americanOdds"))
    correct = _normalize_team(team) == _normalize_team(row.get("winner"))
    return {
        "available": True,
        "winner": team,
        "side": side,
        "probability": probability,
        "probabilityPct": round(probability * 100.0, 2),
        "americanOdds": odds,
        "correct": correct,
        "unitProfit": _unit_profit(correct, odds),
    }


def _corrected_pipeline_state(row: Dict[str, Any]) -> Dict[str, Any]:
    integrity = row.get("winnerOptimizerProtection") or {}
    directional = row.get("directionalScoreV1") or {}
    overlay = row.get("mlOverlay") or {}
    signal_policy = row.get("signalPolicyV13") or {}
    final_store = bool(row.get("finalGuardedStored") or row.get("finalGuardedStoreRequested"))
    playable = _is_playable(row)
    explicit_non_playable = bool(row.get("predictedWinner") and not playable and (
        final_store
        or integrity.get("applied")
        or directional.get("applied")
        or overlay.get("applied")
        or row.get("playabilityStatus") == "NOT_PLAYABLE"
        or "NOT_PLAYABLE" in _tags(row)
    ))
    depth = sum(int(bool(value)) for value in (
        integrity.get("applied"), directional.get("applied"), overlay.get("applied"),
        signal_policy.get("applied"), final_store,
    ))
    return {
        "integrityApplied": bool(integrity.get("applied")),
        "directionalApplied": bool(directional.get("applied")),
        "mlOverlayApplied": bool(overlay.get("applied")),
        "signalPolicyApplied": bool(signal_policy.get("applied")),
        "finalGuardedStored": final_store,
        "officialPrediction": _is_official(row),
        "playable": playable,
        "explicitNonPlayable": explicit_non_playable,
        "pipelineDepth": depth,
        "predictionVisibility": row.get("predictionVisibility"),
        "recommendationStatus": row.get("recommendationStatus"),
        "classificationVersion": VERSION,
    }


def _normalize_audit_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row or {})
    if out.get("status") != "GRADED":
        return out
    official = _is_official(out)
    playable = _is_playable(out)
    probability = _team_probability(out)
    odds = _selected_odds(out)
    correct = bool(out.get("correct"))
    baseline = _market_baseline(out)
    out.update({
        "officialPrediction": official,
        "officialPick": official,
        "playable": playable,
        "playablePick": playable,
        "actionablePick": playable,
        "accuracyTargetEligible": playable,
        "playabilityStatus": "PLAYABLE" if playable else "NOT_PLAYABLE",
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION" if official else "NON_OFFICIAL_OR_UNLOCKED",
        "teamWinProbabilityPct": round(probability * 100.0, 2) if probability is not None else None,
        "mlPickReliabilityPct": _ml_reliability_pct(out),
        "lockedAmericanOdds": odds,
        "unitProfit": _unit_profit(correct, odds),
        "marketBaselineAvailable": bool(baseline.get("available")),
        "marketBaselineWinner": baseline.get("winner"),
        "marketBaselineSide": baseline.get("side"),
        "marketBaselineProbabilityPct": baseline.get("probabilityPct"),
        "marketBaselineAmericanOdds": baseline.get("americanOdds"),
        "marketBaselineCorrect": baseline.get("correct"),
        "marketBaselineUnitProfit": baseline.get("unitProfit"),
        "auditClassificationVersion": VERSION,
    })
    return out


def _wilson_interval(correct: int, count: int, z: float = 1.959963984540054) -> Dict[str, Optional[float]]:
    if count <= 0:
        return {"lowPct": None, "highPct": None}
    p = correct / count
    denominator = 1.0 + z * z / count
    center = (p + z * z / (2.0 * count)) / denominator
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * count)) / count) / denominator
    return {"lowPct": round(max(0.0, center - margin) * 100.0, 2), "highPct": round(min(1.0, center + margin) * 100.0, 2)}


def _accuracy_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    count = len(rows)
    correct = sum(1 for row in rows if row.get("correct") is True)
    interval = _wilson_interval(correct, count)
    return {
        "count": count,
        "correct": correct,
        "wrong": count - correct,
        "accuracyPct": round(correct / count * 100.0, 2) if count else None,
        "confidenceInterval95Pct": interval,
    }


def _proper_scores(rows: List[Dict[str, Any]], probability_key: str, outcome_key: str) -> Dict[str, Any]:
    brier_values: List[float] = []
    log_values: List[float] = []
    for row in rows:
        probability = _f(row.get(probability_key))
        outcome = row.get(outcome_key)
        if probability is None or outcome not in {True, False}:
            continue
        probability = probability / 100.0 if probability > 1.0 else probability
        if not 0.0 < probability < 1.0:
            continue
        y = 1.0 if outcome else 0.0
        brier_values.append((probability - y) ** 2)
        clipped = min(1.0 - 1e-9, max(1e-9, probability))
        log_values.append(-(y * math.log(clipped) + (1.0 - y) * math.log(1.0 - clipped)))
    return {
        "pricedProbabilityCount": len(brier_values),
        "brierScore": round(sum(brier_values) / len(brier_values), 6) if brier_values else None,
        "logLoss": round(sum(log_values) / len(log_values), 6) if log_values else None,
    }


def _roi_stats(rows: List[Dict[str, Any]], profit_key: str, odds_key: str) -> Dict[str, Any]:
    profits = [_f(row.get(profit_key)) for row in rows]
    profits = [value for value in profits if value is not None]
    odds = [_f(row.get(odds_key)) for row in rows]
    odds = [value for value in odds if value is not None]
    total = sum(profits) if profits else 0.0
    return {
        "pricedPickCount": len(profits),
        "unpricedPickCount": len(rows) - len(profits),
        "flatUnitProfit": round(total, 4) if profits else None,
        "flatUnitRoiPct": round(total / len(profits) * 100.0, 2) if profits else None,
        "averageAmericanOdds": round(sum(odds) / len(odds), 2) if odds else None,
    }


def _calibration(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets = [(50, 55), (55, 60), (60, 65), (65, 70), (70, 101)]
    output = []
    for low, high in buckets:
        selected = []
        for row in rows:
            p = _f(row.get("teamWinProbabilityPct"))
            if p is not None and low <= p < high:
                selected.append((p, bool(row.get("correct"))))
        if not selected:
            continue
        average = sum(value for value, _ in selected) / len(selected)
        actual = sum(1 for _, correct in selected if correct) / len(selected) * 100.0
        output.append({
            "bucket": f"{low}-{high - 0.01:.2f}%" if high <= 100 else "70%+",
            "count": len(selected),
            "averagePredictedProbabilityPct": round(average, 2),
            "actualAccuracyPct": round(actual, 2),
            "calibrationGapPct": round(actual - average, 2),
        })
    return output


def _window_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    graded = [row for row in rows if row.get("status") == "GRADED"]
    official = [row for row in graded if row.get("officialPrediction") is True]
    playable = [row for row in official if row.get("playable") is True]
    baseline = [row for row in official if row.get("marketBaselineAvailable") is True]
    official_accuracy = _accuracy_stats(official)
    playable_accuracy = _accuracy_stats(playable)
    baseline_accuracy_rows = [dict(row, correct=row.get("marketBaselineCorrect")) for row in baseline]
    baseline_accuracy = _accuracy_stats(baseline_accuracy_rows)
    model_probability = _proper_scores(official, "teamWinProbabilityPct", "correct")
    market_probability = _proper_scores(baseline, "marketBaselineProbabilityPct", "marketBaselineCorrect")
    model_brier = model_probability.get("brierScore")
    market_brier = market_probability.get("brierScore")
    model_on_baseline = _accuracy_stats(baseline)
    return {
        "gradedCount": len(graded),
        "officialPredictions": {
            **official_accuracy,
            "coverageOfGradedPct": round(len(official) / len(graded) * 100.0, 2) if graded else None,
            "probabilityScoring": model_probability,
            "calibration": _calibration(official),
            "roi": _roi_stats(official, "unitProfit", "lockedAmericanOdds"),
        },
        "playableRecommendations": {
            **playable_accuracy,
            "coverageOfOfficialPct": round(len(playable) / len(official) * 100.0, 2) if official else None,
            "probabilityScoring": _proper_scores(playable, "teamWinProbabilityPct", "correct"),
            "calibration": _calibration(playable),
            "roi": _roi_stats(playable, "unitProfit", "lockedAmericanOdds"),
        },
        "marketFavoriteBaseline": {
            **baseline_accuracy,
            "coverageOfOfficialPct": round(len(baseline) / len(official) * 100.0, 2) if official else None,
            "probabilityScoring": market_probability,
            "roi": _roi_stats(baseline, "marketBaselineUnitProfit", "marketBaselineAmericanOdds"),
        },
        "comparison": {
            "sameGameModelAccuracyPct": model_on_baseline.get("accuracyPct"),
            "marketFavoriteAccuracyPct": baseline_accuracy.get("accuracyPct"),
            "modelAccuracyLiftVsMarketPct": round((model_on_baseline.get("accuracyPct") or 0.0) - (baseline_accuracy.get("accuracyPct") or 0.0), 2) if baseline else None,
            "brierSkillVsMarketPct": round((market_brier - model_brier) / market_brier * 100.0, 2) if model_brier is not None and market_brier not in {None, 0} else None,
            "lowerBrierIsBetter": True,
            "lowerLogLossIsBetter": True,
        },
    }


def _dedupe(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row.get("status") != "GRADED" or not _has_canonical_lock_authority(row):
            continue
        key = "|".join([
            str(row.get("id") or ""), str(row.get("gameKeyBase") or ""),
            str(row.get("commenceTime") or ""), str(row.get("predictedWinner") or ""),
        ])
        if key not in seen:
            seen[key] = row
    return list(seen.values())


def _rows_since(rows: List[Dict[str, Any]], days: Optional[int]) -> List[Dict[str, Any]]:
    deduped = _dedupe(rows)
    if days is None:
        return deduped
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return [row for row in deduped if (_parse_dt(row.get("commenceTime")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]


def _evidence_progress(season_metrics: Dict[str, Any]) -> Dict[str, Any]:
    official = int((season_metrics.get("officialPredictions") or {}).get("count") or 0)
    playable = int((season_metrics.get("playableRecommendations") or {}).get("count") or 0)
    if official >= OFFICIAL_CREDIBLE_TARGET and playable >= PLAYABLE_CREDIBLE_TARGET:
        stage = "CREDIBLE_REAL_WORLD_RECORD"
    elif official >= PROVISIONAL_TARGET:
        stage = "PROVISIONAL_ACCURACY"
    elif official >= DIAGNOSTIC_TARGET:
        stage = "EARLY_DIAGNOSTIC"
    else:
        stage = "ACCUMULATING_EVIDENCE"
    return {
        "stage": stage,
        "officialSettledPredictions": official,
        "playableSettledRecommendations": playable,
        "targets": {
            "earlyDiagnosticOfficial": DIAGNOSTIC_TARGET,
            "provisionalOfficial": PROVISIONAL_TARGET,
            "credibleOfficial": OFFICIAL_CREDIBLE_TARGET,
            "crediblePlayable": PLAYABLE_CREDIBLE_TARGET,
        },
        "remaining": {
            "toEarlyDiagnosticOfficial": max(0, DIAGNOSTIC_TARGET - official),
            "toProvisionalOfficial": max(0, PROVISIONAL_TARGET - official),
            "toCredibleOfficial": max(0, OFFICIAL_CREDIBLE_TARGET - official),
            "toCrediblePlayable": max(0, PLAYABLE_CREDIBLE_TARGET - playable),
        },
        "automaticCollectionEnabled": True,
        "weightsMayChangeAutomatically": False,
    }


def _ledger_row(row: Dict[str, Any]) -> Dict[str, Any]:
    fields = (
        "id", "gameKeyBase", "slateDateEt", "commenceTime", "homeTeam", "awayTeam", "matchup",
        "homeScore", "awayScore", "winner", "predictedWinner", "predictedSide", "correct",
        "officialPrediction", "officialPick", "playable", "playablePick", "playabilityStatus",
        "teamWinProbabilityPct", "mlPickReliabilityPct", "lockedAmericanOdds", "unitProfit",
        "marketBaselineWinner", "marketBaselineSide", "marketBaselineProbabilityPct",
        "marketBaselineAmericanOdds", "marketBaselineCorrect", "marketBaselineUnitProfit",
        "confidenceTier", "score", "tags", "modelVersion", "engine", "finalPipelineVersion",
        "predictionSemanticsVersion", "lockedCardAudit", "auditClassificationVersion",
        "priceBook", "priceSource", "homeSignal", "awaySignal", "mlOverlay",
        "status", "canonicalLockAuthority",
    )
    return {key: row.get(key) for key in fields if key in row}


def _ledger_sk(row: Dict[str, Any]) -> str:
    game = row.get("id") or row.get("gameKeyBase") or row.get("matchup") or "unknown"
    return f"GAME#{row.get('slateDateEt') or 'unknown'}#{game}"


def _store_ledger(module: Any, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    table = getattr(getattr(module, "history", None), "PULLS", None)
    if table is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured", "inserted": 0, "existing": 0, "errors": []}
    inserted = existing = 0
    errors: List[str] = []
    for row in _dedupe(rows):
        if row.get("officialPrediction") is not True or not _has_canonical_lock_authority(row):
            continue
        data = _ledger_row(row)
        item = module.history.ddb_safe({
            "PK": LEDGER_PK,
            "SK": _ledger_sk(row),
            "record_type": "mlb_real_world_accuracy_ledger_row",
            "sport": "mlb",
            "slate_date": row.get("slateDateEt"),
            "game_id": row.get("id"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        })
        try:
            table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
            inserted += 1
        except Exception as exc:
            text = str(exc)
            if "ConditionalCheckFailed" in text:
                existing += 1
            else:
                errors.append(text)
    return {"ok": not errors, "inserted": inserted, "existing": existing, "errors": errors[:20], "ledgerPk": LEDGER_PK}


def _query_ledger(module: Any) -> List[Dict[str, Any]]:
    table = getattr(getattr(module, "history", None), "PULLS", None)
    if table is None:
        return []
    rows: List[Dict[str, Any]] = []
    start_key = None
    while True:
        args = {"KeyConditionExpression": module.history.Key("PK").eq(LEDGER_PK)}
        if start_key:
            args["ExclusiveStartKey"] = start_key
        try:
            response = table.query(**args)
        except Exception:
            break
        for item in response.get("Items") or []:
            data = item.get("data") or item
            if isinstance(data, dict) and _has_canonical_lock_authority(data):
                rows.append(_normalize_audit_row(data))
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            break
    return _dedupe(rows)


def _enhance_summary(base: Dict[str, Any], current: Dict[str, Any], season: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base or {})
    official = current.get("officialPredictions") or {}
    playable = current.get("playableRecommendations") or {}
    baseline = current.get("marketFavoriteBaseline") or {}
    out.update({
        "officialPredictionCount": official.get("count"),
        "officialCorrect": official.get("correct"),
        "officialWrong": official.get("wrong"),
        "rolling24hOfficialAccuracyPct": official.get("accuracyPct"),
        "officialAccuracyConfidenceInterval95Pct": official.get("confidenceInterval95Pct"),
        "playablePredictionCount": playable.get("count"),
        "playableCorrect": playable.get("correct"),
        "playableWrong": playable.get("wrong"),
        "rolling24hPlayableAccuracyPct": playable.get("accuracyPct"),
        "playableCoveragePct": playable.get("coverageOfOfficialPct"),
        "actionablePickCount": playable.get("count"),
        "actionableCorrect": playable.get("correct"),
        "actionableWrong": playable.get("wrong"),
        "rolling24hActionableAccuracyPct": playable.get("accuracyPct"),
        "marketFavoriteBaselineAccuracyPct": baseline.get("accuracyPct"),
        "modelAccuracyLiftVsMarketPct": (current.get("comparison") or {}).get("modelAccuracyLiftVsMarketPct"),
        "officialBrierScore": ((official.get("probabilityScoring") or {}).get("brierScore")),
        "officialLogLoss": ((official.get("probabilityScoring") or {}).get("logLoss")),
        "officialFlatUnitRoiPct": ((official.get("roi") or {}).get("flatUnitRoiPct")),
        "playableFlatUnitRoiPct": ((playable.get("roi") or {}).get("flatUnitRoiPct")),
        "seasonOfficialPredictionCount": (season.get("officialPredictions") or {}).get("count"),
        "seasonPlayablePredictionCount": (season.get("playableRecommendations") or {}).get("count"),
        "accuracyClassificationVersion": VERSION,
    })
    return out


def enhance_report(module: Any, report: Dict[str, Any], historical_rows: Optional[List[Dict[str, Any]]] = None, ledger_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    out = dict(report or {})
    current_rows = [_normalize_audit_row(row) for row in (out.get("rows") or [])]
    historical = [_normalize_audit_row(row) for row in (historical_rows or [])]
    ledger = [_normalize_audit_row(row) for row in (ledger_rows or [])]
    all_rows = _dedupe([*ledger, *historical, *current_rows])
    windows = {
        "current24h": _window_metrics(_rows_since(current_rows, 1)),
        "sevenDay": _window_metrics(_rows_since(all_rows, 7)),
        "thirtyDay": _window_metrics(_rows_since(all_rows, 30)),
        "season": _window_metrics(_rows_since(all_rows, None)),
    }
    out["rows"] = current_rows
    out["realWorldAccuracy"] = {
        "applied": True,
        "version": VERSION,
        "classification": {
            "officialPrediction": "winner selected in a canonical immutable LOCKED#GAME row joined by exact provider identity or a verified immutable official-game-PK/provider alias crosswalk",
            "playableRecommendation": "higher-confidence wagering recommendation; official status is excluded",
        },
        "windows": windows,
        "evidenceProgress": _evidence_progress(windows["season"]),
        "metrics": [
            "official_accuracy", "playable_accuracy", "coverage", "wilson_95_confidence_interval",
            "brier_score", "log_loss", "probability_calibration", "flat_unit_roi",
            "market_favorite_baseline", "model_lift_vs_market", "brier_skill_vs_market",
        ],
        "immutableLedgerPk": LEDGER_PK,
        "policy": "Only completed games with canonical immutable LOCKED#GAME authority and exact or verified immutable provider identity are graded. Daily-card, legacy, fuzzy-match, and displayed-forecast rows are excluded.",
    }
    out["summary"] = _enhance_summary(out.get("summary") or {}, windows["current24h"], windows["season"])
    out["policy"] = (
        "Audit every completed MLB game using the immutable locked prediction. Report official-card accuracy "
        "and playable-recommendation accuracy separately, with probability calibration, flat-unit ROI, and "
        "market-favorite baselines. Accumulate a permanent deduplicated settled-game ledger; never grade live games."
    )
    return out


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_REAL_WORLD_ACCURACY_APPLIED", False):
        return module

    try:
        import mlb_locked_card_audit_v1 as locked_audit
        if not getattr(locked_audit, "_INQSI_REAL_WORLD_SEMANTICS_PATCHED", False):
            original_copy = locked_audit._copy_audit_fields

            def patched_copy(pred: Dict[str, Any]) -> Dict[str, Any]:
                out = dict(original_copy(pred))
                for key in (
                    "modelVersion", "engine", "teamWinProbabilityPct", "mlPickReliabilityPct",
                    "americanOdds", "priceBook", "priceSource", "fairProbabilityPct",
                    "playable", "playablePick", "playabilityStatus", "predictionSemanticsVersion",
                ):
                    if key in pred:
                        out[key] = pred.get(key)
                out["officialPrediction"] = _is_official(pred)
                out["officialPick"] = out["officialPrediction"]
                out["playable"] = _is_playable(pred)
                out["playablePick"] = out["playable"]
                return out

            locked_audit._pipeline_state = _corrected_pipeline_state
            locked_audit._copy_audit_fields = patched_copy
            locked_audit._INQSI_REAL_WORLD_SEMANTICS_PATCHED = True
    except Exception:
        pass

    try:
        import mlb_audit_actionability_patch as actionability_patch
        actionability_patch._is_actionable = _is_playable
    except Exception:
        pass

    original_audit_rows = module.audit_rows
    original_summarize = module.summarize
    original_build = module.build

    def patched_audit_rows(finals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [_normalize_audit_row(row) for row in original_audit_rows(finals)]

    def patched_summarize(rows: List[Dict[str, Any]], historical_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        normalized = [_normalize_audit_row(row) for row in rows]
        historical = [_normalize_audit_row(row) for row in (historical_rows or [])]
        base = original_summarize(normalized, historical_rows=historical)
        current = _window_metrics(_rows_since(normalized, 1))
        season = _window_metrics(_rows_since(_dedupe([*historical, *normalized]), None))
        return _enhance_summary(base, current, season)

    def patched_build(*args, **kwargs):
        store = bool(kwargs.get("store", True))
        write_file = bool(kwargs.get("write_file", True))
        inner_kwargs = dict(kwargs)
        inner_kwargs["store"] = False
        inner_kwargs["write_file"] = False
        report = original_build(*args, **inner_kwargs)
        try:
            historical = module.historical_audit_rows()
        except Exception:
            historical = []
        normalized_historical = [_normalize_audit_row(row) for row in historical]
        normalized_current = [_normalize_audit_row(row) for row in (report.get("rows") or [])]
        ledger_store = _store_ledger(module, [*normalized_historical, *normalized_current]) if store else {"ok": True, "skipped": True}
        ledger_rows = _query_ledger(module)
        report = enhance_report(module, report, historical_rows=normalized_historical, ledger_rows=ledger_rows)
        report["accuracyLedger"] = {
            **ledger_store,
            "queriedRowCount": len(ledger_rows),
            "immutable": True,
            "dedupeKey": "slate_date + provider_game_id_or_matchup",
            "version": VERSION,
        }
        if store:
            try:
                report["stored"] = module.store_report(report)
            except Exception as exc:
                report["storeError"] = str(exc)
        if write_file:
            os.makedirs(os.path.dirname(module.REPORT_PATH) or ".", exist_ok=True)
            with open(module.REPORT_PATH, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, default=str)
                handle.write("\n")
        return report

    module.audit_rows = patched_audit_rows
    module.summarize = patched_summarize
    module.build = patched_build
    module._INQSI_MLB_REAL_WORLD_ACCURACY_APPLIED = True
    return module
