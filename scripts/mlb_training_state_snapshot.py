from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any


WORKFLOWS = [
    "MLB V15.11.1 Accelerated Historical Training",
    "MLB Historical Recovery and Paid Dispatch",
    "MLB Historical Daily Optimizer",
    "MLB Historical Accelerated Training Driver",
    "Dispatch Authorized MLB Historical Training",
    "Observe MLB V15.11.1 Historical Training Chain",
]


def get_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mlb-training-state-snapshot/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_run(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    matches = [row for row in rows if row.get("name") == name]
    matches.sort(key=lambda row: str(row.get("created_at") or ""))
    return matches[-1] if matches else None


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repository or not token:
        raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required")

    root = f"https://api.github.com/repos/{repository}"
    runs_payload = get_json(f"{root}/actions/runs?per_page=100", token)
    runs = list(runs_payload.get("workflow_runs") or [])
    output: list[dict[str, Any]] = []

    for name in WORKFLOWS:
        run = latest_run(runs, name)
        item: dict[str, Any] = {
            "name": name,
            "located": run is not None,
            "run_id": run.get("id") if run else None,
            "status": run.get("status") if run else None,
            "conclusion": run.get("conclusion") if run else None,
            "event": run.get("event") if run else None,
            "head_sha": run.get("head_sha") if run else None,
            "created_at": run.get("created_at") if run else None,
            "updated_at": run.get("updated_at") if run else None,
            "url": run.get("html_url") if run else None,
            "jobs": [],
        }
        if run and run.get("id"):
            jobs_payload = get_json(f"{root}/actions/runs/{run['id']}/jobs?per_page=100", token)
            for job in jobs_payload.get("jobs") or []:
                active_or_failed = next(
                    (
                        step.get("name")
                        for step in job.get("steps") or []
                        if step.get("status") == "in_progress"
                        or step.get("conclusion") == "failure"
                    ),
                    None,
                )
                item["jobs"].append(
                    {
                        "job_id": job.get("id"),
                        "name": job.get("name"),
                        "status": job.get("status"),
                        "conclusion": job.get("conclusion"),
                        "active_or_failed_step": active_or_failed,
                    }
                )
        output.append(item)

    print("MLB_TRAINING_CHAIN_SNAPSHOT=" + json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
