from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

import handler as base
from model_gateway import configured_models, invoke_chain_text


def _without_scores(side: Any) -> Dict[str, Any]:
    if not isinstance(side, dict):
        return {}
    return {
        key: value
        for key, value in side.items()
        if key not in {"score", "runs", "winner", "isWinner"}
    }


def _core_market_summary(game: Dict[str, Any]) -> Dict[str, Any]:
    event = game.get("oddsCore") if isinstance(game.get("oddsCore"), dict) else {}
    books: List[Dict[str, Any]] = []
    for book in (event or {}).get("bookmakers") or []:
        if not isinstance(book, dict):
            continue
        markets: List[Dict[str, Any]] = []
        for market in book.get("markets") or []:
            if not isinstance(market, dict) or market.get("key") not in {
                "h2h",
                "spreads",
                "totals",
            }:
                continue
            outcomes = []
            for outcome in market.get("outcomes") or []:
                if not isinstance(outcome, dict):
                    continue
                outcomes.append(
                    {
                        key: outcome.get(key)
                        for key in ("name", "price", "point")
                        if outcome.get(key) is not None
                    }
                )
            markets.append(
                {
                    "key": market.get("key"),
                    "lastUpdate": market.get("last_update"),
                    "outcomes": outcomes,
                }
            )
        if markets:
            books.append(
                {
                    "key": book.get("key"),
                    "title": book.get("title"),
                    "markets": markets,
                }
            )
        if len(books) >= 12:
            break

    expanded = (
        game.get("oddsExpanded")
        if isinstance(game.get("oddsExpanded"), dict)
        else {}
    )
    expanded_markets = expanded.get("markets") or {}
    expanded_summary = {}
    if isinstance(expanded_markets, dict):
        for key, value in sorted(expanded_markets.items()):
            bookmakers = value.get("bookmakers") if isinstance(value, dict) else []
            expanded_summary[str(key)] = {
                "bookmakerCount": len(bookmakers or []),
                "available": bool(value),
            }

    return {
        "eventId": event.get("id"),
        "commenceTime": event.get("commence_time"),
        "consensus": game.get("marketConsensus") or {},
        "bookmakers": books,
        "expandedMarkets": expanded_summary,
        "expandedErrors": expanded.get("errors") or {},
    }


def _bbs_summary(game: Dict[str, Any]) -> Dict[str, Any]:
    bbs = game.get("bbs") if isinstance(game.get("bbs"), dict) else {}
    league = (
        game.get("bbsLeagueContext")
        if isinstance(game.get("bbsLeagueContext"), dict)
        else {}
    )
    return {
        "match": base._compact_for_llm(bbs.get("match"), limit=800),
        "detail": base._compact_for_llm(bbs.get("detail"), limit=900),
        "statistics": base._compact_for_llm(bbs.get("statistics"), limit=1200),
        "lineups": base._compact_for_llm(bbs.get("lineups"), limit=1200),
        "teamForm": base._compact_for_llm(bbs.get("teamForm"), limit=700),
        "players": base._compact_for_llm(bbs.get("players"), limit=1800),
        "events": base._compact_for_llm(bbs.get("events"), limit=500),
        "weather": base._compact_for_llm(bbs.get("weather"), limit=400),
        "standings": base._compact_for_llm(league.get("standings"), limit=600),
        "injuries": base._compact_for_llm(league.get("injuries"), limit=700),
    }


def _decision_game(game: Dict[str, Any]) -> Dict[str, Any]:
    official = game.get("official") if isinstance(game.get("official"), dict) else {}
    return {
        "gamePk": str(game.get("gamePk") or ""),
        "gameDate": game.get("gameDate"),
        "homeTeam": (game.get("home") or {}).get("name"),
        "awayTeam": (game.get("away") or {}).get("name"),
        "officialMlb": {
            "gamePk": official.get("gamePk"),
            "officialDate": official.get("officialDate"),
            "gameDate": official.get("gameDate"),
            "gameType": official.get("gameType"),
            "gameNumber": official.get("gameNumber"),
            "doubleHeader": official.get("doubleHeader"),
            "venue": official.get("venue") or {},
            "home": _without_scores(official.get("home")),
            "away": _without_scores(official.get("away")),
        },
        "theOddsApi": _core_market_summary(game),
        "bigBallsDataPro": _bbs_summary(game),
    }


def _parse_pick(
    raw: Dict[str, Any],
    game: Dict[str, Any],
    *,
    model_id: str,
    endpoint_family: str,
) -> Dict[str, Any]:
    game_pk = str(game.get("gamePk") or "")
    home = str((game.get("home") or {}).get("name") or "")
    away = str((game.get("away") or {}).get("name") or "")
    if str(raw.get("gamePk") or "") != game_pk:
        raise RuntimeError(f"BEDROCK_GAME_PK_MISMATCH:{game_pk}")
    winner = str(raw.get("winner") or "")
    if winner not in {home, away}:
        raise RuntimeError(f"BEDROCK_WINNER_NOT_EXACT_TEAM:{game_pk}")
    loser = away if winner == home else home
    try:
        probability = float(raw.get("probability") or 0.5)
    except (TypeError, ValueError):
        probability = 0.5
    probability = min(max(probability, 0.50), 0.95)
    return {
        "gamePk": game_pk,
        "gameDate": game.get("gameDate"),
        "homeTeam": home,
        "awayTeam": away,
        "predictedWinner": winner,
        "predictedLoser": loser,
        "probability": round(probability, 6),
        "decisionAuthority": "BEDROCK_LLM",
        "llmModelId": model_id,
        "endpointFamily": endpoint_family,
        "confidence": str(raw.get("confidence") or "MODEL"),
        "rationale": raw.get("rationale"),
        "sourceWeights": raw.get("source_weights") or {},
        "disagreements": raw.get("disagreements") or [],
        "sourcePresence": {
            "mlbStatsApi": bool(game.get("official")),
            "theOddsApi": bool(game.get("oddsCore")),
            "theOddsApiExpanded": bool(game.get("oddsExpanded")),
            "bigBallsDataPro": bool(game.get("bbs")),
        },
    }


def _strict_bedrock_card(packet: Dict[str, Any]) -> Dict[str, Any]:
    games = [row for row in packet.get("games") or [] if isinstance(row, dict)]
    if not games:
        raise RuntimeError("BEDROCK_CARD_HAS_NO_GAMES")

    state = base._recent_accuracy_state()
    prompt = (
        "You are the autonomous MLB winner-selection analyst for Inqsi. "
        "Choose exactly one winner for every scheduled game in this slate using the supplied point-in-time evidence from MLB Stats API, The Odds API, and Big Balls Sports Data Pro. "
        "Never invent missing data, never use final scores or outcomes, never omit a game, and never claim a guarantee. "
        f"When evidence conflicts, use marketAnchorWeight={state.get('marketAnchorWeight')} as the default weight on normalized multi-book consensus. "
        "Return ONLY a JSON object with one key named picks. picks must be an array containing exactly one object per game. "
        "Each object must contain gamePk, winner, probability, confidence, rationale, source_weights, and disagreements. "
        "gamePk must exactly match the supplied gamePk; winner must exactly match that game's homeTeam or awayTeam; probability must be between 0.50 and 0.95. "
        "Keep each rationale to one concise sentence.\n"
        "SLATE="
        + base.json.dumps(
            {
                "slateDateEt": packet.get("slateDateEt"),
                "autonomyState": state,
                "games": [_decision_game(game) for game in games],
            },
            separators=(",", ":"),
            default=str,
        )
    )

    remaining = configured_models()
    errors: List[Dict[str, Any]] = []
    while remaining:
        result = invoke_chain_text(
            prompt,
            remaining,
            max_tokens=max(500, min(1800, len(games) * 150)),
            temperature=0.1,
            top_p=0.9,
        )
        if result.get("ok") is not True:
            errors.extend(result.get("errors") or [])
            break

        model_id = str(result.get("modelId") or "")
        endpoint_family = str(result.get("endpointFamily") or "")
        errors.extend(result.get("errorsBeforeSuccess") or [])
        parsed = base._extract_json(str(result.get("text") or ""))
        raw_picks = parsed.get("picks") if isinstance(parsed, dict) else None
        try:
            if not isinstance(raw_picks, list) or len(raw_picks) != len(games):
                raise RuntimeError("BEDROCK_CARD_GAME_COUNT_MISMATCH")
            by_pk = {
                str(row.get("gamePk") or ""): row
                for row in raw_picks
                if isinstance(row, dict)
            }
            expected = {str(game.get("gamePk") or "") for game in games}
            if set(by_pk) != expected:
                raise RuntimeError("BEDROCK_CARD_GAME_SET_MISMATCH")
            picks = [
                _parse_pick(
                    by_pk[str(game.get("gamePk") or "")],
                    game,
                    model_id=model_id,
                    endpoint_family=endpoint_family,
                )
                for game in games
            ]
        except Exception as exc:
            errors.append(
                {
                    "modelId": model_id,
                    "endpointFamily": endpoint_family,
                    "errorCode": type(exc).__name__,
                    "message": str(exc)[:480],
                }
            )
            remaining = [value for value in remaining if value != model_id]
            continue

        return {
            "version": base.VERSION,
            "authority": "MLB_AUTO_LLM_PRIMARY",
            "slateDateEt": packet.get("slateDateEt"),
            "publishedAtUtc": base._iso(base._now()),
            "deadline": packet.get("deadline"),
            "targetDailyAccuracy": base.TARGET_ACCURACY,
            "targetIsGoalNotGuarantee": True,
            "autonomyState": state,
            "gameCount": len(picks),
            "llmPickCount": len(picks),
            "fallbackPickCount": 0,
            "picks": picks,
            "sourceStatus": packet.get("sourceStatus"),
            "bedrockBatch": {
                "modelId": model_id,
                "endpointFamily": endpoint_family,
                "usage": result.get("usage") or {},
                "attemptedModelIds": result.get("attemptedModelIds") or [],
                "errorsBeforeSuccess": errors,
            },
        }

    raise RuntimeError(
        "BEDROCK_AUTHORITY_UNAVAILABLE:"
        + base.json.dumps(errors, sort_keys=True, separators=(",", ":"))[:12000]
    )


def _strict_bedrock_decision(
    game: Dict[str, Any], state: Dict[str, Any]
) -> Dict[str, Any]:
    """Fail closed if legacy code tries to bypass the slate-level authority."""

    raise RuntimeError("BEDROCK_BATCH_CARD_AUTHORITY_REQUIRED")


base._bedrock_decision = _strict_bedrock_decision

import orchestrator as production  # noqa: E402

# The established production wrapper still enforces complete three-source
# coverage and rejects non-Bedrock picks. Replace only its underlying card
# builder so all games are decided in one quota-efficient Bedrock invocation.
production._ORIGINAL_BUILD_CARD = _strict_bedrock_card


def _run_payload(event: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(event, dict):
        return {}
    path = str(event.get("rawPath") or event.get("path") or "")
    method = str(
        ((event.get("requestContext") or {}).get("http") or {}).get("method")
        or event.get("httpMethod")
        or ""
    ).upper()
    if method == "GET":
        return None
    if method == "POST" and path.endswith("/run"):
        raw = event.get("body")
        try:
            parsed = base.json.loads(raw) if isinstance(raw, str) and raw else {}
        except Exception:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}
    if method:
        return None
    return event


def _late_guard(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    now = base._now()
    slate = str(
        payload.get("slate_date")
        or now.astimezone(base.ET).date().isoformat()
    )
    if base._get(f"CARD#{slate}", "FINAL"):
        return None
    schedule = base._official_schedule(slate)
    if not schedule.get("games"):
        return None
    deadline = base._deadline(schedule)
    deadline_dt = base._parse(deadline.get("publishDeadlineUtc"))
    if deadline_dt is None or now <= deadline_dt:
        return None

    if payload.get("mode") == "deployment_provider_smoke":
        # After the immutable cutoff, deployment verification may inspect fresh
        # provider coverage but may never create or relabel a late prediction.
        packet = production._assemble_with_full_bbd(slate, expanded=False)
        result = {
            "ok": True,
            "status": "COLLECTING",
            "slateDateEt": slate,
            "deadline": deadline,
            "nextFinalWindowAtUtc": base._iso(
                deadline_dt - timedelta(minutes=base.FINAL_WINDOW_MINUTES)
            ),
            "sourceStatus": packet.get("sourceStatus") or {},
            "threeSourceCoverageComplete": packet.get(
                "threeSourceCoverageComplete"
            ),
            "latePublicationPrevented": True,
        }
        production._validate_deployment_smoke(result)
        return result

    raise RuntimeError(
        "AUTHORITATIVE_CARD_DEADLINE_MISSED:"
        + base.json.dumps(
            {
                "slateDateEt": slate,
                "deadlineUtc": base._iso(deadline_dt),
                "nowUtc": base._iso(now),
                "forcePublishIgnored": bool(payload.get("force_publish")),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def lambda_handler(event: Any, context: Any) -> Any:
    payload = _run_payload(event)
    if payload is not None:
        try:
            guarded = _late_guard(payload)
            if guarded is not None:
                return guarded
        except Exception as exc:
            path = str((event or {}).get("rawPath") or (event or {}).get("path") or "") if isinstance(event, dict) else ""
            method = str((event or {}).get("httpMethod") or "").upper() if isinstance(event, dict) else ""
            if method or path:
                return base._response(
                    409,
                    {
                        "ok": False,
                        "service": "mlb-auto-llm",
                        "version": base.VERSION,
                        "errorType": type(exc).__name__,
                        "error": str(exc)[:1000],
                    },
                )
            raise
    return production.lambda_handler(event, context)
