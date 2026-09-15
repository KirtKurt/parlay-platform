"""Source-bound T-10 timing receipts for the published Tennis card."""

from datetime import datetime, timedelta, timezone


def parse_utc(value):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("missing timestamp")
    stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def timing_receipt(*, displayed_commence_time, live_commence_time, cutoff_minutes):
    """Describe the exact schedule start used for the T-10 decision."""
    displayed = parse_utc(displayed_commence_time)
    if str(live_commence_time or "").strip():
        authoritative = parse_utc(live_commence_time)
        source = "LIVE"
    else:
        authoritative = displayed
        source = "COVERAGE_FALLBACK"
    cutoff = authoritative - timedelta(minutes=max(0, int(cutoff_minutes)))
    return {
        "t10_schedule_source": source,
        "t10_commence_time_utc": authoritative.isoformat(),
        "display_commence_time_utc": displayed.isoformat(),
        "schedule_start_consistent": authoritative == displayed,
        "t10_cutoff_utc": cutoff.isoformat(),
    }


def prediction_is_compliant(prediction_timestamp, receipt):
    return parse_utc(prediction_timestamp) <= parse_utc(receipt["t10_cutoff_utc"])
