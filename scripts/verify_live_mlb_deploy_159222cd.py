#!/usr/bin/env python3
"""Read-only verifier for the exact MLB deployment produced by main SHA 159222cd."""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

TARGET_SHA = "159222cd145ae0ba727970775abda22f956fb97c"
TARGET_WORKFLOW = "Deploy SAM to AWS"
API_BASE = "https://7nz8g8unyd.execute-api.us-east-1.amazonaws.com/Prod"
GITHUB_API = "https://api.github.com"


def emit(name: str, value: object) -> None:
    print(f"{name} {json.dumps(value, sort_keys=True, default=str)}", flush=True)


def github_json(repository: str, token: str, path: str) -> dict:
    request = urllib.request.Request(
        GITHUB_API + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "parlay-platform-read-only-deploy-verifier/1.1",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"GitHub API returned non-object for {path}")
    return payload


def locate_deployment(repository: str, token: str) -> dict:
    query = urllib.parse.urlencode(
        {
            "head_sha": TARGET_SHA,
            "event": "push",
            "branch": "main",
            "per_page": 100,
        }
    )
    deadline = time.monotonic() + 30 * 60
    while time.monotonic() < deadline:
        payload = github_json(
            repository,
            token,
            f"/repos/{repository}/actions/runs?{query}",
        )
        matches = [
            run
            for run in payload.get("workflow_runs") or []
            if isinstance(run, dict)
            and run.get("head_sha") == TARGET_SHA
            and run.get("head_branch") == "main"
            and run.get("event") == "push"
            and run.get("name") == TARGET_WORKFLOW
        ]
        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple exact deployments found: {[run.get('id') for run in matches]}"
            )
        if matches:
            run = matches[0]
            emit(
                "DEPLOYMENT_RUN_POLL",
                {
                    "id": run.get("id"),
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "headSha": run.get("head_sha"),
                    "attempt": run.get("run_attempt"),
                },
            )
            if run.get("status") == "completed":
                return run
        else:
            print("DEPLOYMENT_RUN_POLL not_found_yet", flush=True)
        time.sleep(15)
    raise RuntimeError("Exact deployment did not complete within verifier deadline")


def summarize_jobs(repository: str, token: str, run_id: int) -> list[dict]:
    payload = github_json(
        repository,
        token,
        f"/repos/{repository}/actions/runs/{run_id}/jobs?filter=latest&per_page=100",
    )
    jobs = [job for job in payload.get("jobs") or [] if isinstance(job, dict)]
    return [
        {
            "id": job.get("id"),
            "name": job.get("name"),
            "status": job.get("status"),
            "conclusion": job.get("conclusion"),
            "startedAt": job.get("started_at"),
            "completedAt": job.get("completed_at"),
            "failedSteps": [
                step.get("name")
                for step in job.get("steps") or []
                if isinstance(step, dict)
                and step.get("conclusion") not in ("success", "skipped", None)
            ],
        }
        for job in jobs
    ]


def verify_identity(
    repository: str,
    token: str,
    run_id: int,
    run_attempt: int,
) -> dict:
    payload = github_json(
        repository,
        token,
        f"/repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100",
    )
    expected_name = f"mlb-deployment-identity-{run_id}"
    artifacts = [
        artifact
        for artifact in payload.get("artifacts") or []
        if isinstance(artifact, dict)
        and artifact.get("name") == expected_name
        and artifact.get("expired") is False
    ]
    if len(artifacts) != 1:
        raise RuntimeError(f"Expected one {expected_name} artifact; found {len(artifacts)}")

    request = urllib.request.Request(
        artifacts[0]["archive_download_url"],
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "parlay-platform-read-only-deploy-verifier/1.1",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        archive = response.read()
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = sorted(bundle.namelist())
        identity_names = [
            name for name in names if name.endswith("mlb_deploy_identity_latest.json")
        ]
        if len(identity_names) != 1:
            raise RuntimeError(
                f"Deployment artifact identity file count is {len(identity_names)}"
            )
        identity = json.loads(bundle.read(identity_names[0]).decode("utf-8"))

    if not isinstance(identity, dict) or identity.get("ok") is not True:
        raise RuntimeError("Deployment identity proof is not healthy")
    expected_run = f"{run_id}-{run_attempt}"
    if identity.get("expectedGitSha") != TARGET_SHA:
        raise RuntimeError(f"Proof SHA is {identity.get('expectedGitSha')}")
    if identity.get("expectedDeployRunId") != expected_run:
        raise RuntimeError(f"Proof run is {identity.get('expectedDeployRunId')}")
    functions = identity.get("functions") or {}
    if not isinstance(functions, dict) or not functions:
        raise RuntimeError("Deployment identity proof has no functions")

    bad: dict[str, list[str]] = {}
    for name, evidence in functions.items():
        errors: list[str] = []
        if not isinstance(evidence, dict):
            errors.append("non_object")
        else:
            if evidence.get("deployGitSha") != TARGET_SHA:
                errors.append("deploy_sha")
            if evidence.get("deployRunId") != expected_run:
                errors.append("deploy_run")
            if evidence.get("identityMatches") is not True:
                errors.append("identity")
            if evidence.get("configurationMatches") is not True:
                errors.append("configuration")
            if evidence.get("codeArtifactMatchesCleanBuild") is not True:
                errors.append("artifact")
        if errors:
            bad[name] = errors
    if bad:
        raise RuntimeError("Deployment identity mismatch: " + json.dumps(bad, sort_keys=True))

    return {
        "ok": True,
        "proofType": identity.get("proofType"),
        "expectedGitSha": identity.get("expectedGitSha"),
        "expectedDeployRunId": identity.get("expectedDeployRunId"),
        "templateSha256": identity.get("expectedTemplateSha256"),
        "functionCount": len(functions),
        "allFunctionArtifactsMatch": True,
        "artifactId": artifacts[0].get("id"),
        "artifactDigest": artifacts[0].get("digest"),
        "files": names,
    }


def fetch_public_json(path: str) -> tuple[dict, float, int]:
    request = urllib.request.Request(
        API_BASE + path,
        headers={
            "Accept": "application/json",
            "User-Agent": "parlay-platform-read-only-deploy-verifier/1.1",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = int(response.getcode())
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"Public read {path} returned HTTP {exc.code}: {body}") from exc
    elapsed = time.monotonic() - started
    payload = json.loads(raw.decode("utf-8"))
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(
            f"Public read {path} returned status={status} type={type(payload).__name__}"
        )
    return payload, elapsed, status


def verify_live_reads() -> dict:
    summary = {}
    for path, label in (
        ("/v1/health", "health"),
        ("/v1/mlb/locks/status", "lockStatus"),
        ("/v1/mlb/predictions", "predictions"),
    ):
        payload, elapsed, status = fetch_public_json(path)
        if payload.get("ok") is not True:
            raise RuntimeError(f"Public read {path} reports ok={payload.get('ok')}")
        if label != "health" and payload.get("sport") != "mlb":
            raise RuntimeError(f"Public read {path} reports sport={payload.get('sport')}")
        summary[label] = {
            "httpStatus": status,
            "elapsedSeconds": round(elapsed, 3),
            "ok": payload.get("ok"),
            "sport": payload.get("sport"),
            "slateDateEt": payload.get("slateDateEt"),
            "gameCount": payload.get("gameCount"),
            "lockedPredictionCount": payload.get("lockedPredictionCount"),
            "officialPredictionCount": payload.get("officialPredictionCount"),
            "lockedStatusCount": payload.get("lockedStatusCount"),
            "noPredictionDataCount": payload.get("noPredictionDataCount"),
            "operationalDefect": payload.get("operationalDefect"),
        }
    return summary


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    run = locate_deployment(repository, token)
    run_id = int(run["id"])
    run_attempt = int(run.get("run_attempt") or 1)
    emit(
        "DEPLOYMENT_RUN",
        {
            "id": run_id,
            "attempt": run_attempt,
            "name": run.get("name"),
            "event": run.get("event"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "headBranch": run.get("head_branch"),
            "headSha": run.get("head_sha"),
            "createdAt": run.get("created_at"),
            "updatedAt": run.get("updated_at"),
            "url": run.get("html_url"),
        },
    )
    jobs = summarize_jobs(repository, token, run_id)
    emit("DEPLOYMENT_JOBS", jobs)
    if run.get("conclusion") != "success":
        raise RuntimeError(f"Exact deployment concluded {run.get('conclusion')}")
    deploy_jobs = [job for job in jobs if job.get("name") == "deploy"]
    if len(deploy_jobs) != 1 or deploy_jobs[0].get("conclusion") != "success":
        raise RuntimeError("Exact deployment job is not uniquely successful")
    emit(
        "DEPLOYMENT_IDENTITY",
        verify_identity(repository, token, run_id, run_attempt),
    )
    emit("LIVE_PUBLIC_READS", verify_live_reads())
    print("VERIFICATION_OK", flush=True)


if __name__ == "__main__":
    main()
