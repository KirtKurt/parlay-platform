"""Build, audit, and optionally install independently witnessed score history.

No paid services, model reference changes, settlement writes, or predictions.
An existing bundle is reverified against Software Heritage before installation.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soccer_auto.canonical import canonical_json, digest, iso_utc, parse_utc
from soccer_auto.kss1_score_archive import (
    API, ARCHIVE_KEY, ORIGIN, PATHS, SCHEMA, extract_history, git_hash,
    fetch_visit_inventory, tree_entries, verify_remote_witnesses, witness_revision,
)


def fetch_json(url):
    if not url.startswith(API):
        raise ValueError("only the independent archive API is allowed")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "KSS1-score-provenance/1.0"})
    with urlopen(request, timeout=45) as response:
        if not response.geturl().startswith(API):
            raise ValueError("unexpected archive redirect")
        if getattr(response, "status", 200) >= 400:
            raise ValueError(f"ARCHIVE_HTTP_{response.status}")
        payload = json.load(response)
        if not isinstance(payload, (dict, list)):
            raise ValueError("ARCHIVE_EMPTY_OR_INVALID_JSON")
        return payload


def read_cached_snapshot(path):
    try:
        raw = path.read_text()
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        snapshot = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(snapshot, dict) or "branches" not in snapshot:
        return None
    return snapshot


def write_cached_snapshot(path, snapshot):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(canonical_json(snapshot))
    tmp.replace(path)


def build_bundle(repo, *, as_of, cache_dir):
    visits = list(fetch_visit_inventory(fetch_json).values())
    visits = [v for v in visits if v.get("status") == "full"
              and parse_utc("2023-07-01T00:00:00Z") <= parse_utc(v["date"]) <= parse_utc(as_of)]
    visits.sort(key=lambda v: (v["date"], v["visit"]))
    cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_one(snapshot_id):
        path = cache_dir / (snapshot_id + ".json")
        snapshot = read_cached_snapshot(path)
        if snapshot is None:
            snapshot = fetch_json(API + f"snapshot/{snapshot_id}/")
            write_cached_snapshot(path, snapshot)
        return snapshot_id, snapshot

    snapshot_ids = list(dict.fromkeys(visit["snapshot"] for visit in visits))
    with ThreadPoolExecutor(max_workers=3) as pool:
        fetched = dict(pool.map(fetch_one, snapshot_ids))
    witnesses = [{"visit": visit, "snapshot": fetched[visit["snapshot"]]} for visit in visits]
    print(f"Loaded {len(witnesses)} independent archive witnesses", flush=True)
    bundle = {"schema": SCHEMA, "origin": ORIGIN, "retrieved_at": as_of,
              "witnesses": witnesses, "objects": {}, "captures": []}
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)
    def add(oid, kind):
        if oid not in bundle["objects"]:
            raw = git("cat-file", kind, oid)
            if git_hash(kind, raw) != oid:
                raise ValueError("local Git object digest mismatch")
            bundle["objects"][oid] = {"type": kind, "base64": base64.b64encode(raw).decode()}
        return base64.b64decode(bundle["objects"][oid]["base64"])
    for i, witness in enumerate(witnesses):
        revision = witness_revision(witness)
        try:
            commit = add(revision, "commit")
        except subprocess.CalledProcessError:
            # Public immutable object only; never use a newer file as fallback.
            git("fetch", "--quiet", ORIGIN + ".git", revision)
            commit = add(revision, "commit")
        root_oid = commit.split(b"\n", 1)[0][5:].decode()
        root = tree_entries(add(root_oid, "tree"))
        for path in PATHS:
            season, filename = path.split("/")
            if season.encode() not in root:
                continue
            _, season_oid = root[season.encode()]
            files = tree_entries(add(season_oid, "tree"))
            if filename.encode() not in files:
                continue
            _, blob = files[filename.encode()]
            add(blob, "blob")
            bundle["captures"].append({"witness": i, "path": path, "blob": blob})
    return bundle


def install_bundle(store, bundle):
    # Remote comparison is mandatory even for a previously built local bundle.
    verify_remote_witnesses(bundle, fetch_json)
    rows, audit = extract_history(bundle)
    if not rows:
        raise ValueError("no admissible score rows")
    checksum = digest(bundle)
    uri = store.write_artifact("kss1/score_archives", bundle, checksum)
    if digest(store.read_json(uri)) != checksum:
        raise ValueError("archive AWS readback mismatch")
    from soccer_auto.storage import ddb_safe
    pointer = {**ARCHIVE_KEY, "schema": SCHEMA, "artifact_uri": uri,
               "artifact_digest": checksum, "verified_at": iso_utc(datetime.now(timezone.utc)),
               "archive_as_of": bundle["retrieved_at"],
               "automatic_prediction_allowed": False, "audit": audit}
    store.models.put_item(Item=ddb_safe(pointer),
                          ConditionExpression="attribute_not_exists(SK) OR archive_as_of < :cutoff OR artifact_digest = :digest",
                          ExpressionAttributeValues={":cutoff": bundle["retrieved_at"], ":digest": checksum})
    from soccer_auto.kss1_score_archive import load_archive
    loaded, _ = load_archive(store)
    if digest(loaded) != digest(rows):
        raise ValueError("installed archive row readback mismatch")
    return {"installed": True, "artifact_uri": uri, "artifact_digest": checksum, "audit": audit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo", type=Path, help="local OpenFootball Git clone with history")
    parser.add_argument("--bundle", type=Path, help="reverify an existing raw-evidence bundle")
    parser.add_argument("--install", action="store_true", help="install only in the configured isolated SoccerStore")
    parser.add_argument("--train", action="store_true", help="run unchanged local 500/100/100 goals qualification")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.bundle:
        bundle = json.loads(args.bundle.read_text())
    else:
        with tempfile.TemporaryDirectory(prefix="kss1-openfootball-") as tmp:
            repo = args.repo or Path(tmp) / "football.json"
            if not args.repo:
                subprocess.run(["git", "clone", "--quiet", ORIGIN + ".git", str(repo)], check=True)
            bundle = build_bundle(repo, as_of=iso_utc(datetime.now(timezone.utc)), cache_dir=args.out / "snapshots")
    rows, audit = extract_history(bundle)
    (args.out / "score_archive.json").write_text(canonical_json(bundle))
    (args.out / "history.json").write_text(canonical_json({"rows": rows}))
    result = {"archive_digest": digest(bundle), "source_audit": audit, "installed": False}
    if args.train:
        verify_remote_witnesses(bundle, fetch_json)
        from soccer_auto.kss1_features import HistoryIndex, build_training_table
        from soccer_auto.kss1_goals_model import train_and_validate
        print(f"Building chronological features from {len(rows)} admitted scores", flush=True)
        table = build_training_table(HistoryIndex(rows[-5000:]))
        result["training_report"] = train_and_validate(table, min_train=500, min_test=100)
    if args.install:
        from soccer_auto.storage import SoccerStore
        result["installation"] = install_bundle(SoccerStore(), bundle)
        result["installed"] = True
    (args.out / "readiness.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps({k: v for k, v in result.items() if k != "training_report"}, indent=2), flush=True)
    if args.train:
        print(json.dumps({k: v for k, v in result["training_report"].items() if k not in {"model", "candidates", "feature_usage", "baseline", "holdout"}}, indent=2), flush=True)


if __name__ == "__main__":
    main()
