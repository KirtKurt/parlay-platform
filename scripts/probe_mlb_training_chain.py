from __future__ import annotations

import json
import os
import urllib.request

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
API = f"https://api.github.com/repos/{REPO}"
NAMES = [
    "MLB V15.11.1 Accelerated Historical Training",
    "Dispatch Authorized MLB Historical Training",
    "MLB Historical Recovery and Paid Dispatch",
    "MLB Historical Daily Optimizer",
    "MLB Historical Accelerated Training Driver",
    "Observe MLB V15.11.1 Historical Training Chain",
]


def get(path: str) -> dict:
    request = urllib.request.Request(
        API + path,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mlb-v15.11-chain-probe",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


runs = get("/actions/runs?per_page=100").get("workflow_runs", [])
selected = []
for name in NAMES:
    matches = [
        row
        for row in runs
        if row.get("name") == name
        and str(row.get("created_at") or "") >= "2026-07-24T06:45:00Z"
    ]
    if matches:
        selected.append(
            max(matches, key=lambda row: (row.get("created_at") or "", int(row.get("id") or 0)))
        )

print("MLB_CHAIN_STATUS_BEGIN")
for row in selected:
    run_id = int(row["id"])
    print(
        json.dumps(
            {
                "type": "run",
                "id": run_id,
                "name": row.get("name"),
                "event": row.get("event"),
                "status": row.get("status"),
                "conclusion": row.get("conclusion"),
                "head_sha": row.get("head_sha"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "url": row.get("html_url"),
            },
            sort_keys=True,
        )
    )
    for job in get(f"/actions/runs/{run_id}/jobs?per_page=100").get("jobs", []):
        steps = job.get("steps") or []
        print(
            json.dumps(
                {
                    "type": "job",
                    "run_id": run_id,
                    "job_id": job.get("id"),
                    "job": job.get("name"),
                    "status": job.get("status"),
                    "conclusion": job.get("conclusion"),
                    "active_steps": [
                        step.get("name") for step in steps if step.get("status") == "in_progress"
                    ],
                    "failed_steps": [
                        step.get("name") for step in steps if step.get("conclusion") == "failure"
                    ],
                },
                sort_keys=True,
            )
        )
print("MLB_CHAIN_STATUS_END")
if not selected:
    raise SystemExit("No relevant MLB historical training Actions runs found")
