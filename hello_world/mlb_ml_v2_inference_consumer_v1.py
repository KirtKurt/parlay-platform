from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
import time
from typing import Any, Callable, Dict, Mapping, Optional, Tuple


VERSION = "MLB-ML-V2-INFERENCE-CONSUMER-v1-gated-active-champion"
CHAMPION_PK = "MLB_ML_CHAMPION#V2"
CHAMPION_SK = "ACTIVE"
_INSTALL_FLAG = "_INQSI_MLB_V2_INFERENCE_CONSUMER_V1"
_CACHE_TTL_SECONDS = 300
_CACHE: Dict[str, Any] = {"loadedAt": 0.0, "key": None, "champion": None, "status": None}


class InferenceConsumerError(RuntimeError):
    pass


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def enabled() -> bool:
    return _truthy(os.environ.get("INQSI_MLB_V2_INFERENCE_ENABLED", "false"))


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


def _sha256(value: Any) -> str:
    encoded = json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deployment_identity() -> Dict[str, str]:
    return {
        "gitSha": str(os.environ.get("INQSI_DEPLOY_GIT_SHA") or ""),
        "templateSha256": str(
            os.environ.get("INQSI_DEPLOY_TEMPLATE_SHA256") or ""
        ),
    }


def contract_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "installed": True,
        "enabled": enabled(),
        "version": VERSION,
        "championAuthority": "DynamoDB exact active V2 champion",
        "deploymentIdentityRequired": True,
        "frozenChallengerChecksumRequired": True,
        "directionCanChangeOnlyAfterPromotionGate": True,
        "playabilityCanOnlyBecomeCandidate": True,
        "deterministicRiskGatesStillRequired": True,
        "automaticWagerAllowed": False,
    }


def _validate_champion(champion: Any) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if not enabled():
        return None, {
            "ok": True,
            "status": "DISABLED",
            "version": VERSION,
        }
    if not isinstance(champion, Mapping):
        return None, {
            "ok": True,
            "status": "NO_ACTIVE_CHAMPION",
            "version": VERSION,
        }
    value = _plain(dict(champion))
    errors = []
    if value.get("recordType") != "mlb_ml_active_champion_v2":
        errors.append("champion_record_type_mismatch")
    if value.get("runtimeAuthorityActivated") is not True:
        errors.append("runtime_authority_not_activated")
    if value.get("stableChampion") is not True:
        errors.append("stable_champion_not_approved")
    if value.get("shadowOnly") is not False:
        errors.append("champion_is_shadow_only")
    if not (
        value.get("directionAuthorityEnabled") is True
        or value.get("playabilityAuthorityEnabled") is True
    ):
        errors.append("no_runtime_authority_enabled")
    if value.get("automaticWagerAllowed") is not False:
        errors.append("automatic_wager_contract_invalid")

    actual_identity = value.get("deploymentIdentity") or {}
    expected_identity = _deployment_identity()
    if not expected_identity["gitSha"] or not expected_identity["templateSha256"]:
        errors.append("runtime_deployment_identity_missing")
    elif actual_identity != expected_identity:
        errors.append("champion_runtime_deployment_identity_mismatch")

    challenger = value.get("frozenChallenger")
    if not isinstance(challenger, Mapping) or challenger.get("ok") is not True:
        errors.append("frozen_challenger_missing_or_invalid")
    else:
        expected_sha = str(value.get("frozenChallengerSha256") or "")
        if not expected_sha or _sha256(challenger) != expected_sha:
            errors.append("frozen_challenger_checksum_mismatch")
        if challenger.get("thresholdSelectionSource") != (
            "validation_only_before_prospective_cutover"
        ):
            errors.append("challenger_threshold_source_invalid")
        if float(challenger.get("selectedThreshold") or 0.0) <= 0.0:
            errors.append("challenger_selected_threshold_invalid")

    gate = value.get("promotionGate") or {}
    if not isinstance(gate, Mapping) or gate.get("promotionEligible") is not True:
        errors.append("promotion_gate_not_passed")
    if gate.get("testWasUntouched") is not True:
        errors.append("prospective_test_not_untouched")
    if gate.get("calibrationAndProperScoringRequired") is not True:
        errors.append("calibration_gate_missing")

    if errors:
        return None, {
            "ok": False,
            "status": "CHAMPION_REJECTED",
            "version": VERSION,
            "errors": sorted(set(errors)),
            "artifactDigest": value.get("artifactDigest"),
        }
    return value, {
        "ok": True,
        "status": "ACTIVE_CHAMPION_READY",
        "version": VERSION,
        "artifactDigest": value.get("artifactDigest"),
        "directionAuthorityEnabled": value.get("directionAuthorityEnabled") is True,
        "playabilityAuthorityEnabled": value.get("playabilityAuthorityEnabled") is True,
        "deploymentIdentity": actual_identity,
    }


def _default_champion_loader() -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if not enabled():
        return _validate_champion(None)
    table_name = str(os.environ.get("SNAPSHOTS_TABLE") or "")
    if not table_name:
        return None, {
            "ok": False,
            "status": "SNAPSHOTS_TABLE_NOT_CONFIGURED",
            "version": VERSION,
        }
    cache_key = (
        table_name,
        _deployment_identity().get("gitSha"),
        _deployment_identity().get("templateSha256"),
    )
    now = time.monotonic()
    if (
        _CACHE.get("key") == cache_key
        and now - float(_CACHE.get("loadedAt") or 0.0) <= _CACHE_TTL_SECONDS
    ):
        return copy.deepcopy(_CACHE.get("champion")), copy.deepcopy(
            _CACHE.get("status") or {}
        )
    try:
        import boto3

        table = boto3.resource("dynamodb").Table(table_name)
        item = table.get_item(
            Key={"PK": CHAMPION_PK, "SK": CHAMPION_SK},
            ConsistentRead=True,
        ).get("Item")
        payload = item.get("data") if isinstance(item, Mapping) else None
        champion, status = _validate_champion(payload)
    except Exception as exc:
        champion, status = None, {
            "ok": False,
            "status": "CHAMPION_READ_FAILED",
            "version": VERSION,
            "errorType": type(exc).__name__,
        }
    _CACHE.update(
        {
            "key": cache_key,
            "loadedAt": now,
            "champion": copy.deepcopy(champion),
            "status": copy.deepcopy(status),
        }
    )
    return champion, status


def _selected_signal(row: Mapping[str, Any], side: str) -> Dict[str, Any]:
    value = row.get("homeSignal" if side == "home" else "awaySignal")
    return copy.deepcopy(value) if isinstance(value, Mapping) else {}


def load_active_champion() -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Return the exact validated active champion and a public-safe status."""
    return _default_champion_loader()


def _sync_direction(
    row: Dict[str, Any],
    *,
    home_probability: float,
    reliability_probability: float,
    champion: Mapping[str, Any],
) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    home = str(out.get("homeTeam") or "")
    away = str(out.get("awayTeam") or "")
    if not home or not away:
        raise InferenceConsumerError("prediction matchup identity is incomplete")
    side = "home" if home_probability >= 0.5 else "away"
    winner = home if side == "home" else away
    opponent = away if side == "home" else home
    selected_probability = home_probability if side == "home" else 1.0 - home_probability
    signal = _selected_signal(out, side)
    fair_probability = float(signal.get("fairProbability") or 0.5)
    edge = selected_probability - fair_probability
    american_odds = signal.get("americanOdds")
    decimal_odds: Optional[float]
    try:
        price = float(american_odds)
        decimal_odds = 1.0 + (100.0 / abs(price)) if price < 0 else 1.0 + (price / 100.0)
    except Exception:
        decimal_odds = None
    expected_value = (
        selected_probability * decimal_odds - 1.0
        if decimal_odds is not None
        else None
    )
    challenger = champion.get("frozenChallenger") or {}
    selected_threshold = float(challenger.get("selectedThreshold") or 1.0)
    direction_changed = str(out.get("predictedWinner") or "") != winner
    out.update(
        {
            "predictedWinner": winner,
            "predictedSide": side,
            "opponent": opponent,
            "winProbability": round(selected_probability, 6),
            "winProbabilityPct": round(selected_probability * 100.0, 2),
            "teamWinProbabilityPct": round(selected_probability * 100.0, 2),
            "fairProbabilityPct": round(fair_probability * 100.0, 2),
            "edgeVsBook": round(edge, 6),
            "edgeVsBookPct": round(edge * 100.0, 2),
            "expectedValue": (
                round(expected_value, 6) if expected_value is not None else None
            ),
            "expectedValuePct": (
                round(expected_value * 100.0, 2)
                if expected_value is not None
                else None
            ),
            "americanOdds": signal.get("americanOdds"),
            "priceBook": signal.get("priceBook"),
            "priceSource": signal.get("priceSource"),
            "marketSide": signal.get("marketSide"),
            "v2OutcomeHomeProbability": round(home_probability, 6),
            "v2ReliabilityProbability": round(reliability_probability, 6),
            "v2SelectedReliabilityThreshold": selected_threshold,
            "v2ReliabilitySelected": reliability_probability >= selected_threshold,
            "v2PlayabilityCandidate": bool(
                champion.get("playabilityAuthorityEnabled") is True
                and reliability_probability >= selected_threshold
            ),
            "v2DirectionAuthorityApplied": True,
            "v2DirectionChanged": direction_changed,
            "v2ChampionArtifactDigest": champion.get("artifactDigest"),
            "v2InferenceConsumerVersion": VERSION,
            "probabilityAuthority": VERSION,
            # A promoted model may choose the winner, but the downstream signal
            # policy and official-quality gates still decide playability.
            "promoted": False,
            "promotionStatus": "V2_DIRECTION_REQUIRES_DOWNSTREAM_RISK_GATES",
            "pickQuality": "V2_MODEL_DIRECTION_NONPLAYABLE_PENDING_RISK_GATES",
            "automaticWagerAllowed": False,
        }
    )
    out["tags"] = sorted(
        {
            *(str(value) for value in (out.get("tags") or []) if str(value)),
            "MLB_V2_DIRECTION_AUTHORITY",
            *( ["MLB_V2_DIRECTION_CHANGED"] if direction_changed else [] ),
        }
    )
    return out


def apply_direction(
    engine: Any,
    *,
    champion_loader: Optional[
        Callable[[], Tuple[Optional[Dict[str, Any]], Dict[str, Any]]]
    ] = None,
) -> Any:
    """Install V2 direction authority before downstream integrity/risk wrappers."""

    if getattr(engine, _INSTALL_FLAG, False):
        return engine
    original = engine.predict_all
    loader = champion_loader or _default_champion_loader

    @functools.wraps(original)
    def predict_all(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = original(*args, **kwargs)
        if not isinstance(result, dict):
            return result
        champion, status = loader()
        out = copy.deepcopy(result)
        out["v2InferenceConsumer"] = copy.deepcopy(status)
        if not champion or champion.get("directionAuthorityEnabled") is not True:
            out["v2InferenceAuthorityAppliedCount"] = 0
            return out
        try:
            import mlb_ml_dual_model_v2 as dual_model
        except Exception as exc:
            out["v2InferenceConsumer"] = {
                "ok": False,
                "status": "MODEL_RUNTIME_IMPORT_FAILED",
                "version": VERSION,
                "errorType": type(exc).__name__,
            }
            out["v2InferenceAuthorityAppliedCount"] = 0
            return out

        predictions = []
        applied = 0
        rejected = []
        challenger = champion.get("frozenChallenger") or {}
        for row in out.get("predictions") or []:
            if not isinstance(row, dict):
                continue
            try:
                scored = dual_model.score_unlabeled_lock(row, challenger)
                predictions.append(
                    _sync_direction(
                        row,
                        home_probability=float(scored["outcomeProbability"]),
                        reliability_probability=float(
                            scored["reliabilityProbability"]
                        ),
                        champion=champion,
                    )
                )
                applied += 1
            except Exception as exc:
                preserved = copy.deepcopy(row)
                preserved["v2InferenceConsumerVersion"] = VERSION
                preserved["v2InferenceRejected"] = True
                preserved["v2InferenceRejectionType"] = type(exc).__name__
                predictions.append(preserved)
                rejected.append(
                    {
                        "gameId": row.get("gameId"),
                        "reason": type(exc).__name__,
                    }
                )
        out["predictions"] = predictions
        out["v2InferenceAuthorityAppliedCount"] = applied
        out["v2InferenceRejectedCount"] = len(rejected)
        out["v2InferenceRejections"] = rejected
        out["modelVersion"] = (
            f"{out.get('modelVersion') or 'MLB'}+{VERSION}"
            if applied
            else out.get("modelVersion")
        )
        return out

    engine.predict_all = predict_all
    engine.MLB_ML_V2_INFERENCE_CONSUMER_VERSION = VERSION
    engine.MLB_ML_V2_INFERENCE_CONSUMER_INSTALLED = True
    setattr(engine, _INSTALL_FLAG, True)
    return engine
