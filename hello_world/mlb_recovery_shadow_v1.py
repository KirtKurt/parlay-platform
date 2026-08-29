"""Prospective MLB recovery challenger capture and grading.

This module is intentionally shadow-only. It reads canonical pregame odds,
source-honest official MLB statistics, and a private S3 recovery artifact. It
writes only isolated recovery evidence to the snapshots table. It has no IAM or
code path for official prediction rows and cannot override a production pick.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import inqsi_pull_history as history
import mlb_game_winner_engine as current_engine
import mlb_recovery_v10_engine as legacy_v10
import mlb_recovery_v11_engine as legacy_v11

VERSION = "MLB-RECOVERY-SHADOW-RUNTIME-v1-prospective-tminus45"
SELECTION_RECORD_TYPE = "mlb_recovery_shadow_selection"
GRADE_RECORD_TYPE = "mlb_recovery_shadow_grade"
POINTER_KEYS = (
    "mlb/recovery-shadow/v1/fundamentals-latest.json",
    "mlb/recovery-shadow/v1/latest.json",
)
LOCK_MINUTES_BEFORE_GAME = 45
MAX_SOURCE_AGE_MINUTES = 30
MIN_PULL_DEPTH = 4
ET = ZoneInfo("America/New_York")
STATS_BASE = "https://statsapi.mlb.com/api"


class RecoveryShadowError(RuntimeError):
    pass


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


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


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in (None, "", ".---", "-.--", "-.-"):
        return default
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        return float(f"{value:.12g}") if math.isfinite(value) else str(value)
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return str(value)
    except Exception:
        pass
    return value


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _table() -> Any:
    table_name = str(os.environ.get("SNAPSHOTS_TABLE") or "").strip()
    if not table_name:
        raise RecoveryShadowError("SNAPSHOTS_TABLE is not configured")
    return boto3.resource("dynamodb").Table(table_name)


def _bucket() -> str:
    bucket = str(os.environ.get("MLB_ML_ARTIFACTS_BUCKET") or "").strip()
    if not bucket:
        raise RecoveryShadowError("MLB_ML_ARTIFACTS_BUCKET is not configured")
    return bucket


def _s3_json(s3: Any, bucket: str, key: str) -> Dict[str, Any]:
    response = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def _artifact_digest(artifact: Mapping[str, Any]) -> str:
    material = dict(artifact)
    material.pop("artifactDigest", None)
    return _fingerprint(material)


def validate_candidate_artifact(artifact: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(artifact, Mapping):
        raise RecoveryShadowError("recovery artifact is not an object")
    if artifact.get("ok") is not True:
        raise RecoveryShadowError("recovery artifact is not healthy")
    if artifact.get("shadowOnly") is not True:
        raise RecoveryShadowError("recovery artifact is not shadow-only")
    if artifact.get("productionAuthority") is not False:
        raise RecoveryShadowError("recovery artifact claims production authority")
    if artifact.get("officialPickOverrideAllowed") is not False:
        raise RecoveryShadowError("recovery artifact permits official pick override")
    if artifact.get("prospectivePromotionRequired") is not True:
        raise RecoveryShadowError("recovery artifact does not require prospective promotion")
    expected = str(artifact.get("artifactDigest") or "")
    actual = _artifact_digest(artifact)
    if not expected or expected != actual:
        raise RecoveryShadowError("recovery artifact digest mismatch")
    model = artifact.get("model")
    if not isinstance(model, Mapping) or not model.get("features"):
        raise RecoveryShadowError("recovery artifact model is missing")
    gate = artifact.get("noPlayGate")
    if not isinstance(gate, Mapping):
        raise RecoveryShadowError("recovery artifact no-play gate is missing")
    threshold = _number(gate.get("threshold"), _number(gate.get("selectedThreshold")))
    if threshold is None or not 0.5 <= threshold <= 0.95:
        raise RecoveryShadowError("recovery artifact threshold is invalid")
    return copy.deepcopy(dict(artifact))


def load_candidate_artifact(s3: Any = None, bucket: Optional[str] = None) -> Dict[str, Any]:
    s3 = s3 or boto3.client("s3")
    bucket = bucket or _bucket()
    errors: List[str] = []
    for pointer_key in POINTER_KEYS:
        try:
            pointer = _s3_json(s3, bucket, pointer_key)
            if pointer.get("shadowOnly") is not True or pointer.get("productionAuthority") is not False:
                raise RecoveryShadowError("pointer authority contract invalid")
            artifact_key = str(pointer.get("artifactKey") or "")
            if not artifact_key.startswith("mlb/recovery-shadow/"):
                raise RecoveryShadowError("pointer artifact key is outside recovery prefix")
            artifact = validate_candidate_artifact(_s3_json(s3, bucket, artifact_key))
            if str(pointer.get("artifactDigest") or "") != str(artifact.get("artifactDigest") or ""):
                raise RecoveryShadowError("pointer and artifact digest disagree")
            artifact["loadedFrom"] = {
                "bucket": bucket,
                "pointerKey": pointer_key,
                "artifactKey": artifact_key,
            }
            return artifact
        except Exception as exc:
            errors.append(f"{pointer_key}:{type(exc).__name__}:{exc}")
    raise RecoveryShadowError("no valid recovery artifact: " + " | ".join(errors))


def _http_json(path: str, params: Mapping[str, Any], attempts: int = 3) -> Dict[str, Any]:
    url = STATS_BASE + path + "?" + urllib.parse.urlencode(params)
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={
                "accept": "application/json",
                "user-agent": "inqsi-mlb-recovery-shadow/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.25 * (attempt + 1))
    raise RecoveryShadowError(f"official MLB request failed: {url}: {last_error}")


def _schedule(slate_date: str) -> Dict[str, Dict[str, Any]]:
    payload = _http_json(
        "/v1/schedule",
        {
            "sportId": 1,
            "date": slate_date,
            "hydrate": "probablePitcher,team,venue",
        },
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for date_row in payload.get("dates") or []:
        for game in date_row.get("games") or []:
            if not isinstance(game, dict) or str(game.get("gameType") or "") != "R":
                continue
            teams = game.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            game_pk = str(game.get("gamePk") or "")
            rows[game_pk] = {
                "gamePk": game_pk,
                "officialDate": str(game.get("officialDate") or slate_date),
                "gameDate": game.get("gameDate"),
                "homeTeamId": (home.get("team") or {}).get("id"),
                "awayTeamId": (away.get("team") or {}).get("id"),
                "homeTeam": (home.get("team") or {}).get("name"),
                "awayTeam": (away.get("team") or {}).get("name"),
                "homeProbablePitcherId": (home.get("probablePitcher") or {}).get("id"),
                "awayProbablePitcherId": (away.get("probablePitcher") or {}).get("id"),
                "homeProbablePitcher": (home.get("probablePitcher") or {}).get("fullName"),
                "awayProbablePitcher": (away.get("probablePitcher") or {}).get("fullName"),
                "venueId": (game.get("venue") or {}).get("id"),
                "venue": (game.get("venue") or {}).get("name"),
            }
    return rows


def _official_game_pk(game: Mapping[str, Any]) -> str:
    return str(game.get("official_game_pk") or game.get("officialGamePk") or "")


def _game_identity(game: Mapping[str, Any]) -> str:
    official = _official_game_pk(game)
    if official:
        return f"mlb_statsapi:{official}"
    identity = current_engine._game_identity(dict(game))
    return str(identity or "")


def _slate_game(game: Mapping[str, Any], slate_date: str) -> bool:
    commence = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return bool(commence and commence.astimezone(ET).date().isoformat() == slate_date)


def capture_disposition(
    commence_time: Any,
    now: datetime,
    source_pull_at: Optional[datetime],
    pull_depth: int,
) -> Dict[str, Any]:
    start = _parse_dt(commence_time)
    if start is None:
        return {"eligible": False, "reason": "COMMENCE_TIME_INVALID"}
    cutoff = start - timedelta(minutes=LOCK_MINUTES_BEFORE_GAME)
    if now < cutoff:
        return {"eligible": False, "reason": "BEFORE_TMINUS45", "cutoffAtUtc": cutoff.isoformat()}
    if now >= start:
        return {"eligible": False, "reason": "GAME_STARTED_NO_BACKFILL", "cutoffAtUtc": cutoff.isoformat()}
    if source_pull_at is None or source_pull_at > cutoff:
        return {"eligible": False, "reason": "PRELOCK_SOURCE_PULL_MISSING", "cutoffAtUtc": cutoff.isoformat()}
    source_age = (cutoff - source_pull_at).total_seconds() / 60.0
    if source_age > MAX_SOURCE_AGE_MINUTES:
        return {
            "eligible": False,
            "reason": "TMINUS45_SOURCE_STALE",
            "cutoffAtUtc": cutoff.isoformat(),
            "sourceAgeMinutes": round(source_age, 3),
        }
    if pull_depth < MIN_PULL_DEPTH:
        return {
            "eligible": False,
            "reason": "INSUFFICIENT_PULL_DEPTH",
            "cutoffAtUtc": cutoff.isoformat(),
            "pullDepth": pull_depth,
        }
    return {
        "eligible": True,
        "reason": "TMINUS45_PROSPECTIVE_CAPTURE_DUE",
        "cutoffAtUtc": cutoff.isoformat(),
        "sourceAgeMinutes": round(source_age, 3),
        "pullDepth": pull_depth,
    }


def _prelock_pulls(
    pulls: Sequence[Mapping[str, Any]], cutoff: datetime
) -> List[Dict[str, Any]]:
    rows = []
    for pull in pulls:
        pulled_at = _parse_dt(pull.get("pulled_at"))
        if pulled_at is not None and pulled_at <= cutoff:
            rows.append(dict(pull))
    return rows


def _old_pick(module: Any, series: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    home = module._side_score(list(series), "home")
    away = module._side_score(list(series), "away")
    selected = home if float(home.get("score") or 0) >= float(away.get("score") or 0) else away
    probability = float(selected.get("winProbability") or 0.5)
    return {
        "side": selected.get("side"),
        "team": selected.get("team"),
        "probability": probability,
        "homeProbability": probability if selected.get("side") == "home" else 1.0 - probability,
        "score": float(selected.get("score") or 0.0),
        "home": home,
        "away": away,
    }


def _current_pick(series: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    home = current_engine._side_score(list(series), "home")
    away = current_engine._side_score(list(series), "away")
    selected = home if float(home.get("winProbability") or 0) >= float(away.get("winProbability") or 0) else away
    probability = float(selected.get("winProbability") or 0.5)
    return {
        "side": selected.get("side"),
        "team": selected.get("team"),
        "probability": probability,
        "homeProbability": probability if selected.get("side") == "home" else 1.0 - probability,
        "score": float(selected.get("score") or 0.0),
        "home": home,
        "away": away,
    }


def _market_pick(current_series: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    latest = current_series[-1]
    fair = latest.get("fair") or {}
    home_probability = float(fair.get("home") or 0.5)
    side = "home" if home_probability >= float(fair.get("away") or 0.5) else "away"
    game = latest.get("game") or {}
    return {
        "side": side,
        "team": game.get("home_team") if side == "home" else game.get("away_team"),
        "probability": home_probability if side == "home" else 1.0 - home_probability,
        "homeProbability": home_probability,
        "fair": fair,
    }


def _line_pick(old_series: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    home_values = [float(row["probs"].get("home") or 0.5) for row in old_series]
    away_values = [float(row["probs"].get("away") or 0.5) for row in old_series]
    home_delta = home_values[-1] - home_values[0]
    away_delta = away_values[-1] - away_values[0]
    side = "home" if home_delta > away_delta else "away" if away_delta > home_delta else (
        "home" if home_values[-1] >= away_values[-1] else "away"
    )
    game = old_series[-1].get("game") or {}
    probability = home_values[-1] if side == "home" else away_values[-1]
    return {
        "side": side,
        "team": game.get("home_team") if side == "home" else game.get("away_team"),
        "probability": probability,
        "homeProbability": probability if side == "home" else 1.0 - probability,
        "homeDelta": home_delta,
        "awayDelta": away_delta,
    }


def _stat(payload: Mapping[str, Any]) -> Dict[str, Any]:
    for block in payload.get("stats") or []:
        for split in (block.get("splits") or []) if isinstance(block, dict) else []:
            if isinstance(split, dict) and isinstance(split.get("stat"), dict):
                return dict(split["stat"])
    return {}


def _cached_stat(
    cache: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], Dict[str, Any]],
    path: str,
    params: Mapping[str, Any],
) -> Dict[str, Any]:
    key = (path, tuple(sorted((str(name), str(value)) for name, value in params.items())))
    if key not in cache:
        cache[key] = _stat(_http_json(path, params))
    return cache[key]


def _rate(numerator: Any, denominator: Any) -> Optional[float]:
    num = _number(numerator)
    den = _number(denominator)
    return num / den if num is not None and den not in (None, 0.0) else None


def _hitting_features(stat: Mapping[str, Any]) -> Dict[str, Optional[float]]:
    games = _number(stat.get("gamesPlayed"))
    pa = _number(stat.get("plateAppearances"))
    return {
        "ops": _number(stat.get("ops")),
        "runsPerGame": _rate(stat.get("runs"), games),
        "walkRate": _rate(stat.get("baseOnBalls"), pa),
        "strikeoutRate": _rate(stat.get("strikeOuts"), pa),
    }


def _pitching_features(stat: Mapping[str, Any]) -> Dict[str, Optional[float]]:
    innings = _number(stat.get("inningsPitched"))
    starts = _number(stat.get("gamesStarted"))
    walks = _number(stat.get("baseOnBalls"))
    strikeouts = _number(stat.get("strikeOuts"))
    home_runs = _number(stat.get("homeRuns"))
    return {
        "era": _number(stat.get("era")),
        "whip": _number(stat.get("whip")),
        "strikeoutWalkRatio": strikeouts / walks if strikeouts is not None and walks not in (None, 0.0) else None,
        "homeRunsPer9": 9.0 * home_runs / innings if home_runs is not None and innings not in (None, 0.0) else None,
        "inningsPerStart": innings / starts if innings is not None and starts not in (None, 0.0) else None,
    }


def _side_stats(
    team_id: Any,
    pitcher_id: Any,
    game_date: date,
    cache: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], Dict[str, Any]],
) -> Dict[str, Any]:
    season_start = date(game_date.year, 3, 1).isoformat()
    end_date = (game_date - timedelta(days=1)).isoformat()
    recent_start = (game_date - timedelta(days=21)).isoformat()
    out: Dict[str, Any] = {
        "teamId": team_id,
        "probablePitcherId": pitcher_id,
        "seasonHitting": {},
        "recentHitting": {},
        "seasonPitching": {},
        "recentPitching": {},
        "seasonStarter": {},
        "recentStarter": {},
    }
    if team_id:
        out["seasonHitting"] = _hitting_features(
            _cached_stat(
                cache,
                f"/v1/teams/{int(team_id)}/stats",
                {"stats": "byDateRange", "group": "hitting", "startDate": season_start, "endDate": end_date},
            )
        )
        out["recentHitting"] = _hitting_features(
            _cached_stat(
                cache,
                f"/v1/teams/{int(team_id)}/stats",
                {"stats": "byDateRange", "group": "hitting", "startDate": recent_start, "endDate": end_date},
            )
        )
        out["seasonPitching"] = _pitching_features(
            _cached_stat(
                cache,
                f"/v1/teams/{int(team_id)}/stats",
                {"stats": "byDateRange", "group": "pitching", "startDate": season_start, "endDate": end_date},
            )
        )
        out["recentPitching"] = _pitching_features(
            _cached_stat(
                cache,
                f"/v1/teams/{int(team_id)}/stats",
                {"stats": "byDateRange", "group": "pitching", "startDate": recent_start, "endDate": end_date},
            )
        )
    if pitcher_id:
        out["seasonStarter"] = _pitching_features(
            _cached_stat(
                cache,
                f"/v1/people/{int(pitcher_id)}/stats",
                {"stats": "byDateRange", "group": "pitching", "startDate": season_start, "endDate": end_date},
            )
        )
        out["recentStarter"] = _pitching_features(
            _cached_stat(
                cache,
                f"/v1/people/{int(pitcher_id)}/stats",
                {"stats": "byDateRange", "group": "pitching", "startDate": recent_start, "endDate": end_date},
            )
        )
    return out


def _diff(home: Any, away: Any, lower_is_better: bool = False) -> Optional[float]:
    home_number = _number(home)
    away_number = _number(away)
    if home_number is None or away_number is None:
        return None
    return away_number - home_number if lower_is_better else home_number - away_number


def prospective_fundamentals(
    schedule_game: Optional[Mapping[str, Any]],
    stats_cache: Optional[Dict[Tuple[str, Tuple[Tuple[str, str], ...]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not schedule_game:
        return {
            "version": "MLB-RECOVERY-PROSPECTIVE-FUNDAMENTALS-v1",
            "available": False,
            "reason": "OFFICIAL_SCHEDULE_IDENTITY_MISSING",
            "features": {},
            "missingMasks": {},
            "completenessPct": 0.0,
        }
    game_date = date.fromisoformat(str(schedule_game.get("officialDate")))
    cache = stats_cache if stats_cache is not None else {}
    home = _side_stats(
        schedule_game.get("homeTeamId"),
        schedule_game.get("homeProbablePitcherId"),
        game_date,
        cache,
    )
    away = _side_stats(
        schedule_game.get("awayTeamId"),
        schedule_game.get("awayProbablePitcherId"),
        game_date,
        cache,
    )
    hs, as_ = home["seasonHitting"], away["seasonHitting"]
    hr, ar = home["recentHitting"], away["recentHitting"]
    hp, ap = home["seasonPitching"], away["seasonPitching"]
    hpr, apr = home["recentPitching"], away["recentPitching"]
    hsp, asp = home["seasonStarter"], away["seasonStarter"]
    hsr, asr = home["recentStarter"], away["recentStarter"]
    features: Dict[str, Optional[float]] = {
        "offenseOpsGapHome": _diff(hs.get("ops"), as_.get("ops")),
        "offenseRunsPerGameGapHome": _diff(hs.get("runsPerGame"), as_.get("runsPerGame")),
        "offenseWalkRateGapHome": _diff(hs.get("walkRate"), as_.get("walkRate")),
        "offenseStrikeoutRateGapHome": _diff(hs.get("strikeoutRate"), as_.get("strikeoutRate"), True),
        "recentOffenseOpsGapHome": _diff(hr.get("ops"), ar.get("ops")),
        "recentOffenseRunsPerGameGapHome": _diff(hr.get("runsPerGame"), ar.get("runsPerGame")),
        "staffEraGapHome": _diff(hp.get("era"), ap.get("era"), True),
        "staffWhipGapHome": _diff(hp.get("whip"), ap.get("whip"), True),
        "staffStrikeoutWalkGapHome": _diff(hp.get("strikeoutWalkRatio"), ap.get("strikeoutWalkRatio")),
        "staffHomeRunsPer9GapHome": _diff(hp.get("homeRunsPer9"), ap.get("homeRunsPer9"), True),
        "recentStaffEraGapHome": _diff(hpr.get("era"), apr.get("era"), True),
        "recentStaffWhipGapHome": _diff(hpr.get("whip"), apr.get("whip"), True),
        "starterEraGapHome": _diff(hsp.get("era"), asp.get("era"), True),
        "starterWhipGapHome": _diff(hsp.get("whip"), asp.get("whip"), True),
        "starterStrikeoutWalkGapHome": _diff(hsp.get("strikeoutWalkRatio"), asp.get("strikeoutWalkRatio")),
        "starterHomeRunsPer9GapHome": _diff(hsp.get("homeRunsPer9"), asp.get("homeRunsPer9"), True),
        "starterInningsPerStartGapHome": _diff(hsp.get("inningsPerStart"), asp.get("inningsPerStart")),
        "starterRecentEraGapHome": _diff(hsr.get("era"), asr.get("era"), True),
        "starterRecentWhipGapHome": _diff(hsr.get("whip"), asr.get("whip"), True),
        "starterRecentStrikeoutWalkGapHome": _diff(hsr.get("strikeoutWalkRatio"), asr.get("strikeoutWalkRatio")),
        "starterDaysRestGapHome": None,
        "homeField": 1.0,
    }
    masks = {
        f"{name}Missing": value is None
        for name, value in features.items()
        if name != "homeField"
    }
    available_count = sum(value is not None for name, value in features.items() if name != "homeField")
    total = len(features) - 1
    return {
        "version": "MLB-RECOVERY-PROSPECTIVE-FUNDAMENTALS-v1",
        "available": available_count > 0,
        "capturedProspectively": True,
        "asOfDate": (game_date - timedelta(days=1)).isoformat(),
        "strictlyPriorGameDataOnly": True,
        "officialGamePk": schedule_game.get("gamePk"),
        "homeProbablePitcherId": schedule_game.get("homeProbablePitcherId"),
        "awayProbablePitcherId": schedule_game.get("awayProbablePitcherId"),
        "homeProbablePitcher": schedule_game.get("homeProbablePitcher"),
        "awayProbablePitcher": schedule_game.get("awayProbablePitcher"),
        "venueId": schedule_game.get("venueId"),
        "venue": schedule_game.get("venue"),
        "features": features,
        "missingMasks": masks,
        "completenessPct": round(100.0 * available_count / total, 2) if total else 0.0,
    }


def _model_feature_value(
    name: str,
    signals: Mapping[str, Any],
    fundamentals: Mapping[str, Any],
) -> Optional[float]:
    if name in signals:
        return _number(signals.get(name))
    features = fundamentals.get("features") or {}
    masks = fundamentals.get("missingMasks") or {}
    if name in features:
        return _number(features.get(name))
    if name in masks:
        return 1.0 if masks.get(name) is True else 0.0
    return None


def score_candidate(
    artifact: Mapping[str, Any],
    signals: Mapping[str, Any],
    fundamentals: Mapping[str, Any],
) -> Dict[str, Any]:
    model = artifact.get("model") or {}
    z = float(_number(model.get("bias"), 0.0) or 0.0)
    model_features: Dict[str, Any] = {}
    for name in model.get("features") or []:
        raw = _model_feature_value(str(name), signals, fundamentals)
        impute = float(_number((model.get("impute") or {}).get(name), 0.0) or 0.0)
        value = raw if raw is not None else impute
        mean = float(_number((model.get("means") or {}).get(name), 0.0) or 0.0)
        scale = float(_number((model.get("scales") or {}).get(name), 1.0) or 1.0)
        weight = float(_number((model.get("weights") or {}).get(name), 0.0) or 0.0)
        z += weight * ((value - mean) / scale)
        model_features[str(name)] = {
            "value": value,
            "raw": raw,
            "imputed": raw is None,
        }
    probability = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, z))))
    gate = artifact.get("noPlayGate") or {}
    threshold = float(
        _number(gate.get("threshold"), _number(gate.get("selectedThreshold"), 0.70))
        or 0.70
    )
    selected = max(probability, 1.0 - probability) >= threshold
    side = "home" if probability >= 0.5 else "away"
    return {
        "homeWinProbability": round(probability, 8),
        "selectedSide": side,
        "selectedProbability": round(max(probability, 1.0 - probability), 8),
        "selected": selected,
        "threshold": threshold,
        "modelKind": model.get("kind") or (artifact.get("candidateSelection") or {}).get("selectedKind") or "signal_only",
        "modelFeatures": model_features,
    }


def _build_signals(
    prelock_pulls: Sequence[Dict[str, Any]],
    latest_game: Dict[str, Any],
) -> Dict[str, Any]:
    old_series = legacy_v10._series_for_game(list(prelock_pulls), latest_game)
    current_series = current_engine._series_for_game(list(prelock_pulls), latest_game)
    if not old_series or not current_series:
        raise RecoveryShadowError("pregame signal series is incomplete")
    v10 = _old_pick(legacy_v10, old_series)
    v11 = _old_pick(legacy_v11, old_series)
    current = _current_pick(current_series)
    market = _market_pick(current_series)
    line = _line_pick(old_series)
    votes = [market["side"], current["side"], v10["side"], v11["side"], line["side"]]
    home_votes = sum(side == "home" for side in votes)
    signal_features = {
        "marketHomeProbability": market["homeProbability"],
        "currentHomeProbability": current["homeProbability"],
        "v10HomeProbability": v10["homeProbability"],
        "v11HomeProbability": v11["homeProbability"],
        "lineMovementHomeProbability": line["homeProbability"],
        "homeVoteFraction": home_votes / len(votes),
        "pullDepthLog": math.log1p(len(old_series)),
    }
    return {
        "features": signal_features,
        "components": {
            "market": market,
            "current": current,
            "v10": v10,
            "v11": v11,
            "lineMovement": line,
            "votes": {"home": home_votes, "away": len(votes) - home_votes},
        },
        "oldSeriesCount": len(old_series),
        "currentSeriesCount": len(current_series),
        "sourcePullAtUtc": old_series[-1].get("pulled_at"),
    }


def _selection_key(slate_date: str, game_identity: str) -> Dict[str, str]:
    return {
        "PK": f"MLB_RECOVERY_SHADOW#{slate_date}",
        "SK": f"SELECTION#{game_identity}",
    }


def _grade_key(slate_date: str, game_identity: str) -> Dict[str, str]:
    return {
        "PK": f"MLB_RECOVERY_SHADOW#{slate_date}",
        "SK": f"GRADE#{game_identity}",
    }


def _put_once(table: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    try:
        table.put_item(
            Item=history.ddb_safe(item),
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return {"created": True, "pk": item["PK"], "sk": item["SK"]}
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code != "ConditionalCheckFailedException":
            raise
        existing = table.get_item(
            Key={"PK": item["PK"], "SK": item["SK"]},
            ConsistentRead=True,
        ).get("Item")
        return {
            "created": False,
            "pk": item["PK"],
            "sk": item["SK"],
            "existingFingerprint": str((existing or {}).get("selection_fingerprint") or (existing or {}).get("grade_fingerprint") or ""),
        }


def capture(
    slate_date: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
    table: Any = None,
    artifact: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    observed_at = (now or _now_utc()).astimezone(timezone.utc)
    slate = slate_date or observed_at.astimezone(ET).date().isoformat()
    candidate = validate_candidate_artifact(artifact) if artifact is not None else load_candidate_artifact()
    table = table or _table()
    pulls = history.canonicalize_pull_slots(history.query_pulls("mlb", slate, 500), sport="mlb", slate=slate)
    if not pulls:
        return {
            "ok": True,
            "status": "NO_CANONICAL_PULLS",
            "version": VERSION,
            "slateDateEt": slate,
            "capturedCount": 0,
            "productionAuthority": False,
        }
    latest_pull = pulls[-1]
    games = [dict(game) for game in (latest_pull.get("games") or []) if isinstance(game, dict) and _slate_game(game, slate)]
    schedule = _schedule(slate)
    stats_cache: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], Dict[str, Any]] = {}
    captured: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for game in games:
        identity = _game_identity(game)
        start = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
        if not identity or start is None:
            skipped.append({"gameIdentity": identity or None, "reason": "GAME_IDENTITY_OR_TIME_INVALID"})
            continue
        cutoff = start - timedelta(minutes=LOCK_MINUTES_BEFORE_GAME)
        prelock = _prelock_pulls(pulls, cutoff)
        source_pull_at = _parse_dt(prelock[-1].get("pulled_at")) if prelock else None
        disposition = capture_disposition(start, observed_at, source_pull_at, len(prelock))
        if disposition.get("eligible") is not True:
            skipped.append({"gameIdentity": identity, **disposition})
            continue
        try:
            signals = _build_signals(prelock, game)
            official_pk = _official_game_pk(game)
            fundamentals = prospective_fundamentals(schedule.get(official_pk), stats_cache)
            scored = score_candidate(candidate, signals["features"], fundamentals)
            selected_side = scored["selectedSide"]
            predicted_winner = game.get("home_team") if selected_side == "home" else game.get("away_team")
            market_side = signals["components"]["market"]["side"]
            market_winner = game.get("home_team") if market_side == "home" else game.get("away_team")
            key = _selection_key(slate, identity)
            item: Dict[str, Any] = {
                **key,
                "record_type": SELECTION_RECORD_TYPE,
                "version": VERSION,
                "sport": "mlb",
                "slate_date_et": slate,
                "game_identity": identity,
                "official_game_pk": official_pk or None,
                "home_team": game.get("home_team"),
                "away_team": game.get("away_team"),
                "commence_time": start.isoformat(),
                "cutoff_at_utc": cutoff.isoformat(),
                "selection_created_at_utc": observed_at.isoformat(),
                "source_pull_at_utc": signals.get("sourcePullAtUtc"),
                "source_pull_count": len(prelock),
                "candidate_artifact_digest": candidate.get("artifactDigest"),
                "candidate_artifact_key": (candidate.get("loadedFrom") or {}).get("artifactKey"),
                "candidate_model_kind": scored.get("modelKind"),
                "predicted_side": selected_side,
                "predicted_winner": predicted_winner,
                "home_win_probability": scored.get("homeWinProbability"),
                "selected_probability": scored.get("selectedProbability"),
                "playable_selected": scored.get("selected") is True,
                "playable_threshold": scored.get("threshold"),
                "market_baseline_side": market_side,
                "market_baseline_winner": market_winner,
                "market_home_probability": signals["features"].get("marketHomeProbability"),
                "signal_features": signals.get("features"),
                "signal_components": signals.get("components"),
                "standard_fundamentals": fundamentals,
                "model_features": scored.get("modelFeatures"),
                "shadow_only": True,
                "production_authority": False,
                "official_pick_override_allowed": False,
                "outcome_attached": False,
                "prospective_capture": True,
                "selection_write_once": True,
                "capture_disposition": disposition,
            }
            item["selection_fingerprint"] = _fingerprint(item)
            write = _put_once(table, item)
            captured.append(
                {
                    "gameIdentity": identity,
                    "predictedWinner": predicted_winner,
                    "selectedProbability": scored.get("selectedProbability"),
                    "playableSelected": scored.get("selected"),
                    "write": write,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "gameIdentity": identity,
                    "type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    if errors:
        raise RecoveryShadowError("recovery shadow capture failed: " + json.dumps(errors, sort_keys=True))
    return {
        "ok": True,
        "status": "PROSPECTIVE_RECOVERY_CAPTURE_COMPLETE",
        "version": VERSION,
        "slateDateEt": slate,
        "candidateArtifactDigest": candidate.get("artifactDigest"),
        "officialGameCount": len(games),
        "capturedCount": len(captured),
        "selectedCount": sum(row.get("playableSelected") is True for row in captured),
        "skippedCount": len(skipped),
        "captured": captured,
        "skipped": skipped,
        "shadowOnly": True,
        "productionAuthority": False,
        "officialPickOverrideAllowed": False,
    }


def _finals(slate_date: str) -> Dict[str, Dict[str, Any]]:
    payload = _http_json(
        "/v1/schedule",
        {"sportId": 1, "date": slate_date, "hydrate": "team,linescore"},
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for date_row in payload.get("dates") or []:
        for game in date_row.get("games") or []:
            if not isinstance(game, dict) or str(game.get("gameType") or "") != "R":
                continue
            status = game.get("status") or {}
            if str(status.get("abstractGameState") or "").lower() != "final":
                continue
            home = (game.get("teams") or {}).get("home") or {}
            away = (game.get("teams") or {}).get("away") or {}
            try:
                home_score = int(home.get("score"))
                away_score = int(away.get("score"))
            except Exception:
                continue
            if home_score == away_score:
                continue
            home_team = str((home.get("team") or {}).get("name") or "")
            away_team = str((away.get("team") or {}).get("name") or "")
            rows[str(game.get("gamePk") or "")] = {
                "officialGamePk": str(game.get("gamePk") or ""),
                "homeTeam": home_team,
                "awayTeam": away_team,
                "homeScore": home_score,
                "awayScore": away_score,
                "winner": home_team if home_score > away_score else away_team,
                "finalStatus": status.get("detailedState"),
            }
    return rows


def _plain(value: Any) -> Any:
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _query_partition(table: Any, slate_date: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start_key = None
    while True:
        kwargs: Dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(f"MLB_RECOVERY_SHADOW#{slate_date}"),
            "ConsistentRead": True,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        response = table.query(**kwargs)
        rows.extend(_plain(item) for item in (response.get("Items") or []))
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return rows


def grade(
    slate_date: str,
    *,
    table: Any = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    table = table or _table()
    observed_at = (now or _now_utc()).astimezone(timezone.utc)
    items = _query_partition(table, slate_date)
    selections = [item for item in items if item.get("record_type") == SELECTION_RECORD_TYPE]
    existing_grades = {
        str(item.get("game_identity")): item
        for item in items
        if item.get("record_type") == GRADE_RECORD_TYPE
    }
    finals = _finals(slate_date)
    created: List[Dict[str, Any]] = []
    pending: List[str] = []
    for selection in selections:
        identity = str(selection.get("game_identity") or "")
        if identity in existing_grades:
            continue
        official_pk = str(selection.get("official_game_pk") or "")
        result = finals.get(official_pk)
        if not result:
            pending.append(identity)
            continue
        winner = str(result.get("winner") or "")
        predicted = str(selection.get("predicted_winner") or "")
        market = str(selection.get("market_baseline_winner") or "")
        key = _grade_key(slate_date, identity)
        grade_item: Dict[str, Any] = {
            **key,
            "record_type": GRADE_RECORD_TYPE,
            "version": VERSION,
            "sport": "mlb",
            "slate_date_et": slate_date,
            "game_identity": identity,
            "official_game_pk": official_pk,
            "selection_fingerprint": selection.get("selection_fingerprint"),
            "candidate_artifact_digest": selection.get("candidate_artifact_digest"),
            "predicted_winner": predicted,
            "market_baseline_winner": market,
            "winner": winner,
            "correct": predicted == winner,
            "market_correct": market == winner,
            "playable_selected": selection.get("playable_selected") is True,
            "selected_probability": selection.get("selected_probability"),
            "home_win_probability": selection.get("home_win_probability"),
            "home_score": result.get("homeScore"),
            "away_score": result.get("awayScore"),
            "graded_at_utc": observed_at.isoformat(),
            "outcome_source": "MLB_STATS_API_FINAL",
            "selection_mutated": False,
            "shadow_only": True,
            "production_authority": False,
        }
        grade_item["grade_fingerprint"] = _fingerprint(grade_item)
        write = _put_once(table, grade_item)
        created.append({"gameIdentity": identity, "correct": grade_item["correct"], "write": write})
    all_grades = list(existing_grades.values()) + [
        {
            "correct": row["correct"],
            "market_correct": next(
                (
                    (finals.get(str(sel.get("official_game_pk") or "")) or {}).get("winner")
                    == str(sel.get("market_baseline_winner") or "")
                )
                for sel in selections
                if str(sel.get("game_identity") or "") == row["gameIdentity"]
            ),
            "playable_selected": next(
                (sel.get("playable_selected") is True for sel in selections if str(sel.get("game_identity") or "") == row["gameIdentity"]),
                False,
            ),
        }
        for row in created
    ]
    graded = [row for row in all_grades if row.get("correct") in {True, False}]
    selected = [row for row in graded if row.get("playable_selected") is True]
    correct = sum(row.get("correct") is True for row in graded)
    selected_correct = sum(row.get("correct") is True for row in selected)
    market_correct = sum(row.get("market_correct") is True for row in graded)
    return {
        "ok": True,
        "status": "RECOVERY_SHADOW_GRADE_COMPLETE",
        "version": VERSION,
        "slateDateEt": slate_date,
        "selectionCount": len(selections),
        "createdGradeCount": len(created),
        "pendingFinalCount": len(pending),
        "gradedCount": len(graded),
        "correctCount": correct,
        "accuracyPct": round(100.0 * correct / len(graded), 2) if graded else None,
        "marketCorrectCount": market_correct,
        "marketAccuracyPct": round(100.0 * market_correct / len(graded), 2) if graded else None,
        "selectedCount": len(selected),
        "selectedCorrectCount": selected_correct,
        "selectedAccuracyPct": round(100.0 * selected_correct / len(selected), 2) if selected else None,
        "shadowOnly": True,
        "productionAuthority": False,
    }


def grade_recent(days: int = 3, *, table: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    observed_at = (now or _now_utc()).astimezone(timezone.utc)
    table = table or _table()
    results = []
    for offset in range(1, max(1, days) + 1):
        slate = (observed_at.astimezone(ET).date() - timedelta(days=offset)).isoformat()
        results.append(grade(slate, table=table, now=observed_at))
    return {
        "ok": all(row.get("ok") is True for row in results),
        "version": VERSION,
        "status": "RECOVERY_SHADOW_RECENT_GRADES_COMPLETE",
        "results": results,
        "shadowOnly": True,
        "productionAuthority": False,
    }


def status(slate_date: Optional[str] = None, *, table: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    observed_at = (now or _now_utc()).astimezone(timezone.utc)
    slate = slate_date or observed_at.astimezone(ET).date().isoformat()
    table = table or _table()
    items = _query_partition(table, slate)
    selections = [item for item in items if item.get("record_type") == SELECTION_RECORD_TYPE]
    grades = [item for item in items if item.get("record_type") == GRADE_RECORD_TYPE]
    return {
        "ok": True,
        "version": VERSION,
        "status": "RECOVERY_SHADOW_STATUS",
        "slateDateEt": slate,
        "selectionCount": len(selections),
        "selectedCount": sum(item.get("playable_selected") is True for item in selections),
        "gradeCount": len(grades),
        "shadowOnly": True,
        "productionAuthority": False,
        "officialPredictionRowsWritten": 0,
    }


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    request = event if isinstance(event, dict) else {}
    mode = str(request.get("mode") or "capture").strip().lower()
    if mode == "capture":
        return capture(request.get("slateDateEt"))
    if mode == "grade":
        slate = request.get("slateDateEt")
        return grade(str(slate), now=_now_utc()) if slate else grade_recent(int(request.get("days") or 3))
    if mode == "status":
        return status(request.get("slateDateEt"))
    raise RecoveryShadowError(f"unsupported recovery shadow mode: {mode}")
