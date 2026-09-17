"""Recover individual-reliever performance for holdout-free KS1 development rows.

This reader is deliberately development-only.  Its caller must separate the frozen
qualification holdout before invoking :func:`enrich_frame`.  Every bullpen roster comes
from the exact versioned MLB pre-T10 team-context object already bound to the game-table
row, while pitcher results come from the exact official-history object bound to the
input proof.  No provider request, label-dependent row selection, prediction write, or
serving mutation occurs here.

Reliever slots are deterministic *usage ranks*, not leverage-role claims: among relievers
on the observed pre-T10 roster with at least one prior 30-day appearance, rank by prior
30-day appearances descending and player id as a stable tie-break.  The performance
values themselves are strictly prior 30-day official-box summaries.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math

import pandas as pd

from ks1.features import Features
from ks1.historical_feed import feed_team_context
from ks1.inventory import encode

CONTRACT = "KS1-historical-individual-bullpen-development-enrichment-v1"
RANKS = (1, 2, 3)
METRICS = ("fip", "era", "k_bb_pct")
FEATURES = tuple(
    f"individual_bullpen_rank{rank}_{metric}_30d"
    for rank in RANKS for metric in METRICS
)
SUPPORTED = {"SUPPORTED_V1_COMPLETE", "SUPPORTED_V1_EXPLICIT_MISSING"}


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _receipt(value):
    if not value or str(value).lower() in {"nan", "none"}:
        raise ValueError("individual_bullpen_source_receipt_missing")
    receipt = json.loads(value) if isinstance(value, str) else value
    if (not isinstance(receipt, dict)
            or receipt.get("source_type") != "mlb_statsapi_timecoded_team_context"
            or receipt.get("provider") != "MLB Stats API"
            or not receipt.get("bucket") or not receipt.get("key")
            or receipt.get("versionId") in (None, "", "null")
            or not isinstance(receipt.get("sha256"), str)
            or len(receipt["sha256"]) != 64):
        raise ValueError("individual_bullpen_source_receipt_invalid")
    return receipt


def _official_receipt(proof):
    receipt = proof.get("official_history_source")
    if (not isinstance(receipt, dict)
            or not receipt.get("bucket") or not receipt.get("key")
            or receipt.get("versionId") in (None, "", "null")
            or not isinstance(receipt.get("sha256"), str)
            or len(receipt["sha256"]) != 64
            or not isinstance(receipt.get("complete_years"), list)
            or not receipt["complete_years"]):
        raise ValueError("individual_bullpen_official_history_receipt_invalid")
    exact = {
        key: receipt.get(key)
        for key in ("bucket", "key", "versionId", "sha256")
    }
    if not any(
            isinstance(candidate, dict)
            and all(candidate.get(key) == value for key, value in exact.items())
            for candidate in proof.get("source_receipts", [])):
        raise ValueError("individual_bullpen_official_history_receipt_unbound")
    return receipt


def _read_exact(s3, receipt):
    response = s3.get_object(
        Bucket=receipt["bucket"], Key=receipt["key"], VersionId=receipt["versionId"])
    body = response["Body"].read()
    if hashlib.sha256(body).hexdigest() != receipt["sha256"]:
        raise ValueError("individual_bullpen_source_sha256_mismatch")
    return body


def _history(s3, proof, frame):
    receipt = _official_receipt(proof)
    payload = json.loads(_read_exact(s3, receipt))
    games = payload.get("games")
    if not isinstance(games, list) or not games:
        raise ValueError("individual_bullpen_official_history_games_missing")
    seasons = {int(value) for value in pd.to_numeric(frame.get("season"), errors="coerce").dropna()}
    complete = {int(value) for value in receipt["complete_years"]}
    if not seasons.issubset(complete):
        raise ValueError("individual_bullpen_official_history_year_incomplete")
    return Features(games), receipt, len(games)


def _ranked_values(profiles):
    eligible = []
    for profile in profiles or []:
        window = (profile.get("windows") or {}).get("30d") or {}
        appearances = _finite(window.get("appearances"))
        player_id = str(profile.get("player_id") or "")
        if appearances is None or appearances <= 0 or not player_id:
            continue
        eligible.append((-appearances, player_id, window))
    eligible.sort(key=lambda item: (item[0], item[1]))
    result = {name: None for name in FEATURES}
    for rank, (_, _, window) in zip(RANKS, eligible[:len(RANKS)]):
        for metric in METRICS:
            result[f"individual_bullpen_rank{rank}_{metric}_30d"] = _finite(
                window.get(metric))
    return result


def _eligible(row):
    return (row.get("lineup_bullpen_context_evidence") == "historical_timecoded_mlb_feed"
            and row.get("historical_lineup_bullpen_context_status") in SUPPORTED)


def enrich_frame(frame: pd.DataFrame, s3, proof, minimum_nonmissing=300):
    """Add deterministic individual-reliever features to a holdout-free frame.

    Source eligibility is independent of the game label.  Exact pre-T10 team-context
    objects are read by their version id and byte hash.  The official-history object is
    likewise exact-version bound to the same input proof used to build the training table.
    """
    enriched = frame.copy(deep=True)
    history, official_source, official_games = _history(s3, proof, enriched)
    candidates = [(index, row.to_dict()) for index, row in enriched.iterrows()
                  if _eligible(row.to_dict())]
    failures = Counter()
    contexts = []
    bindings = []

    def read_context(item):
        index, row = item
        try:
            receipt = _receipt(row.get("historical_lineup_bullpen_context_source"))
            body = _read_exact(s3, receipt)
            entry = json.loads(body)
            context = feed_team_context({**entry, "receipt": {
                "bucket": receipt["bucket"],
                "key": receipt["key"],
                "versionId": receipt["versionId"],
                "sha256": receipt["sha256"],
            }})
            if context is None or str(context["game_id"]) != str(row["game_id"]):
                raise ValueError("individual_bullpen_team_context_identity_mismatch")
            if any(str(context["sides"][side]["team_id"]) != str(row[side+"_id"])
                   for side in ("home", "away")):
                raise ValueError("individual_bullpen_team_identity_mismatch")
            return index, row, context, {
                "game_id": str(row["game_id"]),
                "bucket": receipt["bucket"],
                "key": receipt["key"],
                "versionId": receipt["versionId"],
                "sha256": receipt["sha256"],
            }, None
        except Exception as exc:
            return index, row, None, None, str(exc) or type(exc).__name__

    with ThreadPoolExecutor(max_workers=16) as pool:
        for index, row, context, binding, error in pool.map(read_context, candidates):
            if context is None:
                failures[error] += 1
                continue
            contexts.append((index, row, context))
            bindings.append(binding)

    recovered = []
    for index, row, context in contexts:
        values = {}
        try:
            for side in ("home", "away"):
                source = context["sides"][side]
                if source.get("bullpen_status") != "OBSERVED_ROSTER_ONLY":
                    raise ValueError("individual_bullpen_roster_unavailable")
                roster_ids = source.get("bullpen_roster_ids") or []
                bullpen = history.bullpen_roster_at(
                    context["as_of"], row[side+"_id"], roster_ids,
                    game_date=str(row["date"]))
                ranked = _ranked_values(bullpen.get("_reliever_profiles", []))
                values.update({side+"_"+name: value for name, value in ranked.items()})
        except Exception as exc:
            failures[str(exc) or type(exc).__name__] += 1
            continue
        recovered.append(values)
        for column, value in values.items():
            enriched.at[index, column] = value

    columns = [side+"_"+name for side in ("home", "away") for name in FEATURES]
    coverage = {column: sum(values.get(column) is not None for values in recovered)
                for column in columns}
    binding_bytes = encode(sorted(bindings, key=lambda item: item["game_id"]))
    report = {
        "contract": CONTRACT,
        "input_rows": len(frame),
        "eligible_timecoded_rows": len(candidates),
        "exact_team_context_rows_verified": len(contexts),
        "feature_rows_recovered": len(recovered),
        "source_failures": dict(sorted(failures.items())),
        "nonmissing_coverage": coverage,
        "minimum_nonmissing": minimum_nonmissing,
        "minimum_reached_features": sorted(
            column for column, count in coverage.items() if count >= minimum_nonmissing),
        "official_history_source": {
            key: official_source.get(key)
            for key in ("bucket", "key", "versionId", "sha256", "complete_years")
        },
        "official_history_games_loaded": official_games,
        "team_context_binding_count": len(bindings),
        "team_context_bindings_sha256": hashlib.sha256(binding_bytes).hexdigest(),
        "ranking_method": "prior_30d_appearances_desc_player_id_tiebreak",
        "role_claimed": False,
        "label_dependent_selection": False,
        "provider_requests": 0,
        "prediction_writes": 0,
        "official_ledger_writes": 0,
    }
    return enriched, report
