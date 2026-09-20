"""Regressions for the shared API timeout and independent health row cap."""
from copy import deepcopy
import json

from soccer_auto import health
from soccer_auto.kss1_picks import recorded_picks
from tests.soccer_auto.test_kss1_picks import Store
from tests.soccer_auto.test_health_contract import Store as HealthStore


def project(row, kwargs):
    names = kwargs["ExpressionAttributeNames"]
    result = {}
    for path in kwargs["ProjectionExpression"].split(", "):
        parts = [names[part] for part in path.split(".")]
        value = row
        for part in parts:
            if not isinstance(value, dict) or part not in value:
                break
            value = value[part]
        else:
            target = result
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = deepcopy(value)
    return result


class ProjectedStore(Store):
    def __init__(self):
        super().__init__()
        self.row["goals_features"] = {"research_only": False, "history": "x" * 100_000}
        self.row["kss1"]["unused_diagnostics"] = "x" * 100_000
        self.query_rows = []
        self.queries = []

    def scan_all(self, table, **kwargs):
        assert kwargs["ConsistentRead"] is True
        return iter(project(row, kwargs) for row in table)

    def query(self, **kwargs):
        assert kwargs["ConsistentRead"] is True
        self.queries.append(kwargs)
        row = project(self.row, kwargs)
        self.query_rows.append(row)
        return {"Items": [row]}


def test_pick_projection_keeps_identical_output_without_large_training_payloads():
    expected = recorded_picks(Store(), "2026-09-14", selection="12")
    store = ProjectedStore()
    assert recorded_picks(store, "2026-09-14", selection="12") == expected
    assert len(json.dumps(store.query_rows)) < 2000
    assert store.query_rows[0]["goals_features"] == {"research_only": False}


def test_projected_research_rows_still_fail_closed():
    store = ProjectedStore()
    store.row["goals_features"]["research_only"] = True
    assert recorded_picks(store, "2026-09-14")["count"] == 0


def test_projected_readback_follows_all_pages_and_retains_latest_valid_record():
    class PagedStore(ProjectedStore):
        def query(self, **kwargs):
            response = super().query(**kwargs)
            if "ExclusiveStartKey" not in kwargs:
                response["Items"][0]["created_at"] = "2026-09-14T18:00:00Z"
                response["Items"][0]["kss1"]["markets"]["p_12"] = .51
                response["LastEvaluatedKey"] = {"PK": "game", "SK": "page2"}
            return response

    store = PagedStore()
    result = recorded_picks(store, "2026-09-14", selection="12")
    assert result["picks"][0]["markets"]["p_12"] == .78
    assert len(store.queries) == 2
    assert store.queries[1]["ExclusiveStartKey"] == {"PK": "game", "SK": "page2"}


def matches(condition, row):
    expression = condition.get_expression()
    op, values = expression["operator"], expression["values"]
    if op == "AND":
        return all(matches(value, row) for value in values)
    value = row.get(values[0].name)
    if op == "=":
        return value == values[1]
    if op == "IN":
        return value in values[1]
    raise AssertionError(op)


class PredictionTable:
    def __init__(self, tail):
        self.rows = [{"model_authority": "SHADOW", "immutable": True, "prediction_status": "SHADOW"}] * 10_001 + tail
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        start = kwargs.get("ExclusiveStartKey", {}).get("index", 0)
        end = min(len(self.rows), start + min(100, kwargs["Limit"]))
        rows = self.rows[start:end]
        if "FilterExpression" in kwargs:
            rows = [row for row in rows if matches(kwargs["FilterExpression"], row)]
        result = {"Items": rows, "ScannedCount": end - start}
        if end < len(self.rows):
            result["LastEvaluatedKey"] = {"index": end}
        return result


def test_shadow_history_does_not_exhaust_public_authority_scan_budget():
    store = HealthStore(events=[], locks=[])
    store.predictions = PredictionTable([])
    result = health.prediction_and_training_health(store)
    assert result["proof_complete"] is True
    assert result["scan_truncated"] is False
    assert len(store.predictions.calls) > 100
    assert result["healthy"] is True


def test_corrupt_public_prediction_after_10000_shadows_still_blocks_health():
    store = HealthStore(events=[], locks=[])
    store.predictions = PredictionTable([{
        "model_authority": "CHAMPION", "prediction_status": "PUBLISHED",
        "immutable": True, "horizon": "T10", "event_key": "corrupt",
        # Missing required binding and schedule evidence must never pass.
    }])
    result = health.prediction_and_training_health(store)
    assert result["proof_complete"] is True
    assert result["integrity_failures"] == 1
    assert result["healthy"] is False


def test_filtered_page_budget_exhaustion_still_blocks_health(monkeypatch):
    store = HealthStore(events=[], locks=[])
    store.predictions = PredictionTable([])
    monkeypatch.setattr(health, "HEALTH_SCAN_MAX_PAGES", 2)
    result = health.prediction_and_training_health(store)
    assert result["proof_complete"] is False
    assert result["healthy"] is False
    assert result["truncated_tables"] == ["predictions"]


def test_relevant_row_limit_still_reports_incomplete():
    rows, truncated = health._scan(PredictionTable([]), limit=2)
    assert len(rows) == 2
    assert truncated is True
