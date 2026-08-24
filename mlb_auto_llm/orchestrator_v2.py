from __future__ import annotations

from typing import Any, Dict, List

import handler as base
from model_gateway import invoke_chain_text


def _strict_bedrock_decision(game: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """Choose one scheduled winner through Bedrock; never substitute market-only authority."""

    home = str((game.get("home") or {}).get("name") or "")
    away = str((game.get("away") or {}).get("name") or "")
    packet = {
        "officialMlb": game.get("official"),
        "theOddsApi": {
            "core": game.get("oddsCore"),
            "expandedMarkets": game.get("oddsExpanded"),
            "consensus": game.get("marketConsensus"),
        },
        "bigBallsDataPro": game.get("bbs"),
        "bigBallsDataLeagueContext": game.get("bbsLeagueContext"),
        "autonomyState": state,
    }
    prompt = (
        "You are the autonomous MLB winner-selection analyst for Inqsi. Choose exactly one winner for this game. "
        "Use all applicable point-in-time evidence in the packet from MLB Stats API, The Odds API, and Big Balls Sports Data Pro. "
        "Never invent missing data, never use scores or outcomes, and never claim a guarantee. "
        f"When evidence conflicts, use marketAnchorWeight={state.get('marketAnchorWeight')} as the default weight on normalized multi-book consensus. "
        "Return ONLY JSON with keys winner, loser, probability, confidence, rationale, source_weights, disagreements. "
        f"winner must be exactly {home!r} or {away!r}; loser must be the other team; probability must be between 0.50 and 0.95.\n"
        "DATA=" + base.json.dumps(base._compact_for_llm(packet), separators=(",", ":"), default=str)
    )

    remaining = list(base.BEDROCK_MODELS)
    errors: List[Dict[str, str]] = []
    while remaining:
        result = invoke_chain_text(
            prompt,
            remaining,
            client=base.BEDROCK,
            max_tokens=900,
            temperature=0.15,
            top_p=0.9,
        )
        if result.get("ok") is not True:
            errors.extend(result.get("errors") or [])
            break

        model_id = str(result.get("modelId") or "")
        errors.extend(result.get("errorsBeforeSuccess") or [])
        parsed = base._extract_json(str(result.get("text") or ""))
        winner = str(parsed.get("winner") or "")
        if winner not in {home, away}:
            errors.append(
                {
                    "modelId": model_id,
                    "endpointFamily": str(result.get("endpointFamily") or ""),
                    "errorCode": "LLM_WINNER_NOT_EXACT_TEAM",
                    "message": "Model output did not name one exact scheduled team.",
                }
            )
            remaining = [value for value in remaining if value != model_id]
            continue

        loser = away if winner == home else home
        try:
            probability = float(parsed.get("probability") or 0.5)
        except (TypeError, ValueError):
            probability = 0.5
        probability = min(max(probability, 0.50), 0.95)
        return {
            "ok": True,
            "authority": "BEDROCK_LLM",
            "modelId": model_id,
            "endpointFamily": result.get("endpointFamily"),
            "winner": winner,
            "loser": loser,
            "probability": round(probability, 6),
            "confidence": str(parsed.get("confidence") or "MODEL"),
            "rationale": parsed.get("rationale"),
            "sourceWeights": parsed.get("source_weights") or {},
            "disagreements": parsed.get("disagreements") or [],
            "usage": result.get("usage") or {},
            "errorsBeforeSuccess": errors,
        }

    raise RuntimeError(
        "BEDROCK_AUTHORITY_UNAVAILABLE:"
        + base.json.dumps(errors, sort_keys=True, separators=(",", ":"))[:12000]
    )


# Patch the shared handler before importing the established enrichment/coverage
# orchestrator.  base._build_card resolves this global at execution time.
base._bedrock_decision = _strict_bedrock_decision

import orchestrator as production  # noqa: E402

lambda_handler = production.lambda_handler
