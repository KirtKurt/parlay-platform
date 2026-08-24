from __future__ import annotations

"""Autonomous Bedrock analyst for the three-source MLB feature envelope.

The LLM is not allowed to invent missing data or use observations effective
after the prediction cutoff. Its structured signal is persisted as a feature and
is measured independently in the daily audit. Existing deterministic/ML
probabilities remain available as evidence supplied to the analyst.
"""

import copy
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


VERSION = "MLB-THREE-API-LLM-ANALYST-v1"
DEFAULT_MODELS: Tuple[str, ...] = (
    "us.amazon.nova-2-lite-v1:0",
    "global.amazon.nova-2-lite-v1:0",
    "us.amazon.nova-lite-v1:0",
    "us.amazon.nova-micro-v1:0",
)
CACHE_SECONDS = max(30, int(os.environ.get("MLB_THREE_API_LLM_CACHE_SECONDS", "300")))
MAX_PROMPT_BYTES = max(8_000, min(100_000, int(os.environ.get("MLB_THREE_API_LLM_MAX_PROMPT_BYTES", "48000"))))
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _team(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _game_teams(game: Dict[str, Any]) -> Tuple[str, str]:
    home = _team(game.get("home_team") or game.get("homeTeam"))
    away = _team(game.get("away_team") or game.get("awayTeam"))
    return home, away


def _game_id(game: Dict[str, Any]) -> str:
    for key in (
        "official_game_pk", "officialGamePk", "provider_event_id", "providerEventId",
        "game_id", "gameId", "id",
    ):
        value = game.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _model_ids() -> List[str]:
    configured = [
        value.strip()
        for value in str(os.environ.get("MLB_THREE_API_LLM_MODEL_IDS", "")).split(",")
        if value.strip()
    ]
    single = str(os.environ.get("MLB_THREE_API_LLM_MODEL_ID", "")).strip()
    if single:
        configured.insert(0, single)
    return list(dict.fromkeys(configured + list(DEFAULT_MODELS)))


def _compact(value: Any, *, depth: int = 0) -> Any:
    if depth > 7:
        return "<depth-limited>"
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(token in key_text.lower() for token in ("api_key", "apikey", "secret", "token")):
                continue
            out[key_text] = _compact(item, depth=depth + 1)
            if len(out) >= 120:
                out["_truncated"] = True
                break
        return out
    if isinstance(value, list):
        return [_compact(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]


def _market_evidence(game: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    bookmakers = game.get("bookmakers") or context.get("bookmakers") or []
    return {
        "provider": "The Odds API",
        "bookmakers": _compact(bookmakers),
        "marketContext": _compact(
            context.get("market_context")
            or context.get("marketContext")
            or context.get("odds")
            or {}
        ),
    }


def _official_evidence(game: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "official_game_pk", "officialGamePk", "official_game_id", "officialGameId",
        "official_commence_time", "officialCommenceTime", "commence_time", "commenceTime",
        "home_team", "homeTeam", "away_team", "awayTeam", "official_status", "officialStatus",
        "official_game_number", "officialGameNumber", "official_double_header", "officialDoubleHeader",
        "schedule_authority", "scheduleAuthority",
    )
    return {
        "provider": "MLB Stats API",
        **{key: _compact(game.get(key)) for key in keys if game.get(key) not in (None, "")},
        "probablePitchers": _compact(context.get("confirmed_probable_pitchers") or {}),
        "travelRest": _compact(context.get("travel_rest") or {}),
        "venueWeather": _compact(
            context.get("weather_wind_roof")
            or context.get("venue")
            or context.get("ballpark_factors")
            or {}
        ),
    }


def _bbd_evidence(context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "Big Balls Data Pro",
        "context": _compact(context.get("big_balls_data_pro") or {}),
        "lineups": _compact(context.get("confirmed_lineups") or {}),
        "injuries": _compact(context.get("injuries_late_scratches_news") or {}),
        "pitching": _compact(context.get("fip_xfip") or {}),
        "offense": _compact(context.get("wrc_plus") or {}),
        "bullpen": _compact(context.get("bullpen_fatigue") or {}),
    }


def build_evidence(
    game: Dict[str, Any],
    context: Dict[str, Any],
    *,
    as_of_utc: Optional[str] = None,
) -> Dict[str, Any]:
    home, away = _game_teams(game)
    return {
        "version": VERSION,
        "asOfUtc": as_of_utc or _now_iso(),
        "gameId": _game_id(game),
        "homeTeam": home,
        "awayTeam": away,
        "officialMlb": _official_evidence(game, context),
        "theOddsApi": _market_evidence(game, context),
        "bigBallsDataPro": _bbd_evidence(context),
        "existingModelEvidence": _compact(
            context.get("model")
            or context.get("model_prediction")
            or context.get("modelPrediction")
            or context.get("ml_signal")
            or {}
        ),
        "sourceStatus": _compact(context.get("three_api_source_status") or {}),
    }


def _prompt(evidence: Dict[str, Any]) -> str:
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)
    if len(payload.encode("utf-8")) > MAX_PROMPT_BYTES:
        payload = payload.encode("utf-8")[:MAX_PROMPT_BYTES].decode("utf-8", errors="ignore")
    return (
        "You are the autonomous MLB pregame analyst for Inqsi. Use only the supplied "
        "point-in-time evidence. Do not use outside knowledge, future information, live "
        "or postgame observations. MLB Stats API owns game identity/start time; The Odds "
        "API owns market evidence; Big Balls Data Pro supplies baseball context. Return "
        "one winner and one loser for this game. Do not pass. Missing or conflicting "
        "evidence must reduce confidence, not be invented. Respond with exactly one JSON "
        "object containing: predicted_winner, predicted_loser, win_probability (0.5 to "
        "0.99), confidence (LOW|MEDIUM|HIGH), reason_codes (array of short strings), "
        "source_completeness (0 to 1), and warnings (array). Evidence:\n" + payload
    )


def _extract_text(response: Dict[str, Any]) -> str:
    output = response.get("output") if isinstance(response, dict) else None
    message = (output or {}).get("message") if isinstance(output, dict) else None
    content = (message or {}).get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        texts = [str(row.get("text") or "") for row in content if isinstance(row, dict)]
        if any(texts):
            return "\n".join(texts)
    return ""


def _parse_json(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    try:
        value = json.loads(stripped)
    except Exception:
        match = re.search(r"\{.*\}", stripped, re.S)
        if not match:
            raise RuntimeError("LLM_RESPONSE_JSON_MISSING")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise RuntimeError("LLM_RESPONSE_NOT_OBJECT")
    return value


def _validate(value: Dict[str, Any], game: Dict[str, Any]) -> Dict[str, Any]:
    home, away = _game_teams(game)
    teams = {home.lower(): home, away.lower(): away}
    winner_raw = _team(value.get("predicted_winner"))
    loser_raw = _team(value.get("predicted_loser"))
    winner = teams.get(winner_raw.lower())
    loser = teams.get(loser_raw.lower())
    if not winner or not loser or winner == loser:
        raise RuntimeError("LLM_WINNER_LOSER_INVALID")
    try:
        probability = float(value.get("win_probability"))
    except Exception as exc:
        raise RuntimeError("LLM_PROBABILITY_INVALID") from exc
    if not 0.5 <= probability <= 0.99:
        raise RuntimeError("LLM_PROBABILITY_OUT_OF_RANGE")
    try:
        completeness = float(value.get("source_completeness"))
    except Exception:
        completeness = 0.0
    completeness = min(1.0, max(0.0, completeness))
    confidence = str(value.get("confidence") or "LOW").upper()
    if confidence not in {"LOW", "MEDIUM", "HIGH"}:
        confidence = "LOW"
    reasons = [str(item)[:80] for item in value.get("reason_codes") or []][:12]
    warnings = [str(item)[:160] for item in value.get("warnings") or []][:12]
    return {
        "predictedWinner": winner,
        "predictedLoser": loser,
        "winProbability": probability,
        "confidence": confidence,
        "reasonCodes": reasons,
        "sourceCompleteness": completeness,
        "warnings": warnings,
    }


def analyze_game(
    game: Dict[str, Any],
    context: Dict[str, Any],
    *,
    as_of_utc: Optional[str] = None,
    bedrock_client: Any = None,
) -> Dict[str, Any]:
    evidence = build_evidence(game, context, as_of_utc=as_of_utc)
    evidence_fingerprint = _fingerprint(evidence)
    cache_key = evidence_fingerprint
    cached = _CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] <= CACHE_SECONDS:
        return copy.deepcopy(cached[1])

    base: Dict[str, Any] = {
        "version": VERSION,
        "gameId": _game_id(game),
        "asOfUtc": evidence["asOfUtc"],
        "evidenceFingerprint": evidence_fingerprint,
        "status": "UNAVAILABLE",
        "attemptedModelIds": [],
        "modelErrors": [],
    }
    try:
        client = bedrock_client
        if client is None:
            import boto3
            client = boto3.client(
                "bedrock-runtime",
                region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
            )
    except Exception as exc:
        base["modelErrors"].append(f"CLIENT:{type(exc).__name__}:{exc}")
        _CACHE[cache_key] = (now, base)
        return copy.deepcopy(base)

    prompt = _prompt(evidence)
    for model_id in _model_ids():
        base["attemptedModelIds"].append(model_id)
        try:
            response = client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 900, "temperature": 0.0, "topP": 0.9},
            )
            parsed = _parse_json(_extract_text(response))
            signal = _validate(parsed, game)
            result = {
                **base,
                **signal,
                "status": "CONNECTED",
                "modelId": model_id,
                "responseFingerprint": _fingerprint(parsed),
            }
            _CACHE[cache_key] = (now, result)
            return copy.deepcopy(result)
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            base["modelErrors"].append(
                {
                    "modelId": model_id,
                    "errorCode": code or type(exc).__name__,
                    "message": str(exc)[:300],
                }
            )
    _CACHE[cache_key] = (now, base)
    return copy.deepcopy(base)


def enrich_advanced_context(
    game: Dict[str, Any],
    context: Dict[str, Any],
    *,
    as_of_utc: Optional[str] = None,
    bedrock_client: Any = None,
) -> Dict[str, Any]:
    merged = copy.deepcopy(context) if isinstance(context, dict) else {}
    signal = analyze_game(
        game,
        merged,
        as_of_utc=as_of_utc,
        bedrock_client=bedrock_client,
    )
    merged["three_api_llm_signal"] = signal
    merged.setdefault("three_api_source_status", {})["bedrockLlmAnalyst"] = signal.get("status")
    merged["three_api_source_status"]["llmEvidenceFingerprint"] = signal.get("evidenceFingerprint")
    return merged
