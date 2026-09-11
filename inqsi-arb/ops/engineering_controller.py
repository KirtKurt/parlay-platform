from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

CONTROLLER_VERSION = "INQSI-ARB-AEC-v2"
USER_AGENT = "inqsi-arb-autonomous-engineering-controller/2.0"
ALLOWED_PATH_PREFIXES = (
    "inqsi-arb/",
    ".github/workflows/inqsi-arb-",
)
DENIED_PATH_PREFIXES = (
    "mlb",
    "tennis",
    "soccer",
    "ks1",
)
KNOWN_WORKFLOWS = {
    "deploy": "inqsi-arb-deploy.yml",
    "repair": "inqsi-arb-repair.yml",
    "latency": "inqsi-arb-latency-proof.yml",
    "controller": "inqsi-arb-controller.yml",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_json(url: str, *, token: str = "", method: str = "GET", payload: Any = None, timeout: int = 30) -> Tuple[int, Any]:
    headers = {"accept": "application/vnd.github+json", "user-agent": USER_AGENT}
    if token:
        headers["authorization"] = f"Bearer {token}"
        headers["x-github-api-version"] = "2022-11-28"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    if body is not None:
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return int(response.status), json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw or b"{}")
        except Exception:
            parsed = {"message": raw.decode("utf-8", "replace")[:1000]}
        return int(exc.code), parsed


def _service_json(url: str, timeout: int = 30) -> Tuple[int, Any]:
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return int(response.status), json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return int(exc.code), json.loads(raw or b"{}")
        except Exception:
            return int(exc.code), {"message": raw.decode("utf-8", "replace")[:1000]}


def path_allowed(path: str) -> bool:
    normalized = path.strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or ".." in pathlib.PurePosixPath(normalized).parts:
        return False
    lowered = normalized.lower()
    if any(lowered == p or lowered.startswith(p + "/") for p in DENIED_PATH_PREFIXES):
        return False
    return any(normalized.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES)


def changed_paths_are_safe(paths: Iterable[str]) -> bool:
    rows = list(paths)
    return bool(rows) and all(path_allowed(path) for path in rows)


@dataclass(frozen=True)
class Action:
    kind: str
    reason: str
    target: str = ""
    destructive: bool = False


@dataclass
class Snapshot:
    checked_at: str
    main_sha: str
    production_ok: bool
    production_failures: List[str]
    active_sports: Optional[int]
    sportsbook_count: Optional[int]
    deploy_conclusion: Optional[str]
    latency_conclusion: Optional[str]
    latency_p95_ms: Optional[float]
    open_arb_prs: int
    code_agent_configured: bool
    paused: bool


class GitHubClient:
    def __init__(self, repository: str, token: str):
        self.repository = repository
        self.token = token
        self.api = f"https://api.github.com/repos/{repository}"

    def get(self, path: str) -> Any:
        status, body = _request_json(self.api + path, token=self.token)
        if status != 200:
            raise RuntimeError(f"github GET {path} failed with HTTP {status}")
        return body

    def post(self, path: str, payload: Any) -> Any:
        status, body = _request_json(self.api + path, token=self.token, method="POST", payload=payload)
        if status not in (200, 201, 202, 204):
            message = body.get("message") if isinstance(body, dict) else status
            raise RuntimeError(f"github POST {path} failed with HTTP {status}: {message}")
        return body

    def main_sha(self) -> str:
        return str(self.get("/git/ref/heads/main")["object"]["sha"])

    def workflow_runs(self, workflow_file: str, *, branch: str = "main", per_page: int = 8) -> List[Dict[str, Any]]:
        body = self.get(f"/actions/workflows/{workflow_file}/runs?branch={branch}&per_page={per_page}")
        return list(body.get("workflow_runs", []))

    def dispatch(self, workflow_file: str, *, ref: str = "main", inputs: Optional[Dict[str, str]] = None) -> None:
        self.post(f"/actions/workflows/{workflow_file}/dispatches", {"ref": ref, "inputs": inputs or {}})

    def open_arb_prs(self) -> List[Dict[str, Any]]:
        body = self.get("/pulls?state=open&per_page=50")
        return [row for row in body if str(row.get("head", {}).get("ref", "")).startswith("agent/inqsi-arb-")]

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        return self.post("/issues", {"title": title, "body": body, "labels": labels or []})


class EngineeringController:
    def __init__(self, gh: GitHubClient, *, api_url: str, sportsbook_url: str = "", latency_artifact_url: str = ""):
        self.gh = gh
        self.api_url = api_url.rstrip("/")
        self.sportsbook_url = sportsbook_url
        self.latency_artifact_url = latency_artifact_url
        self.max_actions = max(1, min(int(os.getenv("ARB_AEC_MAX_ACTIONS", "1")), 3))
        self.allow_dispatch = os.getenv("ARB_AEC_ALLOW_DISPATCH", "true").lower() == "true"
        self.allow_issue = os.getenv("ARB_AEC_ALLOW_ISSUE", "true").lower() == "true"
        self.paused = os.getenv("ARB_AEC_PAUSED", "false").lower() == "true"
        self.code_agent_configured = bool(
            os.getenv("OPENAI_API_KEY", "").strip() and os.getenv("ARB_CODE_AGENT_MODEL", "").strip()
        )

    def _production(self) -> Tuple[bool, List[str], Optional[int], Optional[int]]:
        failures: List[str] = []
        active_sports: Optional[int] = None
        sportsbook_count: Optional[int] = None
        for name, path in (
            ("health", "/v1/arb/health"),
            ("catalog", "/v1/arb/catalog?all=true"),
            ("rules", "/v1/arb/rules"),
        ):
            try:
                status, body = _service_json(self.api_url + path)
                if status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
                    failures.append(name)
                if name == "catalog" and isinstance(body, dict):
                    value = body.get("n_sports")
                    active_sports = int(value) if isinstance(value, int) else active_sports
            except Exception:
                failures.append(name)
        if self.sportsbook_url:
            try:
                status, body = _service_json(self.sportsbook_url, timeout=300)
                if status != 200 or not isinstance(body, dict) or body.get("complete") is not True or body.get("sports_failed") != 0:
                    failures.append("sportsbooks")
                if isinstance(body, dict) and isinstance(body.get("sportsbook_count"), int):
                    sportsbook_count = int(body["sportsbook_count"])
            except Exception:
                failures.append("sportsbooks")
        return not failures, sorted(set(failures)), active_sports, sportsbook_count

    @staticmethod
    def _latest_completed(runs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for run in runs:
            if run.get("status") == "completed":
                return run
        return None

    def snapshot(self) -> Snapshot:
        main_sha = self.gh.main_sha()
        production_ok, failures, active_sports, sportsbook_count = self._production()
        deploy = self._latest_completed(self.gh.workflow_runs(KNOWN_WORKFLOWS["deploy"]))
        latency = self._latest_completed(self.gh.workflow_runs(KNOWN_WORKFLOWS["latency"]))
        p95 = None
        if self.latency_artifact_url:
            try:
                status, body = _service_json(self.latency_artifact_url)
                if status == 200 and isinstance(body, dict):
                    latency_obj = body.get("latency_ms", {})
                    raw = latency_obj.get("p95") if isinstance(latency_obj, dict) else None
                    if isinstance(raw, (int, float)):
                        p95 = float(raw)
            except Exception:
                pass
        return Snapshot(
            checked_at=utcnow(),
            main_sha=main_sha,
            production_ok=production_ok,
            production_failures=failures,
            active_sports=active_sports,
            sportsbook_count=sportsbook_count,
            deploy_conclusion=None if not deploy else deploy.get("conclusion"),
            latency_conclusion=None if not latency else latency.get("conclusion"),
            latency_p95_ms=p95,
            open_arb_prs=len(self.gh.open_arb_prs()),
            code_agent_configured=self.code_agent_configured,
            paused=self.paused,
        )

    def choose_actions(self, snapshot: Snapshot) -> List[Action]:
        if snapshot.paused:
            return [Action("noop", "operator_pause_enabled")]
        if not snapshot.production_ok:
            return [Action("dispatch", "production_health_failed:" + ",".join(snapshot.production_failures), KNOWN_WORKFLOWS["repair"])]
        if snapshot.deploy_conclusion == "failure":
            return [Action("dispatch", "latest_main_deploy_failed", KNOWN_WORKFLOWS["repair"])]
        if snapshot.latency_conclusion == "failure":
            return [Action("issue", "latency_acceptance_gate_failed", "latency")]
        if snapshot.latency_p95_ms is not None and snapshot.latency_p95_ms > 500:
            return [Action("issue", f"latency_p95_above_gate:{snapshot.latency_p95_ms:.1f}ms", "latency")]
        if not snapshot.code_agent_configured:
            return [Action("issue", "code_agent_not_configured; bounded operations remain active", "code-agent")]
        return [Action("code-agent", "advance_highest_priority_ARB_BACKLOG_item", "inqsi-arb/ARB_BACKLOG.md")]

    def _issue_title(self, action: Action) -> str:
        if action.target == "latency":
            return "[ARB AEC] Production latency acceptance gate needs engineering repair"
        if action.target == "code-agent":
            return "[ARB AEC] Code-agent credential/model not configured"
        return "[ARB AEC] Autonomous engineering intervention required"

    def _find_existing_issue(self, title: str) -> Optional[Dict[str, Any]]:
        rows = self.gh.get("/issues?state=open&per_page=100")
        for row in rows:
            if not row.get("pull_request") and row.get("title") == title:
                return row
        return None

    def execute(self, actions: List[Action], snapshot: Snapshot) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for action in actions[: self.max_actions]:
            if action.kind == "noop":
                results.append({"action": asdict(action), "status": "skipped"})
            elif action.kind == "dispatch":
                if not self.allow_dispatch:
                    results.append({"action": asdict(action), "status": "blocked", "reason": "dispatch_disabled"})
                else:
                    self.gh.dispatch(action.target, ref="main")
                    results.append({"action": asdict(action), "status": "dispatched"})
            elif action.kind == "issue":
                if not self.allow_issue:
                    results.append({"action": asdict(action), "status": "blocked", "reason": "issue_creation_disabled"})
                    continue
                title = self._issue_title(action)
                existing = self._find_existing_issue(title)
                if existing:
                    results.append({"action": asdict(action), "status": "already_tracked", "issue": existing.get("number")})
                    continue
                body = (
                    f"Autonomous controller evidence at `{snapshot.checked_at}`.\n\n"
                    f"Reason: `{action.reason}`\n\n"
                    f"Main: `{snapshot.main_sha}`\n"
                    f"Production healthy: `{snapshot.production_ok}`\n"
                    f"Active sports: `{snapshot.active_sports}`\n"
                    f"Sportsbooks observed: `{snapshot.sportsbook_count}`\n\n"
                    "Created by the bounded Inqsi ARB autonomous engineering controller. "
                    "Do not weaken fail-closed settlement, freshness, identity, security, or no-wager-placement protections."
                )
                issue = self.gh.create_issue(title, body)
                results.append({"action": asdict(action), "status": "created", "issue": issue.get("number")})
            elif action.kind == "code-agent":
                results.append(self._run_code_agent(action, snapshot))
            else:
                results.append({"action": asdict(action), "status": "blocked", "reason": "unknown_action"})
        return results

    def _run_code_agent(self, action: Action, snapshot: Snapshot) -> Dict[str, Any]:
        command = os.getenv("ARB_CODE_AGENT_COMMAND", "").strip()
        if not self.code_agent_configured or not command:
            return {"action": asdict(action), "status": "blocked", "reason": "authorized_code_agent_runner_not_configured"}
        env = os.environ.copy()
        env["ARB_AEC_MAIN_SHA"] = snapshot.main_sha
        env["ARB_AEC_ALLOWED_PATHS"] = ",".join(ALLOWED_PATH_PREFIXES)
        env["ARB_AEC_TASK"] = action.reason
        completed = subprocess.run(command, shell=True, env=env, timeout=5400, check=False)
        return {
            "action": asdict(action),
            "status": "completed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", "KirtKurt/parlay-platform"))
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--sportsbook-url", default="")
    parser.add_argument("--latency-artifact-url", default="")
    parser.add_argument("--output", default="arb-engineering-controller-report.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")
    gh = GitHubClient(args.repository, token)
    controller = EngineeringController(
        gh,
        api_url=args.api_url,
        sportsbook_url=args.sportsbook_url,
        latency_artifact_url=args.latency_artifact_url,
    )
    snapshot = controller.snapshot()
    actions = controller.choose_actions(snapshot)
    results = [] if args.dry_run else controller.execute(actions, snapshot)
    report = {
        "controller": CONTROLLER_VERSION,
        "snapshot": asdict(snapshot),
        "actions": [asdict(action) for action in actions],
        "results": results,
        "guardrails": {
            "places_bets": False,
            "max_actions_per_run": controller.max_actions,
            "allowed_path_prefixes": list(ALLOWED_PATH_PREFIXES),
            "unrelated_prediction_systems_mutable": False,
            "arbitrary_code_authoring_requires_external_authorized_runner": True,
        },
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({
        "controller": CONTROLLER_VERSION,
        "checked_at": snapshot.checked_at,
        "main_sha": snapshot.main_sha,
        "production_ok": snapshot.production_ok,
        "production_failures": snapshot.production_failures,
        "deploy_conclusion": snapshot.deploy_conclusion,
        "latency_conclusion": snapshot.latency_conclusion,
        "code_agent_configured": snapshot.code_agent_configured,
        "paused": snapshot.paused,
        "planned_actions": [asdict(action) for action in actions],
        "results": results,
    }, indent=2, sort_keys=True))
    return 2 if any(row.get("status") == "failed" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
