from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}: found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_predictions_api() -> None:
    path = ROOT / "hello_world" / "mlb_v3_read_api.py"
    text = path.read_text(encoding="utf-8")
    import_line = (
        "import mlb_terminal_lifecycle_count_reconciliation as lifecycle_counts\n"
    )
    if import_line not in text:
        replace_once(
            path,
            "import mlb_ml_runtime_install_v3\n",
            "import mlb_ml_runtime_install_v3\n" + import_line,
        )
        text = path.read_text(encoding="utf-8")
    call = (
        "    result = lifecycle_counts.reconcile_payload(\n"
        "        result,\n"
        "        row_field=\"predictions\",\n"
        "    )\n"
    )
    if call not in text:
        replace_once(
            path,
            "    result = dict(result or {})\n    result.update({\n",
            "    result = dict(result or {})\n" + call + "    result.update({\n",
        )


def patch_lock_status_api() -> None:
    path = ROOT / "hello_world" / "mlb_daily_pick_lock_protected.py"
    text = path.read_text(encoding="utf-8")
    import_line = (
        "import mlb_terminal_lifecycle_count_reconciliation as lifecycle_counts\n"
    )
    if import_line not in text:
        replace_once(
            path,
            "import mlb_daily_per_game_lock_patch\n",
            "import mlb_daily_per_game_lock_patch\n" + import_line,
        )
        text = path.read_text(encoding="utf-8")
    call = (
        "    return lifecycle_counts.reconcile_http_response(\n"
        "        out,\n"
        "        row_field=\"perGameStatus\",\n"
        "    )\n"
    )
    if call not in text:
        replace_once(
            path,
            "        out[\"body\"] = json.dumps(payload)\n    return out\n",
            "        out[\"body\"] = json.dumps(payload)\n" + call,
        )


def write_tests() -> None:
    path = ROOT / "tests" / "unit" / "test_mlb_terminal_status_lifecycle_counts.py"
    path.write_text(
        '''from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_terminal_lifecycle_count_reconciliation as lifecycle


def _row(game_id: str, status: str, *, winner=None, locked=False):
    return {
        "gameId": game_id,
        "gameIdentity": game_id,
        "lockStatus": status,
        "lockedPrediction": locked,
        "predictedWinner": winner,
    }


def test_all_missed_rows_are_complete_terminal_lifecycle_without_winners():
    payload = {
        "sport": "mlb",
        "gameCount": 2,
        "lockedPredictionCount": 0,
        "lockedStatusCount": 0,
        "noPredictionDataCount": 0,
        "lockStatusComplete": False,
        "canonicalPredictionComplete": False,
        "operationalDefect": True,
        "perGameStatus": [
            _row("g1", "MISSED_NOT_BACKFILLED"),
            _row("g2", "MISSED_NOT_BACKFILLED"),
        ],
    }

    result = lifecycle.reconcile_payload(payload, row_field="perGameStatus")

    assert result["lockedPredictionCount"] == 0
    assert result["lockedStatusCount"] == 2
    assert result["noPredictionDataCount"] == 2
    assert result["lockStatusComplete"] is True
    assert result["canonicalPredictionComplete"] is False
    assert result["operationalDefect"] is True
    assert result["perGameStatus"] == payload["perGameStatus"]
    assert result["terminalLifecycleCountsDerivedFromRows"] is True


def test_mixed_canonical_no_data_and_missed_rows_reconcile_exactly():
    payload = {
        "gameCount": 3,
        "officialPredictionCount": 0,
        "lockedStatusCount": 0,
        "noPredictionDataCount": 0,
        "predictions": [
            _row("g1", "LOCKED_CANONICAL", winner="Home", locked=True),
            _row("g2", "LOCKED_NO_PREDICTION_DATA"),
            _row("g3", "MISSED_LOCK"),
        ],
        "slateCoverage": {},
        "publicPerGameAuthority": {},
        "lastPossiblePredictionGate": {},
    }

    result = lifecycle.reconcile_payload(payload, row_field="predictions")

    assert result["lockedPredictionCount"] == 1
    assert result["officialPredictionCount"] == 1
    assert result["lockedStatusCount"] == 3
    assert result["noPredictionDataCount"] == 2
    assert result["lockStatusComplete"] is True
    assert result["canonicalPredictionComplete"] is False
    for key in (
        "slateCoverage",
        "publicPerGameAuthority",
        "lastPossiblePredictionGate",
    ):
        assert result[key]["lockedPredictionCount"] == 1
        assert result[key]["lockedStatusCount"] == 3
        assert result[key]["noPredictionDataCount"] == 2
        assert result[key]["lockStatusComplete"] is True


def test_open_or_due_rows_are_not_promoted_to_terminal_status():
    payload = {
        "gameCount": 2,
        "lockedStatusCount": 0,
        "noPredictionDataCount": 0,
        "perGameStatus": [
            _row("g1", "OPEN_PRE_LOCK"),
            _row("g2", "LOCK_DUE_CANONICAL_MISSING"),
        ],
    }

    result = lifecycle.reconcile_payload(payload, row_field="perGameStatus")

    assert result["lockedStatusCount"] == 0
    assert result["noPredictionDataCount"] == 0
    assert result["lockStatusComplete"] is False
    assert result["canonicalPredictionComplete"] is False


def test_partial_or_duplicate_row_sets_fail_closed_without_rewriting_counts():
    partial = {
        "gameCount": 2,
        "lockedStatusCount": 7,
        "noPredictionDataCount": 6,
        "perGameStatus": [_row("g1", "MISSED_LOCK")],
    }
    duplicate = {
        "gameCount": 2,
        "lockedStatusCount": 7,
        "noPredictionDataCount": 6,
        "perGameStatus": [
            _row("g1", "MISSED_LOCK"),
            _row("g1", "MISSED_LOCK"),
        ],
    }

    assert lifecycle.reconcile_payload(
        partial, row_field="perGameStatus"
    )["lockedStatusCount"] == 7
    assert lifecycle.reconcile_payload(
        duplicate, row_field="perGameStatus"
    )["lockedStatusCount"] == 7


def test_http_response_reconciliation_preserves_transport_and_defect_state():
    response = {
        "statusCode": 200,
        "headers": {"cache-control": "no-store"},
        "body": json.dumps(
            {
                "gameCount": 1,
                "lockedStatusCount": 0,
                "noPredictionDataCount": 0,
                "operationalDefect": True,
                "perGameStatus": [_row("g1", "MISSED_NOT_BACKFILLED")],
            }
        ),
    }

    result = lifecycle.reconcile_http_response(
        response, row_field="perGameStatus"
    )
    body = json.loads(result["body"])

    assert result["statusCode"] == 200
    assert result["headers"] == response["headers"]
    assert body["lockedStatusCount"] == 1
    assert body["noPredictionDataCount"] == 1
    assert body["lockStatusComplete"] is True
    assert body["canonicalPredictionComplete"] is False
    assert body["operationalDefect"] is True


def test_both_public_lambdas_install_row_derived_reconciliation():
    read_api = (HELLO_WORLD / "mlb_v3_read_api.py").read_text(encoding="utf-8")
    lock_api = (HELLO_WORLD / "mlb_daily_pick_lock_protected.py").read_text(
        encoding="utf-8"
    )

    assert "mlb_terminal_lifecycle_count_reconciliation" in read_api
    assert 'row_field="predictions"' in read_api
    assert "mlb_terminal_lifecycle_count_reconciliation" in lock_api
    assert 'row_field="perGameStatus"' in lock_api
''',
        encoding="utf-8",
    )


def remove_patch_machinery() -> None:
    for relative in (
        "scripts/codex_patch_mlb_terminal_lifecycle.py",
        ".github/workflows/codex-patch-mlb-terminal-lifecycle.yml",
    ):
        path = ROOT / relative
        if path.exists():
            path.unlink()


def main() -> None:
    patch_predictions_api()
    patch_lock_status_api()
    write_tests()
    remove_patch_machinery()


if __name__ == "__main__":
    main()
