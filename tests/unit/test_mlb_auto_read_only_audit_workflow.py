from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "audit-mlb-auto-today-read-only.yml"


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _audit_program() -> str:
    document = yaml.load(_source(), Loader=yaml.BaseLoader)
    steps = document["jobs"]["audit"]["steps"]
    run = next(
        step["run"]
        for step in steps
        if step.get("name") == "Build and enforce read-only card audit"
    )
    return run.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def _no_champion_authority() -> tuple[dict, dict]:
    model = {
        "ok": False,
        "status": "NO_QUALIFIED_CHAMPION",
        "error": "NO_QUALIFIED_CHAMPION",
        "publicationClosed": True,
        "productionSelectionAllowed": False,
        "qualifiedChampionRequired": True,
        "qualifiedChampionPresent": False,
        "r7ChampionQualified": False,
        "primaryAlgorithmActive": False,
        "retiredAuthoritySuppressed": True,
        "retiredV15_10Eligible": False,
        "legacyFallbackAllowed": False,
        "automaticLegacyRestoreAllowed": False,
        "requestedAuthority": "AWS_ML_PROSPECTIVE_R7",
        "model_version": None,
        "primaryAlgorithm": None,
        "soleProductionAlgorithm": None,
        "game_winner_model": None,
        "r7DeploymentIdentity": None,
    }
    return model, {**model, "count": 0, "winner_predictions": [], "predictions": []}


def _qualified_authority() -> tuple[dict, dict]:
    model = {
        "ok": True,
        "publicationClosed": False,
        "productionSelectionAllowed": True,
        "qualifiedChampionRequired": True,
        "qualifiedChampionPresent": True,
        "r7ChampionQualified": True,
        "primaryAlgorithmActive": True,
        "requestedAuthority": "AWS_ML_PROSPECTIVE_R7",
        "model_version": "mlb-r7-qualified",
        "primaryAlgorithm": "AWS_ML_PROSPECTIVE_R7",
        "r7DeploymentIdentity": "sha256:qualified",
    }
    return model, {"ok": True, "count": 7, "winner_predictions": [{"gamePk": 1}]}


def _write_runtime_fixture(
    root: Path,
    today_status: dict,
    prior_status: dict,
    *,
    authority_model: dict | None = None,
    authority_today: dict | None = None,
    authority_model_http: int = 503,
    authority_today_http: int = 503,
) -> None:
    default_model, default_today = _no_champion_authority()
    authority_model = authority_model if authority_model is not None else default_model
    authority_today = authority_today if authority_today is not None else default_today
    root.mkdir()
    (root / "function.json").write_text(
        json.dumps(
            {
                "FunctionName": "mlb-auto-test",
                "Handler": "orchestrator_v3.lambda_handler",
                "State": "Active",
                "LastUpdateStatus": "Successful",
            }
        ),
        encoding="utf-8",
    )
    for name in ("today", "prior", "authority-model", "authority-today"):
        (root / f"{name}-invocation.json").write_text("{}", encoding="utf-8")
    for name, status in (("today", today_status), ("prior", prior_status)):
        (root / f"{name}-raw.json").write_text(
            json.dumps({"statusCode": 200, "body": json.dumps(status)}),
            encoding="utf-8",
        )
    for name, status, body in (
        ("authority-model", authority_model_http, authority_model),
        ("authority-today", authority_today_http, authority_today),
    ):
        (root / f"{name}-raw.json").write_text(
            json.dumps({"statusCode": status, "body": json.dumps(body)}),
            encoding="utf-8",
        )


def _status(
    *, scheduled: int, deadline: datetime | None, card: dict | None = None
) -> dict:
    deadline_state = (
        {"publishDeadlineUtc": deadline.isoformat()}
        if deadline is not None
        else {"publishDeadlineUtc": None, "reason": "NO_GAMES"}
    )
    return {
        "ok": True,
        "slateDateEt": "2026-08-28",
        "scheduleOk": True,
        "scheduledGames": scheduled,
        "deadline": deadline_state,
        "card": card,
        "audit": None,
    }


def _whole_card(game_count: int) -> dict:
    return {
        "gameCount": game_count,
        "picks": [
            {
                "gamePk": str(index + 1),
                "homeTeam": f"Home {index + 1}",
                "awayTeam": f"Away {index + 1}",
                "predictedWinner": f"Home {index + 1}",
                "predictedLoser": f"Away {index + 1}",
                "probability": 0.6,
                "decisionAuthority": "BEDROCK_LLM",
                "sourcePresence": {
                    "mlbStatsApi": True,
                    "theOddsApi": True,
                    "bigBallsDataPro": True,
                },
            }
            for index in range(game_count)
        ],
    }


def _run_audit_program(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    today_status: dict,
    prior_status: dict,
    *,
    authority_model: dict | None = None,
    authority_today: dict | None = None,
    authority_model_http: int = 503,
    authority_today_http: int = 503,
) -> dict:
    today_status = {**today_status, "slateDateEt": "2026-08-28"}
    prior_status = {**prior_status, "slateDateEt": "2026-08-27"}
    fixture_root = tmp_path / "audit"
    _write_runtime_fixture(
        fixture_root,
        today_status,
        prior_status,
        authority_model=authority_model,
        authority_today=authority_today,
        authority_model_http=authority_model_http,
        authority_today_http=authority_today_http,
    )
    monkeypatch.setenv("TODAY_ET", "2026-08-28")
    monkeypatch.setenv("PRIOR_ET", "2026-08-27")
    monkeypatch.setenv("MLB_AUTO_FINAL_COLLECTION_WINDOW_MINUTES", "20")
    program = _audit_program().replace(
        "root=Path('/tmp/mlb-auto-audit')",
        f"root=Path({str(fixture_root)!r})",
    )
    exec(compile(program, str(WORKFLOW), "exec"), {})
    return json.loads((fixture_root / "audit.json").read_text(encoding="utf-8"))


def test_audit_resolves_eastern_dates_and_reads_each_exact_slate() -> None:
    source = _source()

    assert "ZoneInfo('America/New_York')" in source
    assert "steps.dates.outputs.today_et" in source
    assert "steps.dates.outputs.prior_et" in source
    assert 'TODAY_ET: \'2026-' not in source
    assert 'PRIOR_ET: \'2026-' not in source
    assert "/v1/mlb-auto/today" not in source
    assert source.count('\\"rawPath\\":\\"/v1/mlb-auto/status\\"') == 2
    assert '\\"date\\":\\"$TODAY_ET\\"' in source
    assert '\\"date\\":\\"$PRIOR_ET\\"' in source


def test_audit_accepts_any_nonempty_slate_and_enforces_publication_deadline() -> None:
    source = _source()

    assert "today_scheduled==15" not in source
    assert "today_scheduled>0" not in source
    assert "and prior_scheduled>0" not in source
    assert "schedule_not_explicitly_healthy" in source
    assert "scheduled_games_invalid" in source
    assert "NO_GAMES" in source
    assert "card_present_on_no_games_slate" in source
    assert "if card is not None and len(picks)!=scheduled:" in source
    assert "card is None" in source
    assert "card_missing_after_deadline" in source
    assert "COLLECTING_NOT_DUE" in source
    assert "FINAL_WINDOW" in source
    assert "publication_deadline_unavailable" in source
    assert "observed_at>deadline_utc" in source
    assert "AUTHORITY_READINESS_GATED" in source
    assert "prior_slate_authority_as_of_proof_unavailable" in source
    assert "prior_authority_as_of_unavailable" in source


def test_audit_preserves_strict_authority_and_provider_contracts() -> None:
    source = _source()
    document = yaml.load(source, Loader=yaml.BaseLoader)

    assert isinstance(document, dict)
    assert "{'BEDROCK_LLM','AWS_ML_PROSPECTIVE_R7'}" in source
    assert "('mlbStatsApi','theOddsApi','bigBallsDataPro')" in source
    assert "card_not_whole_slate" in source
    assert "NO_QUALIFIED_CHAMPION_SUPPRESSED" in source
    assert "authority_state_not_explicitly_fail_closed_or_qualified" in source
    assert "legacyFallbackAllowed':False" in source
    assert "today_winner_prediction_count_not_zero" in source
    assert "humanWinnerSelection':False" in source
    assert "immutablePredictionHistoryRewritten':False" in source


def test_predeadline_unpublished_seven_game_slates_are_not_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    proof = _run_audit_program(
        tmp_path,
        monkeypatch,
        _status(scheduled=7, deadline=future),
        _status(scheduled=7, deadline=future),
    )

    assert proof["ok"] is True
    assert proof["today"]["capabilityState"] == "COLLECTING_NOT_DUE"
    assert proof["today"]["cardPublished"] is False


def test_exact_date_zero_game_slates_are_healthy_no_games(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proof = _run_audit_program(
        tmp_path,
        monkeypatch,
        _status(scheduled=0, deadline=None),
        _status(scheduled=0, deadline=None),
    )

    assert proof["ok"] is True
    assert proof["today"]["capabilityState"] == "NO_GAMES"
    assert proof["prior"]["capabilityState"] == "NO_GAMES"
    assert proof["today"]["errors"] == []
    assert proof["prior"]["errors"] == []


def test_card_on_zero_game_slate_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AssertionError):
        _run_audit_program(
            tmp_path,
            monkeypatch,
            _status(scheduled=0, deadline=None, card=_whole_card(0)),
            _status(scheduled=0, deadline=None),
        )

    proof = json.loads((tmp_path / "audit" / "audit.json").read_text(encoding="utf-8"))
    assert proof["ok"] is False
    assert "card_present_on_no_games_slate" in proof["today"]["errors"]


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("scheduleOk", False, "schedule_not_explicitly_healthy"),
        ("scheduledGames", "7", "scheduled_games_invalid:'7'"),
    ],
)
def test_unknown_or_malformed_schedule_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    expected_error: str,
) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    today = _status(scheduled=7, deadline=future)
    today[field] = value

    with pytest.raises(AssertionError):
        _run_audit_program(
            tmp_path,
            monkeypatch,
            today,
            _status(scheduled=7, deadline=future),
        )

    proof = json.loads((tmp_path / "audit" / "audit.json").read_text(encoding="utf-8"))
    assert proof["ok"] is False
    assert expected_error in proof["today"]["errors"]


def test_unpublished_card_fails_only_after_its_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    authority_model, authority_today = _qualified_authority()

    with pytest.raises(AssertionError):
        _run_audit_program(
            tmp_path,
            monkeypatch,
            _status(scheduled=7, deadline=past),
            _status(scheduled=7, deadline=future),
            authority_model=authority_model,
            authority_today=authority_today,
            authority_model_http=200,
            authority_today_http=200,
        )

    proof = json.loads((tmp_path / "audit" / "audit.json").read_text(encoding="utf-8"))
    assert proof["ok"] is False
    assert proof["today"]["capabilityState"] == "CARD_MISSING_AFTER_DEADLINE"
    assert proof["today"]["errors"][0].startswith("card_missing_after_deadline:")


def test_postdeadline_absence_is_suppressed_only_for_explicit_fail_closed_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=1)

    proof = _run_audit_program(
        tmp_path,
        monkeypatch,
        _status(scheduled=7, deadline=past),
        _status(scheduled=7, deadline=past, card=_whole_card(7)),
    )

    assert proof["ok"] is True
    assert proof["productionAuthority"]["valid"] is True
    assert (
        proof["productionAuthority"]["state"]
        == "NO_QUALIFIED_CHAMPION_SUPPRESSED"
    )
    assert proof["productionAuthority"]["winnerPredictionCount"] == 0
    assert proof["today"]["capabilityState"] == "AUTHORITY_READINESS_GATED"
    assert proof["today"]["errors"] == []
    assert proof["prior"]["authorityAsOfState"] == "AUTHORITY_AS_OF_UNAVAILABLE"


def test_current_no_champion_state_cannot_suppress_prior_slate_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    with pytest.raises(AssertionError):
        _run_audit_program(
            tmp_path,
            monkeypatch,
            _status(scheduled=7, deadline=future),
            _status(scheduled=7, deadline=past),
        )

    proof = json.loads((tmp_path / "audit" / "audit.json").read_text(encoding="utf-8"))
    assert proof["productionAuthority"]["state"] == "NO_QUALIFIED_CHAMPION_SUPPRESSED"
    assert proof["prior"]["authorityAsOfState"] == "AUTHORITY_AS_OF_UNAVAILABLE"
    assert proof["prior"]["authorityAsOfValid"] is False
    assert proof["prior"]["capabilityState"] == "CARD_MISSING_AFTER_DEADLINE"
    assert proof["prior"]["errors"][0].startswith("card_missing_after_deadline:")
    assert "prior_authority_as_of_unavailable" in proof["prior"]["errors"]


def test_unknown_authority_fails_even_before_card_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    authority_model, authority_today = _no_champion_authority()
    authority_model.pop("qualifiedChampionPresent")

    with pytest.raises(AssertionError):
        _run_audit_program(
            tmp_path,
            monkeypatch,
            _status(scheduled=7, deadline=future),
            _status(scheduled=7, deadline=future),
            authority_model=authority_model,
            authority_today=authority_today,
        )

    proof = json.loads((tmp_path / "audit" / "audit.json").read_text(encoding="utf-8"))
    assert proof["ok"] is False
    assert proof["productionAuthority"]["state"] == "AUTHORITY_READINESS_UNKNOWN"
    assert proof["productionAuthority"]["valid"] is False
    assert "model_qualifiedChampionPresent_mismatch" in proof["productionAuthority"]["errors"]


def test_nonzero_no_champion_predictions_cannot_suppress_missing_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    authority_model, authority_today = _no_champion_authority()
    authority_today.update(
        {"count": 1, "winner_predictions": [{"gamePk": 1}], "predictions": [{"gamePk": 1}]}
    )

    with pytest.raises(AssertionError):
        _run_audit_program(
            tmp_path,
            monkeypatch,
            _status(scheduled=7, deadline=past),
            _status(scheduled=7, deadline=past),
            authority_model=authority_model,
            authority_today=authority_today,
        )

    proof = json.loads((tmp_path / "audit" / "audit.json").read_text(encoding="utf-8"))
    assert proof["productionAuthority"]["state"] == "AUTHORITY_READINESS_UNKNOWN"
    assert "today_winner_prediction_count_not_zero" in proof["productionAuthority"]["errors"]
    assert proof["today"]["capabilityState"] == "CARD_MISSING_AFTER_DEADLINE"


@pytest.mark.parametrize("invalid_count", [False, 0.0, "0"])
def test_no_champion_count_requires_exact_non_bool_integer_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_count: object,
) -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    authority_model, authority_today = _no_champion_authority()
    authority_today["count"] = invalid_count

    with pytest.raises(AssertionError):
        _run_audit_program(
            tmp_path,
            monkeypatch,
            _status(scheduled=7, deadline=past),
            _status(scheduled=7, deadline=past, card=_whole_card(7)),
            authority_model=authority_model,
            authority_today=authority_today,
        )

    proof = json.loads((tmp_path / "audit" / "audit.json").read_text(encoding="utf-8"))
    assert proof["productionAuthority"]["state"] == "AUTHORITY_READINESS_UNKNOWN"
    assert "today_winner_prediction_count_not_zero" in proof["productionAuthority"]["errors"]
    assert proof["today"]["capabilityState"] == "CARD_MISSING_AFTER_DEADLINE"


def test_unpublished_card_inside_final_window_is_not_marked_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    near_future = datetime.now(timezone.utc) + timedelta(minutes=10)
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    proof = _run_audit_program(
        tmp_path,
        monkeypatch,
        _status(scheduled=7, deadline=near_future),
        _status(scheduled=7, deadline=future),
    )

    assert proof["ok"] is True
    assert proof["today"]["capabilityState"] == "FINAL_WINDOW"


def test_missing_deadline_cannot_be_misclassified_as_not_due(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    today = _status(scheduled=7, deadline=future)
    today["deadline"] = {}

    with pytest.raises(AssertionError):
        _run_audit_program(
            tmp_path,
            monkeypatch,
            today,
            _status(scheduled=7, deadline=future),
        )

    proof = json.loads((tmp_path / "audit" / "audit.json").read_text(encoding="utf-8"))
    assert proof["ok"] is False
    assert proof["today"]["capabilityState"] == "DEADLINE_UNAVAILABLE"
    assert proof["today"]["errors"] == ["publication_deadline_unavailable"]


def test_status_response_must_match_requested_slate_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    today = _status(scheduled=7, deadline=future)

    fixture_root = tmp_path / "audit"
    prior = {**_status(scheduled=7, deadline=future), "slateDateEt": "2026-08-27"}
    _write_runtime_fixture(
        fixture_root,
        {**today, "slateDateEt": "2026-08-26"},
        prior,
    )
    monkeypatch.setenv("TODAY_ET", "2026-08-28")
    monkeypatch.setenv("PRIOR_ET", "2026-08-27")
    monkeypatch.setenv("MLB_AUTO_FINAL_COLLECTION_WINDOW_MINUTES", "20")
    program = _audit_program().replace(
        "root=Path('/tmp/mlb-auto-audit')",
        f"root=Path({str(fixture_root)!r})",
    )

    with pytest.raises(AssertionError):
        exec(compile(program, str(WORKFLOW), "exec"), {})

    proof = json.loads((fixture_root / "audit.json").read_text(encoding="utf-8"))
    assert proof["ok"] is False
    assert proof["today"]["errors"] == ["slate_date_mismatch:2026-08-26/2026-08-28"]


def test_published_partial_card_still_fails_whole_slate_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    with pytest.raises(AssertionError):
        _run_audit_program(
            tmp_path,
            monkeypatch,
            _status(scheduled=7, deadline=future, card={"picks": []}),
            _status(scheduled=7, deadline=future),
        )

    proof = json.loads((tmp_path / "audit" / "audit.json").read_text(encoding="utf-8"))
    assert proof["ok"] is False
    assert proof["today"]["errors"] == ["card_not_whole_slate:0/7"]
