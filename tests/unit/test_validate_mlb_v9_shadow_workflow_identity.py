from __future__ import annotations

import json

from scripts import validate_mlb_v9_shadow_workflow as validation


def test_pull_request_event_uses_checked_out_head_sha(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"head": {"sha": "head-sha-123"}}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MLB_V9_EXPECTED_SOURCE_SHA", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_SHA", "synthetic-merge-sha")

    assert validation._expected_source_sha() == "head-sha-123"


def test_push_event_falls_back_to_github_sha(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"ref": "refs/heads/main"}) + "\n", encoding="utf-8")
    monkeypatch.delenv("MLB_V9_EXPECTED_SOURCE_SHA", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_SHA", "push-sha-456")

    assert validation._expected_source_sha() == "push-sha-456"


def test_explicit_expected_sha_has_priority(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"head": {"sha": "event-head"}}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MLB_V9_EXPECTED_SOURCE_SHA", "override-head")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_SHA", "synthetic-merge-sha")

    assert validation._expected_source_sha() == "override-head"
