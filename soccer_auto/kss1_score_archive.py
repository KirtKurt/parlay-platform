"""Admission of independently witnessed OpenFootball score versions.

Git dates and caller-supplied availability flags are never source evidence.
The installer rechecks Software Heritage over HTTPS; runtime reads only the
installed, content-addressed bundle and rederives rows from its raw Git objects.
"""
from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .canonical import digest, iso_utc, parse_utc
from .kss1_features import normalize_history
from .kss1_identity import normalize_name

ORIGIN = "https://github.com/openfootball/football.json"
API = "https://archive.softwareheritage.org/api/1/"
SCHEMA = "kss1-openfootball-swh-v1"
ARCHIVE_KEY = {"PK": "MODEL#kss1_goals#global", "SK": "VERIFIED_SCORE_ARCHIVE"}
# The upstream sportdb league timezone table defines local wall-clock times.
# Explicit country leagues only: no cups, playoffs, multi-zone competitions.
LEAGUES = {
    "en.1": ("soccer_epl", "Europe/London"),
    "de.1": ("soccer_germany_bundesliga", "Europe/Berlin"),
    "es.1": ("soccer_spain_la_liga", "Europe/Madrid"),
    "it.1": ("soccer_italy_serie_a", "Europe/Rome"),
    "fr.1": ("soccer_france_ligue_one", "Europe/Paris"),
}
SEASONS = ("2023-24", "2024-25", "2025-26")
LEAGUE_NAMES = {
    "en.1": ("English Premier League", "Premier League"),
    "de.1": ("Deutsche Bundesliga", "Bundesliga"),
    "es.1": ("Spain Primera División", "Primera División", "Primera División de España"),
    "it.1": ("Italian Serie A", "Serie A"), "fr.1": ("French Ligue 1", "Ligue 1"),
}
PATHS = tuple(f"{year}/{league}.json" for year in SEASONS for league in LEAGUES)
HEX40 = re.compile(r"^[0-9a-f]{40}$")


def git_hash(kind, raw):
    return hashlib.sha1(f"{kind} {len(raw)}\0".encode() + raw).hexdigest()


def snapshot_hash(snapshot):
    """Software Heritage's published snapshot manifest format (not Git dates)."""
    if snapshot.get("next_branch"):
        raise ValueError("ARCHIVE_PARTIAL_SNAPSHOT")
    parts = []
    for name, branch in sorted(snapshot["branches"].items(), key=lambda item: item[0].encode()):
        if branch is None:
            kind, target = "dangling", b""
        elif branch["target_type"] == "alias":
            kind, target = "alias", branch["target"].encode()
            if branch["target"] == name or branch["target"] not in snapshot["branches"]:
                raise ValueError("ARCHIVE_UNRESOLVED_ALIAS")
        else:
            kind = branch["target_type"]
            if kind not in {"content", "directory", "revision", "release", "snapshot"} or not HEX40.fullmatch(branch["target"]):
                raise ValueError("ARCHIVE_INVALID_SNAPSHOT_TARGET")
            target = bytes.fromhex(branch["target"])
        parts.append(kind.encode() + b" " + name.encode() + b"\0" + str(len(target)).encode() + b":" + target)
    return git_hash("snapshot", b"".join(parts))


def fetch_visit_inventory(fetch_json):
    visits = {}
    url = API + f"origin/{ORIGIN}/visits/?per_page=100"
    while True:
        page = fetch_json(url)
        if not isinstance(page, list):
            raise ValueError("ARCHIVE_INVALID_VISIT_INVENTORY")
        for visit in page:
            if type(visit.get("visit")) is not int or visit["visit"] in visits:
                raise ValueError("ARCHIVE_REPEATED_OR_INVALID_VISIT")
            visits[visit["visit"]] = visit
        if len(page) < 100:
            return visits
        url = API + f"origin/{ORIGIN}/visits/?per_page=100&last_visit={page[-1]['visit']}"


def object_bytes(bundle, oid, kind):
    if not HEX40.fullmatch(str(oid)):
        raise ValueError("ARCHIVE_INVALID_OBJECT_ID")
    obj = bundle["objects"][oid]
    raw = base64.b64decode(obj["base64"], validate=True)
    if obj["type"] != kind or git_hash(kind, raw) != oid:
        raise ValueError("ARCHIVE_GIT_OBJECT_MISMATCH")
    return raw


def tree_entries(raw):
    entries = {}
    offset = 0
    while offset < len(raw):
        end = raw.index(b"\0", offset)
        mode, name = raw[offset:end].split(b" ", 1)
        if len(raw[end + 1:end + 21]) != 20 or name in entries:
            raise ValueError("ARCHIVE_INVALID_TREE")
        entries[name] = (mode, raw[end + 1:end + 21].hex())
        offset = end + 21
    return entries


def witness_revision(witness):
    visit, snapshot = witness["visit"], witness["snapshot"]
    if (visit.get("origin") != ORIGIN or visit.get("status") != "full"
            or visit.get("type") != "git" or type(visit.get("visit")) is not int
            or snapshot.get("id") != visit.get("snapshot")
            or snapshot_hash(snapshot) != visit.get("snapshot")):
        raise ValueError("ARCHIVE_INVALID_INDEPENDENT_WITNESS")
    branch = snapshot["branches"]["refs/heads/master"]
    if branch.get("target_type") != "revision":
        raise ValueError("ARCHIVE_INVALID_BRANCH")
    parse_utc(visit["date"])
    return branch["target"]


def capture_bytes(bundle, capture):
    if capture["path"] not in PATHS:
        raise ValueError("ARCHIVE_UNAPPROVED_COMPETITION_OR_SEASON")
    witness = bundle["witnesses"][capture["witness"]]
    revision = witness_revision(witness)
    commit = object_bytes(bundle, revision, "commit")
    first = commit.split(b"\n", 1)[0]
    if not first.startswith(b"tree "):
        raise ValueError("ARCHIVE_INVALID_COMMIT")
    oid = first[5:].decode("ascii")
    parts = capture["path"].split("/")
    for index, part in enumerate(parts):
        mode, oid = tree_entries(object_bytes(bundle, oid, "tree"))[part.encode()]
        expected = b"40000" if index < len(parts) - 1 else b"100644"
        if mode != expected:
            raise ValueError("ARCHIVE_UNSAFE_FILE_MODE")
    if oid != capture["blob"]:
        raise ValueError("ARCHIVE_FILE_NOT_IN_WITNESSED_REVISION")
    return object_bytes(bundle, oid, "blob"), witness


def exact_kickoff(day, clock, zone):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)) or not re.fullmatch(r"\d{2}:\d{2}", str(clock)):
        raise ValueError("MISSING_EXACT_KICKOFF")
    wall = datetime.fromisoformat(day + "T" + clock)
    tz = ZoneInfo(zone)
    candidates = {wall.replace(tzinfo=tz, fold=f).astimezone(timezone.utc)
                  for f in (0, 1)
                  if wall.replace(tzinfo=tz, fold=f).astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == wall}
    if len(candidates) != 1:
        raise ValueError("AMBIGUOUS_OR_NONEXISTENT_KICKOFF")
    return iso_utc(candidates.pop())


def extract_history(bundle):
    """Rebuild every row from raw proof; no normalized rows are trusted."""
    if bundle.get("schema") != SCHEMA or bundle.get("origin") != ORIGIN:
        raise ValueError("ARCHIVE_SCHEMA_OR_ORIGIN_MISMATCH")
    if not bundle.get("witnesses") or not bundle.get("captures"):
        raise ValueError("ARCHIVE_EMPTY_EVIDENCE")
    retrieved = parse_utc(bundle["retrieved_at"])
    aliases = json.loads(Path(__file__).with_name("kss1_archive_teams.json").read_text())
    versions, rejected, invalid_files, ineligible = {}, Counter(), [], set()
    captures_seen = set()
    for capture in bundle["captures"]:
        raw, witness = capture_bytes(bundle, capture)
        available = iso_utc(parse_utc(witness["visit"]["date"]))
        if parse_utc(available) > retrieved:
            raise ValueError("ARCHIVE_WITNESS_FROM_FUTURE")
        ident = (capture["witness"], capture["path"])
        if ident in captures_seen:
            raise ValueError("ARCHIVE_DUPLICATE_CAPTURE")
        captures_seen.add(ident)
        league = capture["path"].split("/")[1][:-5]
        sport, zone = LEAGUES[league]
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            invalid_files.append({**capture, "reason": "INVALID_SOURCE_JSON"})
            continue
        if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
            invalid_files.append({**capture, "reason": "INVALID_MATCH_LIST"})
            continue
        season = capture["path"].split("/")[0]
        if data.get("name") not in {f"{name} {season.replace('-', '/')}" for name in LEAGUE_NAMES[league]}:
            invalid_files.append({**capture, "reason": "COMPETITION_IDENTITY_MISMATCH"})
            continue
        raw_digest = hashlib.sha256(raw).hexdigest()
        for match in data["matches"]:
            score = match.get("score") or {}
            if "ft" not in score:
                rejected["NO_FINAL_SCORE"] += 1
                continue
            # Do not infer regulation results from extra time, penalties,
            # awarded/abandoned games or formats carrying unknown score fields.
            if (set(score) - {"ft", "ht"} or set(match) - {"round", "date", "time", "team1", "team2", "score"}
                    or not re.fullmatch(r"Matchday \d+", str(match.get("round")))):
                rejected["REGULATION_SEMANTICS_UNRESOLVED"] += 1
                # A flagged score version cannot be resurrected through an
                # earlier unflagged snapshot of that same fixture.
                try:
                    ineligible.add((sport, season, normalize_name(aliases[sport][match["team1"]]),
                                    normalize_name(aliases[sport][match["team2"]])))
                except KeyError:
                    pass
                continue
            if (not isinstance(score["ft"], list) or len(score["ft"]) != 2
                    or any(type(g) is not int or not 0 <= g <= 50 for g in score["ft"])):
                rejected["INVALID_FINAL_SCORE"] += 1
                continue
            try:
                kickoff = exact_kickoff(match.get("date"), match.get("time"), zone)
                home, away = (aliases[sport][match[k]] for k in ("team1", "team2"))
            except (ValueError, KeyError) as exc:
                rejected["UNKNOWN_TEAM" if isinstance(exc, KeyError) else str(exc)] += 1
                continue
            if parse_utc(kickoff) >= parse_utc(available):
                rejected["SCORE_NOT_AFTER_KICKOFF"] += 1
                continue
            year = int(season[:4])
            if not f"{year}-07-01" <= match["date"] < f"{year+1}-07-01":
                rejected["MATCH_OUTSIDE_SEASON"] += 1
                continue
            # Same season/home/away must be one league fixture. This also
            # quarantines kickoff revisions, not just differing score values.
            identity = (sport, season, normalize_name(home), normalize_name(away))
            event = "OPENFOOTBALL#" + digest(identity)
            evidence = {"origin": ORIGIN, "visit": witness["visit"]["visit"],
                        "snapshot": witness["visit"]["snapshot"], "revision": witness_revision(witness),
                        "path": capture["path"], "blob": capture["blob"],
                        "raw_sha256": raw_digest,
                        "available_at": available, "match": match,
                        "timezone": zone, "canonical_teams": [home, away]}
            row = {"event_key": event, "sport_key": sport, "home_team": home, "away_team": away,
                   "commence_time": kickoff, "available_at": available,
                   "home_score": score["ft"][0], "away_score": score["ft"][1],
                   "source_receipt": digest(evidence), "source": "openfootball_software_heritage",
                   "provenance_mode": "VERIFIED_RECEIPT", "archive_evidence": evidence,
                   "home_xg": None, "away_xg": None}
            variant = (kickoff, *score["ft"])
            variants = versions.setdefault(identity, {})
            old = variants.get(variant)
            if old is None or available < old["available_at"]:
                variants[variant] = row
    conflicts = [v for identity, v in versions.items() if len(v) != 1 or identity in ineligible]
    quarantined_seasons = sorted(ineligible | {identity for identity, v in versions.items() if len(v) != 1})
    conflicted_identities = sorted({(r["sport_key"], normalize_name(r["home_team"]),
                                    normalize_name(r["away_team"]), r["commence_time"][:10])
                                   for variants in conflicts for r in variants.values()})
    rows = normalize_history([next(iter(v.values())) for identity, v in versions.items()
                              if len(v) == 1 and identity not in ineligible])
    return rows, {"source": "openfootball_software_heritage", "witnesses": len(bundle["witnesses"]),
                  "captures": len(captures_seen), "accepted_scores": len(rows),
                  "invalid_files": invalid_files,
                  "conflicted_events": len(conflicts), "excluded_observations": dict(rejected),
                  "regulation_flagged_identities": len(ineligible),
                  "conflicted_identities": conflicted_identities,
                  "quarantined_season_identities": quarantined_seasons,
                  "accepted_by_competition": dict(Counter(r["sport_key"] for r in rows)),
                  "oldest_available_at": min((r["available_at"] for r in rows), default=None),
                  "newest_available_at": max((r["available_at"] for r in rows), default=None)}


def verify_remote_witnesses(bundle, fetch_json):
    """Required at installation: never admit a caller's invented visit date."""
    # One paginated inventory binds dates to intrinsic snapshot hashes. Hashing
    # each complete snapshot then binds its revision without 2N API rereads.
    inventory = fetch_visit_inventory(fetch_json)
    for witness in bundle["witnesses"]:
        witness_revision(witness)
        visit = witness["visit"]
        live = inventory.get(visit["visit"], {})
        if any(live.get(k) != visit.get(k) for k in ("origin", "visit", "date", "status", "snapshot", "type")):
            raise ValueError("ARCHIVE_WITNESS_NOT_INDEPENDENTLY_VERIFIED")


def load_archive(store):
    from .storage import plain
    pointer = plain(store.models.get_item(Key=ARCHIVE_KEY, ConsistentRead=True).get("Item"))
    if not pointer:
        return [], {"configured": False, "reason": "NO_VERIFIED_SCORE_ARCHIVE_INSTALLED"}
    checksum = pointer.get("artifact_digest", "")
    expected = f"s3://{store.artifact_bucket}/artifacts/kss1/score_archives/{checksum}.json"
    if (not re.fullmatch(r"[0-9a-f]{64}", checksum) or pointer.get("artifact_uri") != expected
            or pointer.get("schema") != SCHEMA or pointer.get("automatic_prediction_allowed") is not False):
        raise ValueError("ARCHIVE_POINTER_INVALID")
    bundle = store.read_json(expected)
    if digest(bundle) != checksum:
        raise ValueError("ARCHIVE_ARTIFACT_DIGEST_MISMATCH")
    rows, audit = extract_history(bundle)
    return rows, {**audit, "configured": True, "artifact_digest": checksum, "artifact_uri": expected}


def merge_history(signed, archived, quarantined_identities=(), *, quarantined_seasons=()):
    """Deduplicate across provider IDs; conflicting receipts exclude both."""
    grouped = {}
    def identity(sport, home, away, day):
        # These five round-robin leagues have one home/away pairing per season.
        # A changed kickoff date must not create a second apparent fixture.
        if sport in {value[0] for value in LEAGUES.values()}:
            year = int(day[:4]) - int(day[5:7] < "07")
            period = f"SEASON#{year}"
        else:
            period = day
        return sport, normalize_name(home), normalize_name(away), period
    blocked = {identity(*item) for item in quarantined_identities}
    blocked.update((sport, normalize_name(home), normalize_name(away), f"SEASON#{season[:4]}")
                   for sport, season, home, away in quarantined_seasons)
    for row in signed + archived:
        ident = identity(row["sport_key"], row["home_team"], row["away_team"], row["commence_time"][:10])
        grouped.setdefault(ident, []).append(row)
    result, conflicts, duplicates = [], 0, 0
    for ident, rows in grouped.items():
        if ident in blocked or len({(r["commence_time"], r["home_score"], r["away_score"]) for r in rows}) != 1:
            conflicts += 1
            continue
        # Preserve the earliest independently proven receipt; deterministic ties.
        result.append(min(rows, key=lambda r: (r["available_at"], r["source_receipt"])))
        duplicates += len(rows) - 1
    return normalize_history(result), {"cross_source_conflicts": conflicts, "duplicate_scores": duplicates}
