from __future__ import annotations

import pytest

import results_probe_handler as handler
from results_provider import CapabilityReport, CapabilityState


VALID_EVENT = {"provider": "bbs", "sport": "tennis", "action": "probe"}


class FakeProvider:
    def __init__(self, status=CapabilityState.UNSUPPORTED):
        self.status = status
        self.calls = 0

    def probe_capabilities(self):
        self.calls += 1
        return CapabilityReport(
            provider="bbs",
            sport="tennis",
            status=self.status,
            checked_at_utc="2026-07-22T00:00:00+00:00",
            reason="BBS_TENNIS_NOT_ADVERTISED",
            request_count=1,
        )


@pytest.mark.parametrize(
    "event",
    [
        {},
        None,
        {"provider": "mlb", "sport": "tennis", "action": "probe"},
        {"provider": "bbs", "sport": "baseball", "action": "probe"},
        {"provider": "bbs", "sport": "tennis", "action": "fetch"},
        {"provider": "BBS", "sport": "tennis", "action": "probe"},
        {
            "provider": "bbs",
            "sport": "tennis",
            "action": "probe",
            "apiKey": "must-not-be-accepted",
        },
    ],
)
def test_rejects_any_non_fixed_probe_event_before_provider_construction(
    monkeypatch, event
):
    constructed = []

    def forbidden_build():
        constructed.append(True)
        raise AssertionError("provider construction would permit secret/network access")

    monkeypatch.setattr(handler, "_build_provider", forbidden_build)

    with pytest.raises(RuntimeError, match="TENNIS_RESULTS_PROBE_EVENT_REJECTED"):
        handler.lambda_handler(event, None)
    assert constructed == []


def test_valid_event_returns_direct_redacted_capability_report(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(handler, "_build_provider", lambda: provider)

    result = handler.lambda_handler(dict(VALID_EVENT), None)

    assert result["status"] == "UNSUPPORTED"
    assert result["provider"] == "bbs"
    assert result["sport"] == "tennis"
    assert "statusCode" not in result
    assert provider.calls == 1


def test_builder_passes_only_scoped_tennis_secret_arn(monkeypatch):
    seen = []
    provider = FakeProvider()

    def factory(**kwargs):
        seen.append(kwargs)
        return provider

    monkeypatch.setenv("TENNIS_BBS_API_SECRET_ARN", "arn:tennis:bbs")
    monkeypatch.setenv("BBS_API_KEY", "must-not-be-read-by-handler")
    monkeypatch.setattr(handler, "BBSResultsProvider", factory)

    built = handler._build_provider()

    assert built is provider
    assert seen == [{"secret_arn": "arn:tennis:bbs"}]
