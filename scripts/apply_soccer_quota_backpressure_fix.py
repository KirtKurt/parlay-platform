from __future__ import annotations

from pathlib import Path


COLLECTOR = Path("soccer_auto/collector.py")


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = COLLECTOR.read_text()

    helper_anchor = "\n\ndef _catalog_snapshot(\n"
    helper = '''\n\ndef _provider_quota_snapshot(store: SoccerStore) -> dict[str, Any]:
    """Read the latest shared-provider quota without reserving credits.

    Dispatch uses this only as a conservative backpressure signal.  A missing
    or unreadable observation must not become a new outage: the worker's
    atomic admission guard remains the authority before every paid call.
    """
    try:
        latest = (
            store.ops.get_item(
                Key={"PK": "QUOTA_STATE", "SK": "LATEST"},
                ConsistentRead=True,
            ).get("Item")
            or {}
        )
        remaining = int(latest.get("remaining"))
        used = int(latest.get("used"))
    except (AttributeError, TypeError, ValueError):
        return {
            "known": False,
            "exhausted": False,
            "remaining": None,
            "used": None,
            "observed_at": "",
        }
    return {
        "known": True,
        "exhausted": remaining <= 0,
        "remaining": remaining,
        "used": used,
        "observed_at": str(latest.get("observed_at") or ""),
    }
'''
    if "def _provider_quota_snapshot(" not in text:
        text = _replace_once(
            text,
            helper_anchor,
            helper + helper_anchor,
            label="provider quota helper",
        )

    dispatch_anchor = '''    for item in prepared:\n        row = item["row"]\n'''
    dispatch_guard = '''    quota_snapshot = _provider_quota_snapshot(store)
    dispatch_required_count = sum(
        1 for item in prepared if item["dispatch_required"]
    )
    if quota_snapshot["exhausted"] and dispatch_required_count:
        # Do not manufacture queue pressure while the shared provider is
        # definitively out of credits.  We intentionally do not advance
        # last_dispatched_at here, so the very next dispatcher tick can resume
        # immediately after a fresh positive quota observation arrives.
        return {
            "ok": True,
            "system": "soccer_auto",
            "match_day_timezone": DAY_TIMEZONE,
            "daily_lead_hours": COLLECTION_LEAD_HOURS,
            "daily_windows": {key: value.__dict__ for key, value in windows.items()},
            "events_seen": len(events),
            "observed_at": dispatch_observed_at,
            "coverage_manifest_digest": manifest["manifest_digest"],
            "coverage_manifest_events": manifest["event_count"],
            "before_window": before_window,
            "enqueued": 0,
            "skipped": len(prepared),
            "schedule_races": 0,
            "recovered_plans": 0,
            "provider_quota_deferred": True,
            "quota_remaining": quota_snapshot["remaining"],
            "quota_used": quota_snapshot["used"],
            "quota_observed_at": quota_snapshot["observed_at"],
        }
    for item in prepared:
        row = item["row"]
'''
    if "provider_quota_deferred" not in text:
        text = _replace_once(
            text,
            dispatch_anchor,
            dispatch_guard,
            label="dispatch backpressure guard",
        )

    worker_anchor = '''            except (ProviderBudgetDeferred, CoverageExecutionDeferred) as exc:\n                receive_count = max(\n'''
    worker_guard = '''            except (ProviderBudgetDeferred, CoverageExecutionDeferred) as exc:
                if (
                    isinstance(exc, ProviderBudgetDeferred)
                    and job.get("action") == "DISCOVER_EVENT"
                ):
                    # A DISCOVER_EVENT has no paid evidence yet.  Keeping one
                    # replacement SQS message per event while quota is exhausted
                    # only creates an unbounded queue-age/depth alarm.  Retire
                    # this delivery and make the event due again; the dispatcher
                    # is the retry authority once quota becomes positive.
                    job_event = dict(job.get("event") or {})
                    cadence = max(60, int(job.get("cadence_seconds") or 300))
                    dispatch_rearmed = False
                    if job_event.get("event_key"):
                        try:
                            dispatch_rearmed = bool(
                                store.mark_dispatched(
                                    str(job_event["event_key"]),
                                    iso_utc(now_utc() - timedelta(seconds=cadence)),
                                    schedule_revision=int(
                                        job_event.get("schedule_revision") or 0
                                    ),
                                    schedule_identity_value=str(
                                        job_event.get("schedule_identity")
                                        or schedule_identity(job_event)
                                    ),
                                )
                            )
                        except Exception:
                            # The original delivery can still retire safely:
                            # its QUOTA_DEFERRED discovery evidence is durable,
                            # and a later normal cadence will retry even if this
                            # advisory re-arm lost a schedule race.
                            dispatch_rearmed = False
                    processed.append(
                        {
                            **exc.result,
                            "retry_via_dispatcher": True,
                            "retry_reenqueued": False,
                            "dispatch_rearmed": dispatch_rearmed,
                        }
                    )
                    continue
                receive_count = max(
'''
    if "retry_via_dispatcher" not in text:
        text = _replace_once(
            text,
            worker_anchor,
            worker_guard,
            label="worker external-quota drain guard",
        )

    COLLECTOR.write_text(text)


if __name__ == "__main__":
    main()
