"""Recover individual-reliever performance for holdout-free KS1 development rows.

This reader is deliberately development-only. Its caller must separate the frozen
qualification holdout before invoking :func:`enrich_frame`. Every bullpen roster comes
from the exact versioned MLB pre-T10 team-context object already bound to the game-table
row, while pitcher results come from the exact official-history object bound to the
input proof. Retained Statcast may be replayed only when its exact report and every
retained source receipt contributing pitch rows match that same input proof. No provider
request, label-dependent row selection, prediction write, or serving mutation occurs
here.

Reliever slots are deterministic *usage ranks*, not leverage-role claims: among relievers
on the observed pre-T10 roster with at least one prior 30-day appearance, rank by prior
30-day appearances descending and player id as a stable tie-break. The slot identity is
therefore fixed independently of the metric window. Performance values expose the
strictly prior 7-, 15-, and 30-day official-box summaries plus xwOBA only when the
proof-bound retained pitch inventory satisfies the shared point-in-time completeness
checks.
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
from ks1.inventory import RESEARCH, encode
from ks1.sources import load_existing
from ks1.statcast_history import load_training_statcast

CONTRACT = "KS1-historical-individual-bullpen-development-enrichment-v5"
RANKS = (1, 2, 3)
WINDOWS = (7, 15, 30)
METRICS = ("fip", "era", "k_bb_pct", "xwoba")
FEATURES = tuple(
    f"individual_bullpen_rank{rank}_{metric}_{days}d"
    for rank in RANKS for metric in METRICS for days in WINDOWS
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


def _source_identity(receipt):
    if not isinstance(receipt, dict):
        return None
    bucket = str(receipt.get("bucket") or "")
    key = str(receipt.get("key") or "")
    version = str(receipt.get("versionId") or receipt.get("version_id") or "")
    sha256 = str(receipt.get("sha256") or "")
    if not bucket or not key or not version or len(sha256) != 64:
        return None
    return bucket, key, version, sha256


def _preloaded_statcast_identities(receipts):
    """Return the exact compact Statcast pointer/artifact identities read by load_existing."""
    pointer_key = RESEARCH + "statcast.json"
    artifact_prefix = RESEARCH + "statcast/"
    selected = []
    pointer_count = artifact_count = 0
    for candidate in receipts or []:
        key = str(candidate.get("key") or "") if isinstance(candidate, dict) else ""
        if key != pointer_key and not key.startswith(artifact_prefix):
            continue
        identity = _source_identity(candidate)
        if identity is None:
            raise ValueError("individual_bullpen_statcast_preloaded_receipt_invalid")
        selected.append(identity)
        if key == pointer_key:
            pointer_count += 1
        else:
            artifact_count += 1
    if pointer_count != 1 or artifact_count != 1:
        raise ValueError("individual_bullpen_statcast_preloaded_receipt_set_invalid")
    return sorted(selected)


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


def proof_bound_statcast_context(cf, s3, bucket, proof):
    """Replay the exact retained pitch inventory already admitted by ``proof``.

    ``load_existing`` first reads the current compact Statcast pointer/artifact and
    ``load_training_statcast`` then adds retained daily objects without provider calls.
    Fail closed unless both the preloaded compact identities and every replay-added
    immutable receipt are already present in the input proof, and unless the replay
    reproduces the exact report captured while the input table was built. This prevents
    either source path from silently changing development features after the proof was
    created.
    """
    expected_report = proof.get("historical_statcast_report")
    if not isinstance(expected_report, dict):
        raise ValueError("individual_bullpen_statcast_report_missing")
    if expected_report.get("provider_requests") != 0:
        raise ValueError("individual_bullpen_statcast_report_provider_requests")

    proof_receipts = {
        identity for candidate in proof.get("source_receipts", [])
        if (identity := _source_identity(candidate)) is not None
    }
    expected_official = _official_receipt(proof)
    bundle = load_existing(cf, s3, bucket)
    current_official_matches = (
        _source_identity(bundle.get("official_history_source"))
        == _source_identity(expected_official)
    )
    if not current_official_matches:
        # The retained official-history pointer may advance after the input table
        # and proof were created. Rebuild the replay basis from the exact immutable
        # version already bound to the proof instead of either consuming the newer
        # current object or rejecting an otherwise reproducible development run.
        try:
            exact_official = json.loads(_read_exact(s3, expected_official))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                "individual_bullpen_statcast_official_history_restore_invalid"
            ) from exc
        if (not isinstance(exact_official, dict)
                or not isinstance(exact_official.get("games"), list)
                or not exact_official["games"]
                or not isinstance(exact_official.get("schedule"), list)
                or not exact_official["schedule"]):
            raise ValueError(
                "individual_bullpen_statcast_official_history_restore_invalid")
        bundle["full"] = exact_official["games"]
        bundle["schedule"] = exact_official["schedule"]
        bundle["official_history_source"] = dict(expected_official)

    preloaded_identities = _preloaded_statcast_identities(bundle.get("source_receipts", []))
    if any(identity not in proof_receipts for identity in preloaded_identities):
        raise ValueError("individual_bullpen_statcast_preloaded_receipt_unbound")

    before = len(bundle.get("source_receipts", []))
    replay_report = load_training_statcast(bundle, s3, bucket)
    if replay_report.get("provider_requests") != 0:
        raise ValueError("individual_bullpen_statcast_replay_provider_requests")
    if encode(replay_report) != encode(expected_report):
        raise ValueError("individual_bullpen_statcast_replay_report_mismatch")

    replay_receipts = bundle.get("source_receipts", [])[before:]
    replay_identities = [_source_identity(candidate) for candidate in replay_receipts]
    if any(identity is None or identity not in proof_receipts for identity in replay_identities):
        raise ValueError("individual_bullpen_statcast_replay_receipt_unbound")

    rows = bundle.get("statcast")
    if not isinstance(rows, list) or not rows:
        raise ValueError("individual_bullpen_statcast_replay_rows_missing")
    context = {
        "rows": rows,
        "retained_dates": list(bundle.get("statcast_retained_dates", [])),
        "verified_games": list(bundle.get("statcast_verified_games", [])),
        "physical_dates": list(bundle.get("statcast_physical_dates", [])),
        "physical_games": list(bundle.get("statcast_physical_games", [])),
    }
    preloaded = sorted(preloaded_identities)
    replay = sorted(replay_identities)
    all_identities = sorted(set(preloaded + replay))
    evidence = {
        "contract": "KS1-individual-bullpen-proof-bound-statcast-replay-v1",
        "enabled": True,
        "provider_requests": 0,
        "retained_pitch_rows": len(rows),
        "verified_outcome_dates": replay_report.get("verified_outcome_dates"),
        "verified_physical_dates": replay_report.get("verified_physical_dates"),
        "proof_report_sha256": hashlib.sha256(encode(expected_report)).hexdigest(),
        "replay_report_sha256": hashlib.sha256(encode(replay_report)).hexdigest(),
        "preloaded_source_receipt_count": len(preloaded),
        "preloaded_source_receipts_sha256": hashlib.sha256(encode(preloaded)).hexdigest(),
        "replay_source_receipt_count": len(replay),
        "replay_source_receipts_sha256": hashlib.sha256(encode(replay)).hexdigest(),
        "bound_statcast_source_receipt_count": len(all_identities),
        "bound_statcast_source_receipts_sha256": hashlib.sha256(encode(all_identities)).hexdigest(),
        "all_preloaded_statcast_receipts_bound_to_input_proof": True,
        "all_replay_receipts_bound_to_input_proof": True,
        "all_statcast_receipts_bound_to_input_proof": True,
        "current_official_history_matched_input_proof": current_official_matches,
        "official_history_restored_from_input_proof": not current_official_matches,
    }
    return context, evidence


def _history(s3, proof, frame, statcast_context=None):
    receipt = _official_receipt(proof)
    payload = json.loads(_read_exact(s3, receipt))
    games = payload.get("games")
    if not isinstance(games, list) or not games:
        raise ValueError("individual_bullpen_official_history_games_missing")
    seasons = {int(value) for value in pd.to_numeric(frame.get("season"), errors="coerce").dropna()}
    complete = {int(value) for value in receipt["complete_years"]}
    if not seasons.issubset(complete):
        raise ValueError("individual_bullpen_official_history_year_incomplete")
    if statcast_context is None:
        history = Features(games)
    else:
        rows = statcast_context.get("rows")
        if not isinstance(rows, list):
            raise ValueError("individual_bullpen_statcast_rows_invalid")
        history = Features(
            games,
            rows,
            statcast_complete=False,
            statcast_retained_dates=statcast_context.get("retained_dates"),
            statcast_verified_games=statcast_context.get("verified_games"),
            statcast_physical_dates=statcast_context.get("physical_dates"),
            statcast_physical_games=statcast_context.get("physical_games"),
        )
    return history, receipt, len(games)


def _ranked_values(profiles):
    eligible = []
    for profile in profiles or []:
        window_30 = (profile.get("windows") or {}).get("30d") or {}
        appearances = _finite(window_30.get("appearances"))
        player_id = str(profile.get("player_id") or "")
        if appearances is None or appearances <= 0 or not player_id:
            continue
        eligible.append((-appearances, player_id, profile))
    eligible.sort(key=lambda item: (item[0], item[1]))
    result = {name: None for name in FEATURES}
    for rank, (_, _, profile) in zip(RANKS, eligible[:len(RANKS)]):
        windows = profile.get("windows") or {}
        for days in WINDOWS:
            window = windows.get(f"{days}d") or {}
            for metric in METRICS:
                result[f"individual_bullpen_rank{rank}_{metric}_{days}d"] = _finite(
                    window.get(metric))
    return result


def _eligible(row):
    return (row.get("lineup_bullpen_context_evidence") == "historical_timecoded_mlb_feed"
            and row.get("historical_lineup_bullpen_context_status") in SUPPORTED)


def enrich_frame(frame: pd.DataFrame, s3, proof, minimum_nonmissing=300,
                 statcast_context=None, statcast_evidence=None):
    """Add deterministic individual-reliever features to a holdout-free frame.

    Source eligibility is independent of the game label. Exact pre-T10 team-context
    objects are read by their version id and byte hash. The official-history object is
    likewise exact-version bound to the same input proof. xwOBA is available only when
    the caller supplies proof-bound retained Statcast context.
    """
    enriched = frame.copy(deep=True)
    history, official_source, official_games = _history(
        s3, proof, enriched, statcast_context=statcast_context)
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
        "ranking_window_days": 30,
        "performance_windows_days": list(WINDOWS),
        "statcast_replay": statcast_evidence or {"enabled": False},
        "role_claimed": False,
        "label_dependent_selection": False,
        "provider_requests": 0,
        "prediction_writes": 0,
        "official_ledger_writes": 0,
    }
    return enriched, report
