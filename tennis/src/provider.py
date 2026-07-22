from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config import TennisConfig
from contracts import parse_utc, slate_date_et, stable_event_id, utc_iso


API_ROOT = "https://api.the-odds-api.com/v4"
DEFAULT_MAX_BOOK_PRICE_AGE_SECONDS = 900
DEFAULT_MAX_BOOK_FUTURE_SKEW_SECONDS = 60
DEFAULT_QUOTA_PROTECTED_RESERVE = 250
DEFAULT_QUOTA_DAILY_BUDGET = 20_000


class TennisProviderError(RuntimeError):
    pass


def _tour(key: str, title: str) -> str:
    text = f"{key} {title}".lower()
    if "wta" in text:
        return "WTA"
    if "atp" in text:
        return "ATP"
    if "itf" in text:
        return "ITF"
    return "OTHER"


def _discipline(key: str, title: str) -> str:
    text = f"{key} {title}".lower()
    return "doubles" if "double" in text else "singles"


def _header_int(headers: Any, name: str) -> Optional[int]:
    try:
        raw = headers.get(name)
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _config_nonnegative_int(config: Any, name: str, default: int) -> int:
    try:
        value = int(getattr(config, name, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _sanitized_error_code(exc: BaseException) -> str:
    """Return an allow-listed code without reflecting exception text."""

    if not isinstance(exc, TennisProviderError):
        return "unexpected_provider_failure"
    candidate = str(exc).split(":", 1)[0]
    allowed = {
        "odds_event_invalid_commence_time",
        "odds_event_missing_commence_time",
        "odds_events_not_a_list",
        "provider_invalid_json",
        "provider_request_build_failed",
        "provider_request_failed",
    }
    if candidate in allowed:
        return candidate
    if (
        candidate.startswith("provider_http_")
        and candidate.removeprefix("provider_http_").isdigit()
    ):
        return candidate
    return "provider_error"


def _valid_american_price(value: Any) -> bool:
    """Accept only provider JSON numbers in the valid American-odds domain."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    odds = float(value)
    return math.isfinite(odds) and abs(odds) >= 100.0


class OddsApiTennisProvider:
    """Provider adapter with quota-free schedule discovery and gated odds reads."""

    def __init__(self, config: TennisConfig):
        self.config = config
        self.odds_call_count = 0
        self.schedule_call_count = 0
        self._counter_lock = Lock()
        self._quota_remaining: Optional[int] = None
        self._quota_last_request_cost = 1

    def _remember_quota_usage(self, usage: Dict[str, Any]) -> None:
        remaining = usage.get("requestsRemaining")
        last_cost = usage.get("lastRequestCost")
        with self._counter_lock:
            if isinstance(remaining, int) and remaining >= 0:
                self._quota_remaining = remaining
            if isinstance(last_cost, int) and last_cost > 0:
                self._quota_last_request_cost = max(
                    self._quota_last_request_cost, last_cost
                )

    def _quota_snapshot(self) -> Tuple[Optional[int], int]:
        with self._counter_lock:
            return self._quota_remaining, self._quota_last_request_cost

    def _get_json(
        self, path: str, params: Dict[str, Any], *, request_kind: str
    ) -> Tuple[Any, Dict[str, Any]]:
        try:
            query = urllib.parse.urlencode(
                {key: value for key, value in params.items() if value not in (None, "")}
            )
            url = f"{API_ROOT}{path}?{query}"
            request = urllib.request.Request(
                url, headers={"accept": "application/json"}
            )
        except Exception:
            raise TennisProviderError("provider_request_build_failed") from None
        try:
            # Count the external attempt before I/O. Timeouts, transport failures,
            # HTTP errors, and invalid JSON can all consume provider quota.
            with self._counter_lock:
                if request_kind == "odds":
                    self.odds_call_count += 1
                else:
                    self.schedule_call_count += 1
            with urllib.request.urlopen(
                request, timeout=self.config.provider_timeout_seconds
            ) as response:
                raw_body = response.read()
                usage = {
                    "requestsRemaining": _header_int(
                        response.headers, "x-requests-remaining"
                    ),
                    "requestsUsed": _header_int(response.headers, "x-requests-used"),
                    "lastRequestCost": _header_int(response.headers, "x-requests-last"),
                    "fetchedAtUtc": utc_iso(datetime.now(timezone.utc)),
                }
        except urllib.error.HTTPError as exc:
            status = exc.code if isinstance(exc.code, int) else 0
            raise TennisProviderError(f"provider_http_{status}") from None
        except Exception:
            raise TennisProviderError("provider_request_failed") from None

        try:
            data = json.loads(raw_body.decode("utf-8"))
        except Exception:
            raise TennisProviderError("provider_invalid_json") from None

        self._remember_quota_usage(usage)
        return data, usage

    def discover_tournaments(self) -> List[Dict[str, Any]]:
        rows, _ = self._get_json(
            "/sports/",
            {"apiKey": self.config.odds_api_key},
            request_kind="schedule",
        )
        if not isinstance(rows, list):
            raise TennisProviderError("sports_discovery_not_a_list")
        tournaments: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "").strip()
            title = str(row.get("title") or key).strip()
            if str(row.get("group") or "").strip().lower() != "tennis":
                continue
            if row.get("active") is False or row.get("has_outrights") is True:
                continue
            discipline = _discipline(key, title)
            if discipline == "doubles" and not self.config.include_doubles:
                continue
            if not key:
                continue
            tournaments.append(
                {
                    "key": key,
                    "title": title,
                    "tour": _tour(key, title),
                    "discipline": discipline,
                }
            )
        return sorted(tournaments, key=lambda row: row["key"])

    def discover_schedule(
        self, now_utc: datetime
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        current = now_utc.astimezone(timezone.utc)
        schedule_calls_before = self.schedule_call_count
        odds_calls_before = self.odds_call_count
        tournaments = self.discover_tournaments()
        if not tournaments:
            return [], {
                "tournamentCount": 0,
                "scheduleCalls": self.schedule_call_count - schedule_calls_before,
                "oddsCalls": self.odds_call_count - odds_calls_before,
            }

        commence_from = current - timedelta(hours=12)
        commence_to = current + timedelta(hours=self.config.discovery_horizon_hours)
        events: Dict[str, Dict[str, Any]] = {}
        usage_rows = []
        errors = []

        def fetch_tournament_schedule(tournament: Dict[str, Any]):
            raw_events, usage = self._get_json(
                f"/sports/{urllib.parse.quote(tournament['key'], safe='')}/events",
                {
                    "apiKey": self.config.odds_api_key,
                    "dateFormat": "iso",
                    "commenceTimeFrom": utc_iso(commence_from),
                    "commenceTimeTo": utc_iso(commence_to),
                },
                request_kind="schedule",
            )
            if not isinstance(raw_events, list):
                raise TennisProviderError("schedule_events_not_a_list")
            return raw_events, usage

        results: Dict[str, Tuple[Any, Dict[str, Any]]] = {}
        workers = min(self.config.max_parallel_requests, len(tournaments))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(fetch_tournament_schedule, tournament): tournament
                for tournament in tournaments
            }
            for future in as_completed(futures):
                tournament = futures[future]
                try:
                    results[tournament["key"]] = future.result()
                except Exception as exc:
                    errors.append(
                        {
                            "tournamentKey": tournament["key"],
                            "errorCode": _sanitized_error_code(exc),
                        }
                    )

        for tournament in tournaments:
            result = results.get(tournament["key"])
            if result is None:
                continue
            raw_events, usage = result
            usage_rows.append({"tournamentKey": tournament["key"], **usage})
            for raw in raw_events if isinstance(raw_events, list) else []:
                normalized = self._normalize_schedule_event(raw, tournament)
                if normalized is None:
                    continue
                existing = events.get(normalized["event_id"])
                if existing and existing.get("tournament_key") != normalized.get(
                    "tournament_key"
                ):
                    raise TennisProviderError(
                        "provider_event_id_collision_across_tournaments"
                    )
                events[normalized["event_id"]] = normalized

        if errors:
            # Schedule completeness is a hard gate. Details are intentionally not
            # reflected into the exception because provider errors may carry URLs,
            # credentials, or response bodies.
            raise TennisProviderError("schedule_discovery_incomplete")
        return sorted(
            events.values(),
            key=lambda row: (row["commence_time"], row["event_id"]),
        ), {
            "tournamentCount": len(tournaments),
            "eventCount": len(events),
            "scheduleCalls": self.schedule_call_count - schedule_calls_before,
            "oddsCalls": self.odds_call_count - odds_calls_before,
            "usage": usage_rows,
        }

    def _normalize_schedule_event(
        self, raw: Any, tournament: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        event_id = stable_event_id(raw)
        commence = parse_utc(raw.get("commence_time"))
        home = str(raw.get("home_team") or "").strip()
        away = str(raw.get("away_team") or "").strip()
        if not event_id or commence is None or not home or not away:
            return None
        return {
            "event_id": event_id,
            "sport": "tennis",
            "player_a": away,
            "player_b": home,
            "provider_side_a": "away",
            "provider_side_b": "home",
            "commence_time": utc_iso(commence),
            "slate_date_et": slate_date_et(commence, self.config.slate_timezone),
            "tournament_key": tournament["key"],
            "tournament_title": tournament["title"],
            "tour": tournament["tour"],
            "discipline": tournament["discipline"],
        }

    def fetch_odds(
        self, events: Iterable[Dict[str, Any]]
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        odds_calls_before = self.odds_call_count
        event_rows = [dict(event) for event in events or []]
        by_tournament: Dict[str, List[Dict[str, Any]]] = {}
        for event in event_rows:
            key = str(event.get("tournament_key") or "")
            if key and event.get("event_id"):
                by_tournament.setdefault(key, []).append(dict(event))

        odds_by_id: Dict[str, Dict[str, Any]] = {}
        usage_rows = []
        fetched_at_by_tournament: Dict[str, str] = {}
        failed_by_key: Dict[str, Dict[str, Any]] = {}
        successful_tournament_keys: List[str] = []
        empty_tournament_keys: List[str] = []
        attempted_tournament_keys: List[str] = []
        quota_skipped_keys: List[str] = []
        quota_header_missing_keys: List[str] = []
        max_price_age = _config_nonnegative_int(
            self.config,
            "max_book_price_age_seconds",
            DEFAULT_MAX_BOOK_PRICE_AGE_SECONDS,
        )
        max_future_skew = _config_nonnegative_int(
            self.config,
            "max_book_future_skew_seconds",
            DEFAULT_MAX_BOOK_FUTURE_SKEW_SECONDS,
        )
        protected_reserve = _config_nonnegative_int(
            self.config,
            "quota_protected_reserve",
            DEFAULT_QUOTA_PROTECTED_RESERVE,
        )
        daily_budget = _config_nonnegative_int(
            self.config, "quota_daily_budget", DEFAULT_QUOTA_DAILY_BUDGET
        )
        require_quota_headers = bool(
            getattr(self.config, "require_quota_headers", False)
        )

        def fetch_tournament_odds(
            key: str, scheduled: List[Dict[str, Any]]
        ) -> Tuple[Any, Dict[str, Any]]:
            event_ids = sorted(str(event["event_id"]) for event in scheduled)
            raw_rows, usage = self._get_json(
                f"/sports/{urllib.parse.quote(key, safe='')}/odds/",
                {
                    "apiKey": self.config.odds_api_key,
                    "regions": self.config.regions,
                    "markets": self.config.markets,
                    "oddsFormat": self.config.odds_format,
                    "dateFormat": "iso",
                    "eventIds": ",".join(event_ids),
                },
                request_kind="odds",
            )
            if not isinstance(raw_rows, list):
                raise TennisProviderError("odds_events_not_a_list")
            return raw_rows, usage

        odds_results: Dict[str, Tuple[Any, Dict[str, Any]]] = {}
        pending_keys = sorted(by_tournament)
        known_remaining, estimated_unit_cost = self._quota_snapshot()
        workers = max(
            1, min(max(1, int(self.config.max_parallel_requests)), len(pending_keys))
        )
        while pending_keys:
            if known_remaining is None:
                # Probe one tournament before fan-out so the response headers can
                # establish a quota boundary for the remainder of the pull.
                batch_size = 1
            else:
                available_cost = max(0, known_remaining - protected_reserve)
                allowed_calls = available_cost // max(1, estimated_unit_cost)
                if allowed_calls <= 0:
                    quota_skipped_keys.extend(pending_keys)
                    for key in pending_keys:
                        failed_by_key[key] = {
                            "tournamentKey": key,
                            "errorCode": "quota_protected_reserve",
                            "attempted": False,
                        }
                    break
                batch_size = min(workers, allowed_calls)

            batch_keys = pending_keys[:batch_size]
            pending_keys = pending_keys[batch_size:]
            attempted_tournament_keys.extend(batch_keys)
            remaining_before_batch = known_remaining
            batch_usages: List[Dict[str, Any]] = []
            batch_failure_count = 0
            with ThreadPoolExecutor(max_workers=max(1, len(batch_keys))) as executor:
                futures = {
                    executor.submit(fetch_tournament_odds, key, by_tournament[key]): key
                    for key in batch_keys
                }
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        raw_rows, usage = future.result()
                        if require_quota_headers and not isinstance(
                            usage.get("requestsRemaining"), int
                        ):
                            batch_failure_count += 1
                            quota_header_missing_keys.append(key)
                            failed_by_key[key] = {
                                "tournamentKey": key,
                                "errorCode": "quota_headers_missing",
                                "attempted": True,
                            }
                            continue
                        odds_results[key] = (raw_rows, usage)
                        batch_usages.append(usage)
                        self._remember_quota_usage(usage)
                    except Exception as exc:
                        batch_failure_count += 1
                        failed_by_key[key] = {
                            "tournamentKey": key,
                            "errorCode": _sanitized_error_code(exc),
                            "attempted": True,
                        }

            reported_remaining = [
                value
                for value in (usage.get("requestsRemaining") for usage in batch_usages)
                if isinstance(value, int) and value >= 0
            ]
            reported_costs = [
                value
                for value in (usage.get("lastRequestCost") for usage in batch_usages)
                if isinstance(value, int) and value > 0
            ]
            if reported_costs:
                estimated_unit_cost = max(estimated_unit_cost, *reported_costs)
            cached_remaining, cached_cost = self._quota_snapshot()
            estimated_unit_cost = max(estimated_unit_cost, cached_cost)
            if reported_remaining:
                known_remaining = min(reported_remaining)
                # A failed request may still consume quota without returning usage
                # headers, so reserve one estimated unit for each such attempt.
                known_remaining = max(
                    0,
                    known_remaining - batch_failure_count * estimated_unit_cost,
                )
            elif cached_remaining is not None and (
                remaining_before_batch is None
                or cached_remaining != remaining_before_batch
            ):
                known_remaining = max(
                    0,
                    cached_remaining - batch_failure_count * estimated_unit_cost,
                )
            elif remaining_before_batch is not None:
                known_remaining = max(
                    0,
                    remaining_before_batch - len(batch_keys) * estimated_unit_cost,
                )

            if quota_header_missing_keys and require_quota_headers:
                quota_skipped_keys.extend(pending_keys)
                for key in pending_keys:
                    failed_by_key[key] = {
                        "tournamentKey": key,
                        "errorCode": "quota_status_unavailable",
                        "attempted": False,
                    }
                pending_keys = []
                break

        if known_remaining is not None:
            with self._counter_lock:
                self._quota_remaining = known_remaining

        for key, scheduled in sorted(by_tournament.items()):
            result = odds_results.get(key)
            if result is None:
                continue
            raw_rows, usage = result
            usage_rows.append({"tournamentKey": key, **usage})
            fetched_at_by_tournament[key] = str(
                usage.get("fetchedAtUtc") or utc_iso(datetime.now(timezone.utc))
            )
            scheduled_by_id = {str(row["event_id"]): row for row in scheduled}
            normalized_for_tournament: Dict[str, Dict[str, Any]] = {}
            try:
                for raw in raw_rows if isinstance(raw_rows, list) else []:
                    event_id = stable_event_id(raw)
                    base = scheduled_by_id.get(str(event_id or ""))
                    if base is None:
                        continue
                    normalized_for_tournament[str(event_id)] = {
                        **self._normalize_odds_event(
                            raw,
                            base,
                            fetched_at_utc=fetched_at_by_tournament[key],
                            max_book_price_age_seconds=max_price_age,
                            max_book_future_skew_seconds=max_future_skew,
                        ),
                        "fetched_at_utc": fetched_at_by_tournament[key],
                    }
            except Exception as exc:
                failed_by_key[key] = {
                    "tournamentKey": key,
                    "errorCode": _sanitized_error_code(exc),
                    "attempted": True,
                }
                continue

            odds_by_id.update(normalized_for_tournament)
            has_usable_odds = any(
                bool(row.get("books")) for row in normalized_for_tournament.values()
            )
            if has_usable_odds:
                successful_tournament_keys.append(key)
            else:
                empty_tournament_keys.append(key)

        for event in event_rows:
            event_id = str(event.get("event_id") or "")
            if event_id and event_id not in odds_by_id:
                odds_by_id[event_id] = {
                    **dict(event),
                    "books": {},
                    "book_rejections": {},
                    "book_rejection_reason_counts": {},
                    "book_quality": {
                        "accepted_book_count": 0,
                        "rejected_book_count": 0,
                        "rejection_counts": {},
                        "rejections_by_book": {},
                    },
                    "fetched_at_utc": fetched_at_by_tournament.get(
                        str(event.get("tournament_key") or ""),
                        utc_iso(datetime.now(timezone.utc)),
                    ),
                }

        fetched_values = [
            parsed
            for parsed in (
                parse_utc(value) for value in fetched_at_by_tournament.values()
            )
            if parsed is not None
        ]
        fetch_completed_at = (
            max(fetched_values) if fetched_values else datetime.now(timezone.utc)
        )

        rejection_counts_by_event = {
            event_id: dict(row.get("book_rejection_reason_counts") or {})
            for event_id, row in sorted(odds_by_id.items())
            if row.get("book_rejection_reason_counts")
        }
        aggregate_rejection_counts: Dict[str, int] = {}
        for counts in rejection_counts_by_event.values():
            for reason, count in counts.items():
                aggregate_rejection_counts[reason] = aggregate_rejection_counts.get(
                    reason, 0
                ) + int(count)

        interval_minutes = max(
            1,
            _config_nonnegative_int(self.config, "pull_interval_minutes", 15),
        )
        # Forecast a rolling day, not merely the T-8 opening window. A slate
        # can remain active after its first match has begun.
        forecast_pulls = (24 * 60 + interval_minutes - 1) // interval_minutes
        projected_daily_calls = len(by_tournament) * forecast_pulls
        projected_daily_cost = projected_daily_calls * max(1, estimated_unit_cost)
        odds_call_delta = self.odds_call_count - odds_calls_before
        attempted_odds_calls = max(odds_call_delta, len(attempted_tournament_keys))
        reserve_reached = (
            known_remaining is not None and known_remaining <= protected_reserve
        )
        projected_reserve_breach = (
            known_remaining is not None
            and known_remaining - projected_daily_cost < protected_reserve
        )

        return odds_by_id, {
            "tournamentOddsCalls": len(attempted_tournament_keys),
            "oddsCalls": attempted_odds_calls,
            "fetchCompletedAtUtc": utc_iso(fetch_completed_at),
            "usage": usage_rows,
            "successfulTournamentKeys": sorted(successful_tournament_keys),
            "failedTournaments": [failed_by_key[key] for key in sorted(failed_by_key)],
            "emptyTournamentKeys": sorted(empty_tournament_keys),
            "bookRejectionCountsByEvent": rejection_counts_by_event,
            "bookRejectionReasonCounts": dict(
                sorted(aggregate_rejection_counts.items())
            ),
            "quotaStatus": {
                "attemptedOddsCalls": attempted_odds_calls,
                "estimatedAttemptCost": attempted_odds_calls
                * max(1, estimated_unit_cost),
                "requestsRemaining": known_remaining,
                "remainingAfterAttempt": known_remaining,
                "reportedRequestsRemaining": known_remaining,
                "protectedReserve": protected_reserve,
                "protectedReserveReached": reserve_reached,
                "projectedReserveBreach": projected_reserve_breach,
                "dailyBudget": daily_budget,
                "forecastPullsPerDay": forecast_pulls,
                "projectedDailyOddsCalls": projected_daily_calls,
                "projectedDailyRequestCost": projected_daily_cost,
                "dailyBudgetExceeded": projected_daily_cost > daily_budget,
                "quotaHeadersRequired": require_quota_headers,
                "quotaHeaderMissingTournamentKeys": sorted(quota_header_missing_keys),
                "quotaSkippedTournamentKeys": sorted(quota_skipped_keys),
            },
        }

    @staticmethod
    def _normalize_odds_event(
        raw: Dict[str, Any],
        scheduled: Dict[str, Any],
        *,
        fetched_at_utc: Any = None,
        max_book_price_age_seconds: int = DEFAULT_MAX_BOOK_PRICE_AGE_SECONDS,
        max_book_future_skew_seconds: int = DEFAULT_MAX_BOOK_FUTURE_SKEW_SECONDS,
    ) -> Dict[str, Any]:
        player_a = scheduled["player_a"]
        player_b = scheduled["player_b"]
        schedule_commence = parse_utc(scheduled.get("commence_time"))
        odds_commence = parse_utc(raw.get("commence_time"))
        if raw.get("commence_time") and odds_commence is None:
            raise TennisProviderError("odds_event_invalid_commence_time")
        commence_candidates = [
            value for value in (schedule_commence, odds_commence) if value is not None
        ]
        if not commence_candidates:
            raise TennisProviderError("odds_event_missing_commence_time")
        effective_commence = min(commence_candidates)
        fetched_at = parse_utc(fetched_at_utc) or datetime.now(timezone.utc)
        books: Dict[str, Any] = {}
        book_rejections: Dict[str, str] = {}
        rejection_counts: Dict[str, int] = {}

        def reject(book_key: str, reason: str) -> None:
            book_rejections[book_key] = reason
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

        for bookmaker in raw.get("bookmakers") or []:
            if not isinstance(bookmaker, dict):
                continue
            book_key = str(bookmaker.get("key") or "").strip().lower()
            if not book_key:
                continue
            h2h = next(
                (
                    market
                    for market in bookmaker.get("markets") or []
                    if isinstance(market, dict) and market.get("key") == "h2h"
                ),
                None,
            )
            if h2h is None:
                continue
            last_update_raw = h2h.get("last_update") or bookmaker.get("last_update")
            if not last_update_raw:
                reject(book_key, "missing_last_update")
                continue
            last_update = parse_utc(last_update_raw)
            if last_update is None:
                reject(book_key, "invalid_last_update")
                continue
            if last_update >= effective_commence:
                reject(book_key, "last_update_at_or_after_start")
                continue
            if (
                last_update - fetched_at
            ).total_seconds() > max_book_future_skew_seconds:
                reject(book_key, "future_skewed_last_update")
                continue
            if (fetched_at - last_update).total_seconds() > max_book_price_age_seconds:
                reject(book_key, "stale_last_update")
                continue
            prices: Dict[str, Any] = {}
            for outcome in h2h.get("outcomes") or []:
                if not isinstance(outcome, dict):
                    continue
                name = str(outcome.get("name") or "").strip()
                if name == player_a:
                    prices["player_a"] = outcome.get("price")
                elif name == player_b:
                    prices["player_b"] = outcome.get("price")
            if prices.get("player_a") is None or prices.get("player_b") is None:
                reject(book_key, "missing_h2h_outcome")
                continue
            if not all(
                _valid_american_price(prices.get(side))
                for side in ("player_a", "player_b")
            ):
                reject(book_key, "invalid_american_price")
                continue
            books[book_key] = {
                "player_a": prices["player_a"],
                "player_b": prices["player_b"],
                "last_update": utc_iso(last_update),
            }
        return {
            **dict(scheduled),
            "schedule_commence_time": (
                utc_iso(schedule_commence) if schedule_commence else None
            ),
            "odds_commence_time": utc_iso(odds_commence) if odds_commence else None,
            "commence_time": utc_iso(effective_commence),
            "commence_time_cutoff_policy": "earliest_schedule_or_odds",
            "books": books,
            "book_rejections": dict(sorted(book_rejections.items())),
            "book_rejection_reason_counts": dict(sorted(rejection_counts.items())),
            "book_quality": {
                "accepted_book_count": len(books),
                "rejected_book_count": sum(rejection_counts.values()),
                "rejection_counts": dict(sorted(rejection_counts.items())),
                "rejections_by_book": dict(sorted(book_rejections.items())),
            },
        }
