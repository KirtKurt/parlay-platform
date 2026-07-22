from __future__ import annotations

import io
import json
import urllib.error

import pytest

import bbs_results_provider as bbs
from results_provider import (
    CapabilityState,
    CompletionType,
    ResultsProviderError,
    ResultsQuery,
)


KEY = "bbs_test_never_log_this_secret_123456789"


class Response:
    def __init__(
        self,
        payload=None,
        *,
        raw=None,
        status=200,
        headers=None,
        final_url=None,
    ):
        self.status = status
        self.headers = headers or {}
        self._raw = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self._final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def geturl(self):
        return self._final_url

    def read(self, amount=-1):
        return self._raw if amount < 0 else self._raw[:amount]


class SequenceOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if response._final_url is None:
            response._final_url = request.full_url
        return response


def envelope(data, *, error=None, meta=None):
    return {
        "data": data,
        "meta": meta
        or {
            "source": "official-league",
            "confidence": 0.9,
            "request_id": "req-test",
        },
        "error": error,
    }


def sports(*slugs):
    return envelope([{"slug": slug, "name": slug.title()} for slug in slugs])


def valid_match(*, completion="normal", status="finished", winner="home"):
    return {
        "match_id": "tennis-match-1",
        "sport": "tennis",
        "status": status,
        "termination_type": completion,
        "kickoff_utc": "2026-07-22T12:00:00Z",
        "updated_at": "2026-07-22T14:00:00Z",
        "home": {"id": "player-a", "display_name": "Player A"},
        "away": {"id": "player-b", "display_name": "Player B"},
        "winner_side": winner,
        "sets": [
            {"set_number": 1, "home": 6, "away": 4},
            {"set_number": 2, "home": 6, "away": 3},
        ],
    }


def provider_with(*responses):
    opener = SequenceOpener(*responses)
    return bbs.BBSResultsProvider(api_key=KEY, opener=opener), opener


def test_missing_key_performs_zero_network_calls():
    opener = SequenceOpener(Response(sports("tennis")))
    provider = bbs.BBSResultsProvider(opener=opener)

    report = provider.probe_capabilities()

    assert report.status is CapabilityState.NOT_CONFIGURED
    assert report.request_count == 0
    assert opener.requests == []


def test_secret_resolver_is_injectable_and_errors_are_redacted():
    seen = []
    opener = SequenceOpener(Response(sports("baseball")))

    def resolver(arn):
        seen.append(arn)
        return KEY

    provider = bbs.BBSResultsProvider(
        secret_arn="arn:aws:secretsmanager:test:tennis",
        secret_resolver=resolver,
        opener=opener,
    )
    report = provider.probe_capabilities()

    assert report.status is CapabilityState.UNSUPPORTED
    assert seen == ["arn:aws:secretsmanager:test:tennis"]
    assert KEY not in repr(provider)
    assert KEY not in json.dumps(report.to_dict())


def test_table_tennis_and_title_inference_are_rejected_after_one_request():
    provider, opener = provider_with(
        Response(
            envelope(
                [
                    {"slug": "table_tennis", "name": "Tennis"},
                    {"slug": "TENNIS", "name": "Tennis"},
                ]
            )
        )
    )

    report = provider.probe_capabilities()

    assert report.status is CapabilityState.UNSUPPORTED
    assert report.request_count == 1
    assert report.result_route_checked is False
    assert len(opener.requests) == 1
    assert opener.requests[0][0].full_url == f"{bbs.BASE_URL}/v1/sports"


def test_exact_tennis_slug_gates_exact_finished_result_probe():
    provider, opener = provider_with(
        Response(sports("baseball", "tennis")),
        Response(envelope([])),
    )

    report = provider.probe_capabilities()

    assert report.status is CapabilityState.RESULT_ROUTE_UNVERIFIED
    assert report.request_count == 2
    assert report.result_route_checked is True
    second_request, timeout = opener.requests[1]
    assert second_request.full_url == (
        f"{bbs.BASE_URL}/v1/matches?sport=tennis&status=finished&limit=1"
    )
    assert second_request.headers["Authorization"] == f"Bearer {KEY}"
    assert timeout == 4


def test_finished_without_explicit_termination_is_contract_incomplete():
    row = valid_match()
    row.pop("termination_type")
    provider, _ = provider_with(
        Response(sports("tennis")),
        Response(envelope([row])),
    )

    report = provider.probe_capabilities()

    assert report.status is CapabilityState.CONTRACT_INCOMPLETE
    assert report.reason_counts == {"TERMINATION_DETAIL_MISSING_OR_UNKNOWN": 1}
    assert report.ready is False


@pytest.mark.parametrize(
    ("status", "completion", "winner", "reason"),
    [
        ("scheduled", "normal", "home", "MATCH_NOT_TERMINAL"),
        ("live", "normal", "home", "MATCH_NOT_TERMINAL"),
        ("finished", "abandoned", None, "MATCH_PHASE_COMPLETION_CONTRADICTORY"),
        ("cancelled", "normal", None, "MATCH_PHASE_COMPLETION_CONTRADICTORY"),
        ("cancelled", "abandoned", "home", "CANCELLED_MATCH_HAS_WINNER"),
    ],
)
def test_nonterminal_or_contradictory_sample_never_enables_provider(
    status, completion, winner, reason
):
    provider, _ = provider_with(
        Response(sports("tennis")),
        Response(
            envelope(
                [
                    valid_match(
                        status=status,
                        completion=completion,
                        winner=winner,
                    )
                ]
            )
        ),
    )

    report = provider.probe_capabilities()

    assert report.status is CapabilityState.CONTRACT_INCOMPLETE
    assert report.reason_counts[reason] == 1
    assert report.ready is False


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("match_id", "MATCH_ID_MISSING"),
        ("winner_side", "WINNER_MISSING_OR_UNVERIFIED"),
        ("home", "PARTICIPANT_IDENTITY_MISSING"),
    ],
)
def test_identity_and_winner_are_required_for_ready(field, reason):
    row = valid_match()
    row.pop(field)
    provider, _ = provider_with(
        Response(sports("tennis")),
        Response(envelope([row])),
    )

    report = provider.probe_capabilities()

    assert report.status is CapabilityState.CONTRACT_INCOMPLETE
    assert report.reason_counts[reason] == 1


def test_fully_validated_sample_is_ready_and_only_then_can_fetch():
    row = valid_match()
    provider, opener = provider_with(
        Response(sports("tennis")),
        Response(envelope([row])),
        Response(envelope([row], meta={"request_id": "fetch-1"})),
    )

    report = provider.probe_capabilities()
    page = provider.fetch_results(ResultsQuery(date="2026-07-22", limit=25))

    assert report.status is CapabilityState.READY
    assert page.results[0].training_ready is True
    assert page.results[0].completion_type is CompletionType.NORMAL
    assert page.results[0].winner_side == "A"
    assert page.results[0].payload_sha256
    assert opener.requests[2][0].full_url == (
        f"{bbs.BASE_URL}/v1/matches?sport=tennis&status=finished&limit=25"
        "&date=2026-07-22"
    )


def test_fetch_fails_before_a_ready_probe_without_network_access():
    provider, opener = provider_with(Response(sports("table_tennis")))
    report = provider.probe_capabilities()
    assert report.status is CapabilityState.UNSUPPORTED

    with pytest.raises(ResultsProviderError, match="BBS_RESULTS_PROVIDER_NOT_READY"):
        provider.fetch_results(ResultsQuery())
    assert len(opener.requests) == 1


@pytest.mark.parametrize(
    ("completion", "status", "winner", "expected"),
    [
        ("normal", "finished", "home", CompletionType.NORMAL),
        ("retirement", "finished", "home", CompletionType.RETIREMENT),
        ("walkover", "finished", "home", CompletionType.WALKOVER),
        ("default", "finished", "home", CompletionType.DEFAULT),
        ("abandoned", "cancelled", None, CompletionType.ABANDONED),
    ],
)
def test_completion_types_remain_distinct(completion, status, winner, expected):
    row = valid_match(completion=completion, status=status, winner=winner)
    provider, _ = provider_with(
        Response(sports("tennis")),
        Response(envelope([row])),
        Response(envelope([row])),
    )

    assert provider.probe_capabilities().status is CapabilityState.READY
    result = provider.fetch_results(ResultsQuery()).results[0]

    assert result.completion_type is expected
    assert result.training_ready is (expected is CompletionType.NORMAL)


@pytest.mark.parametrize(
    ("code", "expected_state", "reason"),
    [
        (401, CapabilityState.AUTH_FAILED, "BBS_AUTH_REJECTED_HTTP_401"),
        (403, CapabilityState.AUTH_FAILED, "BBS_AUTH_REJECTED_HTTP_403"),
        (429, CapabilityState.RATE_LIMITED, "BBS_RATE_LIMITED"),
        (503, CapabilityState.UPSTREAM_UNAVAILABLE, "BBS_UPSTREAM_HTTP_503"),
        (404, CapabilityState.CONTRACT_INVALID, "BBS_HTTP_404"),
    ],
)
def test_http_failures_map_without_retries_or_body_leaks(code, expected_state, reason):
    body = io.BytesIO(f'{{"secret":"{KEY}"}}'.encode("utf-8"))
    error = urllib.error.HTTPError(
        f"{bbs.BASE_URL}/v1/sports",
        code,
        "upstream details",
        {"Retry-After": "12", "X-Request-Id": "req-safe"},
        body,
    )
    provider, opener = provider_with(error)

    report = provider.probe_capabilities()
    serialized = json.dumps(report.to_dict())

    assert report.status is expected_state
    assert report.reason == reason
    assert report.request_count == 1
    assert len(opener.requests) == 1
    assert KEY not in serialized
    if code == 429:
        assert report.retry_after_seconds == 12


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("sensitive hostname details"),
        TimeoutError("sensitive timeout details"),
        OSError("sensitive socket details"),
    ],
)
def test_network_failures_are_redacted_and_not_retried(error):
    provider, opener = provider_with(error)

    report = provider.probe_capabilities()

    assert report.status is CapabilityState.UPSTREAM_UNAVAILABLE
    assert report.reason == "BBS_NETWORK_UNAVAILABLE"
    assert report.request_count == 1
    assert len(opener.requests) == 1


def test_redirect_is_rejected_even_if_an_injected_opener_follows_it():
    redirected = Response(
        sports("tennis"), final_url="https://redirect.invalid/credential-target"
    )
    provider, opener = provider_with(redirected)

    report = provider.probe_capabilities()

    assert report.status is CapabilityState.CONTRACT_INVALID
    assert report.reason == "BBS_REDIRECT_REJECTED"
    assert len(opener.requests) == 1


def test_oversized_response_is_rejected_at_256_kib():
    provider, _ = provider_with(
        Response(raw=b"{" + (b"x" * bbs.MAX_RESPONSE_BYTES) + b"}")
    )

    report = provider.probe_capabilities()

    assert report.status is CapabilityState.CONTRACT_INVALID
    assert report.reason == "BBS_RESPONSE_TOO_LARGE"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (Response(raw=b"not-json"), "BBS_RESPONSE_NOT_JSON"),
        (Response({"data": []}), "BBS_RESPONSE_ENVELOPE_INCOMPLETE"),
        (
            Response(envelope([], error={"message": "do not retain"})),
            "BBS_RESPONSE_REPORTED_ERROR",
        ),
    ],
)
def test_malformed_or_error_envelopes_fail_closed(response, reason):
    provider, _ = provider_with(response)

    report = provider.probe_capabilities()

    assert report.status is CapabilityState.CONTRACT_INVALID
    assert report.reason == reason


def test_key_is_absent_from_url_repr_report_exception_and_logs(capsys):
    provider, opener = provider_with(Response(sports("table_tennis")))

    report = provider.probe_capabilities()
    with pytest.raises(ResultsProviderError) as exc_info:
        provider.fetch_results(ResultsQuery())
    captured = capsys.readouterr()

    surfaces = [
        repr(provider),
        json.dumps(report.to_dict()),
        str(exc_info.value),
        captured.out,
        captured.err,
        *(request.full_url for request, _ in opener.requests),
    ]
    assert all(KEY not in surface for surface in surfaces)
