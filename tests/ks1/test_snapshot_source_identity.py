from copy import deepcopy

from ks1.sources import filter_snapshots_with_bound_starters


def _snapshot(players, starter=99, pk=1):
    return {
        "officialGamePk": pk,
        "source_key": f"research-v1/snapshots/{pk}.json",
        "features": {"home_starter_era_30d": 3.50, "marketHomeProbability": 0.55},
        "playerWindows": {
            "teams": {
                "home": {"starterId": starter, "players": players},
                "away": {"starterId": None, "players": []},
            }
        },
    }


def test_filter_keeps_exactly_bound_snapshot_starter_without_rewriting_source():
    snapshot = _snapshot([{"id": 99, "name": "Starter"}, {"id": 12, "name": "Other"}])
    before = deepcopy(snapshot)
    accepted, rejected = filter_snapshots_with_bound_starters([snapshot])
    assert accepted == [snapshot]
    assert rejected == []
    assert snapshot == before


def test_filter_rejects_unbound_snapshot_starter_fail_closed():
    snapshot = _snapshot([{"id": 12, "name": "Other"}])
    before = deepcopy(snapshot)
    accepted, rejected = filter_snapshots_with_bound_starters([snapshot])
    assert accepted == []
    assert rejected == [{"officialGamePk": "1", "source_key": snapshot["source_key"]}]
    assert snapshot == before


def test_filter_rejects_duplicate_starter_identity_and_preserves_other_snapshots():
    malformed = _snapshot([{"id": 99}, {"id": 99}], pk=1)
    valid = _snapshot([{"id": 99}], pk=2)
    accepted, rejected = filter_snapshots_with_bound_starters([malformed, valid])
    assert accepted == [valid]
    assert rejected == [{"officialGamePk": "1", "source_key": malformed["source_key"]}]
