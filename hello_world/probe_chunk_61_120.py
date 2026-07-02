        return {"scheduled_event": False, "allowed": True}
    start_text = os.environ.get("MLB_PULL_START_AT_ET") or DEFAULT_START_AT_ET
    start_at = _parse_start(start_text)
    if start_at is None:
        return {"scheduled_event": True, "allowed": True, "enabled": False, "start_at_et": start_text}
    now_et = datetime.now(ZoneInfo("America/New_York"))
    return {"scheduled_event": True, "allowed": now_et >= start_at, "enabled": True, "start_at_et": start_at.isoformat(), "now_et": now_et.isoformat()}


def _to_ddb_safe(module: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    return getattr(module, "_to_ddb", lambda x: x)(item)


def _patch_prediction_item(module: Any) -> None:
    if module is None or getattr(module, "_inqsi_line_movement_prediction_item_patch", False):
        return
    original = module._prediction_item

    def _prediction_item(row: Dict[str, Any], slate_date: str, now: str) -> Dict[str, Any]:
        item = original(row, slate_date, now)
        item.update({
            "model_family": "odds_api_line_movement",
            "line_movement_model_version": MODEL_VERSION,
            "feature_source": "theOddsAPI h2h consensus movement between 15-minute HOT snapshots",
            "pull_interval_minutes": 15,
            "line_movement_inputs": {
                "previous_consensus": row.get("previous_consensus") or {},
                "latest_consensus": row.get("latest_consensus") or {},
                "movement": {"home_delta": row.get("home_delta"), "away_delta": row.get("away_delta"), "hot_delta": row.get("hot_delta")},
                "book_agreement": row.get("book_agreement") or {},
                "spread_signal": row.get("spread_signal") or {},
                "total_signal": row.get("total_signal") or {},
            },
        })
        return item

    module._prediction_item = _prediction_item
    module._inqsi_line_movement_prediction_item_patch = True


def _patch_source_status(module: Any) -> None:
    if module is None or getattr(module, "_inqsi_line_movement_source_status_patch", False):
        return
    original = module.source_status

    def source_status() -> Dict[str, Any]:
        status = original()
        status["line_movement_prediction_policy"] = {
            "status": "CONNECTED",
            "model_version": MODEL_VERSION,
            "source": "theOddsAPI",
            "primary_signal": "15-minute h2h consensus probability movement across books",
            "flat_market_policy": "store a NO_EDGE consensus-favorite attempted winner so every MLB game has a prediction row",
            "pull_interval_minutes": 15,
            "default_start_gate_et": DEFAULT_START_AT_ET,
        }
        return status

    module.source_status = source_status
    module._inqsi_line_movement_source_status_patch = True
