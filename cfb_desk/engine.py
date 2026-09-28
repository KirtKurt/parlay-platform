"""Isolated public-score college engine. ESPN scores only. No other sport."""
from __future__ import annotations

import math
from typing import Any


BASE = 1500.0
K = 32.0
HOME = 85.0
REGRESS = 0.4
ROLL = 5
MIN_HISTORY = 4
LEAN = 0.58
MIN_BODY = 4


def sigmoid(value: float) -> float:
    if value >= 0:
        exp = math.exp(-min(value, 700.0))
        return 1.0 / (1.0 + exp)
    exp = math.exp(max(value, -700.0))
    return exp / (1.0 + exp)


def clip(probability: float) -> float:
    return min(1.0 - 1e-9, max(1e-9, probability))


def log_loss(probability: float, label: float) -> float:
    p = clip(probability)
    return -(label * math.log(p) + (1.0 - label) * math.log(1.0 - p))


def elo_expected(home_elo: float, away_elo: float, neutral: bool) -> float:
    boost = 0.0 if neutral else HOME
    return 1.0 / (1.0 + 10 ** (-(home_elo + boost - away_elo) / 400.0))


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def roll(values: list[float]) -> float:
    slice_ = values[-ROLL:]
    if not slice_:
        return 0.0
    weight = 0.0
    total = 0.0
    last = len(slice_) - 1
    for index, value in enumerate(slice_):
        w = 0.75 ** (last - index)
        weight += w
        total += value * w
    return total / weight


def rest_days(previous: str | None, kickoff: str) -> float:
    if not previous:
        return 7.0
    try:
        from datetime import datetime
        delta = (datetime.fromisoformat(kickoff.replace("Z", "+00:00")) - datetime.fromisoformat(previous.replace("Z", "+00:00"))).total_seconds() / 86400.0
    except ValueError:
        return 7.0
    if not math.isfinite(delta):
        return 7.0
    return max(0.0, min(21.0, delta))


def _team(table: dict[str, dict[str, Any]], name: str, conference: str) -> dict[str, Any]:
    row = table.get(name)
    if row is None:
        row = {
            "elo": BASE,
            "last_kick": None,
            "margins": [],
            "scored": [],
            "allowed": [],
            "games": 0,
            "conference": conference,
        }
        table[name] = row
    if conference:
        row["conference"] = conference
    return row


def conference_elo(table: dict[str, dict[str, Any]], conference: str, self_name: str) -> float:
    if not conference:
        return BASE
    peers = [row for row in table.values() if row["conference"] == conference and row["games"] > 0]
    if len(peers) < 4:
        return BASE
    named = table.get(self_name)
    pool = [row for row in peers if row is not named] if named else peers
    if not pool:
        return BASE
    return mean([row["elo"] for row in pool])


def regress(table: dict[str, dict[str, Any]]) -> None:
    for row in table.values():
        row["elo"] = BASE + (row["elo"] - BASE) * REGRESS


def update_elo(home: dict[str, Any], away: dict[str, Any], home_score: float, away_score: float, neutral: bool) -> None:
    expected = elo_expected(home["elo"], away["elo"], neutral)
    result = 0.5 if home_score == away_score else 1.0 if home_score > away_score else 0.0
    margin = abs(home_score - away_score)
    elo_gap = home["elo"] - away["elo"] if neutral else home["elo"] + HOME - away["elo"]
    mov = math.log(margin + 1.0) * (2.2 / (abs(elo_gap) * 0.001 + 2.2))
    delta = K * min(mov, 2.4) * (result - expected)
    home["elo"] += delta
    away["elo"] -= delta


def appearances(games: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for game in games:
        if not game["completed"]:
            continue
        counts[game["home"]] = counts.get(game["home"], 0) + 1
        counts[game["away"]] = counts.get(game["away"], 0) + 1
    return counts


def walk(games: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    body = appearances(games)
    ordered = sorted(games, key=lambda game: (game["kickoff"], game["id"]))
    table: dict[str, dict[str, Any]] = {}
    season = ordered[0]["season"] if ordered else 0
    rows: list[dict[str, Any]] = []
    excluded = 0
    for game in ordered:
        if game["season"] != season:
            regress(table)
            season = game["season"]
        home = _team(table, game["home"], game.get("homeConference") or "")
        away = _team(table, game["away"], game.get("awayConference") or "")
        prior = elo_expected(home["elo"], away["elo"], game["neutral"])
        home_points = ((roll(home["scored"]) + roll(away["allowed"])) / 2.0 + (0.0 if game["neutral"] else 2.6)) if home["games"] else 28.0
        away_points = ((roll(away["scored"]) + roll(home["allowed"])) / 2.0) if away["games"] else 24.0
        features = [
            (home["elo"] - away["elo"]) / 220.0,
            (conference_elo(table, home["conference"], game["home"]) - conference_elo(table, away["conference"], game["away"])) / 180.0,
            (rest_days(home["last_kick"], game["kickoff"]) - rest_days(away["last_kick"], game["kickoff"])) / 7.0,
            roll(home["margins"]) / 17.0,
            roll(away["margins"]) / 17.0,
            min(1.0, home["games"] / ROLL),
            min(1.0, away["games"] / ROLL),
            0.0 if game["neutral"] else 1.0,
        ]
        trainable = body.get(game["home"], 0) >= MIN_BODY and body.get(game["away"], 0) >= MIN_BODY
        if not trainable and game["completed"]:
            excluded += 1
        rows.append({
            "game": game,
            "prior": prior,
            "features": features,
            "margin": home_points - away_points,
            "total": home_points + away_points,
            "home_games": home["games"],
            "away_games": away["games"],
            "trainable": trainable,
        })
        if game["completed"] and game["homeScore"] is not None and game["awayScore"] is not None:
            update_elo(home, away, float(game["homeScore"]), float(game["awayScore"]), game["neutral"])
            home["margins"].append(float(game["homeScore"]) - float(game["awayScore"]))
            away["margins"].append(float(game["awayScore"]) - float(game["homeScore"]))
            home["scored"].append(float(game["homeScore"]))
            away["scored"].append(float(game["awayScore"]))
            home["allowed"].append(float(game["awayScore"]))
            away["allowed"].append(float(game["homeScore"]))
            home["games"] += 1
            away["games"] += 1
            home["last_kick"] = game["kickoff"]
            away["last_kick"] = game["kickoff"]
    return rows, excluded


def fit_residual(rows: list[dict[str, Any]]) -> dict[str, Any]:
    width = len(rows[0]["features"]) if rows else 0
    means = [0.0] * width
    scales = [1.0] * width
    for index in range(width):
        values = [row["features"][index] for row in rows]
        avg = mean(values)
        variance = mean([(value - avg) ** 2 for value in values])
        means[index] = avg
        scales[index] = math.sqrt(variance) if variance > 1e-8 else 1.0
    weights = [0.0] * (width + 1)
    ordered = sorted(rows, key=lambda row: row["kickoff"])
    for epoch in range(80):
        gradient = [0.0] * len(weights)
        for row in ordered:
            xs = [1.0]
            for index, value in enumerate(row["features"]):
                xs.append((value - means[index]) / (scales[index] or 1.0))
            logit = math.log(clip(row["prior"]) / (1.0 - clip(row["prior"]))) + weights[0]
            for index, value in enumerate(row["features"]):
                logit += weights[index + 1] * ((value - means[index]) / (scales[index] or 1.0))
            error = sigmoid(logit) - row["label"]
            for index, value in enumerate(xs):
                gradient[index] += error * value
        step = 0.03 / math.sqrt(1.0 + epoch * 0.03)
        count = len(ordered) or 1
        for index, weight in enumerate(weights):
            l2 = 0.0 if index == 0 else 0.008 * weight
            weights[index] = weight - step * (gradient[index] / count + l2)
    return {"weights": weights, "means": means, "scales": scales, "temperature": 1.0}


def raw_logit(model: dict[str, Any], features: list[float], prior: float) -> float:
    logit = math.log(clip(prior) / (1.0 - clip(prior))) + model["weights"][0]
    for index, value in enumerate(features):
        logit += model["weights"][index + 1] * ((value - model["means"][index]) / (model["scales"][index] or 1.0))
    return logit


def binary_metrics(probabilities: list[float], labels: list[float]) -> dict[str, float]:
    if not labels:
        return {"logLoss": 0.0, "ece": 1.0, "accuracy": 0.0}
    loss = 0.0
    correct = 0
    bins: list[list[tuple[float, float]]] = [[] for _ in range(10)]
    for index, probability in enumerate(probabilities):
        label = labels[index]
        p = clip(probability)
        loss += log_loss(p, label)
        predicted = 1.0 if p >= 0.5 else 0.0
        correct += 1 if predicted == label else 0
        confidence = p if predicted else 1.0 - p
        bins[min(9, int(math.floor(confidence * 10)))].append((confidence, 1.0 if predicted == label else 0.0))
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        ece += (len(bucket) / len(labels)) * abs(mean([row[0] for row in bucket]) - mean([row[1] for row in bucket]))
    return {"logLoss": loss / len(labels), "ece": ece, "accuracy": correct / len(labels)}


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _imul(left: int, right: int) -> int:
    return _u32(_u32(left) * _u32(right))


def lower_bound(deltas: list[float]) -> float:
    if not deltas:
        return float("-inf")
    state = 4408
    means: list[float] = []
    for _sample in range(400):
        total = 0.0
        for _index in range(len(deltas)):
            state = _u32(state + 0x6D2B79F5)
            t = state
            t = _imul(t ^ (t >> 15), t | 1)
            t = _u32(t ^ _u32(t + _imul(t ^ (t >> 7), t | 61)))
            draw = _u32(t ^ (t >> 14)) / 4294967296.0
            total += deltas[int(math.floor(draw * len(deltas)))]
        means.append(total / len(deltas))
    means.sort()
    return means[int(math.floor(len(means) * 0.05))]


def run_cfb_engine(games: list[dict[str, Any]], focus: dict[str, int]) -> dict[str, Any]:
    rows, excluded = walk(games)
    samples: list[dict[str, Any]] = []
    for row in rows:
        game = row["game"]
        if not row["trainable"] or not game["completed"] or game["homeScore"] is None or game["awayScore"] is None:
            continue
        if game["homeScore"] == game["awayScore"]:
            continue
        samples.append({
            "features": row["features"],
            "label": 1.0 if game["homeScore"] > game["awayScore"] else 0.0,
            "prior": row["prior"],
            "kickoff": game["kickoff"],
            "season": game["season"],
            "week": game["week"],
        })
    train = [row for row in samples if row["season"] == 2024 or (row["season"] == 2025 and row["week"] <= 6)]
    validation = [row for row in samples if row["season"] == 2025 and 7 <= row["week"] <= 10]
    audit = [row for row in samples if row["season"] == 2025 and row["week"] >= 11]
    failures: list[str] = []
    if len(train) < 350:
        failures.append("INSUFFICIENT_TRAINING_ROWS")
    if len(validation) < 100:
        failures.append("INSUFFICIENT_VALIDATION_ROWS")
    if len(audit) < 120:
        failures.append("INSUFFICIENT_AUDIT_ROWS")
    chronology_ok = (
        bool(train)
        and bool(validation)
        and bool(audit)
        and train[-1]["kickoff"] < validation[0]["kickoff"]
        and validation[-1]["kickoff"] < audit[0]["kickoff"]
    )
    if not chronology_ok:
        failures.append("CHRONOLOGY_VIOLATION")
    model = fit_residual(train) if train else None
    if model and validation:
        best = {"loss": float("inf"), "temperature": 1.0}
        for step in range(8, 27):
            temperature = step / 10.0
            loss = mean([
                log_loss(sigmoid(raw_logit(model, row["features"], row["prior"]) / temperature), row["label"])
                for row in validation
            ])
            if loss < best["loss"]:
                best = {"loss": loss, "temperature": temperature}
        model["temperature"] = best["temperature"]
    audit_prob = [sigmoid(raw_logit(model, row["features"], row["prior"]) / model["temperature"]) for row in audit] if model else []
    audit_base = [row["prior"] for row in audit]
    labels = [row["label"] for row in audit]
    candidate = binary_metrics(audit_prob, labels)
    baseline = binary_metrics(audit_base, labels)
    skill = baseline["logLoss"] - candidate["logLoss"]
    deltas = []
    for index, probability in enumerate(audit_prob):
        label = labels[index]
        base = audit_base[index] if index < len(audit_base) else 0.5
        deltas.append(math.log(clip(probability if label else 1.0 - probability) / clip(base if label else 1.0 - base)))
    bound = lower_bound(deltas)
    if skill <= 0:
        failures.append("AUDIT_DOES_NOT_BEAT_ELO")
    if bound <= 0:
        failures.append("AUDIT_SKILL_LOWER_BOUND_NOT_POSITIVE")
    if candidate["ece"] > 0.09:
        failures.append("AUDIT_CALIBRATION_FAILED")
    promoted = not failures and model is not None
    board = []
    for row in rows:
        game = row["game"]
        if game["season"] != focus["season"] or game["week"] != focus["week"]:
            continue
        probability = sigmoid(raw_logit(model, row["features"], row["prior"]) / model["temperature"]) if promoted and model else row["prior"]
        history_ok = row["home_games"] >= MIN_HISTORY and row["away_games"] >= MIN_HISTORY and row["trainable"]
        side = "pass"
        if history_ok and probability >= LEAN:
            side = "home"
        elif history_ok and probability <= 1.0 - LEAN:
            side = "away"
        result = "no-pick" if side == "pass" else "pending"
        if game["completed"] and game["homeScore"] is not None and game["awayScore"] is not None:
            if side == "pass":
                result = "no-pick"
            elif game["homeScore"] == game["awayScore"]:
                result = "push"
            else:
                home_won = game["homeScore"] > game["awayScore"]
                result = "hit" if (side == "home" and home_won) or (side == "away" and not home_won) else "miss"
        board.append({
            "id": game["id"],
            "kickoff": game["kickoff"],
            "week": game["week"],
            "season": game["season"],
            "away": game["away"],
            "home": game["home"],
            "awayName": game["awayName"],
            "homeName": game["homeName"],
            "completed": game["completed"],
            "homeScore": game["homeScore"],
            "awayScore": game["awayScore"],
            "homeWinProbability": probability,
            "expectedMargin": row["margin"],
            "expectedTotal": row["total"],
            "authority": "residual" if promoted else "elo",
            "side": side,
            "result": result,
        })
    board.sort(key=lambda row: (int(bool(row["completed"])), row["kickoff"]))
    return {
        "sport": "CFB",
        "source": "espn_public_scores",
        "market_model": False,
        "authority": "residual" if promoted else "elo",
        "promotedResidual": bool(promoted),
        "failures": failures,
        "excludedCupcakes": excluded,
        "metrics": {
            "train": len(train),
            "validation": len(validation),
            "audit": len(audit),
            "auditLogLoss": candidate["logLoss"],
            "baselineLogLoss": baseline["logLoss"],
            "skill": skill,
            "lowerBound": bound,
            "ece": candidate["ece"],
            "accuracy": candidate["accuracy"],
        },
        "board": board,
    }
