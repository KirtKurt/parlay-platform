"""Read-only dated KSS1 shadow picks; never create forecasts on API reads."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from boto3.dynamodb.conditions import Key

from .canonical import parse_utc
from .storage import now_utc, plain


def recorded_picks(store, day=None, *, selection=None, trained_only=True):
    zone = ZoneInfo("America/New_York")
    day = date.fromisoformat(day) if day else now_utc().astimezone(zone).date()
    if selection not in (None, "12", "1X", "X2"):
        raise ValueError("selection must be 12, 1X or X2")
    start = datetime.combine(day, time(), zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(timezone.utc)
    fixtures = []
    for value in store.scan_all(store.events, ConsistentRead=True):
        row = plain(value)
        if row.get("entity_type") != "SOCCER_EVENT" or row.get("SK") != "METADATA":
            continue
        try:
            if start <= parse_utc(row["commence_time"]) < end:
                fixtures.append(row)
        except (KeyError, ValueError, TypeError):
            continue
    fixtures.sort(key=lambda r: (r["commence_time"], r["event_key"]))
    picks, missing = [], []
    for fixture in fixtures[:500]:
        revision = int(fixture.get("schedule_revision") or 0)
        query = {"KeyConditionExpression": Key("PK").eq(fixture["event_key"]) & Key("SK").begins_with(f"PRED#KSS1#REV#{revision}#TARGET#kss1_book#MODEL#"), "ConsistentRead": True}
        valid = []
        while True:
            response = store.predictions.query(**query)
            for value in response.get("Items", []):
                row = plain(value)
                book = row.get("kss1") or {}
                try:
                    if (row.get("immutable") is not True
                        or row.get("prediction_status") != "SHADOW"
                        or row.get("automatic_prediction_allowed") is not False
                        or row.get("event_key") != fixture["event_key"]
                        or int(row.get("schedule_revision", -1)) != revision
                        or parse_utc(row["commence_time"]) != parse_utc(fixture["commence_time"])
                        or row.get("home_team") != fixture.get("home_team")
                        or row.get("away_team") != fixture.get("away_team")
                        or parse_utc(row["created_at"]) > parse_utc(fixture["commence_time"]) - timedelta(minutes=60)
                        or (book.get("observation") or {}).get("action") != "public_eligible"
                        or (row.get("goals_features") or {}).get("research_only")):
                        continue
                    trained = bool(book.get("goals_model_digest"))
                    if trained and book["goals_model_digest"] != row.get("model_digest"):
                        continue
                    if trained_only and not trained:
                        continue
                    valid.append(row)
                except (KeyError, ValueError, TypeError):
                    continue
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                break
            query["ExclusiveStartKey"] = cursor
        if not valid:
            missing.append({"event_key": fixture["event_key"], "home_team": fixture.get("home_team"), "away_team": fixture.get("away_team"), "commence_time": fixture["commence_time"], "reason": "NO_RECORDED_T60_TRAINED_PICK" if trained_only else "NO_RECORDED_T60_PICK"})
            continue
        row = max(valid, key=lambda r: (parse_utc(r["created_at"]), r["model_digest"]))
        book = row["kss1"]
        markets = book["markets"]
        # Filter the actual threshold-qualified selection, not all fixtures
        # on which the probability of no draw happens to exceed one half.
        if selection and markets.get("double_chance_published") != selection:
            continue
        picks.append({key: row.get(key) for key in ("event_key", "home_team", "away_team", "commence_time", "created_at", "model_digest")} | {"model_state": "FITTED_SHADOW" if book.get("goals_model_digest") else "UNTRAINED_BASELINE", "markets": markets, "input_coverage": book.get("input_coverage")})
    return {"ok": True, "system": "kss1_goals", "date": day.isoformat(), "timezone": "America/New_York", "authority": "SHADOW_LEARNING", "automatic_prediction_allowed": False, "trained_only": trained_only, "selection": selection, "fixture_count": len(fixtures), "truncated": len(fixtures) > 500, "count": len(picks), "picks": picks, "missing": missing, "reason": "NO_MATCHING_RECORDED_PICKS" if not picks else None}
