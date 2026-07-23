from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional


VERSION = "MLB-REVERSAL-SIMILARITY-v2-size-time-path-agreement"


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _bucket(value: Optional[float], boundaries: tuple[float, ...], labels: tuple[str, ...]) -> str:
    if value is None:
        return "missing"
    for boundary, label in zip(boundaries, labels):
        if value < boundary:
            return label
    return labels[-1]


def _full(selected: Dict[str, Any]) -> Dict[str, Any]:
    temporal = selected.get("temporalFeatures") or {}
    horizons = temporal.get("horizons") if isinstance(temporal, dict) else {}
    value = (horizons or {}).get("full") or {}
    return value if isinstance(value, dict) else {}


def signature_payload(selected: Dict[str, Any]) -> Dict[str, Any]:
    full = _full(selected)
    market = full.get("market") or {}
    quality = full.get("signalQuality") or {}
    latest_leg = full.get("latestLeg") or {}
    previous_leg = full.get("previousLeg") or {}
    latest_flip = full.get("latestReversalMarketFlip") or {}
    reversal_count = int(_f(full.get("reversalCount"), _f(selected.get("reversalCount"), 0.0)) or 0.0)
    latest_amplitude = _f(latest_leg.get("amplitudePp"))
    recovery = _f(full.get("latestReversalRecoveryRatio"))
    minutes_before_event = _f(full.get("latestReversalMinutesBeforeEvent"))
    agreement = _f(market.get("weightedBookDirectionAgreement"))
    path_efficiency = _f(full.get("pathEfficiency"))
    signal_quality = _f(quality.get("signalQualityIndex"), _f((selected.get("temporalFeatures") or {}).get("signalQualityIndex")))
    latest_range = _f(market.get("latestBookRangePp"))
    market_flip_count = int(_f(full.get("reversalMarketFlipCount"), 0.0) or 0.0)
    return {
        "version": VERSION,
        "reversalCountBand": "0" if reversal_count == 0 else "1" if reversal_count == 1 else "2" if reversal_count == 2 else "3+",
        "latestLegAmplitudeBandPp": _bucket(
            latest_amplitude,
            (0.5, 1.0, 2.0, 3.0, 5.0, float("inf")),
            ("<0.5", "0.5-1", "1-2", "2-3", "3-5", "5+"),
        ),
        "previousLegAmplitudeBandPp": _bucket(
            _f(previous_leg.get("amplitudePp")),
            (0.5, 1.0, 2.0, 3.0, 5.0, float("inf")),
            ("<0.5", "0.5-1", "1-2", "2-3", "3-5", "5+"),
        ),
        "recoveryRatioBand": _bucket(
            recovery,
            (0.5, 1.0, 2.0, float("inf")),
            ("<0.5", "0.5-1", "1-2", "2+"),
        ),
        "reversalTimingBandMinutesBeforeEvent": _bucket(
            minutes_before_event,
            (180.0, 360.0, 600.0, float("inf")),
            ("<=180", "180-360", "360-600", "600+"),
        ),
        "pathEfficiencyBand": _bucket(
            path_efficiency,
            (0.5, 0.8, float("inf")),
            ("<0.5", "0.5-0.8", "0.8+"),
        ),
        "bookAgreementBand": _bucket(
            agreement,
            (0.5, 0.75, float("inf")),
            ("<0.5", "0.5-0.75", "0.75+"),
        ),
        "bookRangeBandPp": _bucket(
            latest_range,
            (1.5, 3.5, float("inf")),
            ("<1.5", "1.5-3.5", "3.5+"),
        ),
        "signalQualityBand": _bucket(
            signal_quality,
            (40.0, 55.0, 75.0, float("inf")),
            ("<40", "40-55", "55-75", "75+"),
        ),
        "marketFlip": market_flip_count > 0,
        "latestMarketFlipAmplitudeBandPp": _bucket(
            _f(latest_flip.get("amplitudePp")),
            (1.0, 2.0, 3.0, 5.0, float("inf")),
            ("<1", "1-2", "2-3", "3-5", "5+"),
        ),
        "latestLegDirection": int(_f(latest_leg.get("direction"), 0.0) or 0.0),
    }


def signature(selected: Dict[str, Any]) -> str:
    payload = signature_payload(selected)
    source = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def analyze(selected: Dict[str, Any]) -> Dict[str, Any]:
    selected = selected if isinstance(selected, dict) else {}
    full = _full(selected)
    market = full.get("market") or {}
    quality = full.get("signalQuality") or {}
    latest_leg = full.get("latestLeg") or {}
    latest_flip = full.get("latestReversalMarketFlip") or {}
    payload = signature_payload(selected)
    reversal_count = int(_f(full.get("reversalCount"), _f(selected.get("reversalCount"), 0.0)) or 0.0)
    minutes_before_event = _f(full.get("latestReversalMinutesBeforeEvent"))
    latest_direction = int(_f(latest_leg.get("direction"), 0.0) or 0.0)
    path_efficiency = _f(full.get("pathEfficiency"), 0.0) or 0.0
    agreement = _f(market.get("weightedBookDirectionAgreement"), 0.0) or 0.0
    latest_range = _f(market.get("latestBookRangePp"), _f(selected.get("bookDivergence"), 0.0)) or 0.0
    if latest_range <= 1.0 and _f(selected.get("bookDivergence")) is not None:
        latest_range *= 100.0
    market_flip_count = int(_f(full.get("reversalMarketFlipCount"), 0.0) or 0.0)
    flip_amplitude = _f(latest_flip.get("amplitudePp"))

    risk_flags = []
    research_candidates = []
    if reversal_count > 0:
        risk_flags.append("REVERSAL_BASE_RATE_UNPROVEN")
    if (
        latest_direction > 0
        and minutes_before_event is not None
        and 45.0 <= minutes_before_event <= 180.0
    ):
        risk_flags.append("LATE_REVERSAL_DIRECTION_RISK")
    if reversal_count >= 2 and path_efficiency < 0.65:
        risk_flags.append("MULTI_REVERSAL_PATH_NOISE")
    if agreement < 0.5 and int(_f(market.get("eligibleBookCount"), 0.0) or 0.0) >= 2:
        risk_flags.append("LOW_MULTI_BOOK_DIRECTION_AGREEMENT")
    if latest_range >= 3.5:
        risk_flags.append("HIGH_BOOK_DISPERSION")
    if market_flip_count > 0:
        research_candidates.append("MARKET_FLIP_REVERSAL_CANDIDATE")
    if market_flip_count > 0 and flip_amplitude is not None and 2.0 <= flip_amplitude < 3.0:
        research_candidates.append("POSTHOC_MARKET_FLIP_2_TO_3PP_CANDIDATE")

    return {
        "version": VERSION,
        "signature": signature(selected),
        "signaturePayload": payload,
        "riskFlags": sorted(set(risk_flags)),
        "researchCandidates": sorted(set(research_candidates)),
        "signalQualityIndex": _f(quality.get("signalQualityIndex"), 0.0) or 0.0,
        "positiveProductionAuthority": False,
        "riskOnlyUntilProspectiveValidation": True,
        "postHocCandidatesCannotQualifyPicks": True,
        "policy": (
            "Similarity groups are deterministic descriptions of reversal count, size, timing, path, book agreement, "
            "dispersion and market crossing. They may block or stratify picks but cannot add positive authority until "
            "their exact frozen signature passes prospective precision admission."
        ),
    }
