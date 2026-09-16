"""Prove direct lineup season-batting coverage from retained pre-T10 feeds.

This is a development-only source audit.  It never changes the game table, never
scores the frozen qualification holdout, and never writes predictions or model refs.
For development-fit rows only it reads the exact versioned MLB Stats API timecoded
feed already bound to the row, revalidates its historical pregame state, and measures
whether the same per-batter season OPS/OBP/SLG inputs used by live KS1 were present.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from ks1.development import HOLDOUT, frozen_split
from ks1.features import utc
from ks1.historical_feed import TEAM_CONTEXT_PREFIX
from ks1.inventory import encode
from ks1.passive_context import SLOT_WEIGHTS
from ks1.retrain_recent import qualified_training_population, split_development
from ks1.sources import aws_clients

CONTRACT = "KS1-historical-lineup-season-proof-v1"
DIRECT = (
    "lineup_quality_ops", "lineup_quality_obp", "lineup_quality_slg",
    "lineup_top4_ops", "lineup_2_5_ops", "lineup_observed_batters", "lineup_total_pa",
)
SUPPORTED = {"SUPPORTED_V1_COMPLETE", "SUPPORTED_V1_EXPLICIT_MISSING"}


def _number(value, low=None, high=None):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    if low is not None and value < low:
        return None
    if high is not None and value > high:
        return None
    return value


def _positive_id(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return str(number) if number > 0 else None


def _sample(team, identity, slot):
    player = (team.get("players") or {}).get("ID" + identity)
    if (not isinstance(player, dict)
            or _positive_id((player.get("person") or {}).get("id")) != identity
            or str(player.get("battingOrder")) != str(slot * 100)
            or (player.get("gameStatus") or {}).get("isSubstitute") is not False):
        raise ValueError("lineup identity binding failed")
    stats = ((player.get("seasonStats") or {}).get("batting") or {})
    raw_pa = _number(stats.get("plateAppearances"), 0, 2000)
    pa = int(raw_pa) if raw_pa is not None and raw_pa.is_integer() else None
    values = {}
    for name, high in (("ops", 5), ("obp", 1), ("slg", 4)):
        value = _number(stats.get(name), 0, high)
        values[name] = value if pa is not None and pa > 0 else None
    return {
        "player_id": identity,
        "slot": slot,
        "plate_appearances": pa,
        **values,
        "status": ("OBSERVED" if pa is not None and pa > 0 else
                   "NO_PLATE_APPEARANCES" if pa == 0 else "SAMPLE_UNAVAILABLE"),
    }


def _weighted(samples, metric, slots=range(1, 10)):
    selected = [sample for sample in samples if sample["slot"] in slots]
    if not selected or any(sample[metric] is None for sample in selected):
        return None
    values = [(SLOT_WEIGHTS[sample["slot"] - 1], sample[metric]) for sample in selected]
    return sum(weight * value for weight, value in values) / sum(weight for weight, _ in values)


def _direct(samples):
    return {
        "lineup_quality_ops": _weighted(samples, "ops"),
        "lineup_quality_obp": _weighted(samples, "obp"),
        "lineup_quality_slg": _weighted(samples, "slg"),
        "lineup_top4_ops": _weighted(samples, "ops", range(1, 5)),
        "lineup_2_5_ops": _weighted(samples, "ops", range(2, 6)),
        "lineup_observed_batters": float(sum(sample["status"] == "OBSERVED" for sample in samples)),
        "lineup_total_pa": (float(sum(sample["plate_appearances"] for sample in samples))
                            if all(sample["plate_appearances"] is not None for sample in samples)
                            else None),
    }


def _source(row):
    raw = row.get("historical_lineup_bullpen_context_source")
    if not raw or str(raw).lower() in {"nan", "none"}:
        raise ValueError("source_receipt_missing")
    value = json.loads(raw) if isinstance(raw, str) else raw
    if (not isinstance(value, dict)
            or value.get("source_type") != "mlb_statsapi_timecoded_team_context"
            or value.get("provider") != "MLB Stats API"
            or not value.get("bucket") or not value.get("key")
            or value.get("versionId") in (None, "", "null")
            or not isinstance(value.get("sha256"), str)
            or len(value["sha256"]) != 64):
        raise ValueError("source_receipt_invalid")
    return value


def extract(body: bytes, row: dict, receipt: dict):
    """Extract live-equivalent direct lineup values after exact source validation."""
    if hashlib.sha256(body).hexdigest() != receipt["sha256"]:
        raise ValueError("source_sha256_mismatch")
    entry = json.loads(body)
    payload = entry.get("payload")
    game_id = str(row["game_id"])
    start = utc(row["commence_time"])
    cutoff = start - timedelta(minutes=10)
    code = cutoff.strftime("%Y%m%d_%H%M%S")
    endpoint = "https://statsapi.mlb.com/api/v1.1/game/" + game_id + "/feed/live"
    if (not isinstance(payload, dict)
            or str(entry.get("game_id")) != game_id
            or utc(entry.get("commence_time")) != start
            or entry.get("provider") != "MLB Stats API"
            or entry.get("endpoint") != endpoint
            or entry.get("timecode") != code
            or receipt.get("endpoint") != endpoint
            or receipt.get("timecode") != code
            or receipt.get("key") != TEAM_CONTEXT_PREFIX + "game=" + game_id + "/timecode=" + code + ".json"
            or hashlib.sha256(encode(payload)).hexdigest() != entry.get("payload_sha256")
            or entry.get("payload_sha256") != receipt.get("payload_sha256")):
        raise ValueError("source_identity_mismatch")
    try:
        at = datetime.strptime(payload["metaData"]["timeStamp"], "%Y%m%d_%H%M%S").replace(
            tzinfo=timezone.utc)
        data = payload["gameData"]
        live = payload.get("liveData", {})
        box = live.get("boxscore", {}).get("teams", {})
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("payload_shape_invalid") from exc
    if (utc(data["datetime"]["dateTime"]) != start
            or not start - timedelta(days=1) <= at <= cutoff
            or utc(entry.get("retrieved_at")) < at
            or data["status"]["codedGameState"] not in ("P", "S")
            or live.get("decisions")
            or any(event.get("isPitch") is True
                   for play in live.get("plays", {}).get("allPlays", [])
                   for event in play.get("playEvents", []))):
        raise ValueError("payload_not_pregame")
    result = {}
    for side in ("home", "away"):
        team = box.get(side)
        if not isinstance(team, dict):
            raise ValueError("box_team_missing")
        expected_team = _positive_id(row.get(side + "_id"))
        if (_positive_id((team.get("team") or {}).get("id")) != expected_team
                or _positive_id(data["teams"][side]["id"]) != expected_team):
            raise ValueError("team_identity_mismatch")
        order = team.get("battingOrder")
        if not isinstance(order, list) or len(order) != 9:
            raise ValueError("lineup_missing")
        identities = [_positive_id(value) for value in order]
        if None in identities or len(set(identities)) != 9:
            raise ValueError("lineup_identity_invalid")
        frozen = row.get(side + "_lineup_ids")
        if frozen and str(frozen).lower() not in {"nan", "none"}:
            expected = [str(value) for value in json.loads(frozen)]
            if identities != expected:
                raise ValueError("lineup_table_mismatch")
        samples = [_sample(team, identity, slot)
                   for slot, identity in enumerate(identities, 1)]
        result.update({side + "_" + key: value for key, value in _direct(samples).items()})
    return result


def run(input_path: Path, proof_path: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    proof = json.loads(proof_path.read_bytes())
    body = input_path.read_bytes()
    if hashlib.sha256(body).hexdigest() != proof["input_table_sha256"]:
        raise ValueError("input table checksum mismatch")
    frame = pd.read_parquet(input_path)
    train, holdout = frozen_split(frame, json.loads(HOLDOUT.read_bytes()))
    train, population = qualified_training_population(train, proof.get("source_receipts", []))
    fit, development = split_development(train)
    rows = [row for row in fit.to_dict("records")
            if row.get("lineup_bullpen_context_evidence") == "historical_timecoded_mlb_feed"
            and row.get("historical_lineup_bullpen_context_status") in SUPPORTED]
    _, s3, _ = aws_clients("us-east-1", "parlay-platform-dev")

    def one(row):
        try:
            receipt = _source(row)
            response = s3.get_object(Bucket=receipt["bucket"], Key=receipt["key"],
                                     VersionId=receipt["versionId"])
            return extract(response["Body"].read(), row, receipt), None
        except Exception as exc:  # every unproven row remains missing, with a counted reason
            return None, str(exc) or type(exc).__name__

    recovered, failures = [], Counter()
    with ThreadPoolExecutor(max_workers=16) as pool:
        for values, error in pool.map(one, rows):
            if values is not None:
                recovered.append(values)
            else:
                failures[error] += 1
    columns = [side + "_" + feature for side in ("home", "away") for feature in DIRECT]
    coverage = {
        column: sum(values.get(column) is not None for values in recovered)
        for column in columns
    }
    report = {
        "contract": CONTRACT,
        "input_table_sha256": proof["input_table_sha256"],
        "fit_games": len(fit),
        "development_games_not_inspected": len(development),
        "reserved_holdout_games_not_inspected": len(holdout),
        "eligible_timecoded_fit_rows": len(rows),
        "exact_source_rows_verified": len(recovered),
        "source_failures": dict(sorted(failures.items())),
        "nonmissing_fit_coverage": coverage,
        "top4_300_game_floor_reached": all(
            coverage[side + "_lineup_top4_ops"] >= 300 for side in ("home", "away")),
        "training_population": population,
        "prediction_writes": 0,
        "official_ledger_writes": 0,
        "holdout_predictions_generated": 0,
        "authority_effect": "none_source_coverage_probe_only",
    }
    (output / "historical_lineup_season_coverage.json").write_bytes(encode(report))
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--proof", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    run(args.input, args.proof, args.output)


if __name__ == "__main__":
    main()
