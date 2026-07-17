from __future__ import annotations

import math
import os
import hashlib
import json
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import inqsi_pull_history as history
from mlb_slate_coverage_patch import game_identity as _canonical_game_identity
import mlb_temporal_features_v1 as temporal_features

PAYLOAD_FINGERPRINT_VERSION = history.CANONICAL_PAYLOAD_FINGERPRINT_VERSION

SLATE_TZ = ZoneInfo("America/New_York")
ENGINE = "MLB-SINGLE-GAME-ML-PROMOTION-v2.1"
MODEL_VERSION = "INQSI-MLB-SINGLE-GAME-ML-v2.1-aws-sam-production"
PREGAME_SNAPSHOT_RECORD_TYPE = "mlb_immutable_prelock_prediction_snapshot"
PREGAME_SNAPSHOT_VERSION = "MLB-PREGAME-PREDICTION-SNAPSHOT-v2-post-write-ack"
PREGAME_PERSISTENCE_PROOF_TYPE = "DDB_LIVE_PREDICTION_PUT_SUCCESS_ACK-v1"

PRIMARY_BOOK = os.environ.get("ODDS_PRIMARY_BOOK", "fanduel").lower().strip()
BOOK_PRIORITY = [
    PRIMARY_BOOK,
    "draftkings",
    "betmgm",
    "caesars",
    "fanatics",
    "betrivers",
    "bovada",
    "lowvig",
]
PROMOTION_THRESHOLD = float(os.environ.get("MLB_PROMOTION_EDGE_THRESHOLD", "0.0015"))
FALLBACK_THRESHOLD = float(os.environ.get("MLB_PROMOTION_FALLBACK_EDGE_THRESHOLD", "0.0005"))
MIN_EV_FOR_PROMOTION = float(os.environ.get("MLB_MIN_EV_FOR_PROMOTION", "0.0"))
MIN_MODEL_PROB = float(os.environ.get("MLB_MIN_MODEL_PROBABILITY", "0.35"))
MAX_PROMOTED_DOG_PRICE = float(os.environ.get("MLB_MAX_PROMOTED_DOG_PRICE", "180"))
HEAVY_FAVORITE_PRICE = float(os.environ.get("MLB_HEAVY_FAVORITE_PRICE", "-185"))
MAX_BOOK_DIVERGENCE = float(os.environ.get("MLB_MAX_BOOK_DIVERGENCE", "0.075"))


def _today_et() -> str:
    return datetime.now(SLATE_TZ).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _game_day(game: Dict[str, Any]) -> Optional[str]:
    dt = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return dt.astimezone(SLATE_TZ).date().isoformat() if dt else None


def _game_identity(game: Dict[str, Any]) -> str:
    identity = _canonical_game_identity(game)
    # Preserve the historical raw provider id in engine rows while using the
    # exact shared fallback identity for games without a provider id.
    return identity.replace("provider:", "", 1) if identity.startswith("provider:") else identity


def _same_game(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    aid = a.get("game_id") or a.get("id")
    bid = b.get("game_id") or b.get("id")
    if aid and bid and str(aid) == str(bid):
        return True
    return _game_identity(a) == _game_identity(b)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _american_prob(value: Any) -> Optional[float]:
    try:
        a = float(value)
    except Exception:
        return None
    if a == 0:
        return None
    return abs(a) / (abs(a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def _american_decimal(value: Any) -> Optional[float]:
    try:
        a = float(value)
    except Exception:
        return None
    return 1.0 + (100.0 / abs(a)) if a < 0 else 1.0 + (a / 100.0)


def _devig_pair(home_price: Any, away_price: Any) -> Optional[Tuple[float, float]]:
    hp, ap = _american_prob(home_price), _american_prob(away_price)
    if hp is None or ap is None or hp + ap <= 0:
        return None
    return hp / (hp + ap), ap / (hp + ap)


def _books(game: Dict[str, Any]) -> Dict[str, Any]:
    return game.get("books") or {}


def _price_from_book(game: Dict[str, Any], side: str) -> Tuple[Optional[float], Optional[str]]:
    books = _books(game)
    ordered = []
    for book in BOOK_PRIORITY:
        if book and book in books and book not in ordered:
            ordered.append(book)
    ordered.extend([book for book in books if book not in ordered])
    for book in ordered:
        ml = (books.get(book) or {}).get("ml") or (books.get(book) or {}).get("moneyline") or {}
        if ml.get(side) is not None:
            return _safe_float(ml.get(side)), book
    return None, None


def _market_fair(game: Dict[str, Any]) -> Dict[str, Any]:
    home_vals: List[float] = []
    away_vals: List[float] = []
    per_book: Dict[str, Any] = {}
    for book, payload in _books(game).items():
        ml = (payload or {}).get("ml") or (payload or {}).get("moneyline") or {}
        pair = _devig_pair(ml.get("home"), ml.get("away"))
        if not pair:
            continue
        hp, ap = pair
        home_vals.append(hp)
        away_vals.append(ap)
        per_book[str(book)] = {"home": hp, "away": ap}
    if not home_vals:
        return {"home": 0.5, "away": 0.5, "book_count": 0, "book_divergence": 1.0, "book_probs": {}}
    return {
        "home": sum(home_vals) / len(home_vals),
        "away": sum(away_vals) / len(away_vals),
        "book_count": len(home_vals),
        "book_divergence": max(home_vals) - min(home_vals) if len(home_vals) > 1 else 0.0,
        "book_probs": per_book,
    }


def _series_for_game(pulls: List[Dict[str, Any]], latest_game: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for pull in pulls:
        pulled_at = pull.get("pulled_at")
        for game in pull.get("games") or []:
            if _same_game(game, latest_game):
                fair = _market_fair(game)
                if fair.get("book_count", 0) > 0:
                    rows.append({
                        "pull_id": pull.get("pull_id"),
                        "pulled_at": pulled_at,
                        "game": game,
                        "fair": fair,
                    })
                break
    return rows


def _reversals(values: List[float]) -> int:
    signs: List[int] = []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        signs.append(1 if diff > 0.0005 else -1 if diff < -0.0005 else 0)
    return sum(1 for i in range(1, len(signs)) if signs[i] and signs[i - 1] and signs[i] != signs[i - 1])


def _market_side(price: Optional[float]) -> str:
    if price is None:
        return "unknown"
    if price <= -120:
        return "favorite"
    if price >= 105:
        return "underdog"
    return "pickem"


def _confidence_tier(promoted: bool, score: float, edge: float, ev: float) -> str:
    if not promoted:
        return "Watchlist" if edge > 0 else "No Play"
    if score >= 68 and edge >= 0.012 and ev >= 0.025:
        return "Premium"
    if score >= 60 and edge >= 0.006:
        return "Solid"
    return "Promoted"


def _side_score(series: List[Dict[str, Any]], side: str) -> Dict[str, Any]:
    latest = series[-1]
    game = latest["game"]
    fair_vals = [float(row["fair"].get(side, 0.5)) for row in series]
    fair_latest = fair_vals[-1] if fair_vals else 0.5
    fair_start = fair_vals[0] if fair_vals else fair_latest
    delta = fair_latest - fair_start
    reversals = _reversals(fair_vals)
    latest_fair = latest["fair"]
    book_count = int(latest_fair.get("book_count") or 0)
    divergence = float(latest_fair.get("book_divergence") or 0.0)
    price, price_book = _price_from_book(game, side)
    decimal_odds = _american_decimal(price)
    market_side = _market_side(price)
    temporal = temporal_features.summarize_side(series, side, cutoff_at=latest.get("pulled_at"))

    movement_adj = max(-0.03, min(0.03, delta * 0.70))
    if market_side == "underdog" and delta > 0:
        movement_adj += min(0.008, delta * 0.35)
    if market_side == "favorite" and price is not None and price <= HEAVY_FAVORITE_PRICE:
        movement_adj -= 0.004
    if divergence > 0.035:
        movement_adj -= min(0.012, (divergence - 0.035) * 0.5)
    if reversals:
        movement_adj -= min(0.018, reversals * 0.004)
    if len(series) < 4:
        movement_adj *= 0.55

    model_prob = max(0.05, min(0.95, fair_latest + movement_adj))
    edge = model_prob - fair_latest
    ev = (model_prob * decimal_odds - 1.0) if decimal_odds else -1.0

    tags: List[str] = []
    blocked: List[str] = []
    if len(series) < 4:
        tags.append("LOW_PULL_DEPTH")
    if delta >= 0.006:
        tags.append("POSITIVE_MOVE")
    if delta <= -0.006:
        tags.append("NEGATIVE_MOVE")
    if divergence <= 0.02:
        tags.append("BOOK_AGREEMENT")
    if divergence > 0.04:
        tags.append("BOOK_DIVERGENCE")
    if reversals:
        tags.append("REVERSAL")
    if market_side == "underdog":
        tags.append("UNDERDOG")
    elif market_side == "favorite":
        tags.append("FAVORITE")
    else:
        tags.append("PICKEM")

    if price is None:
        blocked.append("NO_BETTABLE_PRICE")
    if book_count < 1:
        blocked.append("NO_BOOK_CONSENSUS")
    if model_prob < MIN_MODEL_PROB:
        blocked.append("MODEL_PROB_TOO_LOW")
    if market_side == "underdog" and price is not None and price > MAX_PROMOTED_DOG_PRICE:
        blocked.append("LONG_DOG_PRICE_GUARD")
    if market_side == "favorite" and price is not None and price <= HEAVY_FAVORITE_PRICE and edge < 0.006:
        blocked.append("HEAVY_FAVORITE_PRICE_GUARD")
    if divergence > MAX_BOOK_DIVERGENCE:
        blocked.append("BOOK_DIVERGENCE_GUARD")
    if ev < MIN_EV_FOR_PROMOTION:
        blocked.append("NEGATIVE_EV_GUARD")

    promoted = not blocked and edge >= PROMOTION_THRESHOLD and ev >= MIN_EV_FOR_PROMOTION
    score = 50.0 + edge * 900.0 + ev * 220.0 + delta * 260.0 - divergence * 110.0 - reversals * 2.5
    if market_side in {"underdog", "pickem"} and edge > 0 and ev > 0:
        score += 4.0
    if market_side == "favorite" and price is not None and price <= HEAVY_FAVORITE_PRICE:
        score -= 4.0
    score = round(max(0.0, min(100.0, score)), 2)

    return {
        "side": side,
        "team": game.get("home_team") if side == "home" else game.get("away_team"),
        "score": score,
        "fairProbability": round(fair_latest, 6),
        "fairProbabilityPct": round(fair_latest * 100.0, 2),
        "winProbability": round(model_prob, 6),
        "winProbabilityPct": round(model_prob * 100.0, 2),
        "edgeVsBook": round(edge, 6),
        "edgeVsBookPct": round(edge * 100.0, 2),
        "expectedValue": round(ev, 6),
        "expectedValuePct": round(ev * 100.0, 2),
        "americanOdds": round(price, 2) if price is not None else None,
        "priceBook": price_book,
        "priceSource": "real_book" if price_book else "missing",
        "marketSide": market_side,
        "probStart": round(fair_start, 6),
        "probLatest": round(fair_latest, 6),
        "delta": round(delta, 6),
        "bookCount": book_count,
        "bookDivergence": round(divergence, 6),
        "reversalCount": reversals,
        "temporalFeatures": temporal,
        "temporalFeatureVersion": temporal_features.VERSION,
        "tags": sorted(set(tags)),
        "blockedReasons": blocked,
        "promoted": promoted,
        "promotionStatus": "PROMOTED" if promoted else "NO_PLAY",
    }


def _prediction_for_game(pulls: List[Dict[str, Any]], latest_game: Dict[str, Any], slate_date: str) -> Optional[Dict[str, Any]]:
    series = _series_for_game(pulls, latest_game)
    if not series:
        return None
    home = _side_score(series, "home")
    away = _side_score(series, "away")

    def sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float]:
        return (
            1.0 if row.get("promoted") else 0.0,
            float(row.get("expectedValue") or -9.0),
            float(row.get("edgeVsBook") or -9.0),
            float(row.get("score") or 0.0),
        )

    pick = home if sort_key(home) >= sort_key(away) else away
    opponent = away if pick["side"] == "home" else home
    source = series[-1]
    return {
        "ok": True,
        "sport": "mlb",
        "modelVersion": MODEL_VERSION,
        "engine": ENGINE,
        "slate_date": slate_date,
        "gameId": latest_game.get("game_id") or latest_game.get("id") or _game_identity(latest_game),
        "gameIdentity": _game_identity(latest_game),
        "gameKey": latest_game.get("game_key"),
        "homeTeam": latest_game.get("home_team"),
        "awayTeam": latest_game.get("away_team"),
        "commenceTime": latest_game.get("commence_time"),
        "providerSportKey": latest_game.get("provider_sport_key"),
        "predictedWinner": pick.get("team"),
        "predictedSide": pick.get("side"),
        "opponent": opponent.get("team"),
        "americanOdds": pick.get("americanOdds"),
        "priceBook": pick.get("priceBook"),
        "priceSource": pick.get("priceSource"),
        "marketSide": pick.get("marketSide"),
        "fairProbabilityPct": pick.get("fairProbabilityPct"),
        "winProbability": pick.get("winProbability"),
        "winProbabilityPct": pick.get("winProbabilityPct"),
        "edgeVsBook": pick.get("edgeVsBook"),
        "edgeVsBookPct": pick.get("edgeVsBookPct"),
        "expectedValue": pick.get("expectedValue"),
        "expectedValuePct": pick.get("expectedValuePct"),
        "promoted": pick.get("promoted"),
        "promotionStatus": pick.get("promotionStatus"),
        "blockedReasons": pick.get("blockedReasons") or [],
        "score": pick.get("score"),
        "confidenceTier": _confidence_tier(bool(pick.get("promoted")), float(pick.get("score") or 0), float(pick.get("edgeVsBook") or 0), float(pick.get("expectedValue") or 0)),
        "pickQuality": "PROMOTED_SINGLE_GAME_ML" if pick.get("promoted") else "WATCHLIST_OR_NO_PLAY",
        "tags": pick.get("tags") or [],
        "pullCountForGame": len(series),
        "homeSignal": home,
        "awaySignal": away,
        "temporalFeatureVersion": temporal_features.VERSION,
        "predictionSourcePullAt": source.get("pulled_at"),
        "predictionSourcePullId": source.get("pull_id"),
        "reason": "Single-game MLB moneyline pick ranked by de-vigged book probability, real bettable price, EV, edge, line movement, book agreement, reversals, and guardrails.",
        "createdAt": _now(),
    }


def _pregame_snapshot_item(row: Dict[str, Any], *, persisted_at: str) -> Dict[str, Any]:
    created_at = str(row.get("createdAt") or row.get("created_at") or _now())
    identity = str(row.get("gameIdentity") or row.get("gameId") or "unknown")
    source_at = str(
        row.get("predictionSourcePullAt")
        or ((row.get("frozenFeatureVector") or {}).get("sourcePullAtUtc"))
        or ""
    )
    identity_material = json.dumps(
        {
            "createdAt": created_at,
            "persistedAt": persisted_at,
            "gameIdentity": identity,
            "sourcePullAt": source_at,
            "predictedWinner": row.get("predictedWinner"),
            "predictedSide": row.get("predictedSide"),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:20]
    live_pk = f"GAME_WINNERS#mlb#{row.get('slate_date')}"
    live_sk = f"GAME#{row.get('commenceTime') or 'unknown'}#{identity}"
    persisted_row = history.ddb_safe(row)
    prediction_payload_fingerprint = history.canonical_payload_fingerprint(persisted_row)
    return history.ddb_safe({
        "PK": live_pk,
        "SK": f"PREGAME#GAME#{identity}#PERSISTED#{persisted_at}#CREATED#{created_at}#{digest}",
        "record_type": PREGAME_SNAPSHOT_RECORD_TYPE,
        "snapshot_version": PREGAME_SNAPSHOT_VERSION,
        "sport": "mlb",
        "slate_date": row.get("slate_date"),
        "game_id": row.get("gameId"),
        "game_identity": identity,
        "game_key": row.get("gameKey"),
        "commence_time": row.get("commenceTime"),
        "predicted_winner": row.get("predictedWinner"),
        "predicted_side": row.get("predictedSide"),
        "prediction_created_at_utc": created_at,
        "prediction_persisted_at_utc": persisted_at,
        "prediction_persistence_proof_type": PREGAME_PERSISTENCE_PROOF_TYPE,
        "prediction_persistence_write_pk": live_pk,
        "prediction_persistence_write_sk": live_sk,
        "prediction_payload_fingerprint_version": PAYLOAD_FINGERPRINT_VERSION,
        "prediction_payload_fingerprint": prediction_payload_fingerprint,
        "prediction_source_pull_at_utc": source_at or None,
        "prediction_source_pull_id": row.get("predictionSourcePullId"),
        "immutable_pregame": True,
        "write_once": True,
        "data": persisted_row,
        "created_at": created_at,
    })


def _put_pregame_snapshot(item: Dict[str, Any]) -> Dict[str, Any]:
    try:
        history.PULLS.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        created = True
    except Exception as exc:
        response = getattr(exc, "response", {}) or {}
        code = str((response.get("Error") or {}).get("Code") or "")
        if code != "ConditionalCheckFailedException":
            raise
        existing = history.PULLS.get_item(
            Key={"PK": item["PK"], "SK": item["SK"]},
            ConsistentRead=True,
        ).get("Item")
        if not existing:
            raise RuntimeError("MLB_PREGAME_SNAPSHOT_COLLISION_WITHOUT_READBACK") from exc
        collision_fields = (
            "data",
            "prediction_payload_fingerprint_version",
            "prediction_payload_fingerprint",
        )
        expected = json.dumps(
            {key: item.get(key) for key in collision_fields},
            sort_keys=True,
            default=str,
        )
        actual = json.dumps(
            {key: existing.get(key) for key in collision_fields},
            sort_keys=True,
            default=str,
        )
        if expected != actual:
            raise RuntimeError("MLB_PREGAME_SNAPSHOT_COLLISION_MISMATCH") from exc
        created = False
    return {
        "ok": True,
        "pk": item["PK"],
        "sk": item["SK"],
        "storageClass": "PREGAME_IMMUTABLE_SNAPSHOT",
        "writeOnce": True,
        "created": created,
        "version": PREGAME_SNAPSHOT_VERSION,
    }


def _store_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
    if history.PULLS is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    item = history.ddb_safe({
        "PK": f"GAME_WINNERS#mlb#{row.get('slate_date')}",
        "SK": f"GAME#{row.get('commenceTime') or 'unknown'}#{row.get('gameIdentity') or row.get('gameId')}",
        "record_type": "mlb_single_game_moneyline_prediction",
        "sport": "mlb",
        "slate_date": row.get("slate_date"),
        "game_id": row.get("gameId"),
        "game_identity": row.get("gameIdentity"),
        "game_key": row.get("gameKey"),
        "predicted_winner": row.get("predictedWinner"),
        "confidence_tier": row.get("confidenceTier"),
        "promotion_status": row.get("promotionStatus"),
        "promoted": row.get("promoted"),
        "score": row.get("score"),
        "win_probability": row.get("winProbability"),
        "edge_vs_book": row.get("edgeVsBook"),
        "expected_value": row.get("expectedValue"),
        "created_at": row.get("createdAt"),
        "data": row,
    })
    # The immutable snapshot timestamp is captured only after DynamoDB has
    # returned success for the live prediction write.  It is therefore a
    # conservative upper bound on when this exact prediction first existed in
    # the table; a timestamp sampled before put_item cannot prove persistence.
    history.PULLS.put_item(Item=item)
    persisted_at = _now()
    snapshot = _put_pregame_snapshot(
        _pregame_snapshot_item(row, persisted_at=persisted_at)
    )
    return {
        "ok": True,
        "pk": item["PK"],
        "sk": item["SK"],
        "storageClass": "LIVE_MUTABLE",
        "pregameSnapshot": snapshot,
    }


def predict_all(game_date: Optional[str] = None, store: bool = False, limit: int = 500) -> Dict[str, Any]:
    slate = game_date or _today_et()
    pulls = history.query_pulls("mlb", slate, limit)
    if not pulls:
        return {"ok": True, "sport": "mlb", "slate_date": slate, "engine": ENGINE, "count": 0, "predictions": [], "message": "No MLB pull history found for this slate."}
    latest_pull = pulls[-1]
    latest_games = [g for g in latest_pull.get("games") or [] if _game_day(g) == slate]
    predictions: List[Dict[str, Any]] = []
    stored = []
    for game in latest_games:
        row = _prediction_for_game(pulls, game, slate)
        if not row:
            continue
        predictions.append(row)

    if predictions and not any(row.get("promoted") for row in predictions):
        fallback = [
            row for row in predictions
            if float(row.get("edgeVsBook") or 0) >= FALLBACK_THRESHOLD
            and float(row.get("expectedValue") or -9) >= MIN_EV_FOR_PROMOTION
            and not row.get("blockedReasons")
        ]
        if fallback:
            best = max(fallback, key=lambda r: (float(r.get("expectedValue") or 0), float(r.get("edgeVsBook") or 0)))
            best["promoted"] = True
            best["promotionStatus"] = "PROMOTED_DYNAMIC_FLOOR"
            best["confidenceTier"] = _confidence_tier(True, float(best.get("score") or 0), float(best.get("edgeVsBook") or 0), float(best.get("expectedValue") or 0))
            best["pickQuality"] = "PROMOTED_SINGLE_GAME_ML_DYNAMIC_FLOOR"
            best["tags"] = sorted(set((best.get("tags") or []) + ["DYNAMIC_PROMOTION_FLOOR"]))

    predictions.sort(key=lambda r: (bool(r.get("promoted")), float(r.get("expectedValue") or -9), float(r.get("edgeVsBook") or -9), float(r.get("score") or 0)), reverse=True)
    for idx, row in enumerate(predictions, 1):
        row["rank"] = idx
        if store:
            row["stored"] = _store_prediction(row)
            stored.append(row.get("stored"))

    return {
        "ok": True,
        "sport": "mlb",
        "slate_date": slate,
        "engine": ENGINE,
        "modelVersion": MODEL_VERSION,
        "promotionThreshold": PROMOTION_THRESHOLD,
        "fallbackPromotionThreshold": FALLBACK_THRESHOLD,
        "pullCount": len(pulls),
        "latestPullAt": latest_pull.get("pulled_at"),
        "latestPullId": latest_pull.get("pull_id"),
        "gameCount": len(latest_games),
        "count": len(predictions),
        "promotedCount": len([row for row in predictions if row.get("promoted")]),
        "allGamesPredicted": len(predictions) == len(latest_games),
        "stored": store,
        "storedCount": len([x for x in stored if x and x.get("ok")]),
        "predictions": predictions,
    }
