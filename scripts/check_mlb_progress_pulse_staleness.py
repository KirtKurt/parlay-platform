#!/usr/bin/env python3
"""Read-only watchdog for the visible MLB progress pulse on issue #567."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

STATE_MARKER = "MLB_PROGRESS_STATE_BASE64"
PULSE_AUTHOR_LOGIN = "github-actions[bot]"
PULSE_AUTHOR_ID = 41898282
PULSE_AUTHOR_TYPE = "Bot"
COMMENT_WINDOW = 100
ACTIVE_RUN_STATUSES = {"queued", "in_progress", "waiting", "pending", "requested"}
REPORTER_JOB_NAME = "read-only-progress-pulse"
REPORTER_ATTEMPT_PROOF_KEY = "_reporterJobAttempted"
REPORTER_ACTIVE_PROOF_KEY = "_reporterJobActive"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "gh_api_failed").strip()[-1000:])
    return result


def _gh_json(path: str) -> Any:
    result = _run(["gh", "api", path])
    return json.loads(result.stdout or "null")


def _issue_comments(repo: str, issue: int) -> list[dict[str, Any]]:
    # The issue-specific endpoint is oldest-first and offers no sort direction.
    # The repository endpoint supports a descending created-time sort, so a
    # single bounded page gives the newest evidence without walking all history.
    rows = _gh_json(
        f"repos/{repo}/issues/comments?"
        f"sort=created&direction=desc&per_page={COMMENT_WINDOW}&page=1"
    )
    if not isinstance(rows, list):
        raise RuntimeError("issue_comments_response_not_a_list")

    expected_suffix = f"/repos/{repo}/issues/{issue}".lower()
    return [
        dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("issue_url") or "").lower().endswith(expected_suffix)
    ]


def _workflow_runs(repo: str, workflow_file: str) -> list[dict[str, Any]]:
    payload = _gh_json(
        f"repos/{repo}/actions/workflows/{workflow_file}/runs?per_page=20"
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("workflow_runs_response_not_an_object")
    rows = payload.get("workflow_runs") or []
    if not isinstance(rows, list):
        raise RuntimeError("workflow_runs_not_a_list")
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _workflow_run_jobs(repo: str, run_id: Any) -> list[dict[str, Any]]:
    payload = _gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
    if not isinstance(payload, Mapping):
        raise RuntimeError("workflow_jobs_response_not_an_object")
    rows = payload.get("jobs") or []
    if not isinstance(rows, list):
        raise RuntimeError("workflow_jobs_not_a_list")
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _annotate_reporter_job_evidence(
    repo: str,
    runs: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    retry_cooldown_minutes: int,
) -> list[dict[str, Any]]:
    """Bind suppression to an actual reporter job, not a decision-only run."""

    annotated: list[dict[str, Any]] = []
    for source in runs:
        row = dict(source)
        status = str(row.get("status") or "")
        event = str(row.get("event") or "")

        # Every workflow_dispatch source in this repository is an explicit
        # forced pulse or a stale-gated dispatch. Automatic schedule,
        # workflow_run, and push decisions need job-level proof.
        if event == "workflow_dispatch":
            row[REPORTER_ATTEMPT_PROOF_KEY] = True
            row[REPORTER_ACTIVE_PROOF_KEY] = status in ACTIVE_RUN_STATUSES
            annotated.append(row)
            continue

        created = _timestamp(row.get("created_at"))
        age_minutes = (
            max(0.0, (now - created).total_seconds() / 60.0)
            if created is not None
            else None
        )
        needs_job_proof = status in ACTIVE_RUN_STATUSES or (
            status == "completed"
            and age_minutes is not None
            and age_minutes < retry_cooldown_minutes
        )
        if needs_job_proof:
            try:
                jobs = _workflow_run_jobs(repo, row.get("id"))
            except Exception:
                # Missing proof must never suppress a required recovery run.
                jobs = []
            reporter_jobs = [
                job for job in jobs if str(job.get("name") or "") == REPORTER_JOB_NAME
            ]
            attempted = any(
                str(job.get("conclusion") or "") != "skipped"
                for job in reporter_jobs
            )
            active = any(
                str(job.get("status") or "") in ACTIVE_RUN_STATUSES
                for job in reporter_jobs
            )
            row[REPORTER_ATTEMPT_PROOF_KEY] = attempted
            row[REPORTER_ACTIVE_PROOF_KEY] = active
        annotated.append(row)
    return annotated


def _is_authoritative_pulse_comment(comment: Mapping[str, Any]) -> bool:
    user = comment.get("user")
    if not isinstance(user, Mapping):
        return False
    try:
        author_id = int(user.get("id"))
    except (TypeError, ValueError):
        return False
    return (
        str(user.get("login") or "") == PULSE_AUTHOR_LOGIN
        and author_id == PULSE_AUTHOR_ID
        and str(user.get("type") or "") == PULSE_AUTHOR_TYPE
    )


def evaluate_staleness(
    comments: Iterable[Mapping[str, Any]],
    runs: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    stale_after_minutes: int,
    retry_cooldown_minutes: int,
    current_run_id: Optional[str] = None,
) -> dict[str, Any]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    visible: list[tuple[datetime, Mapping[str, Any]]] = []
    ignored_untrusted_markers = 0
    for comment in comments:
        if STATE_MARKER not in str(comment.get("body") or ""):
            continue
        if not _is_authoritative_pulse_comment(comment):
            ignored_untrusted_markers += 1
            continue
        created = _timestamp(comment.get("created_at"))
        if created is not None:
            visible.append((created, comment))
    latest_visible = max(visible, key=lambda item: item[0]) if visible else None
    latest_at = latest_visible[0] if latest_visible else None
    age_minutes = (
        max(0.0, round((now - latest_at).total_seconds() / 60.0, 3))
        if latest_at
        else None
    )
    stale = age_minutes is None or age_minutes > stale_after_minutes

    run_rows = [
        row
        for row in runs
        if current_run_id is None or str(row.get("id")) != str(current_run_id)
    ]
    # Automatic triggers can finish their decision job without starting the
    # reporter. Only explicit workflow_dispatch attempts or job-level reporter
    # evidence may suppress another stale recovery dispatch.
    active_runs = [
        row
        for row in run_rows
        if str(row.get("status") or "") in ACTIVE_RUN_STATUSES
        and bool(row.get(REPORTER_ACTIVE_PROOF_KEY))
    ]
    active_run = max(
        active_runs,
        key=lambda row: _timestamp(row.get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        default=None,
    )
    attempted_runs = [
        (_timestamp(row.get("created_at")), row)
        for row in run_rows
        if bool(row.get(REPORTER_ATTEMPT_PROOF_KEY))
        and _timestamp(row.get("created_at")) is not None
    ]
    latest_attempt = (
        max(attempted_runs, key=lambda item: item[0]) if attempted_runs else None
    )
    latest_attempt_age = (
        max(0.0, round((now - latest_attempt[0]).total_seconds() / 60.0, 3))
        if latest_attempt
        else None
    )

    dispatch_required = stale and active_run is None
    reason = "VISIBLE_PULSE_FRESH"
    if stale and active_run is not None:
        dispatch_required = False
        reason = "PULSE_RUN_ALREADY_ACTIVE"
    elif stale and latest_attempt_age is not None and latest_attempt_age < retry_cooldown_minutes:
        dispatch_required = False
        reason = "RECENT_PULSE_ATTEMPT_IN_COOLDOWN"
    elif stale and latest_at is None:
        reason = "NO_VISIBLE_PULSE"
    elif stale:
        reason = "VISIBLE_PULSE_STALE"

    return {
        "checkedAtUtc": now.isoformat(),
        "stale": stale,
        "dispatchRequired": dispatch_required,
        "reason": reason,
        "staleAfterMinutes": stale_after_minutes,
        "visiblePulseAtUtc": latest_at.isoformat() if latest_at else None,
        "visiblePulseAgeMinutes": age_minutes,
        "visiblePulseUrl": latest_visible[1].get("html_url") if latest_visible else None,
        "trustedPulseAuthorLogin": PULSE_AUTHOR_LOGIN,
        "ignoredUntrustedMarkerCount": ignored_untrusted_markers,
        "activeRunId": active_run.get("id") if active_run else None,
        "latestAttemptRunId": latest_attempt[1].get("id") if latest_attempt else None,
        "latestAttemptAgeMinutes": latest_attempt_age,
    }


def _write_github_output(path: str, result: Mapping[str, Any]) -> None:
    visible_age = result.get("visiblePulseAgeMinutes")
    values = {
        "stale": str(bool(result.get("stale"))).lower(),
        "dispatch_required": str(bool(result.get("dispatchRequired"))).lower(),
        "reason": str(result.get("reason") or "UNKNOWN"),
        "visible_pulse_age_minutes": str(
            visible_age if visible_age is not None else "n/a"
        ),
    }
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", "KirtKurt/parlay-platform"),
    )
    parser.add_argument("--issue", type=int, default=567)
    parser.add_argument("--workflow", default="mlb-30m-progress-pulse.yml")
    parser.add_argument(
        "--stale-after-minutes",
        type=int,
        default=int(os.environ.get("MLB_PROGRESS_STALE_AFTER_MINUTES", "28")),
    )
    parser.add_argument("--retry-cooldown-minutes", type=int, default=10)
    parser.add_argument("--current-run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    runs = _annotate_reporter_job_evidence(
        args.repo,
        _workflow_runs(args.repo, args.workflow),
        now=now,
        retry_cooldown_minutes=args.retry_cooldown_minutes,
    )
    result = evaluate_staleness(
        _issue_comments(args.repo, args.issue),
        runs,
        now=now,
        stale_after_minutes=args.stale_after_minutes,
        retry_cooldown_minutes=args.retry_cooldown_minutes,
        current_run_id=args.current_run_id,
    )
    print(json.dumps(result, sort_keys=True))
    if args.github_output:
        _write_github_output(args.github_output, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
