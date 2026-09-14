"""Auditable retrospective pitcher inputs from retained, strictly prior boxes.

Rotation projections are predictions of identity, never confirmed starters.
Source retrieval may postdate the target; effective game times may not. Later
official scoring corrections remain an explicit retrospective limitation.
"""
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import json
import re

from ks1.features import day, number, official_context_pitching, utc
from ks1.inventory import encode, RESEARCH
from ks1.historical_starters import published_starter_index

VERSION = "KS1-prior-pitcher-reconstruction-v1"
FIELDS = ("quality", "recent_form", "velocity", "command", "expected_innings")


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def valid_source(source):
    try:
        return bool(source.get("bucket") and
                    str(source.get("key", "")).startswith(RESEARCH) and
                    source.get("versionId") not in (None, "", "null") and
                    re.fullmatch("[0-9a-f]{64}", str(source.get("sha256", ""))) and
                    source.get("provider") == "MLB Stats API" and
                    utc(source["retrieved_at"]) and source.get("complete_years"))
    except (KeyError, TypeError, ValueError):
        return False


def summarize(entries):
    # Same current-season starts (or appearances) and formulas as Features.at.
    ordered = sorted(entries, key=lambda x: (x[0]["start"], x[0]["game_id"]))
    starts = [x for x in ordered if number(x[1].get("gamesStarted")) == 1]
    ordered = starts or ordered
    season = official_context_pitching([stats for _, stats in ordered])
    recent = official_context_pitching([stats for _, stats in ordered[-3:]])
    outs = [number(stats.get("outs")) for _, stats in ordered[-5:]]
    return {"quality": season["quality"], "command": season["command"],
            "recent_form": recent["command"] if recent["command"] is not None else recent["quality"],
            "velocity": None,
            "expected_innings": round(sum(outs)/(3*len(outs)), 3)
            if outs and all(x is not None for x in outs) else None}


def pregame_identity_index(bundle):
    """Independent pregame identities from the retained source observations."""
    result = defaultdict(list)
    for pk, entry in published_starter_index(bundle.get('published_predictions', [])).items():
        for side, pitcher in entry['sides'].items():
            result[(pk, side)].append({
                'pitcher_id': pitcher['id'], 'team_id': entry['teams'][side],
                'as_of': entry['as_of'], 'commence_time': entry['commence_time'],
                'source': entry['source']})
    receipts = {r['key']: r for r in bundle.get('source_receipts', [])}
    for snapshot in bundle.get('snapshots', []):
        try:
            at, cutoff, start = (utc(snapshot[k]) for k in
                                 ('capturedAtUtc', 'featureCutoffUtc', 'commenceTime'))
            source = receipts.get(snapshot.get('source_key'))
            if (snapshot.get('originalObservation') is not True
                    or snapshot.get('outcomeKnownAtCapture') is not False
                    or not at <= cutoff <= start-timedelta(minutes=10)
                    or not source or not source.get('versionId')
                    or source['versionId'] == 'null'
                    or digest(snapshot['features']) != snapshot['featureFingerprint']):
                continue
            for side, team in snapshot.get('playerWindows', {}).get('teams', {}).items():
                pid = team.get('starterId')
                players = [p for p in team.get('players', []) if str(p['id']) == str(pid)]
                if side in ('home', 'away') and pid and len(players) == 1:
                    result[(str(snapshot['officialGamePk']), side)].append({
                        'pitcher_id': str(pid), 'team_id': str(team['teamId']),
                        'as_of': snapshot['capturedAtUtc'], 'commence_time': snapshot['commenceTime'],
                        'source': source})
        except (KeyError, TypeError, ValueError):
            continue
    return result


class PriorPitcherContext:
    def __init__(self, rows, source, pregame=None):
        self.source = source if valid_source(source) else None
        self.pregame = pregame or {}
        self.teams, self.pitchers = defaultdict(list), defaultdict(list)
        for row in rows:
            self.teams[row["team_id"]].append(row)
            for player in row["players"]:
                self.pitchers[player["id"]].append((row, player["stats"]))

    def at(self, row, side):
        cutoff, target = utc(row["as_of_timestamp"]), date.fromisoformat(row["date"])
        if not self.source or target.year not in self.source["complete_years"]:
            return None
        team_id, game_id = str(row[side+"_id"]), str(row["game_id"])

        def prior(game):
            return (game["game_id"] != game_id and game["day"] < target
                    and game["completed"] < cutoff)

        pitcher_id = row.get(side+"_starter_id")
        if str(pitcher_id) in ('nan', '<NA>'):
            pitcher_id = None
        identity_mode = "observed_pregame_identity_reconstructed_stats"
        identity_evidence = None
        selection_games = []
        if pitcher_id is not None:
            identities = [entry for entry in self.pregame.get((game_id, side), [])
                          if entry['team_id'] == team_id
                          and utc(entry['commence_time']) == utc(row['commence_time'])
                          and utc(entry['as_of']) <= cutoff]
            if not identities:
                return None
            identity_evidence = max(identities, key=lambda entry: utc(entry['as_of']))
            if str(pitcher_id) != identity_evidence['pitcher_id']:
                return None
        if pitcher_id is None:
            # Fixed, outcome-independent cadence rule used by the retained V8
            # reconstruction: last 18 team games, 4-10 days rest, closest to 5.
            identity_mode = "strict_prior_rotation_projection"
            games = sorted((g for g in self.teams[team_id] if prior(g)
                            and g["day"] >= target-timedelta(days=120)),
                           key=lambda g: (g["start"], g["game_id"]))[-18:]
            if len(games) < 5:
                return None
            candidates = defaultdict(list)
            for game in games:
                for player in game["players"]:
                    if number(player["stats"].get("gamesStarted")) == 1:
                        candidates[player["id"]].append(game)
            ranked = []
            for pid, starts in candidates.items():
                last = max(g["day"] for g in starts)
                rest = (target-last).days
                if 4 <= rest <= 10:
                    ranked.append((abs(rest-5), -min(len(starts), 6), -last.toordinal(), pid))
            if not ranked:
                return None
            pitcher_id = min(ranked)[-1]
            selection_games = games
        entries = [(g, stats) for g, stats in self.pitchers[str(pitcher_id)]
                   if prior(g) and g["day"].year == target.year]
        metrics = summarize(entries)
        if any(metrics[key] is None for key in FIELDS if key != "velocity"):
            return None
        source_games = {g["game_id"]: g for g, _ in entries}
        source_games.update({g["game_id"]: g for g in selection_games})
        # Bind every effective source game and the actual prior pitching counts.
        evidence = {
            "version": VERSION, "game_id": game_id, "team_id": team_id, "side": side,
            "pitcher_id": str(pitcher_id), "identity_mode": identity_mode,
            "identity_evidence": identity_evidence,
            "as_of": row["as_of_timestamp"], "target_date": row["date"],
            "source": self.source, "metrics": metrics,
            "inputs": [{"game_id": key, "date": str(g["day"]),
                        "completed_at": g["completed"].isoformat()}
                       for key, g in sorted(source_games.items())],
            "pitching_counts_sha256": digest([
                {"game_id": g["game_id"], "stats": stats} for g, stats in
                sorted(entries, key=lambda item: (item[0]["start"], item[0]["game_id"]))]),
            "target_outcome_used": False, "historical_corrections_possible": True,
        }
        evidence["sha256"] = digest(evidence)
        return evidence


def verified_reconstruction(row, reconstruction=None):
    """Recompute both sides against the independently loaded official artifact.

    Self-signed table metadata is not an authenticated source. Without the
    retained box-score reconstruction engine, qualification fails closed.
    """
    if reconstruction is None or reconstruction.source is None:
        return False
    try:
        proofs = json.loads(row["reconstructed_pitcher_context_proof"])
        cutoff, start = utc(row["as_of_timestamp"]), utc(row["commence_time"])
        if cutoff > start-timedelta(minutes=10):
            return False
        for side in ("home", "away"):
            proof = proofs[side]
            material = {k: v for k, v in proof.items() if k != "sha256"}
            if (proof.get("sha256") != digest(material) or proof.get("version") != VERSION
                    or not valid_source(proof["source"])
                    or proof["source"] != reconstruction.source
                    or set(proof["metrics"]) != set(FIELDS)
                    or not re.fullmatch('[0-9a-f]{64}', str(proof.get('pitching_counts_sha256', '')))
                    or proof["game_id"] != str(row["game_id"])
                    or proof["team_id"] != str(row[side+"_id"]) or proof["side"] != side
                    or proof["as_of"] != row["as_of_timestamp"]
                    or proof["target_date"] != row["date"]
                    or int(row["date"][:4]) not in proof["source"]["complete_years"]
                    or proof["target_outcome_used"] is not False):
                return False
            pid = row.get(side+"_starter_id")
            # Pandas represents missing parquet identities as None/NaN.
            missing = pid is None or str(pid) in ("nan", "<NA>")
            if proof["identity_mode"] == "strict_prior_rotation_projection":
                if not missing:
                    return False
            elif proof["identity_mode"] == "observed_pregame_identity_reconstructed_stats":
                if missing or str(pid) != proof["pitcher_id"] or not str(
                        row.get(side+"_starter_status", "")).startswith("observed_"):
                    return False
            else:
                return False
            if not proof["inputs"] or any(
                    x["game_id"] == str(row["game_id"]) or x["date"] >= row["date"]
                    or utc(x["completed_at"]) >= cutoff for x in proof["inputs"]):
                return False
            for key, value in proof["metrics"].items():
                actual = row.get(side+"_pitcher_context_"+key)
                missing_value = actual is None or str(actual) in ('nan', '<NA>')
                if ((value is None and not missing_value)
                        or (value is not None and (missing_value or isinstance(value, bool)
                                                  or float(actual) != value))):
                    return False
            # Re-run fixed selection and aggregation from the exact full boxes
            # admitted by Reader; this also binds candidate membership, rest,
            # source-game completeness, and all prior pitching counts.
            if reconstruction.at(row, side) != proof:
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False
