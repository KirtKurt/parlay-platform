import base64
from copy import deepcopy
import json

import pytest

from soccer_auto.canonical import digest
from soccer_auto.kss1_features import HistoryIndex
from soccer_auto.kss1_score_archive import (
    API, ARCHIVE_KEY, ORIGIN, SCHEMA, exact_kickoff, extract_history,
    git_hash, load_archive, merge_history, verify_remote_witnesses,
    snapshot_hash,
)


def bundle_fixture(matches=None, *, clock="2025-01-10T00:00:00Z", extra_versions=()):
    """Synthetic Git objects are only test fixtures, never installed evidence."""
    matches = matches if matches is not None else [
        {"round": "Matchday 1", "date": "2025-01-01", "time": "15:00",
         "team1": "Arsenal FC", "team2": "Chelsea FC", "score": {"ft": [2, 1]}}]
    bundle = {"schema": SCHEMA, "origin": ORIGIN, "retrieved_at": "2026-09-15T00:00:00Z",
              "objects": {}, "captures": [], "witnesses": []}
    def add(kind, raw):
        sha = git_hash(kind, raw)
        bundle["objects"][sha] = {"type": kind, "base64": base64.b64encode(raw).decode()}
        return sha
    for i, (rows, at) in enumerate([(matches, clock), *extra_versions]):
        raw = json.dumps({"name": "English Premier League 2024/25", "matches": rows}).encode()
        blob = add("blob", raw)
        season = add("tree", b"100644 en.1.json\0" + bytes.fromhex(blob))
        root = add("tree", b"40000 2024-25\0" + bytes.fromhex(season))
        commit = add("commit", f"tree {root}\nauthor Test <test@example.invalid> 0 +0000\n\nOld author dates prove nothing\n".encode())
        snapshot = {"branches": {"refs/heads/master": {"target_type": "revision", "target": commit}}}
        snapshot["id"] = snapshot_hash(snapshot)
        bundle["witnesses"].append({"visit": {"origin": ORIGIN, "visit": i + 1, "type": "git",
                                           "status": "full", "date": at, "snapshot": snapshot["id"]},
                                    "snapshot": snapshot})
        bundle["captures"].append({"witness": i, "path": "2024-25/en.1.json", "blob": blob})
    return bundle


def test_score_availability_comes_from_independent_visit_not_kickoff_or_git_date():
    rows, audit = extract_history(bundle_fixture())
    assert rows[0]["available_at"] == "2025-01-10T00:00:00Z"
    assert rows[0]["home_team"] == "arsenal"
    assert audit["accepted_scores"] == 1
    fixture = {"event_key": "another", "sport_key": "soccer_epl", "home_team": "Arsenal", "away_team": "Liverpool",
               "commence_time": "2025-01-09T16:00:00Z"}
    assert HistoryIndex(rows).features(fixture, "2025-01-09T15:00:00Z")["counts"]["home"] == 0
    fixture["commence_time"] = "2025-01-11T16:00:00Z"
    assert HistoryIndex(rows).features(fixture, "2025-01-11T15:00:00Z")["counts"]["home"] == 1


@pytest.mark.parametrize("defect", ["bytes", "path", "commit", "partial", "origin", "future"])
def test_forged_or_incomplete_evidence_fails_closed(defect):
    bundle = bundle_fixture()
    witness = bundle["witnesses"][0]
    if defect == "bytes": bundle["objects"][bundle["captures"][0]["blob"]]["base64"] = base64.b64encode(b"{}").decode()
    elif defect == "path": bundle["captures"][0]["path"] = "2024-25/uefa.cl.json"
    elif defect == "commit": witness["snapshot"]["branches"]["refs/heads/master"]["target"] = bundle["captures"][0]["blob"]
    elif defect == "partial": witness["visit"]["status"] = "partial"
    elif defect == "origin": witness["visit"]["origin"] = "https://example.invalid/forged"
    elif defect == "future": witness["visit"]["date"] = "2027-01-01T00:00:00Z"
    with pytest.raises(ValueError): extract_history(bundle)


def test_installation_must_recheck_upstream_timestamp_and_snapshot():
    bundle = bundle_fixture()
    witness = bundle["witnesses"][0]
    def remote(url):
        assert url == API + f"origin/{ORIGIN}/visits/?per_page=100"
        return [deepcopy(witness["visit"])]
    verify_remote_witnesses(bundle, remote)
    def forged(url):
        result = remote(url)
        result[0]["date"] = "2025-02-01T00:00:00Z"
        return result
    with pytest.raises(ValueError, match="INDEPENDENTLY_VERIFIED"):
        verify_remote_witnesses(bundle, forged)


def test_dst_conversion_is_exact_and_missing_or_ambiguous_times_are_rejected():
    assert exact_kickoff("2024-08-16", "20:00", "Europe/London") == "2024-08-16T19:00:00Z"
    assert exact_kickoff("2025-01-01", "20:00", "Europe/London") == "2025-01-01T20:00:00Z"
    for day, clock in [("2025-03-30", "01:30"), ("2025-10-26", "01:30"), ("2025-01-01", None)]:
        with pytest.raises(ValueError): exact_kickoff(day, clock, "Europe/London")


@pytest.mark.parametrize("defect,reason", [("no_time", "MISSING_EXACT_KICKOFF"), ("team", "UNKNOWN_TEAM"),
    ("penalties", "REGULATION_SEMANTICS_UNRESOLVED"), ("awarded", "REGULATION_SEMANTICS_UNRESOLVED"),
    ("boolean", "INVALID_FINAL_SCORE"), ("playoff", "REGULATION_SEMANTICS_UNRESOLVED")])
def test_unresolved_rows_are_excluded_and_counted(defect, reason):
    match = {"round": "Matchday 1", "date": "2025-01-01", "time": "15:00",
             "team1": "Arsenal FC", "team2": "Chelsea FC", "score": {"ft": [2, 1]}}
    if defect == "no_time": match.pop("time")
    elif defect == "team": match["team1"] = "Almost Arsenal"
    elif defect == "penalties": match["score"]["p"] = [4, 3]
    elif defect == "awarded": match["status"] = "awarded"
    elif defect == "boolean": match["score"]["ft"][0] = True
    elif defect == "playoff": match["round"] = "Playoff"
    rows, audit = extract_history(bundle_fixture([match]))
    assert not rows and audit["excluded_observations"][reason] == 1


def test_score_and_reschedule_conflicts_quarantine_all_versions():
    original = {"round": "Matchday 1", "date": "2025-01-01", "time": "15:00",
                "team1": "Arsenal FC", "team2": "Chelsea FC", "score": {"ft": [2, 1]}}
    for field, value in [("time", "16:00"), ("score", {"ft": [1, 1]})]:
        changed = deepcopy(original); changed[field] = value
        rows, audit = extract_history(bundle_fixture([original], extra_versions=[([changed], "2025-01-11T00:00:00Z")]))
        assert rows == [] and audit["conflicted_events"] == 1
        assert len(audit["conflicted_identities"]) == 1
    rows, _ = extract_history(bundle_fixture([original], extra_versions=[([original], "2025-01-11T00:00:00Z")]))
    assert len(rows) == 1 and rows[0]["available_at"] == "2025-01-10T00:00:00Z"


def test_cross_source_ids_cannot_double_count_or_bypass_quarantine():
    archive, _ = extract_history(bundle_fixture())
    signed = deepcopy(archive[0]); signed.update(event_key="ODDS#another", available_at="2025-01-12T00:00:00Z")
    rows, audit = merge_history([signed], archive)
    assert len(rows) == 1 and audit["duplicate_scores"] == 1
    signed["away_score"] = 9
    assert not merge_history([signed], archive)[0]
    key = (archive[0]["sport_key"], "arsenal", "chelsea", "2025-01-01")
    assert not merge_history([], archive, [key])[0]
    revised = deepcopy(archive[0])
    revised.update(event_key="another-provider", commence_time="2025-01-02T15:00:00Z")
    assert not merge_history([revised], archive)[0]
    assert not merge_history([], [revised], [key])[0]


def test_installed_archive_is_connected_to_signed_score_readiness():
    from soccer_auto.kss1_training_runtime import source_history
    from tests.soccer_auto.test_kss1_training_runtime import Store, signed_score
    match = {"round": "Matchday 1", "date": "2025-01-01", "time": "16:00",
             "team1": "Arsenal FC", "team2": "Chelsea FC", "score": {"ft": [2, 1]}}
    bundle = bundle_fixture([match]); checksum = digest(bundle)
    store = Store([signed_score()])
    uri = store.write_artifact("kss1/score_archives", bundle, checksum)
    class ArchiveTable:
        def get_item(self, **kwargs):
            assert kwargs["Key"] == ARCHIVE_KEY
            return {"Item": {"schema": SCHEMA, "artifact_uri": uri, "artifact_digest": checksum,
                             "automatic_prediction_allowed": False}}
    store.models = ArchiveTable()
    audit = {}; rows = source_history(store, audit=audit)
    assert len(rows) == 1
    assert rows[0]["source"] == "the_odds_api_signed_settlement"
    assert audit["score_archive"]["accepted_scores"] == 1
    assert audit["reconciliation"]["duplicate_scores"] == 1


def test_flagged_score_cannot_return_through_earlier_unflagged_capture():
    match = {"round": "Matchday 1", "date": "2025-01-01", "time": "15:00",
             "team1": "Arsenal FC", "team2": "Chelsea FC", "score": {"ft": [2, 1]}}
    flagged = dict(match, status="awarded")
    rows, audit = extract_history(bundle_fixture([match], extra_versions=[([flagged], "2025-01-11T00:00:00Z")]))
    assert not rows and audit["regulation_flagged_identities"] == 1
    # A provider's unflagged result must also stay out when every archive
    # version is flagged (there is no accepted archive row to reconcile).
    signed, _ = extract_history(bundle_fixture([match]))
    _, audit = extract_history(bundle_fixture([flagged]))
    assert not merge_history(signed, [], quarantined_seasons=audit["quarantined_season_identities"])[0]


def test_runtime_rechecks_content_address_and_pointer_scope():
    bundle = bundle_fixture()
    checksum = digest(bundle)
    class Store:
        artifact_bucket = "isolated"
        def get_item(self, **kwargs):
            assert kwargs["Key"] == ARCHIVE_KEY and kwargs["ConsistentRead"]
            return {"Item": self.pointer}
        def read_json(self, uri): return bundle
    store = Store(); store.models = store
    store.pointer = {"schema": SCHEMA, "artifact_digest": checksum,
                     "artifact_uri": f"s3://isolated/artifacts/kss1/score_archives/{checksum}.json",
                     "automatic_prediction_allowed": False}
    assert len(load_archive(store)[0]) == 1
    store.pointer["artifact_uri"] = "s3://another/bucket"
    with pytest.raises(ValueError, match="POINTER"): load_archive(store)
    store.pointer["artifact_uri"] = f"s3://isolated/artifacts/kss1/score_archives/{checksum}.json"
    bundle["retrieved_at"] = "2026-09-14T00:00:00Z"
    with pytest.raises(ValueError, match="DIGEST"): load_archive(store)


@pytest.mark.parametrize("failure", [None, "remote", "readback"])
def test_installer_requires_independent_verification_and_artifact_readback(monkeypatch, failure):
    from urllib.error import HTTPError
    from scripts import import_kss1_score_archive as importer
    from tests.soccer_auto.test_kss1_training_runtime import Store
    bundle = bundle_fixture()
    store = Store()
    class PointerTable:
        item = None
        def put_item(self, **kwargs):
            self.item = kwargs["Item"]
            assert self.item["PK"] == ARCHIVE_KEY["PK"] and self.item["SK"] == ARCHIVE_KEY["SK"]
            assert self.item["automatic_prediction_allowed"] is False
        def get_item(self, **kwargs):
            return {"Item": self.item}
    store.models = PointerTable()
    def independent(url):
        if failure == "remote":
            raise HTTPError(url, 429, "rate limited", {}, None)
        return [deepcopy(bundle["witnesses"][0]["visit"])]
    monkeypatch.setattr(importer, "fetch_json", independent)
    if failure == "readback":
        store.read_json = lambda uri: {"corrupt": True}
    if failure:
        with pytest.raises((HTTPError, ValueError)):
            importer.install_bundle(store, bundle)
        assert store.models.item is None
        if failure == "remote":
            assert store.artifacts == {}
    else:
        assert importer.install_bundle(store, bundle)["installed"] is True
        assert len(load_archive(store)[0]) == 1
