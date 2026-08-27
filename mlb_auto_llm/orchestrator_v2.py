from __future__ import annotations

import copy
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
        "theOddsApi": {
            **_core_market_summary(game),
            # Do not ask a language model to rediscover decimal-odds direction
            # from a large raw bookmaker packet.  The base collector computes a
            # no-vig, multi-book h2h consensus deterministically.  Publishing it
            # alongside the raw evidence prevents the model from treating a
            # larger decimal payout as a larger win probability.
            "normalizedH2hConsensus": copy.deepcopy(
                game.get("marketConsensus")
                if isinstance(game.get("marketConsensus"), dict)
                else {"available": False, "bookCount": 0}
            ),
            "decimalOddsContract": {
                "format": "decimal",
                "lowerPriceMeansHigherImpliedProbability": True,
                "rawImpliedProbabilityFormula": "1 / decimal_price",
                "favoriteAuthority": "normalizedH2hConsensus.marketFavorite",
            },
        },
        "bigBallsDataPro": _compact_bbs(game),
        "autonomyState": state,
    }


def _strict_prompt_packet(
    game: Dict[str, Any],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a bounded prompt without ever truncating the odds contract.

    ``handler._compact_for_llm`` replaces an oversized object with one JSON
    prefix.  Applying it to the complete multi-provider packet can therefore
    let a large, alphabetically earlier BBS payload consume the prefix before
    the normalized market authority is reached.  Bound the bulky evidence
    sources independently and keep the small decision contract in a protected
    structured envelope.
    """

    packet = _strict_packet(game, state)
    odds = packet["theOddsApi"]
    odds_evidence = {
        key: value
        for key, value in odds.items()
        if key not in {"normalizedH2hConsensus", "decimalOddsContract"}
    }
    return {
        "packetContractVersion": (
            "MLB-AUTO-STRICT-PROMPT-v2-nontruncatable-decimal-odds-authority"
        ),
        "autonomyState": base._compact_for_llm(packet["autonomyState"], 2500),
        "officialMlb": base._compact_for_llm(packet["officialMlb"], 4000),
        "theOddsApi": {
            # These two objects are deliberately outside every truncation
            # boundary.  They are the deterministic authority for interpreting
            # the separately bounded raw market evidence.
            "normalizedH2hConsensus": copy.deepcopy(
                odds["normalizedH2hConsensus"]
            ),
            "decimalOddsContract": copy.deepcopy(odds["decimalOddsContract"]),
            "boundedMarketEvidence": base._compact_for_llm(
                odds_evidence,
                9000,
            ),
        },
        "bigBallsDataPro": base._compact_for_llm(
            packet["bigBallsDataPro"],
            9000,
        ),
    }


def _strict_bedrock_decision(
    game: Dict[str, Any],
    state: Dict[str, Any],
    *,
    deployment_smoke_contract: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    home = str((game.get("home") or {}).get("name") or "")
    away = str((game.get("away") or {}).get("name") or "")
    expected_market_favorite = ""
    expected_market_favorite_price: Optional[float] = None
    smoke_prompt = ""
    if deployment_smoke_contract is not None:
        if set(deployment_smoke_contract) != {
            "expectedMarketFavorite",
            "expectedMarketFavoritePrice",
        }:
            raise RuntimeError("DEPLOYMENT_SMOKE_DECISION_CONTRACT_INVALID")
        expected_market_favorite = str(
            deployment_smoke_contract.get("expectedMarketFavorite") or ""
        )
        if expected_market_favorite not in {home, away}:
            raise RuntimeError("DEPLOYMENT_SMOKE_MARKET_FAVORITE_INVALID")
        try:
            expected_market_favorite_price = float(
                deployment_smoke_contract.get("expectedMarketFavoritePrice")
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "DEPLOYMENT_SMOKE_MARKET_FAVORITE_PRICE_INVALID"
            ) from exc
        smoke_prompt = (
            " This is a no-write deployment decision-contract probe with no "
            "non-market evidence. You MUST select the normalized market favorite "
            f"{expected_market_favorite!r}. Also return market_favorite exactly "
            f"{expected_market_favorite!r}, market_favorite_price as "
            f"{expected_market_favorite_price}, and market_interpretation as a "
            "short statement explicitly saying that the lower decimal price means "
            "higher implied probability."
        )
    prompt = (
        "You are the autonomous MLB winner-selection analyst for Inqsi. "
        "Choose exactly one winner using only the supplied pregame evidence. "
        "Do not invent missing data and do not copy a result from postgame fields. "
        "The Odds API prices are DECIMAL odds: a LOWER decimal price means a "
        "HIGHER raw implied win probability (1 / price). Never call the team "
        "with the higher decimal price the favorite. Use the supplied "
        "theOddsApi.normalizedH2hConsensus probabilities and marketFavorite "
        "instead of recomputing or guessing the favorite from raw prices. "
        f"Give normalized consensus at least autonomyState.marketAnchorWeight="
        f"{state.get('marketAnchorWeight')} of the decision weight. If choosing "
        "the normalized market underdog, identify concrete, quantified pregame "
        "evidence strong enough to overcome that anchor in disagreements; "
        "otherwise select the normalized market favorite. "
        "Return ONLY JSON with winner, loser, probability, confidence, rationale, "
        "source_weights, disagreements. "
        f"winner must be exactly {home!r} or {away!r}; loser must be the other team; "
        "probability must be between 0.50 and 0.95."
        + smoke_prompt
        + "\n"
        "DATA="
        + base.json.dumps(
            _strict_prompt_packet(game, state),
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
    if deployment_smoke_contract is not None:
        if winner != expected_market_favorite:
            raise RuntimeError(
                "DEPLOYMENT_SMOKE_DECIMAL_ODDS_FAVORITE_NOT_SELECTED"
            )
        if str(parsed.get("loser") or "") != loser:
            raise RuntimeError("DEPLOYMENT_SMOKE_LOSER_CONTRACT_INVALID")
        if str(parsed.get("market_favorite") or "") != expected_market_favorite:
            raise RuntimeError(
                "DEPLOYMENT_SMOKE_MARKET_FAVORITE_INTERPRETATION_INVALID"
            )
        try:
            parsed_market_favorite_price = float(
                parsed.get("market_favorite_price")
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "DEPLOYMENT_SMOKE_MARKET_FAVORITE_PRICE_MISSING"
            ) from exc
        if (
            expected_market_favorite_price is None
            or abs(parsed_market_favorite_price - expected_market_favorite_price)
            > 0.000001
        ):
            raise RuntimeError(
                "DEPLOYMENT_SMOKE_MARKET_FAVORITE_PRICE_INVALID"
            )
        market_interpretation = str(
            parsed.get("market_interpretation") or ""
        ).lower()
        if not all(
            token in market_interpretation
            for token in ("lower", "decimal", "higher", "probability")
        ):
            raise RuntimeError(
                "DEPLOYMENT_SMOKE_DECIMAL_ODDS_INTERPRETATION_MISSING"
            )
    probability = min(max(float(parsed.get("probability") or 0.5), 0.50), 0.95)
    result = {
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
    if deployment_smoke_contract is not None:
        result["marketFavorite"] = str(parsed.get("market_favorite") or "")
        result["marketFavoritePrice"] = parsed_market_favorite_price
        result["marketInterpretation"] = str(
            parsed.get("market_interpretation") or ""
        )
    return result


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


DEPLOYMENT_DECIMAL_ODDS_SMOKE_MODE = (
    "deployment_decimal_odds_decision_smoke"
)
DEPLOYMENT_DECIMAL_ODDS_SMOKE_CONTRACT = (
    "MLB-AUTO-DEPLOYMENT-DECIMAL-ODDS-SMOKE-v1"
)
_DEPLOYMENT_DECIMAL_ODDS_SMOKE_EVENT = {
    "mode": DEPLOYMENT_DECIMAL_ODDS_SMOKE_MODE,
    "contractVersion": DEPLOYMENT_DECIMAL_ODDS_SMOKE_CONTRACT,
}


def _deployment_decimal_odds_decision_smoke(
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Exercise the deployed strict decision path without a mutation surface.

    The event is intentionally closed rather than configurable: deployment can
    prove one known decimal-odds contract, while callers cannot turn this mode
    into an alternate card-generation or arbitrary-prompt endpoint.
    """

    if event != _DEPLOYMENT_DECIMAL_ODDS_SMOKE_EVENT:
        raise RuntimeError("DEPLOYMENT_DECIMAL_ODDS_SMOKE_EVENT_REJECTED")

    home = "Miami Marlins"
    away = "Boston Red Sox"
    home_price = 1.70
    away_price = 1.411
    game: Dict[str, Any] = {
        "gamePk": "DEPLOYMENT-DECIMAL-ODDS-SMOKE",
        "gameDate": "2099-07-15T23:10:00Z",
        "home": {"name": home},
        "away": {"name": away},
        "official": {
            "gamePk": "DEPLOYMENT-DECIMAL-ODDS-SMOKE",
            "officialDate": "2099-07-15",
            "gameDate": "2099-07-15T23:10:00Z",
            "gameType": "R",
            "home": {"name": home},
            "away": {"name": away},
        },
        "oddsCore": {
            "id": "deployment-decimal-odds-smoke",
            "commence_time": "2099-07-15T23:10:00Z",
            "home_team": home,
            "away_team": away,
            "bookmakers": [
                {
                    "key": "deployment_contract_book",
                    "title": "Deployment contract book",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": home, "price": home_price},
                                {"name": away, "price": away_price},
                            ],
                        }
                    ],
                }
            ],
        },
        "bbs": {
            "match": {"id": "deployment-decimal-odds-smoke"},
            "detail": {
                "deploymentSynthetic": True,
                "nonMarketEvidenceAvailable": False,
            },
        },
    }
    consensus = base._market_consensus(game)
    game["marketConsensus"] = consensus
    if (
        consensus.get("available") is not True
        or consensus.get("marketFavorite") != away
        or float(consensus.get("awayProbability") or 0.0)
        <= float(consensus.get("homeProbability") or 0.0)
        or not away_price < home_price
    ):
        raise RuntimeError(
            "DEPLOYMENT_DECIMAL_ODDS_NORMALIZATION_CONTRACT_FAILED"
        )

    # Guard the application's shared persistence surface even though this
    # branch calls only the strict decision routine. A future refactor that
    # introduces a packet/card/audit write will therefore fail deployment.
    original_put = base._put
    write_attempts: List[Dict[str, str]] = []

    def reject_write(pk: str, sk: str, *_args: Any, **_kwargs: Any) -> bool:
        write_attempts.append({"pk": str(pk), "sk": str(sk)})
        raise RuntimeError(
            "DEPLOYMENT_DECIMAL_ODDS_SMOKE_WRITE_FORBIDDEN:"
            + str(pk)
            + ":"
            + str(sk)
        )

    base._put = reject_write
    try:
        decision = _strict_bedrock_decision(
            game,
            {
                "targetDailyAccuracy": base.TARGET_ACCURACY,
                "recentDays": 0,
                "recentGradedPicks": 0,
                "recentCorrectPicks": 0,
                "recentAccuracy": None,
                "marketAnchorWeight": 1.0,
                "policy": (
                    "Deployment contract: only normalized h2h consensus is "
                    "available, so select its market favorite."
                ),
            },
            deployment_smoke_contract={
                "expectedMarketFavorite": away,
                "expectedMarketFavoritePrice": away_price,
            },
        )
    finally:
        base._put = original_put

    if write_attempts:
        raise RuntimeError("DEPLOYMENT_DECIMAL_ODDS_SMOKE_WRITE_ATTEMPTED")
    if decision.get("authority") != "BEDROCK_LLM":
        raise RuntimeError("DEPLOYMENT_SMOKE_BEDROCK_AUTHORITY_REQUIRED")
    if not decision.get("modelId"):
        raise RuntimeError("DEPLOYMENT_SMOKE_BEDROCK_MODEL_ID_REQUIRED")
    if decision.get("winner") != away or decision.get("loser") != home:
        raise RuntimeError("DEPLOYMENT_SMOKE_DECIMAL_ODDS_DECISION_INVALID")
    if (
        decision.get("marketFavorite") != away
        or float(decision.get("marketFavoritePrice") or 0.0) != away_price
    ):
        raise RuntimeError(
            "DEPLOYMENT_SMOKE_DECIMAL_ODDS_INTERPRETATION_INVALID"
        )

    return {
        "ok": True,
        "status": "DEPLOYMENT_DECIMAL_ODDS_DECISION_VERIFIED",
        "contractVersion": DEPLOYMENT_DECIMAL_ODDS_SMOKE_CONTRACT,
        "syntheticInputOnly": True,
        "writeGuardArmed": True,
        "persistenceAttempted": False,
        "cardMutationAttempted": False,
        "historyMutationAttempted": False,
        "mlFallbackAttempted": False,
        "decisionAuthority": decision.get("authority"),
        "modelId": decision.get("modelId"),
        "winner": decision.get("winner"),
        "loser": decision.get("loser"),
        "marketFavorite": decision.get("marketFavorite"),
        "marketFavoritePrice": decision.get("marketFavoritePrice"),
        "otherTeam": home,
        "otherTeamPrice": home_price,
        "marketInterpretation": decision.get("marketInterpretation"),
        "normalizedH2hConsensus": consensus,
        "decimalOddsContract": {
            "format": "decimal",
            "lowerPriceMeansHigherImpliedProbability": True,
            "rawImpliedProbabilityFormula": "1 / decimal_price",
            "favoriteAuthority": "normalizedH2hConsensus.marketFavorite",
        },
        "errorsBeforeBedrockSuccess": decision.get("errorsBeforeSuccess") or [],
    }


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
    if (
        isinstance(event, dict)
        and event.get("mode") == DEPLOYMENT_DECIMAL_ODDS_SMOKE_MODE
    ):
        return _deployment_decimal_odds_decision_smoke(event)

    payload = _run_payload(event)
    if payload is not None:
        try:
            if payload.get("mode") == DEPLOYMENT_DECIMAL_ODDS_SMOKE_MODE:
                raise RuntimeError(
                    "DEPLOYMENT_DECIMAL_ODDS_SMOKE_DIRECT_INVOKE_REQUIRED"
                )
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
