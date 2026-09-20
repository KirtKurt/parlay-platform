"""Development-only tracing of settled KS1 misses against immutable pregame signals.

The tracer joins the already-committed official grading ledger back to the exact locked
prediction rows in the same AWS capture. It excludes every game in the frozen 300-game
qualification holdout before diagnostics are evaluated. Results are descriptive evidence
for future chronological development only: no prediction, lock, calibration, model-ref,
qualification, or serving state is changed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
import json
import os
from pathlib import Path
from typing import Any

from ks1.calibration_store import checkpoint_prefix, commit_json, latest_checkpoint, read_json
from ks1.features import ET, utc
from ks1.forensic_consideration import evaluate
from ks1.inventory import encode
from ks1.platt_inputs import dataset

CONTRACT = "KS1-settled-loss-trace-v1"
HOLDOUT_PATH = Path(__file__).with_name("qualification_holdout_20260914.json")
FROZEN_GAME_IDS_SHA256 = "b13a31c7650e308b058f318648deb600edeeef4ebce2b053806cdc5b8b4fb87a"


def require_main_workflow() -> None:
    if not (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "KirtKurt/parlay-platform"
            and os.environ.get("GITHUB_REF") == "refs/heads/main"
            and os.environ.get("GITHUB_EVENT_NAME") in ("schedule", "push", "workflow_dispatch")
            and os.environ.get("GITHUB_WORKFLOW_REF") ==
                "KirtKurt/parlay-platform/.github/workflows/mlb-research-ingestion.yml@refs/heads/main"):
        raise ValueError("loss trace writes require the existing main research ingestion workflow")


def holdout_ids(path: Path = HOLDOUT_PATH) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = [str(value) for value in payload.get("game_ids", [])]
    if len(ids) != 300 or len(set(ids)) != 300:
        raise ValueError("frozen qualification holdout must contain exactly 300 unique game IDs")
    if __import__("hashlib").sha256(encode(ids)).hexdigest() != FROZEN_GAME_IDS_SHA256:
        raise ValueError("frozen qualification holdout game identities changed")
    return set(ids)


def _locked_index(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for entry in source.get("locked") or []:
        row = entry.get("row") or {}
        game_id = str(row.get("game_id") or "")
        if not game_id:
            raise ValueError("locked trace row missing game_id")
        if game_id in index:
            raise ValueError("duplicate locked game ID in loss trace capture")
        index[game_id] = entry
    return index


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value or str(value).lower() in {"nan", "none"}:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _contribution_summary(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: {
        "win_influence": [], "loss_influence": [], "win_score": [], "loss_score": []
    })
    for item in observations:
        outcome = "win" if item["selected_won"] else "loss"
        for name, metrics in item.get("contribution_groups", {}).items():
            influence = metrics.get("decision_influence_pct")
            score = metrics.get("signal_score")
            if isinstance(influence, (int, float)):
                groups[name][outcome + "_influence"].append(float(influence))
            if isinstance(score, (int, float)):
                groups[name][outcome + "_score"].append(float(score))

    def avg(values: list[float]):
        return sum(values) / len(values) if values else None

    output = []
    for name, values in groups.items():
        output.append({
            "group": name,
            "win_rows": len(values["win_influence"]),
            "loss_rows": len(values["loss_influence"]),
            "win_avg_decision_influence_pct": avg(values["win_influence"]),
            "loss_avg_decision_influence_pct": avg(values["loss_influence"]),
            "win_avg_signal_score": avg(values["win_score"]),
            "loss_avg_signal_score": avg(values["loss_score"]),
            "win_avg_abs_signal_score": avg([abs(v) for v in values["win_score"]]),
            "loss_avg_abs_signal_score": avg([abs(v) for v in values["loss_score"]]),
        })
    return sorted(output, key=lambda row: row["group"])


def _summary(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    wins = sum(item["selected_won"] for item in observations)
    losses = len(observations) - wins
    baseline_loss_rate = losses / len(observations) if observations else None

    flag_counts: dict[str, Counter] = defaultdict(Counter)
    pair_counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    severity_counts: dict[str, Counter] = defaultdict(Counter)
    for item in observations:
        outcome = "win" if item["selected_won"] else "loss"
        codes = sorted({flag["code"] for flag in item["flags"]})
        for code in codes:
            flag_counts[code][outcome] += 1
        for pair in combinations(codes, 2):
            pair_counts[pair][outcome] += 1
        severity_counts[item["severity"]][outcome] += 1

    def finish(name: Any, counts: Counter) -> dict[str, Any]:
        win_count, loss_count = counts["win"], counts["loss"]
        support = win_count + loss_count
        loss_rate = loss_count / support if support else None
        return {
            "pattern": name,
            "support": support,
            "wins": win_count,
            "losses": loss_count,
            "loss_rate": loss_rate,
            "loss_rate_lift_vs_sample": None if loss_rate is None or baseline_loss_rate is None else loss_rate - baseline_loss_rate,
        }

    flags = [finish(code, counts) for code, counts in flag_counts.items()]
    flags.sort(key=lambda row: (-row["support"], str(row["pattern"])))
    pairs = [finish(list(pair), counts) for pair, counts in pair_counts.items()]
    pairs.sort(key=lambda row: (-row["support"], str(row["pattern"])))
    severities = [finish(level, counts) for level, counts in severity_counts.items()]
    order = ("none", "watch", "medium", "high")
    severities.sort(key=lambda row: order.index(row["pattern"]) if row["pattern"] in order else 99)
    return flags, pairs, severities


def build(source: dict[str, Any], ledger: dict[str, Any], frozen_ids: set[str]) -> dict[str, Any]:
    if source.get("system") != "KS1" or source.get("errors"):
        raise ValueError("invalid KS1 source capture for loss tracing")
    if ledger.get("system") != "KS1":
        raise ValueError("invalid KS1 ledger for loss tracing")

    # Inspect IDs only until the frozen holdout is excluded. Neither dataset()
    # nor evaluate() may receive its labels, predictions, or first-seen records.
    grades = []
    seen_ids = set()
    holdout_excluded = 0
    outside_horizon = 0
    # Research ingestion retains the current and previous ET calendar years.
    # Older official grades remain in the ledger, but cannot be reproduced
    # from this capture. Do not silently waive missing finals inside the window.
    finals_start = datetime(utc(source['as_of']).astimezone(ET).year - 1, 1, 1, tzinfo=ET)
    for grade in ledger.get("rows") or []:
        game_id = str(grade.get("game_id") or "")
        if not game_id or game_id in seen_ids:
            raise ValueError("missing or duplicate game ID in loss trace ledger")
        seen_ids.add(game_id)
        if game_id in frozen_ids:
            holdout_excluded += 1
            continue
        if utc(grade['locked_at']) + timedelta(minutes=10) < finals_start:
            outside_horizon += 1
            continue
        grades.append(grade)
    trace_ids = {str(grade['game_id']) for grade in grades}
    first_seen = (source.get('platt_model') or {}).get('label_first_seen', {})
    trace_source = {
        'system': 'KS1', 'as_of': source['as_of'],
        'locked': [entry for entry in source.get('locked') or []
                   if str((entry.get('row') or {}).get('game_id') or '') in trace_ids],
        'finals': {gid: final for gid, final in (source.get('finals') or {}).items()
                   if str(gid) in trace_ids},
        'final_sources': source.get('final_sources'),
        'platt_model': {'label_first_seen': {gid: record for gid, record in first_seen.items()
                                           if str(gid) in trace_ids}},
    }
    # Re-run the unchanged prospective admission contract only on this scope.
    admitted, admission = dataset(trace_source, include_predecessors=True)
    admitted_by_id = {str(row["game_id"]): row for row in admitted}
    if len(admitted_by_id) != len(admitted):
        raise ValueError("duplicate admitted grade ID in loss trace")
    locked = _locked_index(trace_source)

    observations: list[dict[str, Any]] = []
    incomplete_signal_rows = Counter()
    for grade in grades:
        game_id = str(grade.get("game_id") or "")
        if not game_id or game_id not in admitted_by_id:
            raise ValueError("committed ledger row is not prospectively reproducible from capture")
        reproduced = admitted_by_id[game_id]
        final = (trace_source.get("finals") or {}).get(game_id)
        if (reproduced.get("signature") != grade.get("signature")
                or int(reproduced.get("home_win")) != int(grade.get("home_win"))
                or not isinstance(final, dict)
                or final.get("home_score") != grade.get("home_score")
                or final.get("away_score") != grade.get("away_score")
                or trace_source.get("final_sources") != grade.get("final_evidence")):
            raise ValueError(
                "committed grade or final evidence differs from prospectively reproduced observation"
            )
        entry = locked.get(game_id)
        if entry is None:
            raise ValueError("committed grade missing immutable locked prediction row")
        if entry.get("evidence") != grade.get("lock_evidence"):
            raise ValueError("loss trace row is not bound to the committed lock evidence")
        row = entry["row"]
        if (str(row.get("model_version")) != str(grade.get("raw_model_version"))
                or float(row.get("p_home")) != float(grade.get("p_home"))):
            raise ValueError("loss trace row differs from committed model/probability")
        diagnostic = evaluate(row)
        selected_home = float(row["p_home"]) >= 0.5
        selected_won = bool(selected_home == bool(grade["home_win"]))
        has_starter = bool(row.get("starter_profile_json"))
        has_lineup_bullpen = bool(row.get("lineup_bullpen_profile_json"))
        has_contributions = bool(row.get("signal_contributions_json"))
        if not has_starter:
            incomplete_signal_rows["starter_profile"] += 1
        if not has_lineup_bullpen:
            incomplete_signal_rows["lineup_bullpen_profile"] += 1
        if not has_contributions:
            incomplete_signal_rows["signal_contributions"] += 1
        contributions = _object(row.get("signal_contributions_json"))
        contribution_groups = {}
        for name, metrics in (contributions.get("groups") or {}).items():
            if not isinstance(metrics, dict):
                continue
            influence, score = metrics.get("decision_influence_pct"), metrics.get("signal_score")
            if isinstance(influence, (int, float)) or isinstance(score, (int, float)):
                contribution_groups[str(name)] = {
                    "decision_influence_pct": float(influence) if isinstance(influence, (int, float)) else None,
                    # Retained SHAP scores are home-log-odds oriented. Orient
                    # them to the selected side before comparing wins and losses.
                    "signal_score": (
                        float(score) * (1.0 if selected_home else -1.0)
                        if isinstance(score, (int, float)) else None
                    ),
                }
        observations.append({
            "game_id": game_id,
            "date": row.get("date"),
            "locked_at": grade.get("locked_at"),
            "raw_model_version": grade.get("raw_model_version"),
            "selected_team": diagnostic.get("selected_team"),
            "selected_probability": diagnostic.get("model_selected_probability"),
            "selected_won": selected_won,
            "severity": diagnostic.get("severity"),
            "counter_signal_points": diagnostic.get("counter_signal_points"),
            "flags": diagnostic.get("flags") or [],
            "contribution_groups": contribution_groups,
            "signal_surface": {
                "starter_profile": has_starter,
                "lineup_bullpen_profile": has_lineup_bullpen,
                "signal_contributions": has_contributions,
            },
            "lock_evidence": grade.get("lock_evidence"),
        })

    observations.sort(key=lambda row: (str(row.get("locked_at") or ""), row["game_id"]))
    wins = sum(item["selected_won"] for item in observations)
    losses = len(observations) - wins
    model_summaries = []
    for version in sorted({str(row["raw_model_version"]) for row in observations}):
        version_rows = [row for row in observations
                        if str(row["raw_model_version"]) == version]
        version_wins = sum(row["selected_won"] for row in version_rows)
        flag_summary, pair_summary, severity_summary = _summary(version_rows)
        model_summaries.append({
            "raw_model_version": version,
            "sample": {
                "rows": len(version_rows),
                "wins": version_wins,
                "losses": len(version_rows) - version_wins,
            },
            "flag_summary": flag_summary,
            "pair_summary": pair_summary,
            "severity_summary": severity_summary,
            "contribution_summary": _contribution_summary(version_rows),
        })
    return {
        "contract": CONTRACT,
        "system": "KS1",
        "as_of": source.get("as_of"),
        "ledger_as_of": ledger.get("as_of"),
        "sample": {
            "committed_ledger_rows": len(ledger.get("rows") or []),
            "prospectively_reproduced_rows": len(admitted),
            "frozen_holdout_ids_excluded": holdout_excluded,
            "outside_final_horizon_rows": outside_horizon,
            "analyzed_non_holdout_rows": len(observations),
            "wins": wins,
            "losses": losses,
            "loss_rate": losses / len(observations) if observations else None,
            "missing_signal_surface_rows": dict(sorted(incomplete_signal_rows.items())),
        },
        "reproducibility_scope": {
            "finals_start_date_et": finals_start.date().isoformat(),
            "policy": "current_and_previous_ET_calendar_years_only; older ledger rows excluded, not regraded",
        },
        "holdout_boundary": {
            "id_list_read_only_for_exclusion": True,
            "manifest_game_ids_sha256": FROZEN_GAME_IDS_SHA256,
            "holdout_labels_read": 0,
            "holdout_predictions_scored": 0,
            "qualification_runs": 0,
        },
        "summary_scope": "per_raw_model_version_only",
        "signal_score_orientation": "selected_side_raw_log_odds",
        "model_summaries": model_summaries,
        "observations": observations,
        "admission": admission,
        "authority_effect": "development_diagnostic_only_no_prediction_calibration_model_ref_lock_or_serving_effect",
        "prediction_writes": 0,
        "official_ledger_writes": 0,
        "model_ref_writes": 0,
        "lock_writes": 0,
        "trained_lightgbm": False,
        "pattern_policy": "descriptive trace only; any learned feature or recipe change must be reconstructed strictly pregame and pass purged development plus the unchanged frozen-300 qualification gates",
    }


def publish(source: dict[str, Any], output: Path, *, s3, bucket: str) -> dict[str, Any]:
    require_main_workflow()
    checkpoint = latest_checkpoint(s3, bucket, datetime.now(timezone.utc).isoformat())
    if checkpoint is None:
        raise ValueError("loss trace requires a committed KS1 nightly checkpoint")
    ledger = checkpoint["ledger"]
    state = checkpoint["state"]
    prefix = checkpoint_prefix(state["night_date"], int(state.get("catchup_revision", 0)))
    key = prefix + "loss_trace.json"

    # A retry can arrive after the official checkpoint committed but before its
    # derived trace did. Reuse a verified existing trace without rebuilding it
    # from a later capture; otherwise recover the missing trace for this exact
    # immutable checkpoint.
    existing, existing_proof = read_json(s3, bucket, key)
    if existing is not None:
        if (existing.get("contract") != CONTRACT or existing.get("system") != "KS1"
                or existing.get("ledger_as_of") != ledger.get("as_of")
                or not existing.get("as_of")
                or utc(existing["as_of"]) > utc(source.get("as_of"))
                or any(existing.get(field) != 0 for field in (
                    "prediction_writes", "official_ledger_writes",
                    "model_ref_writes", "lock_writes"))):
            raise ValueError(
                "refuse to overwrite or reuse loss trace not bound to the committed KS1 checkpoint"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(encode(existing))
        return {
            "status": "already_published", "key": key, "proof": existing_proof,
            "sample": existing["sample"], "authority_effect": existing["authority_effect"],
        }

    payload = build(source, ledger, holdout_ids())
    stored, proof = commit_json(s3, bucket, key, payload)
    if stored != payload:
        raise ValueError("loss trace AWS readback mismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encode(stored))
    return {"status": "published", "key": key, "proof": proof, "sample": stored["sample"], "authority_effect": stored["authority_effect"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    source = json.loads(args.inputs.read_bytes())
    if not args.publish:
        raise ValueError("loss trace is bound to the committed AWS nightly ledger; use --publish")
    from ks1.sources import aws_clients
    _, s3, bucket = aws_clients("us-east-1", "parlay-platform-dev")
    result = publish(source, args.output, s3=s3, bucket=bucket)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
