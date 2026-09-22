"""Pregame KS1 counter-signal diagnostics from already frozen point-in-time evidence.

This module is deliberately diagnostic-only. It never changes p_home, the selected
side, calibration, model identity, serving authority, or T-10 preservation. Its
purpose is to surface the exact classes of counter-signals identified in the
2026-09-15 loss-forensics review so they are visible on the next card and can be
promoted only through the normal chronological qualification path.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

CONTRACT = "KS1-forensic-consideration-v1"
csv.field_size_limit(min(sys.maxsize, 10_000_000))


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _profile(value):
    if not value or str(value).lower() in {"nan", "none"}:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _delta(metrics, short, long):
    a, b = _num(metrics.get(short)), _num(metrics.get(long))
    return None if a is None or b is None else a - b


def _append(flags, code, side, strength, evidence):
    flags.append({"code": code, "side": side, "strength": strength, "evidence": evidence})


def evaluate(row):
    """Return counter-signals without changing the frozen KS1 prediction."""
    p_home = _num(row.get("p_home"))
    market_home = _num(row.get("market_home_prob"))
    if p_home is None:
        raise ValueError("p_home required")
    selected = "home" if p_home >= 0.5 else "away"
    opponent = "away" if selected == "home" else "home"
    selected_probability = p_home if selected == "home" else 1.0 - p_home
    flags = []

    if market_home is not None:
        market_selected = market_home if selected == "home" else 1.0 - market_home
        disagreement = abs(p_home - market_home)
        if market_selected < 0.5 and disagreement >= 0.05:
            _append(flags, "MARKET_FAVORS_OPPOSITE_SIDE", opponent, "high" if disagreement >= 0.10 else "medium",
                    {"model_selected_probability": selected_probability,
                     "market_selected_probability": market_selected,
                     "model_market_gap_pp": round(disagreement * 100, 2)})
        elif disagreement >= 0.08:
            _append(flags, "LARGE_MODEL_MARKET_DISAGREEMENT", opponent if market_selected < selected_probability else selected,
                    "medium", {"model_market_gap_pp": round(disagreement * 100, 2)})

    starter = _profile(row.get("starter_profile_json"))
    starter_sides = starter.get("sides", {}) if isinstance(starter.get("sides"), dict) else {}
    starter_summary = {}
    for side in ("home", "away"):
        metrics = ((starter_sides.get(side) or {}).get("metrics") or {})
        era_delta = _delta(metrics, "era_7d", "era_30d")
        fip_delta = _delta(metrics, "fip_7d", "fip_30d")
        xwoba_delta = _delta(metrics, "xwoba_7d", "xwoba_30d")
        expected_ip = _num(metrics.get("expected_innings_last5"))
        starter_summary[side] = {"era_7d_minus_30d": era_delta,
                                 "fip_7d_minus_30d": fip_delta,
                                 "xwoba_7d_minus_30d": xwoba_delta,
                                 "expected_innings_last5": expected_ip}
        deterioration = ((era_delta is not None and era_delta >= 2.0)
                         or (fip_delta is not None and fip_delta >= 1.5)
                         or (xwoba_delta is not None and xwoba_delta >= 0.040))
        improvement = ((era_delta is not None and era_delta <= -2.0)
                       or (fip_delta is not None and fip_delta <= -1.5)
                       or (xwoba_delta is not None and xwoba_delta <= -0.040))
        if side == selected and deterioration:
            _append(flags, "SELECTED_STARTER_RECENT_DETERIORATION", opponent, "medium", starter_summary[side])
        if side == opponent and improvement:
            _append(flags, "OPPONENT_STARTER_RECENT_IMPROVEMENT", opponent, "medium", starter_summary[side])
        if side == selected and expected_ip is not None and expected_ip <= 3.0:
            _append(flags, "SELECTED_STARTER_LOW_EXPECTED_IP", opponent, "medium",
                    {"expected_innings_last5": expected_ip})

    context = _profile(row.get("lineup_bullpen_profile_json"))
    sides = context.get("sides", {}) if isinstance(context.get("sides"), dict) else {}
    sf = ((sides.get(selected) or {}).get("features") or {})
    of = ((sides.get(opponent) or {}).get("features") or {})

    comparisons = [
        ("lineup_ops_7d", 0.080, "higher", "OPPONENT_LINEUP_OPS_ADVANTAGE"),
        ("lineup_xwoba_7d", 0.030, "higher", "OPPONENT_LINEUP_XWOBA_ADVANTAGE"),
        ("lineup_top4_ops", 0.080, "higher", "OPPONENT_TOP4_OPS_ADVANTAGE"),
        ("bullpen_context_fip_7d", 1.000, "lower", "OPPONENT_BULLPEN_FIP_ADVANTAGE"),
        ("bullpen_context_era_7d", 1.500, "lower", "OPPONENT_BULLPEN_ERA_ADVANTAGE"),
        ("bullpen_context_available_count", 2.000, "higher", "OPPONENT_BULLPEN_DEPTH_ADVANTAGE"),
    ]
    for key, threshold, direction, code in comparisons:
        selected_value, opponent_value = _num(sf.get(key)), _num(of.get(key))
        if selected_value is None or opponent_value is None:
            continue
        gap = opponent_value - selected_value
        qualifies = gap >= threshold if direction == "higher" else gap <= -threshold
        if qualifies:
            _append(flags, code, opponent, "medium",
                    {"selected": selected_value, "opponent": opponent_value, "opponent_minus_selected": gap})

    weights = {"high": 2, "medium": 1, "low": 0}
    points = sum(weights.get(flag["strength"], 0) for flag in flags)
    # A high counter-signal plus any corroborating medium signal is high visibility.
    # Three independent medium signals are likewise enough to demand review.
    severity = "high" if points >= 3 else "medium" if points >= 2 else "watch" if points else "none"
    return {
        "contract": CONTRACT,
        "date": row.get("date"),
        "game_id": str(row.get("game_id")),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "as_of": row.get("as_of"),
        "commence_time": row.get("commence_time"),
        "selected_side": selected,
        "selected_team": row.get(selected + "_team"),
        "model_selected_probability": selected_probability,
        "severity": severity,
        "counter_signal_points": points,
        "flags": flags,
        "starter_regime_summary": starter_summary,
        "authority_effect": "diagnostic_only_no_pick_probability_calibration_or_lock_change",
    }


def build(predictions_csv):
    path = Path(predictions_csv)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    results = [evaluate(row) for row in rows]
    # Add a reporting view without changing detector decisions or frozen rows.
    # In particular, flag.side is a direction, not the observed pitcher's side.
    from ks1.reporting_evidence import report_evidence
    odds_path = path.parent / "odds_cache.parquet"
    odds_rows, odds_error = [], None
    if odds_path.exists():
        try:
            import pyarrow.parquet as pq
            odds_rows = pq.read_table(odds_path).to_pylist()
        except (ImportError, OSError, ValueError) as exc:
            odds_error = type(exc).__name__
    for row, result in zip(rows, results):
        result["reporting_evidence"] = report_evidence(row, result, odds_rows)
        if odds_error:
            result["reporting_evidence"]["moneylines"]["cache_read_error"] = odds_error
    return {
        "contract": CONTRACT,
        "source": str(path),
        "rows": len(results),
        "flagged_games": sum(item["severity"] != "none" for item in results),
        "high_games": sum(item["severity"] == "high" for item in results),
        "authority_effect": "none",
        "games": results,
        "candidate_training_features": [
            "starter_era_7d_minus_30d", "starter_fip_7d_minus_30d", "starter_xwoba_7d_minus_30d",
            "starter_expected_innings_last5_x_bullpen_expected_innings",
            "lineup_ops_7d_minus_30d", "lineup_xwoba_7d_minus_30d",
            "bullpen_fip_7d_minus_30d", "bullpen_era_7d_minus_30d",
            "bullpen_available_count", "model_market_probability_gap",
        ],
        "promotion_rule": "candidate only; normal 300-game chronological Brier/log-loss/provenance gates still apply",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="KS1 daily output root containing date=*/predictions.csv")
    args = parser.parse_args(argv)
    root = Path(args.root)
    targets = sorted(root.glob("date=*/predictions.csv"))
    if not targets:
        raise SystemExit("no KS1 predictions.csv found")
    for source in targets:
        payload = build(source)
        destination = source.parent / "forensic_consideration.json"
        destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(destination), "rows": payload["rows"],
                          "flagged_games": payload["flagged_games"], "high_games": payload["high_games"]}))


if __name__ == "__main__":
    main()
