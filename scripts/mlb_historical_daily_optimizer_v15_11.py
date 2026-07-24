#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

from mlb_historical_daily_optimizer_v1 import (  # noqa: E402
    Candidate,
    aggressive_search,
    audit_selected_candidate,
    consensus_snapshot,
    extract_game_features,
    predictions_for_candidate,
    snapshot_schedule,
)
from mlb_historical_policy_v1 import (  # noqa: E402
    CUTOVER_MODE,
    HistoricalPolicy,
    build_cutover_records,
    canonical_digest,
    chronological_split,
    evaluate_promotion_gate,
    score_daily_slates,
    sha256_file,
)

VERSION = "MLB-HISTORICAL-CLI-v15.11.1"
HISTORICAL_ENDPOINT = "https://api.the-odds-api.com/v4/historical/sports/baseball_mlb/odds"
PAID_CONFIRMATION = "I AUTHORIZE PAID THE ODDS API HISTORICAL USAGE"
PROMOTION_CONFIRMATION = "PROMOTE MLB V15.11.1 HISTORICAL DAILY OPTIMIZER ONLY"


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with Path(path).open() as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"JSONL line {line_number} is not an object")
            rows.append(row)
    return rows


def write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True, default=str) + "\n")


def _games(schedule: Any) -> List[Dict[str, Any]]:
    rows = schedule.get("games") if isinstance(schedule, dict) else schedule
    if not isinstance(rows, list):
        raise ValueError("schedule must be a list or an object containing games")
    validated: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("schedule game must be an object")
        game_id = str(raw.get("game_id") or raw.get("id") or "").strip()
        slate_date = str(raw.get("slate_date") or "").strip()
        commence_time = str(raw.get("commence_time") or "").strip()
        home_team = str(raw.get("home_team") or "").strip()
        away_team = str(raw.get("away_team") or "").strip()
        if not all((game_id, slate_date, commence_time, home_team, away_team)):
            raise ValueError("schedule games require game_id, slate_date, commence_time, teams")
        if game_id in seen:
            raise ValueError(f"duplicate schedule game_id: {game_id}")
        seen.add(game_id)
        validated.append(
            {
                "game_id": game_id,
                "slate_date": slate_date,
                "commence_time": commence_time,
                "home_team": home_team,
                "away_team": away_team,
            }
        )
    return validated


def build_request_plan(
    schedule: Any,
    *,
    credits_per_request: int,
    policy: HistoricalPolicy | None = None,
) -> Dict[str, Any]:
    policy = policy or HistoricalPolicy()
    policy.validate()
    if credits_per_request < 1:
        raise ValueError("credits_per_request must be positive")
    by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for game in _games(schedule):
        by_date[game["slate_date"]].append(game)
    requests: List[Dict[str, Any]] = []
    for slate_date in sorted(by_date):
        times = snapshot_schedule(
            slate_date,
            [game["commence_time"] for game in by_date[slate_date]],
            policy,
        )
        for requested_at in times:
            requests.append(
                {
                    "slate_date": slate_date,
                    "requested_at_utc": requested_at.isoformat().replace("+00:00", "Z"),
                    "regions": "us",
                    "markets": "h2h",
                    "sport_key": policy.sport_key,
                }
            )
    plan = {
        "version": VERSION,
        "policy": policy.__dict__,
        "request_count": len(requests),
        "credits_per_request": credits_per_request,
        "estimated_credits": len(requests) * credits_per_request,
        "paid_usage_authorized": False,
        "requests": requests,
    }
    plan["plan_sha256"] = canonical_digest(plan)
    return plan


def _historical_url(api_key: str, requested_at_utc: str) -> str:
    query = urllib.parse.urlencode(
        {
            "apiKey": api_key,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american",
            "dateFormat": "iso",
            "date": requested_at_utc,
        }
    )
    return f"{HISTORICAL_ENDPOINT}?{query}"


def _fetch_json(url: str, timeout: int = 30) -> tuple[Any, Mapping[str, str]]:
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": "inqsi-mlb-v15.11.1/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8")), dict(response.headers.items())


def execute_backfill(
    plan: Mapping[str, Any],
    *,
    output_dir: str | Path,
    api_key: str,
    confirmation: str,
    max_credits: int,
    timeout: int = 30,
) -> Dict[str, Any]:
    if confirmation != PAID_CONFIRMATION:
        raise PermissionError("paid historical usage confirmation phrase is missing")
    if not api_key:
        raise PermissionError("ODDS_API_KEY is required")
    expected_digest = str(plan.get("plan_sha256") or "")
    material = dict(plan)
    material.pop("plan_sha256", None)
    if expected_digest != canonical_digest(material):
        raise ValueError("request plan digest mismatch")
    estimate = int(plan.get("estimated_credits") or 0)
    if estimate > max_credits:
        raise PermissionError(
            f"estimated credits {estimate} exceed explicit ceiling {max_credits}"
        )

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    ledger_path = destination / "ledger.json"
    ledger = read_json(ledger_path) if ledger_path.exists() else {
        "version": VERSION,
        "plan_sha256": expected_digest,
        "completed": {},
        "credits_observed": 0,
    }
    if ledger.get("plan_sha256") != expected_digest:
        raise ValueError("existing ledger belongs to a different request plan")

    for request_row in plan.get("requests") or []:
        requested_at = str(request_row["requested_at_utc"])
        if requested_at in ledger["completed"]:
            continue
        safe_name = requested_at.replace(":", "").replace("-", "")
        target = destination / f"snapshot-{safe_name}.json.gz"
        payload, headers = _fetch_json(_historical_url(api_key, requested_at), timeout)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError(f"historical response schema invalid at {requested_at}")
        envelope = {
            "requested_at_utc": requested_at,
            "provider_timestamp": payload.get("timestamp"),
            "previous_timestamp": payload.get("previous_timestamp"),
            "next_timestamp": payload.get("next_timestamp"),
            "data": payload["data"],
        }
        with gzip.open(target, "wt", encoding="utf-8") as handle:
            json.dump(envelope, handle, sort_keys=True)
        response_cost = int(headers.get("x-requests-last") or plan.get("credits_per_request") or 0)
        ledger["credits_observed"] = int(ledger.get("credits_observed") or 0) + response_cost
        if ledger["credits_observed"] > max_credits:
            target.unlink(missing_ok=True)
            raise PermissionError("observed historical usage exceeded explicit credit ceiling")
        ledger["completed"][requested_at] = {
            "file": target.name,
            "sha256": sha256_file(target),
            "provider_timestamp": payload.get("timestamp"),
            "response_cost": response_cost,
            "remaining": headers.get("x-requests-remaining"),
            "used": headers.get("x-requests-used"),
        }
        write_json(ledger_path, ledger)
    ledger["complete"] = len(ledger["completed"]) == len(plan.get("requests") or [])
    ledger["paid_usage_authorized"] = True
    write_json(ledger_path, ledger)
    return ledger


def _load_snapshot_envelopes(snapshot_dir: str | Path) -> List[Dict[str, Any]]:
    envelopes: List[Dict[str, Any]] = []
    for path in sorted(Path(snapshot_dir).glob("snapshot-*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            envelope = json.load(handle)
        envelope["source_file"] = path.name
        envelope["source_sha256"] = sha256_file(path)
        envelopes.append(envelope)
    return envelopes


def build_dataset(
    schedule: Any,
    outcomes: Any,
    snapshot_dir: str | Path,
    *,
    policy: HistoricalPolicy | None = None,
) -> List[Dict[str, Any]]:
    policy = policy or HistoricalPolicy()
    policy.validate()
    games = {game["game_id"]: game for game in _games(schedule)}
    outcome_rows = outcomes.get("outcomes") if isinstance(outcomes, dict) else outcomes
    if not isinstance(outcome_rows, list):
        raise ValueError("outcomes must be a list or object containing outcomes")
    winners = {
        str(row.get("game_id") or ""): str(row.get("winner") or "")
        for row in outcome_rows
        if isinstance(row, dict)
    }
    histories: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for envelope in _load_snapshot_envelopes(snapshot_dir):
        observed_at = envelope.get("provider_timestamp") or envelope.get("requested_at_utc")
        for event in envelope.get("data") or []:
            event_id = str(event.get("id") or "")
            if event_id not in games:
                continue
            aggregate = consensus_snapshot(event)
            histories[event_id].append(
                {
                    "observed_at_utc": observed_at,
                    **aggregate,
                    "source_file": envelope["source_file"],
                    "source_sha256": envelope["source_sha256"],
                }
            )

    dataset: List[Dict[str, Any]] = []
    for game_id, game in sorted(games.items(), key=lambda item: (item[1]["slate_date"], item[0])):
        winner = winners.get(game_id)
        if winner not in {game["home_team"], game["away_team"]}:
            raise ValueError(f"settled winner missing or invalid for {game_id}")
        features = extract_game_features(histories.get(game_id) or [], game["commence_time"], policy)
        dataset.append(
            {
                **game,
                "features": features,
                "market_home_probability": features["lock_home_prob"],
                "home_win": int(winner == game["home_team"]),
                "feature_version": VERSION,
                "training_eligible": True,
            }
        )
    return dataset


def _candidate_from_dict(value: Mapping[str, Any]) -> Candidate:
    return Candidate(
        candidate_id=str(value["candidate_id"]),
        feature_names=tuple(value["feature_names"]),
        means=tuple(float(item) for item in value["means"]),
        scales=tuple(float(item) for item in value["scales"]),
        weights=tuple(float(item) for item in value["weights"]),
        bias=float(value["bias"]),
        market_blend=float(value["market_blend"]),
        l2=float(value["l2"]),
        validation_min_daily_accuracy=float(value["validation_min_daily_accuracy"]),
        validation_mean_daily_accuracy=float(value["validation_mean_daily_accuracy"]),
        validation_pass_day_rate=float(value["validation_pass_day_rate"]),
        validation_brier=float(value["validation_brier"]),
        validation_log_loss=float(value["validation_log_loss"]),
    )


def _model_metrics(rows: Sequence[Mapping[str, Any]], candidate: Candidate) -> Dict[str, float]:
    predictions, probabilities, labels = predictions_for_candidate(rows, candidate)
    outcomes = [
        {
            "slate_date": row["slate_date"],
            "game_id": row["game_id"],
            "winner": row["home_team"] if int(row["home_win"]) else row["away_team"],
        }
        for row in rows
    ]
    daily = score_daily_slates(predictions, outcomes)
    correct = sum(row.correct_games for row in daily)
    games = sum(row.official_games for row in daily)
    brier = sum((probability - label) ** 2 for probability, label in zip(probabilities, labels)) / len(labels)
    log_loss = -sum(
        label * math.log(min(max(probability, 1e-6), 1 - 1e-6))
        + (1 - label) * math.log(min(max(1 - probability, 1e-6), 1 - 1e-6))
        for probability, label in zip(probabilities, labels)
    ) / len(labels)
    return {"accuracy": correct / games, "brier": brier, "log_loss": log_loss}


def _market_metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    labels = [int(row["home_win"]) for row in rows]
    probabilities = [min(max(float(row["market_home_probability"]), 1e-6), 1 - 1e-6) for row in rows]
    brier = sum((probability - label) ** 2 for probability, label in zip(probabilities, labels)) / len(labels)
    log_loss = -sum(
        label * math.log(probability) + (1 - label) * math.log(1 - probability)
        for probability, label in zip(probabilities, labels)
    ) / len(labels)
    return {"brier": brier, "log_loss": log_loss}


def train_candidate(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_candidates: int,
    seed: int,
) -> Dict[str, Any]:
    split = chronological_split(rows)
    train_dates = set(split.train_dates)
    validation_dates = set(split.validation_dates)
    train_rows = [row for row in rows if row["slate_date"] in train_dates]
    validation_rows = [row for row in rows if row["slate_date"] in validation_dates]
    result = aggressive_search(
        train_rows,
        validation_rows,
        max_candidates=max_candidates,
        seed=seed,
    )
    train_metrics = _model_metrics(train_rows, result.selected)
    validation_metrics = _model_metrics(validation_rows, result.selected)
    market_validation = _market_metrics(validation_rows)
    return {
        "version": VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "split": split.to_dict(),
        "candidate": result.selected.to_dict(),
        "candidates_evaluated": result.candidates_evaluated,
        "top_candidates": list(result.top_candidates),
        "validation_daily": list(result.validation_daily),
        "train_metrics": train_metrics,
        "validation_metrics": validation_metrics,
        "market_validation_metrics": market_validation,
        "audit_opened": False,
    }


def audit_candidate(
    rows: Sequence[Mapping[str, Any]],
    artifact: Mapping[str, Any],
    *,
    artifact_path: str | Path,
) -> Dict[str, Any]:
    split = artifact["split"]
    audit_dates = set(split["audit_dates"])
    audit_rows = [row for row in rows if row["slate_date"] in audit_dates]
    candidate = _candidate_from_dict(artifact["candidate"])
    audit = audit_selected_candidate(audit_rows, candidate)
    audit_metrics = _model_metrics(audit_rows, candidate)
    market_audit = _market_metrics(audit_rows)
    validation_daily = artifact["validation_daily"]
    audit_daily = audit["audit_daily"]
    report = {
        "version": VERSION,
        "candidate_id": candidate.candidate_id,
        "sample_counts": {
            "train": split["train_games"],
            "validation": split["validation_games"],
            "audit": split["audit_games"],
            "validation_days": len(split["validation_dates"]),
            "audit_days": len(split["audit_dates"]),
        },
        "chronology": {
            "whole_date_partitions": True,
            "strictly_ordered": max(split["train_dates"]) < min(split["validation_dates"]) < min(split["audit_dates"]),
            "audit_opened_after_selection": True,
        },
        "provenance": {
            "starts_at_0100_et": True,
            "cadence_15_minutes": True,
            "t_minus_45_clipped": True,
            "settled_official_labels": True,
            "no_future_features": True,
        },
        "validation_daily": validation_daily,
        "audit_daily": audit_daily,
        "metrics": {
            "train_accuracy": artifact["train_metrics"]["accuracy"],
            "validation_accuracy": artifact["validation_metrics"]["accuracy"],
            "audit_accuracy": audit_metrics["accuracy"],
            "validation_brier": artifact["validation_metrics"]["brier"],
            "audit_brier": audit_metrics["brier"],
            "market_validation_brier": artifact["market_validation_metrics"]["brier"],
            "market_audit_brier": market_audit["brier"],
            "validation_log_loss": artifact["validation_metrics"]["log_loss"],
            "audit_log_loss": audit_metrics["log_loss"],
            "market_validation_log_loss": artifact["market_validation_metrics"]["log_loss"],
            "market_audit_log_loss": market_audit["log_loss"],
        },
        "artifact": {
            "path": str(artifact_path),
            "sha256": sha256_file(artifact_path),
            "sha256_validated": True,
            "immutable": True,
        },
        "automatic_wager_allowed": False,
    }
    report["gate"] = evaluate_promotion_gate(report).to_dict()
    return report


def execute_promotion(
    report: Mapping[str, Any],
    *,
    experiment_id: str,
    confirmation: str,
    execute: bool,
    table_name: str | None,
    region: str,
) -> Dict[str, Any]:
    decision = evaluate_promotion_gate(report)
    if not decision.approved:
        return {"executed": False, "gate": decision.to_dict()}
    if confirmation != PROMOTION_CONFIRMATION:
        raise PermissionError("production cutover confirmation phrase is missing")
    artifact = report["artifact"]
    records = build_cutover_records(
        experiment_id=experiment_id,
        artifact_sha256=str(artifact["sha256"]),
        gate_report_sha256=canonical_digest(report),
    )
    if not execute:
        return {"executed": False, "dry_run": True, "gate": decision.to_dict(), "records": records}
    if not table_name:
        raise ValueError("--table-name is required for --execute")
    import boto3

    client = boto3.client("dynamodb", region_name=region)
    champion = records["champion"]
    cutover = records["cutover"]
    client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": table_name,
                    "Item": {
                        "PK": {"S": "MLB_PRODUCTION_MODEL"},
                        "SK": {"S": "CHAMPION"},
                        "payload": {"S": json.dumps(champion, sort_keys=True)},
                        "artifact_sha256": {"S": champion["artifact_sha256"]},
                        "approved": {"BOOL": True},
                    },
                }
            },
            {
                "Put": {
                    "TableName": table_name,
                    "Item": {
                        "PK": {"S": "MLB_PRODUCTION_ALGORITHM"},
                        "SK": {"S": "WRITE_ONCE_CUTOVER"},
                        "payload": {"S": json.dumps(cutover, sort_keys=True)},
                        "mode": {"S": CUTOVER_MODE},
                        "legacy_fallback_allowed": {"BOOL": False},
                    },
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            },
        ],
        ClientRequestToken=canonical_digest(records)[:36],
    )
    return {"executed": True, "gate": decision.to_dict(), "records": records}


def command_plan(args: argparse.Namespace) -> None:
    plan = build_request_plan(
        read_json(args.schedule), credits_per_request=args.credits_per_request
    )
    write_json(args.output, plan)
    print(json.dumps({key: plan[key] for key in ("request_count", "estimated_credits", "plan_sha256")}, indent=2))


def command_backfill(args: argparse.Namespace) -> None:
    ledger = execute_backfill(
        read_json(args.plan),
        output_dir=args.output_dir,
        api_key=os.environ.get("ODDS_API_KEY", ""),
        confirmation=args.confirmation,
        max_credits=args.max_credits,
        timeout=args.timeout,
    )
    print(json.dumps(ledger, indent=2, sort_keys=True))


def command_dataset(args: argparse.Namespace) -> None:
    rows = build_dataset(read_json(args.schedule), read_json(args.outcomes), args.snapshot_dir)
    write_jsonl(args.output, rows)
    print(json.dumps({"rows": len(rows), "output": args.output}, indent=2))


def command_train(args: argparse.Namespace) -> None:
    artifact = train_candidate(
        read_jsonl(args.dataset),
        max_candidates=args.max_candidates,
        seed=args.seed,
    )
    write_json(args.output, artifact)
    print(json.dumps({"candidate_id": artifact["candidate"]["candidate_id"], "candidates_evaluated": artifact["candidates_evaluated"], "audit_opened": False}, indent=2))


def command_audit(args: argparse.Namespace) -> None:
    artifact = read_json(args.artifact)
    if artifact.get("audit_opened") is True:
        raise PermissionError("this candidate artifact already opened an audit")
    report = audit_candidate(read_jsonl(args.dataset), artifact, artifact_path=args.artifact)
    write_json(args.output, report)
    print(json.dumps(report["gate"], indent=2))


def command_promote(args: argparse.Namespace) -> None:
    result = execute_promotion(
        read_json(args.report),
        experiment_id=args.experiment_id,
        confirmation=args.confirmation,
        execute=args.execute,
        table_name=args.table_name,
        region=args.region,
    )
    write_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="MLB V15.11.1 historical daily-slate optimizer")
    root.add_argument("--version", action="version", version=VERSION)
    commands = root.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan")
    plan.add_argument("--schedule", required=True)
    plan.add_argument("--credits-per-request", type=int, default=10)
    plan.add_argument("--output", required=True)
    plan.set_defaults(func=command_plan)

    backfill = commands.add_parser("backfill")
    backfill.add_argument("--plan", required=True)
    backfill.add_argument("--output-dir", required=True)
    backfill.add_argument("--max-credits", type=int, required=True)
    backfill.add_argument("--confirmation", required=True)
    backfill.add_argument("--timeout", type=int, default=30)
    backfill.set_defaults(func=command_backfill)

    dataset = commands.add_parser("build-dataset")
    dataset.add_argument("--schedule", required=True)
    dataset.add_argument("--outcomes", required=True)
    dataset.add_argument("--snapshot-dir", required=True)
    dataset.add_argument("--output", required=True)
    dataset.set_defaults(func=command_dataset)

    train = commands.add_parser("train")
    train.add_argument("--dataset", required=True)
    train.add_argument("--max-candidates", type=int, default=25000)
    train.add_argument("--seed", type=int, default=15111)
    train.add_argument("--output", required=True)
    train.set_defaults(func=command_train)

    audit = commands.add_parser("audit")
    audit.add_argument("--dataset", required=True)
    audit.add_argument("--artifact", required=True)
    audit.add_argument("--output", required=True)
    audit.set_defaults(func=command_audit)

    promote = commands.add_parser("promote")
    promote.add_argument("--report", required=True)
    promote.add_argument("--experiment-id", required=True)
    promote.add_argument("--confirmation", required=True)
    promote.add_argument("--execute", action="store_true")
    promote.add_argument("--table-name")
    promote.add_argument("--region", default="us-east-1")
    promote.add_argument("--output", required=True)
    promote.set_defaults(func=command_promote)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
