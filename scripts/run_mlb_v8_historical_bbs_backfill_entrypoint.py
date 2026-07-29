#!/usr/bin/env python3
"""Operational entrypoint for the historical BBS fundamentals backfill.

The optional V8 fundamentals stack may not exist yet. Historical BBS learning does
not require that Lambda stack; it only requires an immutable S3 bucket. When the
isolated stack is absent, this adapter maps its expected bucket output to the live
historical optimizer's versioned artifacts bucket. Historical game discovery uses
BBS's stored-match surface and begins with the newest canonical games because the
provider's current MLB archive is 2026-first rather than complete for early 2025.

Historical resources are routed only to public BBS surfaces that actually exist:
stored lineups and stored match stats. Missing historical injuries, weather, or park
snapshots stay explicit and cannot silently become training inputs.
"""
from __future__ import annotations

import copy
import json
import os
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from botocore.exceptions import ClientError

import run_mlb_v8_historical_bbs_backfill as backfill

VERSION = "MLB-V8-HISTORICAL-BBS-OPERATIONAL-ENTRYPOINT-v4-resource-surfaces"


def _stack_missing(exc: ClientError) -> bool:
    error = exc.response.get("Error") or {}
    return bool(
        str(error.get("Code") or "") == "ValidationError"
        and "does not exist" in str(error.get("Message") or exc).lower()
    )


def install_bucket_fallback(
    module: Any,
    *,
    historical_stack: str,
    fundamentals_stack: str,
) -> Any:
    """Use the historical artifacts bucket only when the optional V8 stack is absent."""
    if getattr(module, "_INQSI_HISTORICAL_BBS_BUCKET_FALLBACK_INSTALLED", False):
        return module
    original_outputs = module._outputs

    def outputs(cloudformation: Any, stack_name: str):
        try:
            return original_outputs(cloudformation, stack_name)
        except ClientError as exc:
            if stack_name != fundamentals_stack or not _stack_missing(exc):
                raise
            historical = original_outputs(cloudformation, historical_stack)
            bucket = str(historical.get("HistoricalArtifactsBucketName") or "").strip()
            if not bucket:
                raise RuntimeError(
                    "historical BBS fallback artifacts bucket output is missing"
                ) from exc
            return {
                "FundamentalsArtifactsBucketName": bucket,
                "HistoricalBbsManifestBucketSource": VERSION,
            }

    module._outputs = outputs
    module._INQSI_HISTORICAL_BBS_BUCKET_FALLBACK_INSTALLED = True
    return module


def install_stored_match_surface(client_class: Any) -> Any:
    """Force historical discovery through `/v1/stored/matches`."""
    if getattr(client_class, "_INQSI_HISTORICAL_BBS_STORED_MATCHES_INSTALLED", False):
        return client_class
    original = client_class.list_mlb_matches

    def list_stored(
        self: Any,
        game_date: str,
        *,
        limit: int = 50,
        as_of: str | None = None,
        stored: bool = False,
    ):
        return original(
            self,
            game_date,
            limit=limit,
            as_of=as_of,
            stored=True,
        )

    client_class.list_mlb_matches = list_stored
    client_class._INQSI_HISTORICAL_BBS_STORED_MATCHES_INSTALLED = True
    return client_class


def _side(value: Any, side: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    for key in (side, f"{side}Team", f"{side}_team"):
        row = value.get(key)
        if isinstance(row, Mapping):
            return row
    return {}


def _first_mapping(value: Mapping[str, Any], names: Sequence[str]) -> Mapping[str, Any]:
    for name in names:
        row = value.get(name)
        if isinstance(row, Mapping):
            return row
    return {}


def _first_list(value: Mapping[str, Any], names: Sequence[str]) -> list[Any]:
    for name in names:
        row = value.get(name)
        if isinstance(row, list):
            return row
    return []


def _adapt_lineup_payload(payload: Mapping[str, Any], resource: str) -> dict[str, Any]:
    data = payload.get("data")
    root = data if isinstance(data, Mapping) else {}
    adapted: dict[str, Any] = {}
    for side in ("away", "home"):
        side_row = _side(root, side)
        if resource == "pitchers":
            pitcher = _first_mapping(
                side_row,
                (
                    "startingPitcher",
                    "starting_pitcher",
                    "starter",
                    "probablePitcher",
                    "probable_pitcher",
                    "pitcher",
                ),
            )
            if not pitcher:
                pitcher = _first_mapping(
                    root,
                    (
                        f"{side}StartingPitcher",
                        f"{side}_starting_pitcher",
                        f"{side}Starter",
                        f"{side}_starter",
                    ),
                )
            adapted[side] = copy.deepcopy(dict(pitcher))
        else:
            players = _first_list(
                side_row,
                ("lineup", "players", "batters", "battingOrder", "batting_order"),
            )
            if not players:
                players = _first_list(
                    root,
                    (
                        f"{side}Lineup",
                        f"{side}_lineup",
                        f"{side}BattingOrder",
                        f"{side}_batting_order",
                    ),
                )
            confirmed = side_row.get("confirmed")
            if confirmed is None:
                confirmed = root.get(f"{side}Confirmed")
            adapted[side] = {
                "players": copy.deepcopy(players),
                "confirmed": confirmed,
            }
    out = dict(payload)
    out["data"] = adapted
    return out


def _adapt_stats_payload(payload: Mapping[str, Any], resource: str) -> dict[str, Any]:
    data = payload.get("data")
    root = data if isinstance(data, Mapping) else {}
    adapted: dict[str, Any] = {}
    for side in ("away", "home"):
        side_row = _side(root, side)
        if resource == "bullpens":
            bullpen = _first_mapping(
                side_row,
                ("bullpen", "reliefPitching", "relief_pitching", "pitching"),
            )
            adapted[side] = copy.deepcopy(dict(bullpen or side_row))
        else:
            team_stats = _first_mapping(
                side_row,
                ("teamStats", "team_stats", "stats", "totals"),
            )
            adapted[side] = copy.deepcopy(dict(team_stats or side_row))
    out = dict(payload)
    out["data"] = adapted
    return out


def install_historical_resource_surfaces(client_class: Any) -> Any:
    """Route historical resources to BBS's actual stored endpoints.

    The adapter does not convert current injuries or completed-game statistics into
    pregame evidence. The existing effective-time gate must still prove each returned
    payload was available by the target game's immutable T-45 lock.
    """
    if getattr(client_class, "_INQSI_HISTORICAL_BBS_RESOURCE_SURFACES_INSTALLED", False):
        return client_class
    original = client_class.get_mlb_match_resource

    def historical_resource(
        self: Any,
        match_id: str,
        resource: str,
        *,
        game_date: str | None = None,
        as_of: str | None = None,
    ):
        name = str(resource).strip().lower()
        if name in {"injuries", "weather", "park"}:
            raise backfill.BBSClientError(
                f"BBS_HISTORICAL_{name.upper()}_POINT_IN_TIME_UNAVAILABLE"
            )
        if name not in {"pitchers", "lineups", "bullpens", "team_context"}:
            return original(
                self,
                match_id,
                resource,
                game_date=game_date,
                as_of=as_of,
            )
        safe_id = urllib.parse.quote(str(match_id).strip(), safe="")
        endpoint = (
            f"/v1/stored/matches/{safe_id}/lineups"
            if name in {"pitchers", "lineups"}
            else f"/v1/stored/matches/{safe_id}/stats"
        )
        params: dict[str, Any] = {
            "sport": "baseball",
            "league": "mlb",
            "date": game_date,
        }
        if as_of:
            params[backfill.BigBallsDataClient.__module__ and "as_of"] = as_of
        cache = getattr(self, "_inqsi_historical_bbs_resource_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_inqsi_historical_bbs_resource_cache", cache)
        cache_key = (endpoint, tuple(sorted((str(k), str(v)) for k, v in params.items() if v is not None)))
        if cache_key not in cache:
            payload, headers = self._request(endpoint, params)
            value = dict(payload)
            value["_transport"] = self._transport(
                headers,
                requested_date=game_date,
                requested_as_of=as_of,
                endpoint=endpoint,
            )
            cache[cache_key] = value
        payload = copy.deepcopy(cache[cache_key])
        return (
            _adapt_lineup_payload(payload, name)
            if name in {"pitchers", "lineups"}
            else _adapt_stats_payload(payload, name)
        )

    client_class.get_mlb_match_resource = historical_resource
    client_class._INQSI_HISTORICAL_BBS_RESOURCE_SURFACES_INSTALLED = True
    return client_class


def install_newest_coverage_window(module: Any) -> Any:
    """Probe the newest unprocessed games first without consulting outcomes."""
    if getattr(module, "_INQSI_HISTORICAL_BBS_NEWEST_WINDOW_INSTALLED", False):
        return module
    original = module._load_canonical_games

    def newest_first(state: Mapping[str, Any], s3: Any):
        return list(reversed(original(state, s3)))

    module._load_canonical_games = newest_first
    module._INQSI_HISTORICAL_BBS_NEWEST_WINDOW_INSTALLED = True
    return module


def _shape(value: Any, depth: int = 0) -> Any:
    """Return a value-free structural signature, capped to two levels."""
    if depth >= 2:
        if isinstance(value, Mapping):
            return "object"
        if isinstance(value, list):
            return f"array[{len(value)}]"
        if value is None:
            return "null"
        return type(value).__name__
    if isinstance(value, Mapping):
        return {
            str(key): _shape(value[key], depth + 1)
            for key in sorted(value, key=str)[:30]
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "count": len(value),
            "item": _shape(value[0], depth + 1) if value else "empty",
        }
    if value is None:
        return "null"
    return type(value).__name__


def install_safe_diagnostics(module: Any) -> Any:
    """Add value-free coverage, endpoint, and eligibility counts to the report."""
    if getattr(module, "_INQSI_HISTORICAL_BBS_DIAGNOSTICS_INSTALLED", False):
        return module

    original_crosswalk = module.crosswalk_provider_rows
    original_snapshot = module.build_training_snapshot
    original_run = module.run
    discovery: list[dict[str, Any]] = []
    eligibility_errors: Counter[str] = Counter()
    resource_errors: Counter[str] = Counter()
    resource_shapes: dict[str, Any] = {}

    def crosswalk(
        provider_rows: Sequence[Mapping[str, Any]],
        canonical_games: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ):
        result = original_crosswalk(provider_rows, canonical_games, **kwargs)
        day = str(canonical_games[0].get("slateDateEt") or "") if canonical_games else ""
        discovery.append(
            {
                "slateDateEt": day,
                "providerRowCount": len(provider_rows),
                "canonicalGameCount": len(canonical_games),
                "acceptedCrosswalkCount": int(result.get("acceptedCount") or 0),
                "quarantinedProviderRowCount": int(result.get("quarantinedCount") or 0),
                "unmatchedCanonicalGameCount": max(
                    0,
                    len(canonical_games) - int(result.get("acceptedCount") or 0),
                ),
            }
        )
        return result

    def snapshot(*args: Any, **kwargs: Any):
        resources = args[3] if len(args) > 3 and isinstance(args[3], Mapping) else kwargs.get("resources") or {}
        for name, envelope in resources.items():
            if not isinstance(envelope, Mapping):
                resource_errors[f"{name}:invalid_envelope"] += 1
                continue
            error = envelope.get("error")
            if error:
                resource_errors[f"{name}:{str(error)[:120]}"] += 1
            elif name not in resource_shapes:
                resource_shapes[str(name)] = _shape(envelope.get("data"))
        value = original_snapshot(*args, **kwargs)
        for error in value.get("eligibilityErrors") or []:
            eligibility_errors[str(error)] += 1
        return value

    def run(*args: Any, **kwargs: Any):
        discovery.clear()
        eligibility_errors.clear()
        resource_errors.clear()
        resource_shapes.clear()
        report = original_run(*args, **kwargs)
        report["selectionOrder"] = "newest_unprocessed_canonical_games_first"
        report["providerDiscovery"] = list(discovery)
        report["providerDiscoveryDateCount"] = len(discovery)
        report["providerRowsReturned"] = sum(
            int(row.get("providerRowCount") or 0) for row in discovery
        )
        report["acceptedCrosswalkCount"] = sum(
            int(row.get("acceptedCrosswalkCount") or 0) for row in discovery
        )
        report["unmatchedCanonicalGameCount"] = sum(
            int(row.get("unmatchedCanonicalGameCount") or 0) for row in discovery
        )
        report["eligibilityErrorCounts"] = dict(sorted(eligibility_errors.items()))
        report["resourceErrorCounts"] = dict(sorted(resource_errors.items()))
        report["resourceDataShapes"] = dict(sorted(resource_shapes.items()))
        report["diagnosticsContainProviderValues"] = False
        output = kwargs.get("output")
        if isinstance(output, Path):
            output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report

    module.crosswalk_provider_rows = crosswalk
    module.build_training_snapshot = snapshot
    module.run = run
    module._INQSI_HISTORICAL_BBS_DIAGNOSTICS_INSTALLED = True
    return module


def main() -> int:
    install_bucket_fallback(
        backfill,
        historical_stack=os.environ.get(
            "HISTORICAL_STACK", backfill.DEFAULT_HISTORICAL_STACK
        ),
        fundamentals_stack=os.environ.get(
            "FUNDAMENTALS_STACK", backfill.DEFAULT_FUNDAMENTALS_STACK
        ),
    )
    install_stored_match_surface(backfill.BigBallsDataClient)
    install_historical_resource_surfaces(backfill.BigBallsDataClient)
    install_newest_coverage_window(backfill)
    install_safe_diagnostics(backfill)
    return backfill.main()


if __name__ == "__main__":
    raise SystemExit(main())
