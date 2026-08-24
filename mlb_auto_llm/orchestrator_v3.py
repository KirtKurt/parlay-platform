from __future__ import annotations

import copy
import json
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Tuple

import handler as base
import orchestrator_v2 as strict_bedrock
import orchestrator as production
from ml_authority import AUTHORITY as ML_AUTHORITY
from ml_authority import build_card as build_ml_card


ALLOWED_MODEL_AUTHORITIES = {"BEDROCK_LLM", ML_AUTHORITY}
_STRICT_BEDROCK_CARD = production._ORIGINAL_BUILD_CARD


def _record_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in (
        "id",
        "match_id",
        "matchId",
        "event_id",
        "eventId",
        "fixture_id",
        "fixtureId",
        "game_id",
        "gameId",
        "uuid",
    ):
        if value.get(key):
            return str(value[key]).strip()
    return ""


def _nested_team_name(row: Dict[str, Any], side: str) -> str:
    keys = (
        side,
        side + "_team",
        side + "Team",
        side + "_team_name",
        side + "TeamName",
        side + "_name",
        side + "Name",
    )
    value: Any = None
    for key in keys:
        if row.get(key) is not None:
            value = row.get(key)
            break
    if isinstance(value, dict):
        nested = value.get("team") if isinstance(value.get("team"), dict) else value
        return base._team_name(nested)
    return str(value or "")


def _provider_start(row: Dict[str, Any]) -> Any:
    for key in (
        "kickoff_utc",
        "start_time",
        "startTime",
        "commence_time",
        "commenceTime",
        "scheduled_at",
        "scheduledAt",
        "scheduled",
        "game_date",
        "gameDate",
        "date",
    ):
        if row.get(key):
            value = row.get(key)
            if isinstance(value, dict):
                value = value.get("utc") or value.get("dateTime") or value.get("value")
            return value
    return None


def _match_event_v2(
    game: Dict[str, Any],
    rows: Iterable[Dict[str, Any]],
    *,
    provider: str,
) -> Optional[Dict[str, Any]]:
    home = base._normalize((game.get("home") or {}).get("name"))
    away = base._normalize((game.get("away") or {}).get("name"))
    official_start = base._parse(game.get("gameDate"))
    matches: List[Tuple[int, int, Dict[str, Any]]] = []
    for index, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        if provider == "odds":
            row_home = row.get("home_team") or row.get("homeTeam")
            row_away = row.get("away_team") or row.get("awayTeam")
        else:
            row_home = _nested_team_name(row, "home")
            row_away = _nested_team_name(row, "away")
        exact = base._normalize(row_home) == home and base._normalize(row_away) == away
        reversed_pair = (
            base._normalize(row_home) == away and base._normalize(row_away) == home
        )
        if not exact and not reversed_pair:
            continue
        provider_start = base._parse(_provider_start(row))
        if provider_start is not None and official_start is not None:
            drift = abs(int((provider_start - official_start).total_seconds()))
            if drift > 18 * 3600:
                continue
        else:
            drift = 12 * 3600
        orientation_penalty = 0 if exact else 6 * 3600
        matches.append((drift + orientation_penalty, index, row))
    matches.sort(key=lambda item: (item[0], item[1]))
    return copy.deepcopy(matches[0][2]) if matches else None


def _source_presence_v2(game: Dict[str, Any]) -> Dict[str, bool]:
    official = game.get("official")
    odds = game.get("oddsCore")
    bbs = game.get("bbs")
    bbs_match = bbs.get("match") if isinstance(bbs, dict) else None
    return {
        "mlbStatsApi": bool(isinstance(official, dict) and official.get("gamePk")),
        "theOddsApi": bool(_record_id(odds)),
        "bigBallsDataPro": bool(
            isinstance(bbs, dict)
            and bbs.get("ok", True) is not False
            and _record_id(bbs_match)
        ),
    }


def _odds_event_markets_parallel(event_id: str) -> Dict[str, Any]:
    output: Dict[str, Any] = {"eventId": event_id, "markets": {}, "errors": {}}

    def fetch_market(market: str) -> Tuple[str, Optional[Dict[str, Any]], Any]:
        params = urllib.parse.urlencode(
            {
                "apiKey": base.ODDS_API_KEY,
                "regions": "us",
                "markets": market,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            }
        )
        url = (
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/"
            + urllib.parse.quote(event_id)
            + "/odds?"
            + params
        )
        try:
            payload, headers = base._http_json(url, timeout=12)
            return market, payload if isinstance(payload, dict) else None, headers
        except urllib.error.HTTPError as exc:
            return market, None, f"HTTP_{exc.code}"
        except Exception as exc:
            return market, None, type(exc).__name__

    with ThreadPoolExecutor(max_workers=min(16, len(base.ODDS_MARKETS))) as pool:
        futures = [pool.submit(fetch_market, market) for market in base.ODDS_MARKETS]
        for future in as_completed(futures):
            market, payload, metadata = future.result()
            if payload is not None:
                output["markets"][market] = payload
                if isinstance(metadata, dict):
                    output["quota"] = {
                        "remaining": metadata.get("x-requests-remaining"),
                        "used": metadata.get("x-requests-used"),
                      "last": metadata.get("x-requests-last"),
                    }
            else:
                output["errors"][market] = metadata
    return output


def _model_card(packet: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _STRICT_BEDROCK_CARD(packet)
    except RuntimeError as exc:
        if not str(exc).startswith("BEDROCK_AUTHORITY_UNAVAILABLE:"):
            raise
        return build_ml_card(packet, bedrock_failure=str(exc))


def _build_card_three_source_model(packet: Dict[str, Any]) -> Dict[str, Any]:
    packet = production._apply_source_coverage(packet)
    missing = production._missing_three_source_games(packet)
    if missing or packet.get("threeSourceCoverageComplete") is not True:
        raise RuntimeError(
            "THREE_SOURCE_GAME_COVERAGE_INCOMPLETE:"
            + json.dumps(missing, sort_keys=True, separators=(",", ":"))
        )

    card = _model_card(packet)
    picks = [row for row in card.get("picks") or [] if isinstance(row, dict)]
    authorities = {str(row.get("decisionAuthority") or "") for row in picks}
    invalid = sorted(authorities - ALLOWED_MODEL_AUTHORITIES)
    if not picks or len(picks) != len(packet.get("games") or []) or invalid:
        raise RuntimeError(
            "MODEL_DECISION_AUTHORITY_REQUIRED:"
            + json.dumps(
                {
                    "pickCount": len(picks),
                    "gameCount": len(packet.get("games") or []),
                    "authorities": sorted(authorities),
                    "invalid": invalid,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    if len(authorities) != 1:
        raise RuntimeError(
            "MIXED_MODEL_AUTHORITIES_FORBIDDEN:" + json.dumps(sorted(authorities))
        )

    card["threeSourceCoverageComplete"] = True
    card["modelAuthorityComplete"] = True
    card["bedrockAuthorityComplete"] = authorities == {"BEDROCK_LLM"}
    card["mlAuthorityComplete"] = authorities == {ML_AUTHORITY}
    card["decisionAuthority"] = next(iter(authorities))
    card["sourcePresenceByGamePk"] = packet.get("sourcePresenceByGamePk") or {}
    return card


def _validate_deployment_smoke_v3(result: Dict[str, Any]) -> None:
    status = result.get("status")
    if status == "NO_GAMES":
        return
    if status == "COLLECTING":
        source = result.get("sourceStatus") or {}
        failures = {
            name: value
            for name in ("mlbStatsApi", "theOddsApi", "bigBallsDataPro")
            if (value := source.get(name) or {}).get("ok") is not True
        }
        if failures:
            raise RuntimeError(
                "THREE_SOURCE_GAME_COVERAGE_INCOMPLETE:"
                + json.dumps(failures, sort_keys=True, separators=(",", ":"))
            )
        return
    if status in {"CARD_PUBLISHED", "CARD_ALREADY_PUBLISHED"}:
        card = result.get("card") or {}
        if card.get("threeSourceCoverageComplete") is not True:
            raise RuntimeError("THREE_SOURCE_GAME_COVERAGE_INCOMPLETE:published_card")
        if card.get("modelAuthorityComplete") is not True:
            raise RuntimeError("MODEL_DECISION_AUTHORITY_REQUIRED:published_card")
        picks = [row for row in card.get("picks") or [] if isinstance(row, dict)]
        authorities = {str(row.get("decisionAuthority") or "") for row in picks}
        if not picks or not authorities or not authorities <= ALLOWED_MODEL_AUTHORITIES:
            raise RuntimeError("MODEL_DECISION_AUTHORITY_REQUIRED:published_picks")
        if len(authorities) != 1:
            raise RuntimeError("MIXED_MODEL_AUTHORITIES_FORBIDDEN:published_card")
        return
    raise RuntimeError(f"UNEXPECTED_DEPLOYMENT_SMOKE_STATUS:{status}")


base._match_event = _match_event_v2
base._odds_event_markets = _odds_event_markets_parallel
production._source_presence = _source_presence_v2
production._ORIGINAL_BUILD_CARD = _model_card
production._build_card_three_source_bedrock = _build_card_three_source_model
production._validate_deployment_smoke = _validate_deployment_smoke_v3
base._build_card = _build_card_three_source_model


def lambda_handler(event: Any, context: Any) -> Any:
    return strict_bedrock.lambda_handler(event, context)
