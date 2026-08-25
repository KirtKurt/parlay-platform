from __future__ import annotations

from datetime import datetime, timedelta
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
        "bookmakers": books,
        "expandedMarkets": expanded_summary,
    }


def _compact_bbs(game: Dict[str, Any]) -> Dict[str, Any]:
    value = game.get("bbs") if isinstance(game.get("bbs"), dict) else {}
    match = value.get("match") if isinstance(value.get("match"), dict) else {}
    compact: Dict[str, Any] = {
        "match": match,
        "detail": value.get("detail"),
        "odds": value.get("odds"),
        "statistics": value.get("statistics"),
        "lineups": value.get("lineups"),
    }
    return compact


def _strict_packet(game: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    official = game.get("official") if isinstance(game.get("official"), dict) else {}
    safe_official = {
        "gamePk": official.get("gamePk"),
        "officialDate": official.get("officialDate"),
        "gameDate": official.get("gameDate"),
        "gameType": official.get("gameType"),
        "gameNumber": official.get("gameNumber"),
        "doubleHeader": official.get("doubleHeader"),
        "venue": official.get("venue"),
        "home": _without_scores(official.get("home")),
        "away": _without_scores(official.get("away")),
    }
    return {
        "officialMlb": safe_official,
        "theOddsApi": _core_market_summary(game),
        "bigBallsDataPro": _compact_bbs(game),
        "autonomyState": state,
    }


def _strict_bedrock_decision(game: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    home = str((game.get("home") or {}).get("name") or "")
    away = str((game.get("away") or {}).get("name") or "")
    prompt = (
        "You are the autonomous MLB winner-selection analyst for Inqsi. "
        "Choose exactly one winner using only the supplied pregame evidence. "
        "Do not invent missing data and do not copy a result from postgame fields. "
        "Return ONLY JSON with winner, loser, probability, confidence, rationale, "
        "source_weights, disagreements. "
        f"winner must be exactly {home!r} or {away!r}; loser must be the other team; "
        "probability must be between 0.50 and 0.95.\n"
        "DATA="
        + base.json.dumps(
            base._compact_for_llm(_strict_packet(game, state), 30000),
            separators=(",", ":"),
            default=str,
        )
    )
    response = invoke_chain_text(
        prompt,
        models=configured_models(),
        max_tokens=900,
        temperature=0.15,
    )
    if response.get("ok") is not True:
        raise RuntimeError(
            "BEDROCK_AUTHORITY_UNAVAILABLE:"
            + base.json.dumps(
                response,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )[:12000]
        )
    parsed = base._extract_json(str(response.get("text") or ""))
    winner = str(parsed.get("winner") or "")
    if winner not in {home, away}:
        raise RuntimeError("LLM_WINNER_NOT_EXACT_TEAM")
    loser = away if winner == home else home
    probability = min(max(float(parsed.get("probability") or 0.5), 0.50), 0.95)
    return {
        "ok": True,
        "authority": "BEDROCK_LLM",
        "modelId": response.get("modelId"),
        "winner": winner,
        "loser": loser,
        "probability": round(probability, 6),
        "confidence": str(parsed.get("confidence") or "MODEL"),
        "rationale": parsed.get("rationale"),
        "sourceWeights": parsed.get("source_weights") or {},
        "disagreements": parsed.get("disagreements") or [],
        "errorsBeforeSuccess": response.get("errorsBeforeSuccess") or [],
    }


def _build_strict_bedrock_card(packet: Dict[str, Any]) -> Dict[str, Any]:
    state = base._recent_accuracy_state()
    picks = []
    for game in packet.get("games") or []:
        decision = _strict_bedrock_decision(game, state)
        picks.append(
            {
                "gamePk": game.get("gamePk"),
                "gameDate": game.get("gameDate"),
                "homeTeam": (game.get("home") or {}).get("name"),
                "awayTeam": (game.get("away") or {}).get("name"),
                "predictedWinner": decision.get("winner"),
                "predictedLoser": decision.get("loser"),
                "probability": decision.get("probability"),
                "decisionAuthority": decision.get("authority"),
                "llmModelId": decision.get("modelId"),
                "confidence": decision.get("confidence"),
                "rationale": decision.get("rationale"),
                "sourceWeights": decision.get("sourceWeights"),
                "disagreements": decision.get("disagreements"),
                "sourcePresence": {
                    "mlbStatsApi": bool(game.get("official")),
                    "theOddsApi": bool(game.get("oddsCore")),
                    "theOddsApiExpanded": bool(game.get("oddsExpanded")),
                    "bigBallsDataPro": bool(game.get("bbs")),
                },
            }
        )
    return {
        "version": base.VERSION,
        "authority": "MLB_AUTO_LLM_BEDROCK",
        "slateDateEt": packet.get("slateDateEt"),
        "publishedAtUtc": base._iso(base._now()),
        "deadline": packet.get("deadline"),
        "targetDailyAccuracy": base.TARGET_ACCURACY,
        "dataSources": [
            "MLB Stats API",
            "The Odds API",
            "Big Balls Sports Data Pro",
            "Amazon Bedrock",
        ],
        "fullyAutonomous": True,
        "decisionPolicy": "BEDROCK_ONLY_NO_MARKET_DECISION_FALLBACK",
        "modelState": state,
        "picks": picks,
        "sourceStatus": packet.get("sourceStatus"),
    }


base._build_card = _build_strict_bedrock_card

import orchestrator as production


production._build_card_three_source_bedrock = _build_strict_bedrock_card
production._ORIGINAL_BUILD_CARD = _build_strict_bedrock_card


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


def _next_future_provider_probe(
    slate: str,
    now: Any,
) -> Optional[Dict[str, Any]]:
    """Return the next pre-cutoff MLB slate for read-only deployment proof.

    The live Odds endpoint can legitimately stop returning games after they
    begin. A deployment performed after today's immutable card cutoff must not
    reinterpret that absence as broken provider coverage and must never create
    a late card. Instead, validate all three providers against the next slate
    whose publication deadline is still in the future. This path is used only
    by deployment_provider_smoke and performs no prediction write.
    """
    try:
        anchor = datetime.strptime(slate, "%Y-%m-%d").date()
    except Exception:
        return None
    for offset in range(1, 8):
        candidate = (anchor + timedelta(days=offset)).isoformat()
        candidate_schedule = base._official_schedule(candidate)
        if not candidate_schedule.get("games"):
            continue
        candidate_deadline = base._deadline(candidate_schedule)
        candidate_deadline_dt = base._parse(candidate_deadline.get("publishDeadlineUtc"))
        if candidate_deadline_dt is None or now >= candidate_deadline_dt:
            continue
        return {
            "slate": candidate,
            "schedule": candidate_schedule,
            "deadline": candidate_deadline,
            "deadlineDt": candidate_deadline_dt,
        }
    return None


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
        # Never publish or relabel a card after the immutable cutoff. Current
        # live odds may legitimately omit games that have already started, so
        # validate provider completeness on the next still-pre-cutoff slate.
        probe = _next_future_provider_probe(slate, now)
        if probe is None:
            raise RuntimeError(
                "NO_FUTURE_PRE_CUTOFF_SLATE_FOR_PROVIDER_SMOKE:"
                + base.json.dumps(
                    {"requestedSlateDateEt": slate, "nowUtc": base._iso(now)},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        probe_slate = str(probe["slate"])
        probe_deadline = probe["deadline"]
        probe_deadline_dt = probe["deadlineDt"]
        packet = production._assemble_with_full_bbd(probe_slate, expanded=False)
        result = {
            "ok": True,
            "status": "COLLECTING",
            "requestedSlateDateEt": slate,
            "slateDateEt": probe_slate,
            "deadline": probe_deadline,
            "nextFinalWindowAtUtc": base._iso(
                probe_deadline_dt - timedelta(minutes=base.FINAL_WINDOW_MINUTES)
            ),
            "sourceStatus": packet.get("sourceStatus") or {},
            "threeSourceCoverageComplete": packet.get(
                "threeSourceCoverageComplete"
            ),
            "latePublicationPrevented": True,
            "providerProbeUsedFutureSlate": True,
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
