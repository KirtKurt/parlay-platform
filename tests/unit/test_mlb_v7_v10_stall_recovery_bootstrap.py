from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(
    ".github/workflows/mlb-v7-v10-stall-recovery-bootstrap.yml"
)


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_bootstrap_bypasses_old_queues_without_deploying_production_stack() -> None:
    text = _text()

    assert "group: mlb-v7-v10-stall-recovery-bootstrap-${{ github.sha }}" in text
    assert "cancel-in-progress: false" in text
    assert "sam deploy" not in text
    assert "parlay-platform-dev" not in text
    assert "mode\":\"authorize" not in text
    assert "manualCursorMutation" not in text


def test_bootstrap_is_provider_neutral_and_bounded() -> None:
    text = _text()

    assert "--limit 1" in text
    assert "15m" in text
    assert "official_mlb_plus_internal_canonical_context" in text
    assert "V8_HISTORICAL_OFFICIAL_CONTEXT_SHADOW_ONLY" in text
    assert "bbsApiUsed') is False" in text
    assert "bbsCredentialRead') is False" in text
    assert "BBS_API_KEY" not in text
    assert "BBS_API_SECRET_ARN" not in text
    assert "api.bigballsdata.com" not in text


def test_bootstrap_publishes_fresh_v10_and_compact_v9_evidence() -> None:
    text = _text()

    assert "MLB-V10-DISCOVERY-CADENCE-v3-material-state-fresh-evidence" in text
    assert "value.get('sourceSha') == os.environ['GITHUB_SHA']" in text
    assert "str(value.get('runId')) == str(os.environ['GITHUB_RUN_ID'])" in text
    assert "value.get('stalledStage') is None" in text
    assert "compact_latest_pointer" in text
    assert "fullEvidenceRetainedAsWorkflowArtifact" in text
    assert "productionAuthorityChanged') is not False" in text


def test_bootstrap_contains_no_invisible_unicode_or_recursive_report_trigger() -> None:
    text = _text()

    assert "\u200b" not in text
    assert "\ufeff" not in text
    assert "PYTHONPATH=.:hello_world:scripts" in text
    trigger_section = text.split("permissions:", 1)[0]
    assert "runtime_reports/" not in trigger_section
