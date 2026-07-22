"""Fail-closed Big Balls Sports Data capability probe for tennis results.

BBS does not currently advertise tennis in its public sports contract.  This
adapter therefore probes support before it ever addresses the results route.
It is intentionally independent of the MLB BBS client so tennis can be built,
deployed, and revoked without changing the MLB package.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from results_provider import (
    CapabilityReport,
    CapabilityState,
    CompletionType,
    MatchPhase,
    NormalizedTennisResult,
    ResultPage,
    ResultsProviderError,
    ResultsQuery,
    SetScore,
)


PROVIDER_NAME = "bbs"
SPORT_SLUG = "tennis"
BASE_URL = "https://api.bigballsdata.com"
REQUEST_TIMEOUT_SECONDS = 4
MAX_RESPONSE_BYTES = 256 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into an HTTPError instead of forwarding a bearer."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _default_opener(request: urllib.request.Request, *, timeout: int) -> Any:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def resolve_secret_value(secret_arn: str) -> str:
    """Resolve the scoped tennis secret without exposing SDK error details."""

    if not isinstance(secret_arn, str) or not secret_arn.strip():
        raise ResultsProviderError("BBS_CREDENTIAL_NOT_CONFIGURED")
    try:
        import boto3

        response = boto3.client("secretsmanager").get_secret_value(
            SecretId=secret_arn.strip()
        )
    except Exception:
        raise ResultsProviderError("BBS_SECRET_RETRIEVAL_FAILED") from None
    try:
        value = response.get("SecretString")
    except Exception:
        raise ResultsProviderError("BBS_SECRET_RETRIEVAL_FAILED") from None
    if not isinstance(value, str) or not value.strip():
        raise ResultsProviderError("BBS_SECRET_VALUE_MISSING")
    return value.strip()


@dataclass(frozen=True)
class _SafeTransportMetadata:
    request_id: Optional[str] = None
    quota_limit: Optional[int] = None
    quota_remaining: Optional[int] = None
    quota_reset: Optional[int] = None
    retry_after_seconds: Optional[int] = None


class _ProbeFailure(RuntimeError):
    def __init__(
        self,
        status: CapabilityState,
        reason: str,
        metadata: Optional[_SafeTransportMetadata] = None,
    ) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.metadata = metadata or _SafeTransportMetadata()


def _safe_nonnegative_int(value: Any, *, maximum: int = 2**63 - 1) -> Optional[int]:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= maximum else None


def _safe_token(value: Any, *, maximum_length: int = 128) -> Optional[str]:
    if not isinstance(value, (str, int)):
        return None
    token = str(value)
    if not 1 <= len(token) <= maximum_length:
        return None
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
    return token if all(character in allowed for character in token) else None


def _shape_fingerprint(value: Any) -> str:
    """Hash JSON shape only; never retain provider row values in probe output."""

    def shape(node: Any) -> Any:
        if isinstance(node, dict):
            return {str(key): shape(child) for key, child in sorted(node.items())}
        if isinstance(node, list):
            variants = {
                json.dumps(shape(child), sort_keys=True, separators=(",", ":"))
                for child in node[:20]
            }
            return {"type": "array", "variants": sorted(variants)}
        if node is None:
            return "null"
        if isinstance(node, bool):
            return "boolean"
        if isinstance(node, (int, float)):
            return "number"
        return "string"

    encoded = json.dumps(shape(value), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _safe_metadata(
    headers: Mapping[str, Any], meta: Optional[Mapping[str, Any]] = None
) -> _SafeTransportMetadata:
    lowered = {str(key).lower(): value for key, value in headers.items()}
    provider_meta = meta if isinstance(meta, Mapping) else {}
    request_id = _safe_token(
        lowered.get("x-request-id") or provider_meta.get("request_id")
    )
    return _SafeTransportMetadata(
        request_id=request_id,
        quota_limit=_safe_nonnegative_int(lowered.get("x-ratelimit-limit")),
        quota_remaining=_safe_nonnegative_int(lowered.get("x-ratelimit-remaining")),
        quota_reset=_safe_nonnegative_int(lowered.get("x-ratelimit-reset")),
        retry_after_seconds=_safe_nonnegative_int(
            lowered.get("retry-after"), maximum=86400
        ),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_utc(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _first_value(mapping: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _participant_identity(value: Any) -> Optional[Tuple[str, str]]:
    if not isinstance(value, Mapping):
        return None
    participant_id = _first_value(
        value, ("id", "participant_id", "player_id", "competitor_id")
    )
    name = _first_value(value, ("display_name", "name", "player_name"))
    if participant_id is None or name is None:
        return None
    participant_id_text = str(participant_id).strip()
    name_text = str(name).strip()
    if not participant_id_text or not name_text:
        return None
    return participant_id_text, name_text


def _participants(
    row: Mapping[str, Any],
) -> Tuple[Optional[Tuple[str, str]], Optional[Tuple[str, str]], Dict[str, str]]:
    aliases: Dict[str, str] = {}
    first: Any = row.get("participant_a") or row.get("player_a")
    second: Any = row.get("participant_b") or row.get("player_b")
    if first is None or second is None:
        participants = row.get("participants")
        if isinstance(participants, Sequence) and not isinstance(
            participants, (str, bytes)
        ):
            if len(participants) >= 2:
                first, second = participants[0], participants[1]
    if first is None or second is None:
        first, second = row.get("home"), row.get("away")
        aliases.update({"home": "A", "away": "B"})
    else:
        aliases.update(
            {
                "a": "A",
                "b": "B",
                "participant_a": "A",
                "participant_b": "B",
                "player_a": "A",
                "player_b": "B",
            }
        )
    first_identity = _participant_identity(first)
    second_identity = _participant_identity(second)
    if first_identity:
        aliases[first_identity[0]] = "A"
    if second_identity:
        aliases[second_identity[0]] = "B"
    return first_identity, second_identity, aliases


def _winner_side(
    row: Mapping[str, Any],
    aliases: Mapping[str, str],
    participants: Sequence[Any],
) -> Optional[str]:
    winner: Any = _first_value(row, ("winner_side", "winner_id", "winner"))
    if isinstance(winner, Mapping):
        winner = _first_value(
            winner, ("side", "id", "participant_id", "player_id", "competitor_id")
        )
    if winner is None:
        for index, participant in enumerate(participants):
            if isinstance(participant, Mapping) and participant.get("winner") is True:
                return "A" if index == 0 else "B"
        return None
    text = str(winner).strip()
    if text in aliases:
        return aliases[text]
    lowered = text.lower()
    lowered_aliases = {key.lower(): side for key, side in aliases.items()}
    return lowered_aliases.get(lowered)


def _phase(raw_status: str) -> Optional[MatchPhase]:
    token = raw_status.strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"scheduled", "pending", "not_started", "pre_match", "prematch"}:
        return MatchPhase.SCHEDULED
    if token in {"live", "in_progress", "started", "playing"}:
        return MatchPhase.IN_PROGRESS
    if token in {
        "finished",
        "final",
        "completed",
        "complete",
        "retired",
        "retirement",
        "walkover",
        "walk_over",
        "default",
    }:
        return MatchPhase.FINAL
    if token in {"postponed", "delayed"}:
        return MatchPhase.POSTPONED
    if token in {"suspended", "interrupted"}:
        return MatchPhase.SUSPENDED
    if token in {"cancelled", "canceled", "abandoned", "abandonment"}:
        return MatchPhase.CANCELLED
    return None


def _completion(value: Any) -> CompletionType:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in {
        "normal",
        "completed_normally",
        "played",
        "full_match",
        "full_time",
    }:
        return CompletionType.NORMAL
    if token in {"retired", "retirement", "ret"}:
        return CompletionType.RETIREMENT
    if token in {"walkover", "walk_over", "wo", "w_o"}:
        return CompletionType.WALKOVER
    if token in {"default", "defaulted"}:
        return CompletionType.DEFAULT
    if token in {"abandoned", "abandonment"}:
        return CompletionType.ABANDONED
    return CompletionType.UNKNOWN


def _explicit_completion(row: Mapping[str, Any], raw_status: str) -> CompletionType:
    value = _first_value(
        row,
        (
            "completion_type",
            "termination_type",
            "termination",
            "finish_type",
            "result_type",
            "outcome_type",
            "end_reason",
        ),
    )
    completion = _completion(value)
    if completion is not CompletionType.UNKNOWN:
        return completion
    # A status such as "retired" is itself explicit termination detail.  The
    # generic status "finished" is deliberately not treated as normal.
    return _completion(raw_status)


def _terminal_contract_reasons(
    phase: Optional[MatchPhase],
    completion: CompletionType,
    winner: Optional[str],
) -> Tuple[str, ...]:
    """Validate terminal-state combinations returned by the results route.

    The upstream query asks for finished matches, but the provider must not be
    trusted to honor that filter.  Only coherent final results, or a coherent
    cancelled/abandoned result, may establish or use the capability.
    """

    if phase is None:
        return ()
    if completion is CompletionType.UNKNOWN:
        # The caller already emits the more specific missing-termination
        # reason; avoid obscuring it with a secondary combination error.
        return ()
    if phase is MatchPhase.FINAL:
        if completion not in {
            CompletionType.NORMAL,
            CompletionType.RETIREMENT,
            CompletionType.WALKOVER,
            CompletionType.DEFAULT,
        }:
            return ("MATCH_PHASE_COMPLETION_CONTRADICTORY",)
        if winner not in {"A", "B"}:
            return ("WINNER_MISSING_OR_UNVERIFIED",)
        return ()
    if phase is MatchPhase.CANCELLED:
        reasons = []
        if completion is not CompletionType.ABANDONED:
            reasons.append("MATCH_PHASE_COMPLETION_CONTRADICTORY")
        if winner in {"A", "B"}:
            reasons.append("CANCELLED_MATCH_HAS_WINNER")
        return tuple(reasons)
    return ("MATCH_NOT_TERMINAL",)


def _set_scores(row: Mapping[str, Any]) -> Tuple[Tuple[SetScore, ...], bool]:
    raw_sets = row.get("sets")
    if raw_sets is None:
        score = row.get("score")
        raw_sets = score.get("sets") if isinstance(score, Mapping) else None
    if raw_sets is None:
        return (), True
    if not isinstance(raw_sets, list):
        return (), False
    scores = []
    for index, raw_set in enumerate(raw_sets, start=1):
        if not isinstance(raw_set, Mapping):
            return (), False
        first = _first_value(
            raw_set, ("participant_a", "player_a", "a", "home", "home_score")
        )
        second = _first_value(
            raw_set, ("participant_b", "player_b", "b", "away", "away_score")
        )
        try:
            first_games = int(first)
            second_games = int(second)
            set_number = int(
                raw_set.get("set_number") or raw_set.get("number") or index
            )
        except (TypeError, ValueError):
            return (), False
        if first_games < 0 or second_games < 0 or set_number <= 0:
            return (), False
        scores.append(SetScore(set_number, first_games, second_games))
    return tuple(scores), True


def _confidence(row: Mapping[str, Any], meta: Mapping[str, Any]) -> Optional[float]:
    raw = row.get("confidence", meta.get("confidence"))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if 0.0 <= value <= 1.0 else None


def _normalize_row(
    row: Any,
    *,
    fetched_at_utc: str,
    meta: Mapping[str, Any],
) -> Tuple[Optional[NormalizedTennisResult], Tuple[str, ...]]:
    if not isinstance(row, Mapping):
        return None, ("ROW_NOT_OBJECT",)

    reasons = []
    match_id_value = _first_value(row, ("match_id", "id", "event_id"))
    match_id = str(match_id_value).strip() if match_id_value is not None else ""
    if not match_id:
        reasons.append("MATCH_ID_MISSING")

    participant_a, participant_b, aliases = _participants(row)
    if participant_a is None or participant_b is None:
        reasons.append("PARTICIPANT_IDENTITY_MISSING")

    raw_status_value = _first_value(row, ("status", "match_status", "state"))
    raw_status = str(raw_status_value).strip() if raw_status_value is not None else ""
    phase = _phase(raw_status)
    if phase is None:
        reasons.append("MATCH_PHASE_INVALID")

    completion = _explicit_completion(row, raw_status)
    if completion is CompletionType.UNKNOWN:
        reasons.append("TERMINATION_DETAIL_MISSING_OR_UNKNOWN")

    first_raw = row.get("participant_a") or row.get("player_a") or row.get("home")
    second_raw = row.get("participant_b") or row.get("player_b") or row.get("away")
    raw_participants = row.get("participants")
    if (
        (first_raw is None or second_raw is None)
        and isinstance(raw_participants, Sequence)
        and not isinstance(raw_participants, (str, bytes))
        and len(raw_participants) >= 2
    ):
        first_raw, second_raw = raw_participants[0], raw_participants[1]
    winner = _winner_side(row, aliases, (first_raw, second_raw))
    reasons.extend(_terminal_contract_reasons(phase, completion, winner))

    set_scores, sets_valid = _set_scores(row)
    if not sets_valid:
        reasons.append("SET_SCORES_INVALID")

    if reasons or participant_a is None or participant_b is None or phase is None:
        return None, tuple(sorted(set(reasons)))

    payload = json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request_id = _safe_token(meta.get("request_id"))
    return (
        NormalizedTennisResult(
            provider=PROVIDER_NAME,
            provider_match_id=match_id,
            start_time_utc=_canonical_utc(
                _first_value(
                    row,
                    ("start_time_utc", "kickoff_utc", "scheduled_at", "start_time"),
                )
            ),
            updated_at_utc=_canonical_utc(
                _first_value(row, ("updated_at", "updated_at_utc", "last_updated"))
            ),
            fetched_at_utc=fetched_at_utc,
            participant_a_id=participant_a[0],
            participant_a_name=participant_a[1],
            participant_b_id=participant_b[0],
            participant_b_name=participant_b[1],
            phase=phase,
            completion_type=completion,
            winner_side=winner,
            set_scores=set_scores,
            raw_status=raw_status[:64],
            confidence=_confidence(row, meta),
            request_id=request_id,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
        ),
        (),
    )


class BBSResultsProvider:
    """Manual-only BBS tennis results adapter with a mandatory capability gate."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        secret_arn: Optional[str] = None,
        secret_resolver: Callable[[str], str] = resolve_secret_value,
        opener: Callable[..., Any] = _default_opener,
    ) -> None:
        self._configured_api_key = api_key.strip() if isinstance(api_key, str) else None
        self._secret_arn = secret_arn.strip() if isinstance(secret_arn, str) else None
        self._secret_resolver = secret_resolver
        self._opener = opener
        self._resolved_api_key: Optional[str] = None
        self._request_count = 0
        self._last_capability: Optional[CapabilityReport] = None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider={PROVIDER_NAME!r}, credential=<redacted>)"
        )

    @property
    def request_count(self) -> int:
        return self._request_count

    def _api_key(self) -> str:
        if self._resolved_api_key:
            return self._resolved_api_key
        if self._configured_api_key:
            self._resolved_api_key = self._configured_api_key
            return self._resolved_api_key
        if not self._secret_arn:
            raise _ProbeFailure(
                CapabilityState.NOT_CONFIGURED, "BBS_CREDENTIAL_NOT_CONFIGURED"
            )
        try:
            value = self._secret_resolver(self._secret_arn)
        except Exception:
            raise _ProbeFailure(
                CapabilityState.NOT_CONFIGURED, "BBS_SECRET_RETRIEVAL_FAILED"
            ) from None
        if not isinstance(value, str) or not value.strip():
            raise _ProbeFailure(
                CapabilityState.NOT_CONFIGURED, "BBS_SECRET_VALUE_MISSING"
            )
        self._resolved_api_key = value.strip()
        return self._resolved_api_key

    @staticmethod
    def _http_failure(
        status_code: int, metadata: _SafeTransportMetadata
    ) -> _ProbeFailure:
        if 300 <= status_code <= 399:
            return _ProbeFailure(
                CapabilityState.CONTRACT_INVALID,
                "BBS_REDIRECT_REJECTED",
                metadata,
            )
        if status_code in {401, 403}:
            return _ProbeFailure(
                CapabilityState.AUTH_FAILED,
                f"BBS_AUTH_REJECTED_HTTP_{status_code}",
                metadata,
            )
        if status_code == 429:
            return _ProbeFailure(
                CapabilityState.RATE_LIMITED, "BBS_RATE_LIMITED", metadata
            )
        if 500 <= status_code <= 599:
            return _ProbeFailure(
                CapabilityState.UPSTREAM_UNAVAILABLE,
                f"BBS_UPSTREAM_HTTP_{status_code}",
                metadata,
            )
        return _ProbeFailure(
            CapabilityState.CONTRACT_INVALID,
            f"BBS_HTTP_{status_code}",
            metadata,
        )

    def _request_json(
        self, path: str, params: Optional[Mapping[str, Any]] = None
    ) -> Tuple[Dict[str, Any], _SafeTransportMetadata]:
        key = self._api_key()
        query = urllib.parse.urlencode(
            {name: value for name, value in (params or {}).items() if value is not None}
        )
        expected_url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(
            expected_url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": "inqsi-tennis-bbs-probe/1.0",
            },
            method="GET",
        )
        self._request_count += 1
        try:
            with self._opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status_value = getattr(response, "status", None)
                status = int(
                    status_value if status_value is not None else response.getcode()
                )
                headers = {
                    str(name).lower(): value
                    for name, value in getattr(response, "headers", {}).items()
                }
                metadata = _safe_metadata(headers)
                final_url_getter = getattr(response, "geturl", None)
                final_url = (
                    final_url_getter() if callable(final_url_getter) else expected_url
                )
                if final_url != expected_url:
                    raise _ProbeFailure(
                        CapabilityState.CONTRACT_INVALID,
                        "BBS_REDIRECT_REJECTED",
                        metadata,
                    )
                if status != 200:
                    raise self._http_failure(status, metadata)
                content_length = _safe_nonnegative_int(headers.get("content-length"))
                if content_length is not None and content_length > MAX_RESPONSE_BYTES:
                    raise _ProbeFailure(
                        CapabilityState.CONTRACT_INVALID,
                        "BBS_RESPONSE_TOO_LARGE",
                        metadata,
                    )
                try:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                except TypeError:
                    # Compatibility for small unit-test response doubles.  The
                    # production HTTPResponse always supports bounded reads.
                    raw = response.read()
        except _ProbeFailure:
            raise
        except urllib.error.HTTPError as exc:
            headers = {
                str(name).lower(): value for name, value in (exc.headers or {}).items()
            }
            raise self._http_failure(exc.code, _safe_metadata(headers)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise _ProbeFailure(
                CapabilityState.UPSTREAM_UNAVAILABLE, "BBS_NETWORK_UNAVAILABLE"
            ) from None
        except Exception:
            raise _ProbeFailure(
                CapabilityState.UPSTREAM_UNAVAILABLE, "BBS_NETWORK_UNAVAILABLE"
            ) from None

        if not isinstance(raw, (bytes, bytearray)):
            raise _ProbeFailure(
                CapabilityState.CONTRACT_INVALID,
                "BBS_RESPONSE_BODY_INVALID",
                metadata,
            )
        if len(raw) > MAX_RESPONSE_BYTES:
            raise _ProbeFailure(
                CapabilityState.CONTRACT_INVALID,
                "BBS_RESPONSE_TOO_LARGE",
                metadata,
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _ProbeFailure(
                CapabilityState.CONTRACT_INVALID,
                "BBS_RESPONSE_NOT_JSON",
                metadata,
            ) from None
        if not isinstance(payload, dict):
            raise _ProbeFailure(
                CapabilityState.CONTRACT_INVALID,
                "BBS_RESPONSE_NOT_OBJECT",
                metadata,
            )
        if not {"data", "meta", "error"}.issubset(payload):
            raise _ProbeFailure(
                CapabilityState.CONTRACT_INVALID,
                "BBS_RESPONSE_ENVELOPE_INCOMPLETE",
                metadata,
            )
        if payload.get("error") is not None:
            raise _ProbeFailure(
                CapabilityState.CONTRACT_INVALID,
                "BBS_RESPONSE_REPORTED_ERROR",
                metadata,
            )
        if not isinstance(payload.get("meta"), dict):
            raise _ProbeFailure(
                CapabilityState.CONTRACT_INVALID,
                "BBS_RESPONSE_META_INVALID",
                metadata,
            )
        return payload, _safe_metadata(headers, payload["meta"])

    def _report(
        self,
        *,
        status: CapabilityState,
        reason: str,
        request_count: int,
        result_route_checked: bool,
        metadata: Optional[_SafeTransportMetadata] = None,
        schema_fingerprint: Optional[str] = None,
        reason_counts: Optional[Mapping[str, int]] = None,
    ) -> CapabilityReport:
        safe = metadata or _SafeTransportMetadata()
        report = CapabilityReport(
            provider=PROVIDER_NAME,
            sport=SPORT_SLUG,
            status=status,
            checked_at_utc=_utc_now(),
            reason=reason,
            request_count=request_count,
            result_route_checked=result_route_checked,
            request_id=safe.request_id,
            quota_limit=safe.quota_limit,
            quota_remaining=safe.quota_remaining,
            quota_reset=safe.quota_reset,
            retry_after_seconds=safe.retry_after_seconds,
            schema_fingerprint=schema_fingerprint,
            reason_counts=reason_counts or {},
        )
        self._last_capability = report
        return report

    def probe_capabilities(self) -> CapabilityReport:
        started_at = self._request_count
        result_route_checked = False
        try:
            sports, sports_metadata = self._request_json("/v1/sports")
            sports_data = sports.get("data")
            if not isinstance(sports_data, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("slug"), str)
                for row in sports_data
            ):
                return self._report(
                    status=CapabilityState.CONTRACT_INVALID,
                    reason="BBS_SPORTS_CONTRACT_INVALID",
                    request_count=self._request_count - started_at,
                    result_route_checked=False,
                    metadata=sports_metadata,
                    schema_fingerprint=_shape_fingerprint(sports_data),
                )

            # Exact equality is intentional: title inference, case folding, and
            # substring matches could activate table_tennis or another sport.
            if not any(row.get("slug") == SPORT_SLUG for row in sports_data):
                return self._report(
                    status=CapabilityState.UNSUPPORTED,
                    reason="BBS_TENNIS_NOT_ADVERTISED",
                    request_count=self._request_count - started_at,
                    result_route_checked=False,
                    metadata=sports_metadata,
                    schema_fingerprint=_shape_fingerprint(sports_data),
                )

            result_route_checked = True
            matches, match_metadata = self._request_json(
                "/v1/matches",
                {"sport": SPORT_SLUG, "status": "finished", "limit": 1},
            )
            rows = matches.get("data")
            if not isinstance(rows, list):
                return self._report(
                    status=CapabilityState.CONTRACT_INVALID,
                    reason="BBS_TENNIS_RESULTS_NOT_ARRAY",
                    request_count=self._request_count - started_at,
                    result_route_checked=True,
                    metadata=match_metadata,
                    schema_fingerprint=_shape_fingerprint(rows),
                )
            if not rows:
                return self._report(
                    status=CapabilityState.RESULT_ROUTE_UNVERIFIED,
                    reason="BBS_TENNIS_RESULT_SAMPLE_EMPTY",
                    request_count=self._request_count - started_at,
                    result_route_checked=True,
                    metadata=match_metadata,
                    schema_fingerprint=_shape_fingerprint(rows),
                )

            fetched_at = _utc_now()
            _normalized, reasons = _normalize_row(
                rows[0], fetched_at_utc=fetched_at, meta=matches["meta"]
            )
            if reasons:
                reason_counts = {
                    reason: reasons.count(reason) for reason in set(reasons)
                }
                return self._report(
                    status=CapabilityState.CONTRACT_INCOMPLETE,
                    reason="BBS_TENNIS_RESULT_CONTRACT_INCOMPLETE",
                    request_count=self._request_count - started_at,
                    result_route_checked=True,
                    metadata=match_metadata,
                    schema_fingerprint=_shape_fingerprint(rows[0]),
                    reason_counts=reason_counts,
                )
            return self._report(
                status=CapabilityState.READY,
                reason="BBS_TENNIS_RESULTS_READY",
                request_count=self._request_count - started_at,
                result_route_checked=True,
                metadata=match_metadata,
                schema_fingerprint=_shape_fingerprint(rows[0]),
            )
        except _ProbeFailure as exc:
            return self._report(
                status=exc.status,
                reason=exc.reason,
                request_count=self._request_count - started_at,
                result_route_checked=result_route_checked,
                metadata=exc.metadata,
            )

    def fetch_results(self, query: ResultsQuery) -> ResultPage:
        if self._last_capability is None or not self._last_capability.ready:
            raise ResultsProviderError("BBS_RESULTS_PROVIDER_NOT_READY")
        if not isinstance(query, ResultsQuery):
            raise ResultsProviderError("BBS_RESULTS_QUERY_INVALID")

        params: Dict[str, Any] = {
            "sport": SPORT_SLUG,
            "status": "finished",
            "limit": int(query.limit),
        }
        if query.date is not None:
            params["date"] = query.date
        if query.cursor is not None:
            params["cursor"] = query.cursor
        try:
            payload, metadata = self._request_json("/v1/matches", params)
        except _ProbeFailure as exc:
            raise ResultsProviderError(exc.reason) from None
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ResultsProviderError("BBS_TENNIS_RESULTS_NOT_ARRAY")

        fetched_at = _utc_now()
        normalized = []
        for row in rows:
            result, reasons = _normalize_row(
                row, fetched_at_utc=fetched_at, meta=payload["meta"]
            )
            if result is None or reasons:
                raise ResultsProviderError("BBS_TENNIS_RESULT_CONTRACT_INCOMPLETE")
            normalized.append(result)

        raw_next = payload["meta"].get("next_cursor")
        next_cursor = (
            str(raw_next)
            if isinstance(raw_next, (str, int)) and 1 <= len(str(raw_next)) <= 512
            else None
        )
        return ResultPage(
            provider=PROVIDER_NAME,
            sport=SPORT_SLUG,
            fetched_at_utc=fetched_at,
            results=tuple(normalized),
            request_id=metadata.request_id,
            next_cursor=next_cursor,
        )


__all__ = [
    "BASE_URL",
    "BBSResultsProvider",
    "MAX_RESPONSE_BYTES",
    "PROVIDER_NAME",
    "REQUEST_TIMEOUT_SECONDS",
    "SPORT_SLUG",
    "resolve_secret_value",
]
