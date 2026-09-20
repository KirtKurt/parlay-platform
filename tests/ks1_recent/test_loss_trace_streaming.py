"""Regression coverage for bounded-memory immutable final receipt verification."""
import gc

import pytest

import ks1.loss_trace as subject


class Payload(dict):
    live = 0
    max_live = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).live += 1
        type(self).max_live = max(type(self).max_live, type(self).live)

    def __del__(self):
        type(self).live -= 1


def _game(game_id, home=2, away=1):
    return {
        'officialGamePk': int(game_id),
        'completedAtUtc': '2026-09-19T23:00:00+00:00',
        'teams': {
            'home': {'team': {'id': 10}, 'teamStats': {'batting': {'runs': home}}},
            'away': {'team': {'id': 20}, 'teamStats': {'batting': {'runs': away}}},
        },
    }


def _receipt(index):
    return {
        'bucket': 'b', 'key': f'prior-{index}.json', 'versionId': f'v{index}',
        'sha256': f'{index:064x}',
    }


def test_verified_final_receipts_streams_unique_versions_without_retaining_payloads(monkeypatch):
    Payload.live = Payload.max_live = 0
    grades = []
    payloads = {}
    # 40 historical receipt versions, each shared by three official grades. The
    # old cache retained every decoded payload; the repaired verifier may retain
    # only the currently inspected version while preserving one read per version.
    for version in range(1, 41):
        receipt = _receipt(version)
        games = []
        for offset in range(3):
            game_id = str(version * 10 + offset)
            games.append(_game(game_id))
            grades.append({
                'game_id': game_id, 'home_score': 2, 'away_score': 1,
                'final_evidence': [dict(receipt)],
            })
        payloads[(receipt['key'], receipt['versionId'])] = games

    calls = []
    def read_json(s3, bucket, key, version):
        calls.append((key, version))
        index = int(key.removeprefix('prior-').removesuffix('.json'))
        return Payload(games=payloads[(key, version)]), {'sha256': f'{index:064x}'}

    monkeypatch.setattr(subject, 'read_json', read_json)
    result = subject.verified_final_receipts(object(), 'b', grades)
    gc.collect()
    assert len(result) == len(grades)
    assert len(calls) == len(set(calls)) == 40
    assert Payload.max_live <= 2
    assert Payload.live == 0
    assert all(row['home_id'] == '10' and row['away_id'] == '20' for row in result.values())


def test_verified_final_receipts_preserves_ordered_fallback_and_score_drift_failure(monkeypatch):
    receipts = [_receipt(1), _receipt(2)]
    grade = {
        'game_id': '7', 'home_score': 4, 'away_score': 3,
        'final_evidence': receipts,
    }
    seen = []
    def read_json(s3, bucket, key, version):
        seen.append((key, version))
        index = 1 if key == 'prior-1.json' else 2
        games = [_game('8', 1, 0)] if index == 1 else [_game('7', 4, 3)]
        return {'games': games}, {'sha256': receipts[index - 1]['sha256']}

    monkeypatch.setattr(subject, 'read_json', read_json)
    result = subject.verified_final_receipts(object(), 'b', [grade])
    assert seen == [('prior-1.json', 'v1'), ('prior-2.json', 'v2')]
    assert result['7']['final_evidence'] == receipts

    def changed_score(s3, bucket, key, version):
        return {'games': [_game('7', 9, 3)]}, {'sha256': receipts[0]['sha256']}
    monkeypatch.setattr(subject, 'read_json', changed_score)
    with pytest.raises(ValueError, match='committed score differs'):
        subject.verified_final_receipts(object(), 'b', [dict(grade, final_evidence=[receipts[0]])])


def test_conflicting_checksum_for_same_version_fails_before_source_read(monkeypatch):
    receipt = _receipt(1)
    grades = [
        {'game_id': '1', 'home_score': 2, 'away_score': 1, 'final_evidence': [dict(receipt)]},
        {'game_id': '2', 'home_score': 2, 'away_score': 1,
         'final_evidence': [dict(receipt, sha256='f' * 64)]},
    ]
    monkeypatch.setattr(subject, 'read_json', lambda *args: pytest.fail('read contradictory receipt'))
    with pytest.raises(ValueError, match='conflicting checksum'):
        subject.verified_final_receipts(object(), 'b', grades)
