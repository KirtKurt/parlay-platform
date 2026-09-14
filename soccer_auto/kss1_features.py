"""One point-in-time feature builder for goals training and shadow serving.

Availability is a receipt timestamp, never inferred from kickoff in production.
Research archives with assumed availability are explicitly marked and cannot
become a production-qualified model. No same-match statistics are features.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta
from typing import Any, Mapping

from .canonical import digest, iso_utc, parse_utc
from .kss1_identity import normalize_name

SCHEMA = "kss1-team-history-v1"
WINDOW_GAMES = 20
WINDOW_DAYS = 365
PRIOR_GAMES = 5
MIN_TEAM_GAMES = 5


def number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a goal observation")
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > 50:
        raise ValueError("invalid goal/xG observation")
    return result


def normalize_history(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate receipts, sort deterministically, reject conflicting duplicates."""
    found = {}
    for source in rows:
        row = dict(source)
        for key in ("event_key", "sport_key", "home_team", "away_team", "source_receipt"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"missing history {key}")
        if len(row["source_receipt"]) != 64 or any(c not in "0123456789abcdef" for c in row["source_receipt"]):
            raise ValueError("source receipt must be a SHA256 digest")
        row["home_team"] = normalize_name(row["home_team"])
        row["away_team"] = normalize_name(row["away_team"])
        if not row["home_team"] or not row["away_team"] or row["home_team"] == row["away_team"]:
            raise ValueError("invalid team identity")
        for key in ("commence_time", "available_at"):
            row[key] = iso_utc(parse_utc(row[key]))
        if parse_utc(row["available_at"]) <= parse_utc(row["commence_time"]):
            raise ValueError("final score cannot be available before kickoff")
        for key in ("home_score", "away_score"):
            value = number(row[key])
            if not value.is_integer():
                raise ValueError("final goals must be integers")
            row[key] = int(value)
        has_xg = row.get("home_xg") is not None and row.get("away_xg") is not None
        if has_xg:
            row["home_xg"], row["away_xg"] = number(row["home_xg"]), number(row["away_xg"])
            row["xg_available_at"] = iso_utc(parse_utc(row["xg_available_at"]))
            if parse_utc(row["xg_available_at"]) <= parse_utc(row["commence_time"]):
                raise ValueError("same-match observed xG cannot predate kickoff")
            receipt = row.get("xg_source_receipt", "")
            if len(receipt) != 64 or any(c not in "0123456789abcdef" for c in receipt):
                raise ValueError("xG requires its own source receipt")
        else:
            row["home_xg"] = row["away_xg"] = None
            row["xg_available_at"] = None
            row["xg_source_receipt"] = None
        row["provenance_mode"] = row.get("provenance_mode", "UNVERIFIED")
        if row["provenance_mode"] not in {"VERIFIED_RECEIPT", "DATE_ASSUMED_RESEARCH"}:
            raise ValueError("unverified history availability")
        prior = found.get(row["event_key"])
        if prior is not None and prior != row:
            raise ValueError("conflicting historical event")
        found[row["event_key"]] = row
    return sorted(found.values(), key=lambda r: (r["commence_time"], r["event_key"]))


class HistoryIndex:
    def __init__(self, rows: list[Mapping[str, Any]]):
        self.rows = normalize_history(rows)
        self.by_competition = defaultdict(list)
        for row in self.rows:
            self.by_competition[row["sport_key"]].append(row)

    def features(self, fixture: Mapping[str, Any], as_of: str) -> dict[str, Any]:
        cutoff = parse_utc(as_of)
        kickoff = parse_utc(fixture["commence_time"])
        if cutoff > kickoff - timedelta(minutes=60):
            raise ValueError("goals features must be frozen on or before T60")
        teams = [normalize_name(fixture[key]) for key in ("home_team", "away_team")]
        if not all(teams) or teams[0] == teams[1]:
            raise ValueError("invalid fixture identity")
        start = cutoff - timedelta(days=WINDOW_DAYS)
        eligible = []
        for original in self.by_competition.get(fixture["sport_key"], []):
            if original["event_key"] == fixture["event_key"]:
                continue
            if not start <= parse_utc(original["commence_time"]) < cutoff:
                continue
            if parse_utc(original["available_at"]) > cutoff:
                continue
            row = dict(original)
            if row["xg_available_at"] is None or parse_utc(row["xg_available_at"]) > cutoff:
                row["home_xg"] = row["away_xg"] = None
                row["xg_available_at"] = row["xg_source_receipt"] = None
            eligible.append(row)
        n = len(eligible)
        league_home = (sum(r["home_score"] for r in eligible) + 20 * 1.45) / (n + 20)
        league_away = (sum(r["away_score"] for r in eligible) + 20 * 1.15) / (n + 20)
        league_mean = (league_home + league_away) / 2
        xg_rows = [r for r in eligible if r["home_xg"] is not None]
        xg_mean = ((sum(r["home_xg"] + r["away_xg"] for r in xg_rows) / (2 * len(xg_rows))) if xg_rows else None)
        values = {"league_home": league_home, "league_away": league_away}
        counts = {}
        for side, team in zip(("home", "away"), teams):
            history = [r for r in eligible if team in (r["home_team"], r["away_team"])][-WINDOW_GAMES:]
            def total(field_home, field_away):
                return sum(r[field_home] if r["home_team"] == team else r[field_away] for r in history)
            counts[side] = len(history)
            values[f"{side}_attack"] = (total("home_score", "away_score") + PRIOR_GAMES * league_mean) / ((len(history) + PRIOR_GAMES) * league_mean)
            values[f"{side}_defence"] = (total("away_score", "home_score") + PRIOR_GAMES * league_mean) / ((len(history) + PRIOR_GAMES) * league_mean)
            xhistory = [r for r in history if r["home_xg"] is not None]
            counts[side + "_xg"] = len(xhistory)
            for kind, own, other in (("attack", "home_xg", "away_xg"), ("defence", "away_xg", "home_xg")):
                values[f"{side}_xg_{kind}"] = None
                if xhistory and xg_mean is not None and xg_mean > 0:
                    observed = sum(r[own] if r["home_team"] == team else r[other] for r in xhistory)
                    values[f"{side}_xg_{kind}"] = (observed + PRIOR_GAMES * xg_mean) / ((len(xhistory) + PRIOR_GAMES) * xg_mean)
        # Hash exactly the evidence used, excluding unavailable future xG fields.
        receipts = [{k: r[k] for k in ("event_key", "source_receipt", "available_at", "xg_source_receipt", "xg_available_at")} for r in eligible]
        used_at = [r["available_at"] for r in eligible] + [r["xg_available_at"] for r in eligible if r["xg_available_at"]]
        result = {
            "schema": SCHEMA, "event_key": fixture["event_key"],
            "sport_key": fixture["sport_key"], "home_team": teams[0], "away_team": teams[1],
            "commence_time": iso_utc(kickoff), "as_of": iso_utc(cutoff),
            "values": values, "counts": counts, "league_count": n,
            "team_strength_complete": min(counts["home"], counts["away"]) >= MIN_TEAM_GAMES,
            "xg_complete": min(counts["home_xg"], counts["away_xg"]) >= MIN_TEAM_GAMES,
            "source_receipts": receipts,
            "source_max_available_at": max(used_at, default=None),
            "research_only": any(r["provenance_mode"] != "VERIFIED_RECEIPT" for r in eligible),
            "window_games": WINDOW_GAMES, "window_days": WINDOW_DAYS,
        }
        result["feature_digest"] = digest(result)
        return result


def build_training_table(index: HistoryIndex) -> list[dict[str, Any]]:
    result = []
    for row in index.rows:
        as_of = iso_utc(parse_utc(row["commence_time"]) - timedelta(minutes=60))
        features = index.features(row, as_of)
        result.append({"event_key": row["event_key"], "commence_time": row["commence_time"],
                       "label_available_at": row["available_at"], "label_receipt": row["source_receipt"],
                       "home_score": row["home_score"], "away_score": row["away_score"],
                       "features": features, "research_only": features["research_only"] or row["provenance_mode"] != "VERIFIED_RECEIPT"})
    return result
