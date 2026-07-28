from hello_world import mlb_historical_optimizer_v7_recovery_entrypoint as entrypoint


def _raise(message):
    raise AssertionError(message)


def _block_mutators(monkeypatch):
    monkeypatch.setattr(
        entrypoint.rematerialization,
        "run_once",
        lambda: _raise("status attempted feature rematerialization"),
    )
    monkeypatch.setattr(
        entrypoint.base,
        "_repair_precompetitive_extension_state",
        lambda: _raise("status attempted competitive range repair"),
    )
    monkeypatch.setattr(
        entrypoint.base,
        "_append_authorized_range_extension",
        lambda: _raise("status attempted range extension"),
    )


def test_status_bypasses_rematerialization_and_base_mutators(monkeypatch):
    _block_mutators(monkeypatch)

    observed = []

    def status_handler(event, context):
        observed.append((event, context))
        return {
            "ok": True,
            "status": "BACKFILLING",
            "state": {"phase": "BACKFILLING", "revision": 1001},
        }

    monkeypatch.setattr(entrypoint.base.optimizer_handler, "lambda_handler", status_handler)

    event = {"mode": "status", "run": "unit_status"}
    value = entrypoint.lambda_handler(event, None)

    assert observed == [(event, None)]
    assert value["ok"] is True
    assert value["state"]["revision"] == 1001
    assert value["oddsMarketExpansion"]["authority"] == "SHADOW_ONLY"
    assert value["supervisedShadow"]["rangeExtensionRunsBeforeRematerialization"] is True
    assert value["version"] == entrypoint.VERSION


def test_http_get_is_resolved_as_read_only_status(monkeypatch):
    _block_mutators(monkeypatch)
    monkeypatch.setattr(
        entrypoint.base.optimizer_handler,
        "lambda_handler",
        lambda event, context: {"ok": True, "state": {"phase": "BACKFILLING"}},
    )

    value = entrypoint.lambda_handler(
        {"requestContext": {"http": {"method": "GET"}}},
        None,
    )

    assert value["ok"] is True
    assert value["state"]["phase"] == "BACKFILLING"


def test_orchestration_extends_before_rematerialization_and_optimizer(monkeypatch):
    calls = []
    monkeypatch.setattr(
        entrypoint.base,
        "_repair_precompetitive_extension_state",
        lambda: calls.append("repair"),
    )
    monkeypatch.setattr(
        entrypoint.base,
        "_append_authorized_range_extension",
        lambda: calls.append("extend"),
    )
    monkeypatch.setattr(
        entrypoint.rematerialization,
        "run_once",
        lambda: calls.append("rematerialize") or None,
    )

    def optimizer_handler(event, context):
        calls.append("optimizer")
        return {"ok": True, "status": "BACKFILLING", "state": {"phase": "BACKFILLING"}}

    monkeypatch.setattr(entrypoint.base.optimizer_handler, "lambda_handler", optimizer_handler)

    event = {"mode": "orchestrate", "run": "unit_orchestrate"}
    value = entrypoint.lambda_handler(event, None)

    assert calls == ["repair", "extend", "rematerialize", "optimizer"]
    assert value["ok"] is True
    assert value["oddsMarketExpansion"]["productionV7Unchanged"] is True
    assert value["supervisedShadow"]["rangeExtensionRunsBeforeRematerialization"] is True


def test_pending_rematerialization_short_circuits_after_range_extension(monkeypatch):
    calls = []
    migration = {
        "ok": True,
        "status": "REMATERIALIZING_FEATURES",
        "state": {"phase": "REMATERIALIZING_FEATURES"},
    }
    monkeypatch.setattr(
        entrypoint.base,
        "_repair_precompetitive_extension_state",
        lambda: calls.append("repair"),
    )
    monkeypatch.setattr(
        entrypoint.base,
        "_append_authorized_range_extension",
        lambda: calls.append("extend"),
    )
    monkeypatch.setattr(
        entrypoint.rematerialization,
        "run_once",
        lambda: calls.append("rematerialize") or migration,
    )
    monkeypatch.setattr(
        entrypoint.base.optimizer_handler,
        "lambda_handler",
        lambda event, context: _raise("optimizer ran before rematerialization completed"),
    )

    value = entrypoint.lambda_handler({"mode": "orchestrate"}, None)

    assert calls == ["repair", "extend", "rematerialize"]
    assert value["status"] == "REMATERIALIZING_FEATURES"
    assert value["oddsMarketExpansion"]["authority"] == "SHADOW_ONLY"
