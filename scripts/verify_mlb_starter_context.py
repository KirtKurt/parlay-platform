"""Read-only live proof of the official starter-to-immutable-snapshot path."""
import json
import sys
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hello_world"))
import mlb_advanced_context as context
import mlb_fundamentals_snapshot_v2 as snapshots
import mlb_ml_dual_model_v2 as dual


def persisted_observations(table, day):
    """Inspect protected collector output without refetching or rebuilding data."""
    from boto3.dynamodb.conditions import Key

    rows, cursor = [], None
    while True:
        query = {"KeyConditionExpression": Key("PK").eq(f"GAME_WINNERS#mlb#{day}")
                 & Key("SK").begins_with("GAME#"), "ConsistentRead": True}
        if cursor:
            query["ExclusiveStartKey"] = cursor
        page = table.query(**query)
        rows.extend(page.get("Items") or [])
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            break
    observed = []
    for stored in rows:
        row = stored.get("data") or stored
        snapshot = row.get("fundamentalsSnapshotV2") or {}
        quality = (snapshot.get("groups") or {}).get("starter_quality") or {}
        if context.starter_context.VERSION not in str(quality.get("dataset") or ""):
            continue
        errors = snapshots.validate(snapshot)
        if errors:
            raise RuntimeError("persisted starter snapshot invalid: " + ",".join(errors))
        values = quality.get("values") or {}
        if all(values.get(side + "Era") is not None and values.get(side + "KMinusBbPct") is not None
               for side in ("home", "away")):
            observed.append({"gameId": row.get("gameId") or row.get("game_id"),
                             "sourceRetrievedAtUtc": quality.get("retrievedAtUtc"),
                             "sourcePayloadFingerprint": quality.get("payloadFingerprint"),
                             "snapshotFingerprint": snapshot.get("fingerprint")})
    return {"storedGameRowCount": len(rows), "gamesWithPersistedStarterRates": len(observed),
            "status": "PERSISTED_OBSERVATIONS_VERIFIED" if observed else "AWAITING_NEW_SNAPSHOT_CAPTURE",
            "readOnly": True, "games": observed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--persisted", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    schedule = context._statsapi_schedule(day)
    if schedule.get("ok") is not True:
        raise RuntimeError("official schedule unavailable")
    rows = []
    for game in context._schedule_games(schedule):
        teams = game["teams"]
        row = {"gameId": f"mlb_statsapi:{game['gamePk']}", "officialGamePk": game["gamePk"],
               "slateDateEt": day, "homeTeam": teams["home"]["team"]["name"],
               "awayTeam": teams["away"]["team"]["name"]}
        snapshot = snapshots.build(row)
        quality = snapshot["groups"]["starter_quality"]
        hand = snapshot["groups"]["starter_handedness_splits"]
        observed = all(quality["values"].get(side + "Era") is not None
                       and quality["values"].get(side + "KMinusBbPct") is not None
                       for side in ("home", "away"))
        if observed:
            assert quality["identifiers"]["officialGamePk"] == game["gamePk"]
            for side in ("home", "away"):
                assert quality["identifiers"][side + "EntityId"] == teams[side]["probablePitcher"]["id"]
            assert quality["payloadFingerprint"]
            assert datetime.fromisoformat(quality["retrievedAtUtc"]) < (
                datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00")) - timedelta(minutes=45))
        assert quality["complete"] is False
        assert dual._strict_features({"fundamentalsSnapshotV2": snapshot}, {}) == dual._strict_features({}, {})
        rows.append({"officialGamePk": game["gamePk"], "starterRatesObserved": observed,
                     "starterQualityStatus": quality["status"],
                     "pitcherHandsObserved": all(hand["values"].get(side + "StarterHand") for side in ("home", "away")),
                     "sourceRetrievedAtUtc": quality.get("retrievedAtUtc"),
                     "sourcePayloadFingerprint": quality.get("payloadFingerprint"),
                     "snapshotFingerprint": snapshot["fingerprint"],
                     "unavailableReason": None if observed else quality.get("missingReason")})
    report = {"ok": True, "version": context.starter_context.VERSION,
              "verifiedAtUtc": datetime.now(timezone.utc).isoformat(), "slateDateEt": day,
              "officialGameCount": len(rows),
              "gamesWithBothStarterRates": sum(row["starterRatesObserved"] for row in rows),
              "gamesWithBothPitcherHands": sum(bool(row["pitcherHandsObserved"]) for row in rows),
              "frozenR8ModelInputsChanged": False, "productionAuthorityChanged": False,
              "immutableSnapshotsWrittenByProbe": False, "games": rows}
    if args.persisted:
        import boto3
        report["persistedCollectorEvidence"] = persisted_observations(
            boto3.resource("dynamodb").Table("parlay_platform_snapshots"), day)
    path = ROOT / "runtime_reports/mlb_starter_context_live_proof_latest.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "games"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
