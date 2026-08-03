from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.publish_monotonic_json import publish


def write(path: Path, created: str, run_id: str, marker: str) -> None:
    path.write_text(
        json.dumps({"createdAtUtc": created, "runId": run_id, "marker": marker}) + "\n",
        encoding="utf-8",
    )


def supervised(
    created: str,
    run_id: str,
    *,
    status: str,
    applied: int,
    revision: int,
    ok: bool = True,
    context_field: str = "historicalBbsFundamentals",
) -> dict:
    return {
        "proofType": "MLB_SUPERVISED_SHADOW_AWS_EVALUATION",
        "createdAtUtc": created,
        "runId": run_id,
        "ok": ok,
        context_field: {
            "status": status,
            "appliedGameCount": applied,
            "pointerRevision": revision,
            "recordCount": 4020,
        },
    }


def test_newer_candidate_replaces_existing(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    write(existing, "2026-07-28T23:17:00+00:00", "200", "current")
    write(candidate, "2026-07-28T23:32:00+00:00", "201", "candidate")

    result = publish(candidate, existing, existing)

    assert result["published"] is True
    assert json.loads(existing.read_text())["marker"] == "candidate"
    assert not list(tmp_path.glob(".latest.json.*.tmp"))


def test_older_candidate_cannot_roll_back_latest(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    write(existing, "2026-07-28T23:17:00+00:00", "201", "current")
    write(candidate, "2026-07-28T22:16:00+00:00", "199", "stale")

    result = publish(candidate, existing, existing)

    assert result["published"] is False
    assert json.loads(existing.read_text())["marker"] == "current"


def test_equal_timestamp_uses_run_id_as_tie_breaker(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    stamp = "2026-07-28T23:17:00+00:00"
    write(existing, stamp, "201", "current")
    write(candidate, stamp, "202", "candidate")

    result = publish(candidate, existing, existing)

    assert result["published"] is True
    assert json.loads(existing.read_text())["marker"] == "candidate"


def test_equal_timestamp_lower_run_id_cannot_replace_latest(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    stamp = "2026-07-28T23:17:00+00:00"
    write(existing, stamp, "202", "current")
    write(candidate, stamp, "201", "stale")

    result = publish(candidate, existing, existing)

    assert result["published"] is False
    assert json.loads(existing.read_text())["marker"] == "current"


def test_candidate_without_timestamp_fails_closed(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    write(existing, "2026-07-28T23:17:00+00:00", "201", "current")
    candidate.write_text('{"runId":"202"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="no valid timestamp"):
        publish(candidate, existing, existing)

    assert json.loads(existing.read_text())["marker"] == "current"


def test_candidate_without_timestamp_cannot_create_new_latest(tmp_path):
    candidate = tmp_path / "candidate.json"
    latest = tmp_path / "latest.json"
    candidate.write_text('{"runId":"202"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="no valid timestamp"):
        publish(candidate, latest, latest)

    assert not latest.exists()


def test_empty_candidate_preserves_existing(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    write(existing, "2026-07-28T23:17:00+00:00", "201", "current")
    candidate.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="empty or missing JSON evidence"):
        publish(candidate, existing, existing)

    assert json.loads(existing.read_text())["marker"] == "current"


def test_invalid_json_candidate_preserves_existing(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    write(existing, "2026-07-28T23:17:00+00:00", "201", "current")
    candidate.write_text("{", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        publish(candidate, existing, existing)

    assert json.loads(existing.read_text())["marker"] == "current"


def test_disabled_bbd_candidate_cannot_replace_applied_evidence(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    existing.write_text(
        json.dumps(
            supervised(
                "2026-07-29T20:30:00+00:00",
                "300",
                status="APPLIED",
                applied=950,
                revision=8,
            )
        )
        + "\n"
    )
    candidate.write_text(
        json.dumps(
            supervised(
                "2026-07-29T20:35:00+00:00",
                "301",
                status="DISABLED",
                applied=0,
                revision=0,
            )
        )
        + "\n"
    )

    result = publish(candidate, existing, existing)

    assert result["published"] is False
    assert (
        result["rejectionReason"]
        == "applied_bbd_evidence_cannot_be_replaced_by_disabled_or_missing_overlay"
    )
    assert json.loads(existing.read_text())["historicalBbsFundamentals"]["appliedGameCount"] == 950


def test_disabled_official_context_cannot_replace_applied_evidence(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    existing.write_text(
        json.dumps(
            supervised(
                "2026-07-29T20:30:00+00:00",
                "300",
                status="APPLIED",
                applied=950,
                revision=8,
                context_field="historicalContext",
            )
        )
        + "\n"
    )
    candidate.write_text(
        json.dumps(
            supervised(
                "2026-07-29T20:35:00+00:00",
                "301",
                status="DISABLED",
                applied=0,
                revision=0,
                context_field="historicalContext",
            )
        )
        + "\n"
    )

    result = publish(candidate, existing, existing)

    assert result["published"] is False
    assert (
        result["rejectionReason"]
        == "applied_historical_context_cannot_be_replaced_by_disabled_or_missing_context"
    )


def test_failed_candidate_cannot_replace_successful_evidence(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    existing.write_text(
        json.dumps(
            supervised(
                "2026-07-29T20:30:00+00:00",
                "300",
                status="APPLIED",
                applied=950,
                revision=8,
            )
        )
        + "\n"
    )
    candidate.write_text(
        json.dumps(
            supervised(
                "2026-07-29T20:35:00+00:00",
                "301",
                status="APPLIED",
                applied=950,
                revision=8,
                ok=False,
            )
        )
        + "\n"
    )

    result = publish(candidate, existing, existing)

    assert result["published"] is False
    assert result["rejectionReason"] == "successful_evidence_cannot_be_replaced_by_failed_evidence"


def test_higher_bbd_revision_and_applied_count_can_advance(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    existing.write_text(
        json.dumps(
            supervised(
                "2026-07-29T20:30:00+00:00",
                "300",
                status="APPLIED",
                applied=850,
                revision=7,
            )
        )
        + "\n"
    )
    candidate.write_text(
        json.dumps(
            supervised(
                "2026-07-29T20:35:00+00:00",
                "301",
                status="APPLIED",
                applied=950,
                revision=8,
            )
        )
        + "\n"
    )

    result = publish(candidate, existing, existing)

    assert result["published"] is True
    assert json.loads(existing.read_text())["historicalBbsFundamentals"]["pointerRevision"] == 8


def test_lower_bbd_revision_cannot_replace_latest_even_when_newer(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    existing.write_text(
        json.dumps(
            supervised(
                "2026-07-29T20:30:00+00:00",
                "300",
                status="APPLIED",
                applied=950,
                revision=8,
            )
        )
        + "\n"
    )
    candidate.write_text(
        json.dumps(
            supervised(
                "2026-07-29T20:35:00+00:00",
                "301",
                status="APPLIED",
                applied=850,
                revision=7,
            )
        )
        + "\n"
    )

    result = publish(candidate, existing, existing)

    assert result["published"] is False
    assert result["rejectionReason"] == "historical_bbd_pointer_revision_regressed"


def test_top_level_revision_cannot_regress(tmp_path):
    candidate = tmp_path / "candidate.json"
    existing = tmp_path / "latest.json"
    existing.write_text(
        json.dumps(
            {
                "proofType": "MLB_V8_HISTORICAL_CONTEXT_BACKFILL",
                "createdAtUtc": "2026-07-29T20:30:00+00:00",
                "runId": "300",
                "ok": True,
                "activePointerRevision": 9,
            }
        )
        + "\n"
    )
    candidate.write_text(
        json.dumps(
            {
                "proofType": "MLB_V8_HISTORICAL_CONTEXT_BACKFILL",
                "createdAtUtc": "2026-07-29T20:35:00+00:00",
                "runId": "301",
                "ok": True,
                "activePointerRevision": 8,
            }
        )
        + "\n"
    )

    result = publish(candidate, existing, existing)

    assert result["published"] is False
    assert result["rejectionReason"] == "evidence_revision_regressed"
