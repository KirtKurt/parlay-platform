import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "ops" / "engineering_controller.py"
spec = importlib.util.spec_from_file_location("engineering_controller", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
sys.modules["engineering_controller"] = mod
spec.loader.exec_module(mod)


class FakeGH:
    def __init__(self, deploy="success", latency="success"):
        self.deploy = deploy
        self.latency = latency
        self.dispatched = []
        self.issues = []

    def main_sha(self):
        return "abc123"

    def workflow_runs(self, workflow_file, branch="main", per_page=8):
        conclusion = self.latency if "latency" in workflow_file else self.deploy
        return [{"status": "completed", "conclusion": conclusion, "id": 10}]

    def open_arb_prs(self):
        return []

    def dispatch(self, workflow_file, ref="main", inputs=None):
        self.dispatched.append((workflow_file, ref))

    def get(self, path):
        if path.startswith("/issues"):
            return []
        raise AssertionError(path)

    def create_issue(self, title, body, labels=None):
        row = {"number": len(self.issues) + 1, "title": title, "body": body}
        self.issues.append(row)
        return row


def make_snapshot(**overrides):
    values = dict(
        checked_at="2026-09-11T00:00:00+00:00",
        main_sha="abc123",
        production_ok=True,
        production_failures=[],
        active_sports=86,
        sportsbook_count=73,
        deploy_conclusion="success",
        latency_conclusion="success",
        latency_p95_ms=100.0,
        open_arb_prs=0,
        code_agent_configured=True,
        paused=False,
    )
    values.update(overrides)
    return mod.Snapshot(**values)


def controller(monkeypatch, gh=None):
    monkeypatch.setenv("ARB_AEC_MAX_ACTIONS", "1")
    monkeypatch.setenv("ARB_AEC_ALLOW_DISPATCH", "true")
    monkeypatch.setenv("ARB_AEC_ALLOW_ISSUE", "true")
    return mod.EngineeringController(gh or FakeGH(), api_url="https://example.test")


def test_path_allowlist_isolated_to_arb():
    assert mod.path_allowed("inqsi-arb/ops/controller.py")
    assert mod.path_allowed(".github/workflows/inqsi-arb-deploy.yml")
    assert not mod.path_allowed("mlb/app.py")
    assert not mod.path_allowed("tennis/foo.py")
    assert not mod.path_allowed(".github/workflows/mlb-prod.yml")
    assert not mod.path_allowed("../secret.txt")


def test_production_failure_has_highest_priority(monkeypatch):
    c = controller(monkeypatch)
    snap = make_snapshot(production_ok=False, production_failures=["health"], latency_conclusion="failure")
    actions = c.choose_actions(snap)
    assert len(actions) == 1
    assert actions[0].kind == "dispatch"
    assert actions[0].target == "inqsi-arb-repair.yml"


def test_latency_failure_never_lowers_gate(monkeypatch):
    c = controller(monkeypatch)
    actions = c.choose_actions(make_snapshot(latency_conclusion="failure", latency_p95_ms=1058.1))
    assert actions[0].kind == "issue"
    assert actions[0].target == "latency"
    assert "failed" in actions[0].reason


def test_missing_code_agent_becomes_explicit_blocker(monkeypatch):
    c = controller(monkeypatch)
    actions = c.choose_actions(make_snapshot(code_agent_configured=False))
    assert actions[0].kind == "issue"
    assert actions[0].target == "code-agent"


def test_operator_pause_prevents_mutation(monkeypatch):
    c = controller(monkeypatch)
    actions = c.choose_actions(make_snapshot(paused=True, production_ok=False, production_failures=["health"]))
    assert actions == [mod.Action("noop", "operator_pause_enabled")]


def test_one_bounded_repair_dispatch(monkeypatch):
    gh = FakeGH()
    c = controller(monkeypatch, gh)
    snap = make_snapshot(production_ok=False, production_failures=["health"])
    results = c.execute(c.choose_actions(snap), snap)
    assert gh.dispatched == [("inqsi-arb-repair.yml", "main")]
    assert results[0]["status"] == "dispatched"


def test_changed_paths_must_all_be_safe():
    assert mod.changed_paths_are_safe(["inqsi-arb/a.py", ".github/workflows/inqsi-arb-x.yml"])
    assert not mod.changed_paths_are_safe(["inqsi-arb/a.py", "soccer/a.py"])
    assert not mod.changed_paths_are_safe([])
