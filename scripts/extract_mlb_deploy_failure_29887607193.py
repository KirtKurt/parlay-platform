#!/usr/bin/env python3
"""Download and compact the exact failed deployment job log for review."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

RUN_ID = 29887607193
JOB_ID = 88821292056
KEYWORDS = re.compile(
    r"(?i)(smoke test read-only mlb lock status|traceback|timeout|timed out|"
    r"transienthttpprobeexhausted|http 5\d\d|504|error|exception|process completed|"
    r"status_url|locks/status|deployment_run|batchget|batch_get)"
)


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/actions/jobs/{JOB_ID}/logs",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "parlay-platform-read-only-deploy-log-extractor/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read()
    text = raw.decode("utf-8-sig", "replace")
    lines = text.splitlines()

    selected: set[int] = set(range(max(0, len(lines) - 500), len(lines)))
    for index, line in enumerate(lines):
        if KEYWORDS.search(line):
            selected.update(range(max(0, index - 8), min(len(lines), index + 13)))
    ordered = sorted(selected)
    excerpt = [lines[index] for index in ordered]
    Path("mlb_deploy_failure_excerpt.txt").write_text(
        "\n".join(excerpt) + "\n",
        encoding="utf-8",
    )
    summary = {
        "runId": RUN_ID,
        "jobId": JOB_ID,
        "lineCount": len(lines),
        "excerptLineCount": len(excerpt),
        "keywordHits": [
            {"line": index + 1, "text": lines[index][-1000:]}
            for index in range(len(lines))
            if KEYWORDS.search(lines[index])
        ][-100:],
    }
    Path("mlb_deploy_failure_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
