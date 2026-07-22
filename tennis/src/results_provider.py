"""Provider-neutral contracts for tennis match results.

The collector deliberately does not depend on this module yet.  Results are a
separate, manually probed boundary until a provider proves that it can express
tennis completion semantics without collapsing retirements, walkovers, and
other non-normal endings into ordinary final results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Tuple, runtime_checkable


class CapabilityState(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    UNSUPPORTED = "UNSUPPORTED"
    RESULT_ROUTE_UNVERIFIED = "RESULT_ROUTE_UNVERIFIED"
    CONTRACT_INCOMPLETE = "CONTRACT_INCOMPLETE"
    READY = "READY"


class MatchPhase(str, Enum):
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    FINAL = "FINAL"
    POSTPONED = "POSTPONED"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"


class CompletionType(str, Enum):
    NORMAL = "NORMAL"
    RETIREMENT = "RETIREMENT"
    WALKOVER = "WALKOVER"
    DEFAULT = "DEFAULT"
    ABANDONED = "ABANDONED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CapabilityReport:
    """Redaction-safe outcome of a provider capability probe."""

    provider: str
    sport: str
    status: CapabilityState
    checked_at_utc: str
    reason: str
    request_count: int = 0
    result_route_checked: bool = False
    request_id: Optional[str] = None
    quota_limit: Optional[int] = None
    quota_remaining: Optional[int] = None
    quota_reset: Optional[int] = None
    retry_after_seconds: Optional[int] = None
    schema_fingerprint: Optional[str] = None
    reason_counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status is CapabilityState.READY

    def to_dict(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "provider": self.provider,
            "sport": self.sport,
            "status": self.status.value,
            "checkedAtUtc": self.checked_at_utc,
            "reason": self.reason,
            "requestCount": self.request_count,
            "resultRouteChecked": self.result_route_checked,
            "ready": self.ready,
        }
        optional = {
            "requestId": self.request_id,
            "quotaLimit": self.quota_limit,
            "quotaRemaining": self.quota_remaining,
            "quotaReset": self.quota_reset,
            "retryAfterSeconds": self.retry_after_seconds,
            "schemaFingerprint": self.schema_fingerprint,
        }
        report.update(
            {key: value for key, value in optional.items() if value is not None}
        )
        if self.reason_counts:
            report["reasonCounts"] = {
                str(key): int(value)
                for key, value in sorted(self.reason_counts.items())
                if int(value) > 0
            }
        return report


@dataclass(frozen=True)
class ResultsQuery:
    """A bounded provider-neutral request for completed tennis results."""

    date: Optional[str] = None
    limit: int = 50
    cursor: Optional[str] = None

    def __post_init__(self) -> None:
        if not 1 <= int(self.limit) <= 200:
            raise ValueError("RESULTS_QUERY_LIMIT_OUT_OF_RANGE")
        if self.date is not None:
            value = str(self.date)
            if (
                not value
                or len(value) > 32
                or any(
                    character
                    not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
                    for character in value
                )
            ):
                raise ValueError("RESULTS_QUERY_DATE_INVALID")
        if self.cursor is not None and (
            not str(self.cursor) or len(str(self.cursor)) > 512
        ):
            raise ValueError("RESULTS_QUERY_CURSOR_INVALID")


@dataclass(frozen=True)
class SetScore:
    set_number: int
    participant_a_games: int
    participant_b_games: int


@dataclass(frozen=True)
class NormalizedTennisResult:
    provider: str
    provider_match_id: str
    start_time_utc: Optional[str]
    updated_at_utc: Optional[str]
    fetched_at_utc: str
    participant_a_id: str
    participant_a_name: str
    participant_b_id: str
    participant_b_name: str
    phase: MatchPhase
    completion_type: CompletionType
    winner_side: Optional[str]
    set_scores: Tuple[SetScore, ...]
    raw_status: str
    confidence: Optional[float]
    request_id: Optional[str]
    payload_sha256: str

    @property
    def verified_winner(self) -> bool:
        return self.winner_side in {"A", "B"}

    @property
    def training_ready(self) -> bool:
        """Only an ordinary final with a verified winner may label training."""

        return (
            self.phase is MatchPhase.FINAL
            and self.completion_type is CompletionType.NORMAL
            and self.verified_winner
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "providerMatchId": self.provider_match_id,
            "startTimeUtc": self.start_time_utc,
            "updatedAtUtc": self.updated_at_utc,
            "fetchedAtUtc": self.fetched_at_utc,
            "participantA": {
                "id": self.participant_a_id,
                "name": self.participant_a_name,
            },
            "participantB": {
                "id": self.participant_b_id,
                "name": self.participant_b_name,
            },
            "phase": self.phase.value,
            "completionType": self.completion_type.value,
            "winnerSide": self.winner_side,
            "setScores": [
                {
                    "setNumber": score.set_number,
                    "participantAGames": score.participant_a_games,
                    "participantBGames": score.participant_b_games,
                }
                for score in self.set_scores
            ],
            "rawStatus": self.raw_status,
            "confidence": self.confidence,
            "requestId": self.request_id,
            "payloadSha256": self.payload_sha256,
            "verifiedWinner": self.verified_winner,
            "trainingReady": self.training_ready,
        }


@dataclass(frozen=True)
class ResultPage:
    provider: str
    sport: str
    fetched_at_utc: str
    results: Tuple[NormalizedTennisResult, ...]
    request_id: Optional[str] = None
    next_cursor: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "sport": self.sport,
            "fetchedAtUtc": self.fetched_at_utc,
            "results": [result.to_dict() for result in self.results],
            "requestId": self.request_id,
            "nextCursor": self.next_cursor,
        }


class ResultsProviderError(RuntimeError):
    """A stable reason code, never a raw upstream exception or response body."""


@runtime_checkable
class TennisResultsProvider(Protocol):
    def probe_capabilities(self) -> CapabilityReport: ...

    def fetch_results(self, query: ResultsQuery) -> ResultPage: ...
