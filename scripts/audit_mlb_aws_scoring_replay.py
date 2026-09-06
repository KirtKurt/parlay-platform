#!/usr/bin/env python3
"""Replay historical MLB scoring models at the immutable T-minus-45 boundary.

This tool is deliberately read-only. It queries canonical DynamoDB pull history,
joins only FINAL official MLB outcomes, replays fixed historical/current scoring
formulas using pulls timestamped no later than each game's T-minus-45 cutoff,
and writes diagnostic artifacts. It never stores a pick, model, or prediction.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import inqsi_pull_history as history  # noqa: E402
import mlb_game_winner_engine as current_engine  # noqa: E402

VERSION = "MLB-AWS-SCORING-REPLAY-v1-tminus45-read-only"
OLD_V10_COMMIT = "bc7c752cf755a21cc14f2d4db3eb271ea35ed143"
OLD_V11_COMMIT = "7ffbd34552c3f5a70ff1a5e93671b6ffff4ca803"
LOCK_MINUTES = 45
OFFICIAL_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


class ReplayError(RuntimeError):
    pass


def _plain(value: Any) -> Any:
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date_range(start: str, end: str) -> List[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if end_date < start_date:
        raise ReplayError("end date is before start date")
    rows: List[str] = []
    cursor = start_date
    while cursor <= end_date:
        rows.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return rows


def _http_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "inqsi-mlb-scoring-replay/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _official_finals(start_date: str, end_date: str) -> Dict[str, List[Dict[str, Any]]]:
    query = urllib.parse.urlencode(
        {
            "sportId": 1,
            "startDate": start_date,
            "endDate": end_date,
            "hydrate": "team,linescore",
        }
    )
    payload = _http_json(f"{OFFICIAL_SCHEDULE_URL}?{query}")
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for date_entry in payload.get("dates") or []:
        slate = str(date_entry.get("date") or "")
        for game in date_entry.get("games") or []:
            if str(game.get("gameType") or "") != "R":
                continue
            status = game.get("status") or {}
            if str(status.get("abstractGameState") or "").lower() != "final":
                continue
            home = game.get("teams", {}).get("home", {})
            away = game.get("teams", {}).get("away", {})
            home_team = str((home.get("team") or {}).get("name") or "")
            away_team = str((away.get("team") or {}).get("name") or "")
            try:
                home_score = int(home.get("score"))
                away_score = int(away.get("score"))
            except Exception:
                continue
            if not home_team or not away_team or home_score == away_score:
                continue
            game_date = _parse_dt(game.get("gameDate"))
            if game_date is None:
                continue
            out[slate].append(
                {
                    "gamePk": str(game.get("gamePk") or ""),
                    "slateDateEt": slate,
                    "commenceTime": game_date.isoformat(),
                    "homeTeam": home_team,
                    "awayTeam": away_team,
                    "homeScore": home_score,
                    "awayScore": away_score,
                    "winner": home_team if home_score > away_score else away_team,
                    "gameNumber": int(game.get("gameNumber") or 1),
                    "doubleHeader": str(game.get("doubleHeader") or "N"),
                }
            )
    for slate in out:
        out[slate].sort(key=lambda row: (row["commenceTime"], row["gamePk"]))
    return dict(out)


def _norm_team(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    aliases = {
        "oakland athletics": "athletics",
        "athletics": "athletics",
        "la angels": "los angeles angels",
        "los angeles angels of anaheim": "los angeles angels",
        "ny yankees": "new york yankees",
        "ny mets": "new york mets",
        "sf giants": "san francisco giants",
        "kc royals": "kansas city royals",
        "sd padres": "san diego padres",
        "tb rays": "tampa bay rays",
    }
    return aliases.get(text, text)


def _nickname(value: Any) -> str:
    text = _norm_team(value)
    for suffix in ("red sox", "white sox", "blue jays"):
        if text.endswith(suffix):
            return suffix
    return text.split()[-1] if text else ""


def _same_team(left: Any, right: Any) -> bool:
    return _norm_team(left) == _norm_team(right) or (
        _nickname(left) and _nickname(left) == _nickname(right)
    )


def _game_matches(outcome: Mapping[str, Any], game: Mapping[str, Any]) -> bool:
    official_pk = str(game.get("official_game_pk") or game.get("officialGamePk") or "")
    if official_pk and official_pk == str(outcome.get("gamePk") or ""):
        return True
    return _same_team(outcome.get("homeTeam"), game.get("home_team") or game.get("homeTeam")) and _same_team(
        outcome.get("awayTeam"), game.get("away_team") or game.get("awayTeam")
    )


def _closest_matching_game(
    pull: Mapping[str, Any], outcome: Mapping[str, Any]
) -> Optional[Dict[str, Any]]:
    matches = [
        dict(game)
        for game in (pull.get("games") or [])
        if isinstance(game, dict) and _game_matches(outcome, game)
    ]
    if not matches:
        return None
    official_pk = str(outcome.get("gamePk") or "")
    exact = [
        game
        for game in matches
        if str(game.get("official_game_pk") or game.get("officialGamePk") or "") == official_pk
    ]
    if exact:
        return exact[0]
    target = _parse_dt(outcome.get("commenceTime"))
    if target is None:
        return matches[0]

    def distance(game: Mapping[str, Any]) -> float:
        commence = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
        return abs((commence - target).total_seconds()) if commence else float("inf")

    selected = min(matches, key=distance)
    return selected if distance(selected) <= 8 * 3600 else None


def _load_historical_engine(commit: str, module_name: str):
    source = subprocess.check_output(
        ["git", "show", f"{commit}:hello_world/mlb_game_winner_engine.py"],
        cwd=ROOT,
        text=True,
    )
    temp_dir = Path(tempfile.mkdtemp(prefix=f"{module_name}-"))
    path = temp_dir / f"{module_name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ReplayError(f"unable to load historical engine {commit}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _series_for_outcome(
    pulls: Sequence[Mapping[str, Any]], outcome: Mapping[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    start = _parse_dt(outcome.get("commenceTime"))
    if start is None:
        return [], []
    cutoff = start - timedelta(minutes=LOCK_MINUTES)
    old_series: List[Dict[str, Any]] = []
    current_series: List[Dict[str, Any]] = []
    for pull in pulls:
        pulled_at = _parse_dt(pull.get("pulled_at"))
        if pulled_at is None or pulled_at > cutoff:
            continue
        game = _closest_matching_game(pull, outcome)
        if game is None:
            continue
        probabilities = history.book_probs(game)
        current_fair = current_engine._market_fair(game)
        if probabilities:
            old_series.append(
                {
                    "pulled_at": pulled_at.isoformat(),
                    "game": game,
                    "probs": probabilities,
                }
            )
        if int(current_fair.get("book_count") or 0) > 0:
            current_series.append(
                {
                    "pull_id": pull.get("pull_id"),
                    "pulled_at": pulled_at.isoformat(),
                    "game": game,
                    "fair": current_fair,
                    "canonicalPullSlot": pull.get("canonicalPullSlot") or {},
                }
            )
    old_series.sort(key=lambda row: row["pulled_at"])
    current_series.sort(key=lambda row: row["pulled_at"])
    return old_series, current_series


def _score_old(module: Any, series: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not series:
        return None
    home = module._side_score(list(series), "home")
    away = module._side_score(list(series), "away")
    selected = home if float(home.get("score") or 0.0) >= float(away.get("score") or 0.0) else away
    opponent = away if selected is home else home
    tier = module._confidence_tier(
        float(selected.get("winProbability") or 0.5),
        float(selected.get("score") or 0.0),
        list(selected.get("tags") or []),
    )
    return {
        "side": selected.get("side"),
        "team": selected.get("team"),
        "score": float(selected.get("score") or 0.0),
        "probability": float(selected.get("winProbability") or 0.5),
        "marketProbability": float(selected.get("marketConsensusProbability") or 0.5),
        "delta": float(selected.get("delta") or 0.0),
        "reversals": int(selected.get("reversalCount") or 0),
        "divergence": float(selected.get("bookDivergence") or 0.0),
        "scoreMargin": abs(float(home.get("score") or 0.0) - float(away.get("score") or 0.0)),
        "probabilityMargin": abs(float(selected.get("winProbability") or 0.5) - float(opponent.get("winProbability") or 0.5)),
        "tier": tier,
        "tags": list(selected.get("tags") or []),
    }


def _score_current(series: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not series:
        return None
    home = current_engine._side_score(list(series), "home")
    away = current_engine._side_score(list(series), "away")
    selected = home if float(home.get("winProbability") or 0.0) >= float(away.get("winProbability") or 0.0) else away
    opponent = away if selected is home else home
    tier = current_engine._confidence_tier(
        bool(selected.get("promoted")),
        float(selected.get("score") or 0.0),
        float(selected.get("edgeVsBook") or 0.0),
        float(selected.get("expectedValue") or 0.0),
    )
    return {
        "side": selected.get("side"),
        "team": selected.get("team"),
        "score": float(selected.get("score") or 0.0),
        "probability": float(selected.get("winProbability") or 0.5),
        "marketProbability": float(selected.get("fairProbability") or 0.5),
        "delta": float(selected.get("delta") or 0.0),
        "reversals": int(selected.get("reversalCount") or 0),
        "divergence": float(selected.get("bookDivergence") or 0.0),
        "scoreMargin": abs(float(home.get("score") or 0.0) - float(away.get("score") or 0.0)),
        "probabilityMargin": abs(float(selected.get("winProbability") or 0.5) - float(opponent.get("winProbability") or 0.5)),
        "tier": tier,
        "tags": list(selected.get("tags") or []),
        "promoted": bool(selected.get("promoted")),
        "expectedValue": float(selected.get("expectedValue") or -1.0),
    }


def _market_pick(series: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not series:
        return None
    latest = series[-1]
    fair = latest.get("fair") or {}
    side = "home" if float(fair.get("home") or 0.5) >= float(fair.get("away") or 0.5) else "away"
    game = latest.get("game") or {}
    probability = float(fair.get(side) or 0.5)
    return {
        "side": side,
        "team": game.get("home_team") if side == "home" else game.get("away_team"),
        "probability": probability,
        "marketProbability": probability,
        "score": 50.0 + abs(probability - 0.5) * 100.0,
        "scoreMargin": abs(float(fair.get("home") or 0.5) - float(fair.get("away") or 0.5)) * 100.0,
        "probabilityMargin": abs(float(fair.get("home") or 0.5) - float(fair.get("away") or 0.5)),
        "tier": "MARKET",
    }


def _line_movement_pick(old_series: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not old_series:
        return None
    home_values = [float(row["probs"].get("home") or 0.5) for row in old_series]
    away_values = [float(row["probs"].get("away") or 0.5) for row in old_series]
    home_delta = home_values[-1] - home_values[0]
    away_delta = away_values[-1] - away_values[0]
    side = "home" if home_delta > away_delta else "away" if away_delta > home_delta else (
        "home" if home_values[-1] >= away_values[-1] else "away"
    )
    game = old_series[-1].get("game") or {}
    delta = home_delta if side == "home" else away_delta
    return {
        "side": side,
        "team": game.get("home_team") if side == "home" else game.get("away_team"),
        "probability": home_values[-1] if side == "home" else away_values[-1],
        "marketProbability": home_values[-1] if side == "home" else away_values[-1],
        "delta": delta,
        "score": 50.0 + delta * 900.0,
        "scoreMargin": abs(home_delta - away_delta) * 100.0,
        "probabilityMargin": abs(home_delta - away_delta),
        "tier": "LINE_MOVEMENT",
    }


def _ensemble(models: Mapping[str, Optional[Mapping[str, Any]]]) -> Optional[Dict[str, Any]]:
    available = {name: row for name, row in models.items() if isinstance(row, Mapping) and row.get("side") in {"home", "away"}}
    if not available:
        return None
    votes = Counter(str(row.get("side")) for row in available.values())
    max_votes = max(votes.values())
    leaders = sorted(side for side, count in votes.items() if count == max_votes)
    if len(leaders) == 1:
        selected_side = leaders[0]
    else:
        selected_side = str((available.get("market") or available.get("v11") or available.get("current") or {}).get("side") or leaders[0])
    aligned = [row for row in available.values() if row.get("side") == selected_side]
    team = next((row.get("team") for row in aligned if row.get("team")), None)
    probabilities = [float(row.get("probability") or 0.5) for row in aligned]
    agreement = votes[selected_side]
    return {
        "side": selected_side,
        "team": team,
        "probability": sum(probabilities) / len(probabilities),
        "marketProbability": float((available.get("market") or {}).get("probability") or 0.5),
        "score": 50.0 + 10.0 * agreement + 5.0 * (agreement / max(len(available), 1)),
        "scoreMargin": float(agreement),
        "probabilityMargin": float(agreement) / max(len(available), 1),
        "tier": f"AGREEMENT_{agreement}_OF_{len(available)}",
        "agreementCount": agreement,
        "modelCount": len(available),
        "votes": dict(votes),
    }


def _correct(prediction: Optional[Mapping[str, Any]], outcome: Mapping[str, Any]) -> Optional[bool]:
    if not isinstance(prediction, Mapping) or not prediction.get("team"):
        return None
    return _same_team(prediction.get("team"), outcome.get("winner"))


def _accuracy(rows: Sequence[Mapping[str, Any]], model: str, predicate=None) -> Dict[str, Any]:
    graded = []
    for row in rows:
        prediction = row.get("models", {}).get(model)
        if not isinstance(prediction, Mapping):
            continue
        if predicate is not None and not predicate(prediction):
            continue
        result = prediction.get("correct")
        if result in {True, False}:
            graded.append(bool(result))
    correct = sum(1 for value in graded if value)
    count = len(graded)
    return {
        "count": count,
        "correct": correct,
        "wrong": count - correct,
        "accuracyPct": round(100.0 * correct / count, 2) if count else None,
    }


def _frontier(rows: Sequence[Mapping[str, Any]], model: str) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    score_thresholds = [50, 55, 60, 65, 70, 72, 75, 80]
    probability_thresholds = [0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.65, 0.70]
    agreement_thresholds = [2, 3, 4, 5]
    for threshold in score_thresholds:
        metrics = _accuracy(rows, model, lambda p, t=threshold: float(p.get("score") or 0.0) >= t)
        output.append({"field": "score", "threshold": threshold, **metrics})
    for threshold in probability_thresholds:
        metrics = _accuracy(rows, model, lambda p, t=threshold: float(p.get("probability") or 0.0) >= t)
        output.append({"field": "probability", "threshold": threshold, **metrics})
    if model == "ensemble":
        for threshold in agreement_thresholds:
            metrics = _accuracy(rows, model, lambda p, t=threshold: int(p.get("agreementCount") or 0) >= t)
            output.append({"field": "agreementCount", "threshold": threshold, **metrics})
    return output


def _best_selective(frontier: Sequence[Mapping[str, Any]], minimum_count: int) -> Optional[Dict[str, Any]]:
    candidates = [
        dict(row)
        for row in frontier
        if int(row.get("count") or 0) >= minimum_count and row.get("accuracyPct") is not None
    ]
    candidates.sort(key=lambda row: (float(row["accuracyPct"]), int(row["count"])), reverse=True)
    return candidates[0] if candidates else None


def run(start_date: str, end_date: str) -> Dict[str, Any]:
    if not os.environ.get("SNAPSHOTS_TABLE"):
        raise ReplayError("SNAPSHOTS_TABLE is required")
    v10 = _load_historical_engine(OLD_V10_COMMIT, "mlb_game_winner_v10_replay")
    v11 = _load_historical_engine(OLD_V11_COMMIT, "mlb_game_winner_v11_replay")
    finals_by_date = _official_finals(start_date, end_date)
    rows: List[Dict[str, Any]] = []
    date_diagnostics: List[Dict[str, Any]] = []

    for slate in _date_range(start_date, end_date):
        pulls = history.query_pulls("mlb", slate, 500)
        outcomes = finals_by_date.get(slate) or []
        matched = 0
        for outcome in outcomes:
            old_series, current_series = _series_for_outcome(pulls, outcome)
            if not old_series or not current_series:
                continue
            matched += 1
            model_rows: Dict[str, Optional[Dict[str, Any]]] = {
                "v10": _score_old(v10, old_series),
                "v11": _score_old(v11, old_series),
                "current": _score_current(current_series),
                "market": _market_pick(current_series),
                "lineMovement": _line_movement_pick(old_series),
            }
            model_rows["ensemble"] = _ensemble(model_rows)
            for prediction in model_rows.values():
                if isinstance(prediction, dict):
                    prediction["correct"] = _correct(prediction, outcome)
            rows.append(
                {
                    "slateDateEt": slate,
                    "gamePk": outcome.get("gamePk"),
                    "commenceTime": outcome.get("commenceTime"),
                    "homeTeam": outcome.get("homeTeam"),
                    "awayTeam": outcome.get("awayTeam"),
                    "winner": outcome.get("winner"),
                    "homeScore": outcome.get("homeScore"),
                    "awayScore": outcome.get("awayScore"),
                    "prelockCutoffUtc": (
                        _parse_dt(outcome.get("commenceTime")) - timedelta(minutes=LOCK_MINUTES)
                    ).isoformat(),
                    "pullCountBeforeCutoff": len(old_series),
                    "firstPullAtUtc": old_series[0].get("pulled_at"),
                    "lastPullAtUtc": old_series[-1].get("pulled_at"),
                    "models": model_rows,
                }
            )
        date_diagnostics.append(
            {
                "slateDateEt": slate,
                "canonicalPullCount": len(pulls),
                "officialFinalGameCount": len(outcomes),
                "matchedPregameGameCount": matched,
                "missingPregameGameCount": max(0, len(outcomes) - matched),
            }
        )

    rows.sort(key=lambda row: (row["slateDateEt"], row["commenceTime"], row["gamePk"]))
    models = ["v10", "v11", "current", "market", "lineMovement", "ensemble"]
    metrics = {model: _accuracy(rows, model) for model in models}
    frontiers = {model: _frontier(rows, model) for model in models}
    selective = {
        model: {
            str(minimum): _best_selective(frontiers[model], minimum)
            for minimum in (5, 10, 20, 30, 50, 100)
        }
        for model in models
    }
    tier_metrics = {
        "v10": {
            tier: _accuracy(rows, "v10", lambda p, t=tier: str(p.get("tier")) == t)
            for tier in ("Premium", "Solid", "Lean", "Coin Flip", "Pass", "Baseline")
        },
        "v11": {
            tier: _accuracy(rows, "v11", lambda p, t=tier: str(p.get("tier")) == t)
            for tier in ("Premium", "Solid", "Lean", "Coin Flip", "Pass", "Baseline")
        },
        "current": {
            tier: _accuracy(rows, "current", lambda p, t=tier: str(p.get("tier")) == t)
            for tier in ("Premium", "Solid", "Promoted", "Watchlist", "No Play")
        },
    }
    high_depth = sum(1 for row in rows if int(row.get("pullCountBeforeCutoff") or 0) >= 12)
    at_least_four = sum(1 for row in rows if int(row.get("pullCountBeforeCutoff") or 0) >= 4)
    return {
        "ok": True,
        "readOnly": True,
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "range": {"startDate": start_date, "endDate": end_date},
        "modelSources": {
            "v10Commit": OLD_V10_COMMIT,
            "v11Commit": OLD_V11_COMMIT,
            "currentModelVersion": current_engine.MODEL_VERSION,
        },
        "leakageControls": {
            "outcomesFromOfficialMlbStatsApiFinalOnly": True,
            "canonicalDynamoDbPullHistoryOnly": True,
            "latestAllowedPullMinutesBeforeGame": LOCK_MINUTES,
            "postCutoffPullsExcluded": True,
            "predictionsPersisted": False,
            "awsWritesPerformed": False,
        },
        "summary": {
            "dateCount": len(date_diagnostics),
            "officialFinalGameCount": sum(int(row["officialFinalGameCount"]) for row in date_diagnostics),
            "matchedReplayGameCount": len(rows),
            "gamesWithAtLeast4PregamePulls": at_least_four,
            "gamesWithAtLeast12PregamePulls": high_depth,
            "currentAwsV2PartitionRowsRequired": 500,
            "replayRowsSufficientForCurrentPartitionMinimums": len(rows) >= 500,
            "modelMetrics": metrics,
            "tierMetrics": tier_metrics,
            "bestSelectiveThresholdByMinimumCount": selective,
        },
        "dateDiagnostics": date_diagnostics,
        "coverageAccuracyFrontiers": frontiers,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run(args.start_date, args.end_date)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_plain(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "summary": report["summary"],
                "output": str(output),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
