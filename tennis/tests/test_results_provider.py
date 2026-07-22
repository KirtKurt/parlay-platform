from __future__ import annotations

from results_provider import (
    CapabilityReport,
    CapabilityState,
    CompletionType,
    MatchPhase,
    NormalizedTennisResult,
    ResultPage,
    ResultsQuery,
    SetScore,
    TennisResultsProvider,
)


class ContractProvider:
    def probe_capabilities(self):
        return CapabilityReport(
            provider="test",
            sport="tennis",
            status=CapabilityState.READY,
            checked_at_utc="2026-07-22T00:00:00+00:00",
            reason="TEST_READY",
        )

    def fetch_results(self, query):
        return ResultPage(
            provider="test",
            sport="tennis",
            fetched_at_utc="2026-07-22T00:00:00+00:00",
            results=(),
        )


def result(*, completion=CompletionType.NORMAL, winner="A", phase=MatchPhase.FINAL):
    return NormalizedTennisResult(
        provider="test",
        provider_match_id="match-1",
        start_time_utc="2026-07-21T12:00:00+00:00",
        updated_at_utc="2026-07-21T14:00:00+00:00",
        fetched_at_utc="2026-07-21T14:01:00+00:00",
        participant_a_id="a",
        participant_a_name="Player A",
        participant_b_id="b",
        participant_b_name="Player B",
        phase=phase,
        completion_type=completion,
        winner_side=winner,
        set_scores=(SetScore(1, 6, 4),),
        raw_status="finished",
        confidence=0.9,
        request_id="req-1",
        payload_sha256="0" * 64,
    )


def test_runtime_protocol_exposes_probe_and_fetch_contract():
    assert isinstance(ContractProvider(), TennisResultsProvider)


def test_only_normal_final_with_verified_winner_is_training_ready():
    assert result().training_ready is True
    assert result(completion=CompletionType.RETIREMENT).training_ready is False
    assert result(completion=CompletionType.WALKOVER).training_ready is False
    assert result(completion=CompletionType.DEFAULT).training_ready is False
    assert result(completion=CompletionType.ABANDONED).training_ready is False
    assert result(completion=CompletionType.UNKNOWN).training_ready is False
    assert result(winner=None).training_ready is False
    assert result(phase=MatchPhase.SUSPENDED).training_ready is False


def test_capability_report_serializes_exact_state_and_no_empty_metadata():
    report = CapabilityReport(
        provider="bbs",
        sport="tennis",
        status=CapabilityState.UNSUPPORTED,
        checked_at_utc="2026-07-22T00:00:00+00:00",
        reason="BBS_TENNIS_NOT_ADVERTISED",
        request_count=1,
    ).to_dict()

    assert report["status"] == "UNSUPPORTED"
    assert report["ready"] is False
    assert report["requestCount"] == 1
    assert "requestId" not in report


def test_results_query_is_bounded_and_sport_cannot_be_overridden():
    query = ResultsQuery(date="2026-07-22", limit=200)
    assert query.date == "2026-07-22"
    assert not hasattr(query, "sport")

    for invalid in (0, 201):
        try:
            ResultsQuery(limit=invalid)
        except ValueError as exc:
            assert str(exc) == "RESULTS_QUERY_LIMIT_OUT_OF_RANGE"
        else:
            raise AssertionError("invalid limit was accepted")
