from __future__ import annotations

import math
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple
from zoneinfo import ZoneInfo

from mlb_historical_policy_v1 import HistoricalPolicy, score_daily_slates

VERSION = "MLB-HISTORICAL-DAILY-OPTIMIZER-v15.11.1"
DEFAULT_SHARP_BOOKS = frozenset({"pinnacle", "betonlineag", "lowvig"})
FEATURE_NAMES = (
    "open_home_prob",
    "lock_home_prob",
    "net_move",
    "move_15m",
    "move_30m",
    "move_60m",
    "move_120m",
    "move_240m",
    "velocity",
    "acceleration",
    "volatility",
    "path_length",
    "trend_efficiency",
    "reversal_count",
    "favorite_flip_count",
    "max_short_move",
    "early_late_divergence",
    "book_dispersion",
    "sharp_divergence",
    "book_count",
    "overround",
    "snapshot_count",
)


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    feature_names: Tuple[str, ...]
    means: Tuple[float, ...]
    scales: Tuple[float, ...]
    weights: Tuple[float, ...]
    bias: float
    market_blend: float
    l2: float
    validation_min_daily_accuracy: float
    validation_mean_daily_accuracy: float
    validation_pass_day_rate: float
    validation_brier: float
    validation_log_loss: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchResult:
    version: str
    candidates_evaluated: int
    selected: Candidate
    validation_daily: Tuple[Dict[str, Any], ...]
    top_candidates: Tuple[Dict[str, Any], ...]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def american_to_implied_probability(odds: float) -> float:
    odds = float(odds)
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def devig_two_way(home_odds: float, away_odds: float) -> Tuple[float, float, float]:
    home_raw = american_to_implied_probability(home_odds)
    away_raw = american_to_implied_probability(away_odds)
    total = home_raw + away_raw
    if total <= 0:
        raise ValueError("invalid two-way market")
    return home_raw / total, away_raw / total, total - 1.0


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def snapshot_schedule(
    slate_date: str | date,
    commence_times: Iterable[Any],
    policy: HistoricalPolicy | None = None,
) -> Tuple[datetime, ...]:
    policy = policy or HistoricalPolicy()
    policy.validate()
    slate = date.fromisoformat(str(slate_date)) if not isinstance(slate_date, date) else slate_date
    et = ZoneInfo(policy.timezone)
    start_local = datetime.combine(slate, time(1, 0), tzinfo=et)
    locks = [
        _parse_datetime(value) - timedelta(minutes=policy.lock_minutes_before_commence)
        for value in commence_times
    ]
    if not locks:
        return tuple()
    latest_lock = max(locks)
    cursor = start_local.astimezone(timezone.utc)
    timestamps: List[datetime] = []
    step = timedelta(minutes=policy.snapshot_minutes)
    while cursor <= latest_lock:
        timestamps.append(cursor)
        cursor += step
    return tuple(timestamps)


def clip_game_snapshots(
    snapshots: Iterable[Mapping[str, Any]],
    commence_time: Any,
    policy: HistoricalPolicy | None = None,
) -> List[Dict[str, Any]]:
    policy = policy or HistoricalPolicy()
    policy.validate()
    lock_at = _parse_datetime(commence_time) - timedelta(
        minutes=policy.lock_minutes_before_commence
    )
    clipped: List[Dict[str, Any]] = []
    seen: set[datetime] = set()
    for row in snapshots:
        observed_at = _parse_datetime(
            row.get("observed_at_utc") or row.get("timestamp") or row.get("requested_at_utc")
        )
        if observed_at > lock_at or observed_at in seen:
            continue
        seen.add(observed_at)
        item = dict(row)
        item["observed_at_utc"] = observed_at.isoformat().replace("+00:00", "Z")
        clipped.append(item)
    clipped.sort(key=lambda row: row["observed_at_utc"])
    return clipped


def consensus_snapshot(
    event: Mapping[str, Any],
    sharp_books: Iterable[str] = DEFAULT_SHARP_BOOKS,
) -> Dict[str, Any]:
    home_team = str(event.get("home_team") or "").strip()
    away_team = str(event.get("away_team") or "").strip()
    if not home_team or not away_team:
        raise ValueError("event requires home_team and away_team")
    sharp = {str(value).lower() for value in sharp_books}
    home_probabilities: List[float] = []
    sharp_probabilities: List[float] = []
    overrounds: List[float] = []

    for bookmaker in event.get("bookmakers") or []:
        key = str(bookmaker.get("key") or "").lower()
        h2h = next(
            (market for market in bookmaker.get("markets") or [] if market.get("key") == "h2h"),
            None,
        )
        if not h2h:
            continue
        prices = {
            str(outcome.get("name") or ""): outcome.get("price")
            for outcome in h2h.get("outcomes") or []
        }
        if home_team not in prices or away_team not in prices:
            continue
        try:
            home_prob, _, overround = devig_two_way(
                float(prices[home_team]), float(prices[away_team])
            )
        except (TypeError, ValueError):
            continue
        home_probabilities.append(home_prob)
        overrounds.append(overround)
        if key in sharp:
            sharp_probabilities.append(home_prob)

    if not home_probabilities:
        raise ValueError("no valid two-way h2h bookmaker observations")
    consensus = statistics.fmean(home_probabilities)
    sharp_consensus = (
        statistics.fmean(sharp_probabilities) if sharp_probabilities else consensus
    )
    dispersion = (
        statistics.pstdev(home_probabilities) if len(home_probabilities) > 1 else 0.0
    )
    return {
        "home_probability": consensus,
        "sharp_home_probability": sharp_consensus,
        "book_dispersion": dispersion,
        "book_count": len(home_probabilities),
        "sharp_book_count": len(sharp_probabilities),
        "overround": statistics.fmean(overrounds),
    }


def _value_at_or_before(
    values: Sequence[Tuple[datetime, float]],
    target: datetime,
) -> float:
    eligible = [value for observed_at, value in values if observed_at <= target]
    return eligible[-1] if eligible else values[0][1]


def _sign(value: float, tolerance: float = 1e-9) -> int:
    if value > tolerance:
        return 1
    if value < -tolerance:
        return -1
    return 0


def extract_game_features(
    snapshots: Iterable[Mapping[str, Any]],
    commence_time: Any,
    policy: HistoricalPolicy | None = None,
) -> Dict[str, float]:
    policy = policy or HistoricalPolicy()
    clipped = clip_game_snapshots(snapshots, commence_time, policy)
    if not clipped:
        raise ValueError("game has no pre-lock historical snapshots")

    probability_path: List[Tuple[datetime, float]] = []
    sharp_path: List[float] = []
    dispersions: List[float] = []
    book_counts: List[float] = []
    overrounds: List[float] = []
    for row in clipped:
        observed_at = _parse_datetime(row["observed_at_utc"])
        probability = float(row["home_probability"])
        if not 0.0 < probability < 1.0:
            raise ValueError("home_probability must be inside (0, 1)")
        probability_path.append((observed_at, probability))
        sharp_path.append(float(row.get("sharp_home_probability", probability)))
        dispersions.append(float(row.get("book_dispersion", 0.0)))
        book_counts.append(float(row.get("book_count", 0.0)))
        overrounds.append(float(row.get("overround", 0.0)))

    lock_at = _parse_datetime(commence_time) - timedelta(
        minutes=policy.lock_minutes_before_commence
    )
    open_probability = probability_path[0][1]
    lock_probability = probability_path[-1][1]
    deltas = [
        probability_path[index][1] - probability_path[index - 1][1]
        for index in range(1, len(probability_path))
    ]
    signs = [_sign(value) for value in deltas if _sign(value)]
    reversal_count = sum(
        signs[index] != signs[index - 1] for index in range(1, len(signs))
    )
    favorite_states = [value >= 0.5 for _, value in probability_path]
    favorite_flips = sum(
        favorite_states[index] != favorite_states[index - 1]
        for index in range(1, len(favorite_states))
    )
    path_length = sum(abs(value) for value in deltas)
    net_move = lock_probability - open_probability
    trend_efficiency = abs(net_move) / path_length if path_length else 0.0
    volatility = statistics.pstdev([value for _, value in probability_path]) if len(probability_path) > 1 else 0.0
    velocity = statistics.fmean(deltas) if deltas else 0.0
    acceleration_values = [
        deltas[index] - deltas[index - 1] for index in range(1, len(deltas))
    ]
    acceleration = statistics.fmean(acceleration_values) if acceleration_values else 0.0
    max_short_move = max((abs(value) for value in deltas), default=0.0)

    def trailing(minutes: int) -> float:
        prior = _value_at_or_before(probability_path, lock_at - timedelta(minutes=minutes))
        return lock_probability - prior

    midpoint = max(1, len(deltas) // 2)
    early = sum(deltas[:midpoint]) if deltas else 0.0
    late = sum(deltas[midpoint:]) if deltas else 0.0
    return {
        "open_home_prob": open_probability,
        "lock_home_prob": lock_probability,
        "net_move": net_move,
        "move_15m": trailing(15),
        "move_30m": trailing(30),
        "move_60m": trailing(60),
        "move_120m": trailing(120),
        "move_240m": trailing(240),
        "velocity": velocity,
        "acceleration": acceleration,
        "volatility": volatility,
        "path_length": path_length,
        "trend_efficiency": trend_efficiency,
        "reversal_count": float(reversal_count),
        "favorite_flip_count": float(favorite_flips),
        "max_short_move": max_short_move,
        "early_late_divergence": late - early,
        "book_dispersion": statistics.fmean(dispersions),
        "sharp_divergence": sharp_path[-1] - lock_probability,
        "book_count": statistics.fmean(book_counts),
        "overround": statistics.fmean(overrounds),
        "snapshot_count": float(len(probability_path)),
    }


def _safe_probability(value: float) -> float:
    return min(max(float(value), 1e-6), 1.0 - 1e-6)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _prepare_matrix(
    rows: Sequence[Mapping[str, Any]],
    feature_names: Sequence[str],
    means: Sequence[float] | None = None,
    scales: Sequence[float] | None = None,
) -> Tuple[List[List[float]], Tuple[float, ...], Tuple[float, ...]]:
    raw = [
        [float((row.get("features") or {}).get(name, 0.0)) for name in feature_names]
        for row in rows
    ]
    if not raw:
        raise ValueError("empty model matrix")
    columns = list(zip(*raw))
    if means is None:
        means = tuple(statistics.fmean(column) for column in columns)
    if scales is None:
        scales = tuple(
            statistics.pstdev(column) if len(set(column)) > 1 else 1.0
            for column in columns
        )
    normalized = [
        [
            (value - float(means[index])) / (float(scales[index]) or 1.0)
            for index, value in enumerate(row)
        ]
        for row in raw
    ]
    return normalized, tuple(float(value) for value in means), tuple(float(value) or 1.0 for value in scales)


def _fit_logistic(
    matrix: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    l2: float,
    iterations: int = 350,
    learning_rate: float = 0.04,
) -> Tuple[List[float], float]:
    if len(matrix) != len(labels) or not matrix:
        raise ValueError("training matrix and labels are invalid")
    width = len(matrix[0])
    weights = [0.0] * width
    prevalence = min(max(statistics.fmean(labels), 1e-4), 1.0 - 1e-4)
    bias = math.log(prevalence / (1.0 - prevalence))
    count = float(len(labels))
    for step in range(iterations):
        gradient = [0.0] * width
        bias_gradient = 0.0
        for row, label in zip(matrix, labels):
            probability = _sigmoid(bias + sum(w * value for w, value in zip(weights, row)))
            error = probability - label
            bias_gradient += error
            for index, value in enumerate(row):
                gradient[index] += error * value
        rate = learning_rate / math.sqrt(1.0 + step / 40.0)
        bias -= rate * bias_gradient / count
        for index in range(width):
            weights[index] -= rate * (gradient[index] / count + l2 * weights[index])
    return weights, bias


def _predict_probability(
    row: Mapping[str, Any],
    candidate: Candidate,
) -> float:
    features = row.get("features") or {}
    normalized = [
        (float(features.get(name, 0.0)) - candidate.means[index])
        / (candidate.scales[index] or 1.0)
        for index, name in enumerate(candidate.feature_names)
    ]
    model_probability = _sigmoid(
        candidate.bias + sum(weight * value for weight, value in zip(candidate.weights, normalized))
    )
    market_probability = _safe_probability(
        float(row.get("market_home_probability", features.get("lock_home_prob", 0.5)))
    )
    return _safe_probability(
        candidate.market_blend * market_probability
        + (1.0 - candidate.market_blend) * model_probability
    )


def predictions_for_candidate(
    rows: Sequence[Mapping[str, Any]],
    candidate: Candidate,
) -> Tuple[List[Dict[str, Any]], List[float], List[int]]:
    predictions: List[Dict[str, Any]] = []
    probabilities: List[float] = []
    labels: List[int] = []
    for row in rows:
        probability = _predict_probability(row, candidate)
        home_team = str(row.get("home_team") or "").strip()
        away_team = str(row.get("away_team") or "").strip()
        if not home_team or not away_team:
            raise ValueError("model rows require home_team and away_team")
        pick = home_team if probability >= 0.5 else away_team
        predictions.append(
            {
                "slate_date": row["slate_date"],
                "game_id": row["game_id"],
                "pick": pick,
                "home_probability": probability,
            }
        )
        probabilities.append(probability)
        labels.append(int(row["home_win"]))
    return predictions, probabilities, labels


def _outcomes(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "slate_date": row["slate_date"],
            "game_id": row["game_id"],
            "winner": row["home_team"] if int(row["home_win"]) else row["away_team"],
        }
        for row in rows
    ]


def _metrics(
    rows: Sequence[Mapping[str, Any]],
    candidate: Candidate,
    policy: HistoricalPolicy,
) -> Dict[str, Any]:
    predictions, probabilities, labels = predictions_for_candidate(rows, candidate)
    daily = score_daily_slates(predictions, _outcomes(rows), policy)
    brier = statistics.fmean((probability - label) ** 2 for probability, label in zip(probabilities, labels))
    log_loss = -statistics.fmean(
        label * math.log(_safe_probability(probability))
        + (1 - label) * math.log(_safe_probability(1.0 - probability))
        for probability, label in zip(probabilities, labels)
    )
    accuracies = [row.accuracy for row in daily]
    return {
        "daily": [row.to_dict() for row in daily],
        "min_daily_accuracy": min(accuracies),
        "mean_daily_accuracy": statistics.fmean(accuracies),
        "pass_day_rate": statistics.fmean(float(row.passed) for row in daily),
        "brier": brier,
        "log_loss": log_loss,
    }


def _rank(metrics: Mapping[str, Any]) -> Tuple[float, float, float, float, float]:
    return (
        float(metrics["min_daily_accuracy"]),
        float(metrics["pass_day_rate"]),
        float(metrics["mean_daily_accuracy"]),
        -float(metrics["brier"]),
        -float(metrics["log_loss"]),
    )


def aggressive_search(
    train_rows: Sequence[Mapping[str, Any]],
    validation_rows: Sequence[Mapping[str, Any]],
    *,
    max_candidates: int = 25000,
    seed: int = 15111,
    policy: HistoricalPolicy | None = None,
) -> SearchResult:
    policy = policy or HistoricalPolicy()
    policy.validate()
    if len(train_rows) < policy.min_train_games:
        raise ValueError("aggressive search requires at least 1000 training games")
    if len(validation_rows) < policy.min_validation_games:
        raise ValueError("aggressive search requires at least 200 validation games")
    if max_candidates < 1 or max_candidates > 250000:
        raise ValueError("max_candidates must be between 1 and 250000")

    feature_names = FEATURE_NAMES
    train_matrix, means, scales = _prepare_matrix(train_rows, feature_names)
    labels = [int(row["home_win"]) for row in train_rows]
    base_models: List[Tuple[float, List[float], float]] = []
    for l2 in (0.001, 0.01, 0.1, 0.5, 1.0, 2.0):
        weights, bias = _fit_logistic(train_matrix, labels, l2=l2)
        base_models.append((l2, weights, bias))

    rng = random.Random(seed)
    best: Tuple[Tuple[float, ...], Candidate, Dict[str, Any]] | None = None
    leaderboard: List[Tuple[Tuple[float, ...], Candidate, Dict[str, Any]]] = []
    blends = (0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0)

    for index in range(max_candidates):
        l2, base_weights, base_bias = base_models[index % len(base_models)]
        temperature = 0.0 if index < len(base_models) * len(blends) else rng.choice((0.05, 0.10, 0.20, 0.35, 0.50))
        weights = [
            weight + rng.gauss(0.0, temperature) for weight in base_weights
        ]
        if index >= len(base_models) * len(blends):
            dropout = rng.choice((0.0, 0.05, 0.10, 0.20))
            weights = [0.0 if rng.random() < dropout else value for value in weights]
        bias = base_bias + (rng.gauss(0.0, temperature / 2.0) if temperature else 0.0)
        blend = blends[(index // len(base_models)) % len(blends)] if index < len(base_models) * len(blends) else rng.choice(blends)
        shell = Candidate(
            candidate_id=f"mlb-v15.11.1-{seed}-{index:06d}",
            feature_names=tuple(feature_names),
            means=means,
            scales=scales,
            weights=tuple(weights),
            bias=bias,
            market_blend=blend,
            l2=l2,
            validation_min_daily_accuracy=0.0,
            validation_mean_daily_accuracy=0.0,
            validation_pass_day_rate=0.0,
            validation_brier=0.0,
            validation_log_loss=0.0,
        )
        metrics = _metrics(validation_rows, shell, policy)
        candidate = Candidate(
            **{
                **shell.to_dict(),
                "validation_min_daily_accuracy": metrics["min_daily_accuracy"],
                "validation_mean_daily_accuracy": metrics["mean_daily_accuracy"],
                "validation_pass_day_rate": metrics["pass_day_rate"],
                "validation_brier": metrics["brier"],
                "validation_log_loss": metrics["log_loss"],
            }
        )
        ranked = (_rank(metrics), candidate, metrics)
        if best is None or ranked[0] > best[0]:
            best = ranked
        leaderboard.append(ranked)
        if len(leaderboard) > 50:
            leaderboard.sort(key=lambda item: item[0], reverse=True)
            del leaderboard[25:]

    if best is None:
        raise RuntimeError("candidate search produced no result")
    leaderboard.sort(key=lambda item: item[0], reverse=True)
    return SearchResult(
        version=VERSION,
        candidates_evaluated=max_candidates,
        selected=best[1],
        validation_daily=tuple(best[2]["daily"]),
        top_candidates=tuple(
            {
                "rank": rank + 1,
                "candidate": item[1].to_dict(),
            }
            for rank, item in enumerate(leaderboard[:20])
        ),
    )


def audit_selected_candidate(
    audit_rows: Sequence[Mapping[str, Any]],
    candidate: Candidate | Mapping[str, Any],
    *,
    policy: HistoricalPolicy | None = None,
) -> Dict[str, Any]:
    policy = policy or HistoricalPolicy()
    policy.validate()
    if len(audit_rows) < policy.min_audit_games:
        raise ValueError("untouched audit requires at least 200 later games")
    if not isinstance(candidate, Candidate):
        candidate = Candidate(
            candidate_id=str(candidate["candidate_id"]),
            feature_names=tuple(candidate["feature_names"]),
            means=tuple(float(value) for value in candidate["means"]),
            scales=tuple(float(value) for value in candidate["scales"]),
            weights=tuple(float(value) for value in candidate["weights"]),
            bias=float(candidate["bias"]),
            market_blend=float(candidate["market_blend"]),
            l2=float(candidate["l2"]),
            validation_min_daily_accuracy=float(candidate["validation_min_daily_accuracy"]),
            validation_mean_daily_accuracy=float(candidate["validation_mean_daily_accuracy"]),
            validation_pass_day_rate=float(candidate["validation_pass_day_rate"]),
            validation_brier=float(candidate["validation_brier"]),
            validation_log_loss=float(candidate["validation_log_loss"]),
        )
    metrics = _metrics(audit_rows, candidate, policy)
    return {
        "version": VERSION,
        "candidate_id": candidate.candidate_id,
        "audit_opened_after_selection": True,
        "audit_daily": metrics["daily"],
        "audit_min_daily_accuracy": metrics["min_daily_accuracy"],
        "audit_mean_daily_accuracy": metrics["mean_daily_accuracy"],
        "audit_pass_day_rate": metrics["pass_day_rate"],
        "audit_brier": metrics["brier"],
        "audit_log_loss": metrics["log_loss"],
    }
