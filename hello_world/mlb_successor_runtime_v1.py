"""Durable successor development and qualification under existing trainer leases.

R8 is read-only input here. This namespace owns a single separate protocol,
frozen candidate, write-once predictions and first sealed prospective test.
Only an explicit review of that exact qualified artifact can activate serving.
"""
from __future__ import annotations

import copy
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import mlb_successor_model_v2 as model

PK = "MLB_ML_SUCCESSOR#" + model.EXPERIMENT_ID
ET = ZoneInfo("America/New_York")
VERSION = "MLB-SUCCESSOR-DURABLE-QUALIFIED-DIRECTION-v1"


def safe(value):
    if isinstance(value, float): return Decimal(str(value))
    if isinstance(value, dict): return {k: safe(v) for k, v in value.items()}
    if isinstance(value, list): return [safe(v) for v in value]
    return value


class Repository:
    def __init__(self, table):
        self.table = table

    def get(self, key):
        item = self.table.get_item(Key={"PK": PK, "SK": key}, ConsistentRead=True).get("Item")
        if not item: return None
        data = item["data"]
        if item.get("fingerprint") != model.fingerprint(data):
            raise ValueError("successor storage fingerprint mismatch")
        return data

    def once(self, key, data):
        item = {"PK": PK, "SK": key, "data": data, "fingerprint": model.fingerprint(data),
                "record_type": VERSION, "immutable": True}
        try:
            self.table.put_item(Item=safe(item), ConditionExpression="attribute_not_exists(PK)")
            return data
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            existing = self.get(key)
            if model.fingerprint(existing) != model.fingerprint(data):
                raise ValueError("immutable successor record conflict") from exc
            return existing

    def status(self, mode, value):
        self.table.put_item(Item=safe({"PK": PK, "SK": "STATUS#" + mode,
                                      "data": value, "fingerprint": model.fingerprint(value),
                                      "record_type": VERSION}))

    def predictions(self, day):
        from boto3.dynamodb.conditions import Key
        args = {"KeyConditionExpression": Key("PK").eq(PK) & Key("SK").begins_with("PREDICTION#" + day + "#"),
                "ConsistentRead": True}
        result = []
        while True:
            response = self.table.query(**args)
            for item in response.get("Items", []):
                if item.get("fingerprint") != model.fingerprint(item["data"]):
                    raise ValueError("successor prediction storage fingerprint mismatch")
                result.append(item["data"])
            if not response.get("LastEvaluatedKey"): return result
            args["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def repository():
    import boto3
    from botocore.config import Config
    table = os.environ.get("SNAPSHOTS_TABLE")
    if not table: raise ValueError("snapshots table unavailable")
    return Repository(boto3.resource("dynamodb", config=Config(connect_timeout=2, read_timeout=3,
                                                            retries={"max_attempts": 1})).Table(table))


def frozen_candidate(repo):
    frozen = repo.get("FROZEN")
    if frozen:
        if frozen.get("protocolFingerprint") != model.fingerprint(model.PROTOCOL):
            raise ValueError("successor protocol changed after freeze")
        material = {k: v for k, v in frozen.items() if k != "artifactDigest"}
        if frozen.get("artifactDigest") != model.fingerprint(material):
            raise ValueError("frozen successor artifact mismatch")
    return frozen


def develop(repo, accepted_rows, now, deployment, artifact_store):
    protocol = repo.get("PROTOCOL")
    if protocol is None:
        protocol = repo.once("PROTOCOL", {"protocol": model.PROTOCOL, "createdAtUtc": now.isoformat(),
                                         "deploymentIdentity": deployment})
    if model.fingerprint(protocol["protocol"]) != model.fingerprint(model.PROTOCOL):
        raise ValueError("persisted successor protocol mismatch")
    frozen = frozen_candidate(repo)
    rows, rejected = [], Counter()
    for row in accepted_rows:
        try: rows.append(model.record(row, labeled=True))
        except (ValueError, TypeError, KeyError) as exc: rejected[str(exc)] += 1
    report = {"ok": True, "version": VERSION, "experimentId": model.EXPERIMENT_ID,
              "updatedAtUtc": now.isoformat(), "deploymentIdentity": deployment,
              "acceptedDevelopmentRows": len(rows), "rejectedRows": dict(rejected),
              "productionAuthorityChanged": False, "automaticPromotionEnabled": False}
    if frozen is None:
        development = model.development(rows)
        report.update(development)
        candidate = development.get("candidate")
        if candidate:
            # A validation screen decides whether to spend a new prospective
            # test. It is development evidence, never qualification evidence.
            blockers = model.direction_blockers(candidate["validation"], 500, 100)
            report["validationScreenBlockers"] = blockers
            if not blockers:
                material = {"version": model.VERSION, "experimentId": model.EXPERIMENT_ID,
                            "protocolFingerprint": model.fingerprint(model.PROTOCOL),
                            "candidate": candidate, "developmentFingerprint": development["developmentFingerprint"],
                            "partitionFingerprints": development["partitionFingerprints"],
                            "developmentCounts": development["counts"], "frozenAtUtc": now.isoformat(),
                            "firstProspectiveSlateDate": (now.astimezone(ET).date() + timedelta(days=1)).isoformat(),
                            "deploymentIdentity": deployment}
                # Retain exact labeled development inputs in a versioned artifact.
                material["developmentArtifact"] = artifact_store.put_versioned_json(
                    f"mlb/experiments/{model.EXPERIMENT_ID}/development/{model.fingerprint(rows)}/dataset.json",
                    {"rows": rows, "development": development, "protocol": model.PROTOCOL})
                frozen = repo.once("FROZEN", {**material, "artifactDigest": model.fingerprint(material)})
    if frozen:
        report.update(evaluate_prospective(repo, rows, frozen, now))
        report["artifactDigest"] = frozen["artifactDigest"]
    repo.status("TRAINING", report)
    return report


def prediction(row, frozen, captured, deployment):
    record = model.record(row, labeled=False)
    start = model.timestamp(record["commenceTime"])
    if (record["slateDateEt"] < frozen["firstProspectiveSlateDate"]
            or not model.timestamp(frozen["frozenAtUtc"]) < captured < start
            or model.timestamp(record["featureLockAtUtc"]) > captured):
        raise ValueError("prediction outside frozen pregame capture interval")
    probability = model.score(record, frozen["candidate"])
    return {"version": VERSION, "experimentId": model.EXPERIMENT_ID,
            "artifactDigest": frozen["artifactDigest"], "capturedAtUtc": captured.isoformat(),
            "deploymentIdentity": deployment, "record": record,
            "homeProbability": probability, "awayProbability": 1-probability,
            "predictedSide": "home" if probability >= 0.5 else "away",
            "outcomeKnownAtCapture": False, "automaticWagerAllowed": False,
            "playabilityAuthorityEnabled": False, "writeOnce": True}


def verify_prediction(entry, frozen):
    if entry.get("version") != VERSION or entry.get("artifactDigest") != frozen["artifactDigest"]:
        raise ValueError("prediction artifact identity mismatch")
    record = entry["record"]
    if record.get("inputFingerprint") != model.input_fingerprint(record):
        raise ValueError("prediction input fingerprint mismatch")
    captured = model.timestamp(entry["capturedAtUtc"])
    if (entry.get("outcomeKnownAtCapture") is not False
            or any(k in record for k in ("winner", "correct", "homeWon", "pickCorrect"))
            or record["slateDateEt"] < frozen["firstProspectiveSlateDate"]
            or not model.timestamp(frozen["frozenAtUtc"]) < captured < model.timestamp(record["commenceTime"])
            or model.timestamp(record["featureLockAtUtc"]) > captured):
        raise ValueError("invalid pre-outcome prediction receipt")
    p = model.number(entry.get("homeProbability"))
    if p is None or abs(p-model.score(record, frozen["candidate"])) > 1e-10:
        raise ValueError("prediction does not replay exact frozen model")
    away = model.number(entry.get("awayProbability"))
    if away is None or abs(p + away - 1) > 1e-10:
        raise ValueError("prediction probability pair mismatch")
    if entry.get("predictedSide") != ("home" if p >= .5 else "away"):
        raise ValueError("prediction side mismatch")
    return entry


def capture(repo, locked_rows, clock, deployment):
    frozen = frozen_candidate(repo)
    if not frozen:
        return {"ok": True, "status": "WAITING_FOR_SUCCESSOR_DEVELOPMENT", "capturedCount": 0}
    created, existing, skipped = 0, 0, Counter()
    for row in locked_rows:
        day, game = str(row.get("slateDateEt") or ""), str(row.get("officialGamePk") or "")
        key = f"PREDICTION#{day}#{game}"
        previous = repo.get(key)
        if previous:
            verify_prediction(previous, frozen)
            existing += 1
            continue
        try: entry = prediction(row, frozen, clock(), deployment)
        except (ValueError, TypeError, KeyError) as exc:
            skipped[str(exc)] += 1
            continue
        # Prevent a slow score operation from creating a prediction after start.
        if clock() >= model.timestamp(entry["record"]["commenceTime"]):
            skipped["crossed_first_pitch_during_score"] += 1
            continue
        repo.once(key, entry)
        created += 1
    report = {"ok": True, "status": "SUCCESSOR_CAPTURE_COMPLETE", "capturedCount": created,
              "existingCount": existing, "skipped": dict(skipped), "updatedAtUtc": clock().isoformat(),
              "artifactDigest": frozen["artifactDigest"], "productionAuthorityChanged": False}
    repo.status("CAPTURE", report)
    return report


def evaluate_prospective(repo, rows, frozen, now):
    sealed = repo.get("QUALIFICATION")
    if sealed:
        return {"status": "SEALED_QUALIFICATION_PASSED_AWAITING_REVIEW" if not qualification_blockers(sealed, frozen) else "SEALED_SUCCESSOR_TEST_FAILED",
                "qualification": qualification_summary(sealed, frozen)}
    selected, skipped_dates = [], []
    dates = sorted({r["slateDateEt"] for r in rows if r["slateDateEt"] >= frozen["firstProspectiveSlateDate"]
                    and r["slateDateEt"] < now.astimezone(ET).date().isoformat()})
    for day in dates:
        final_rows = [r for r in rows if r["slateDateEt"] == day]
        entries = {e["record"]["officialGamePk"]: verify_prediction(e, frozen) for e in repo.predictions(day)}
        # All recorded predictions and accepted final rows must join exactly.
        # An unresolved game blocks its entire slate rather than dropping a loss.
        if set(entries) != {r["officialGamePk"] for r in final_rows}:
            skipped_dates.append(day)
            continue
        for row in final_rows:
            entry = entries[row["officialGamePk"]]
            if entry["record"]["inputFingerprint"] != row["inputFingerprint"]:
                raise ValueError("settlement changed frozen successor inputs")
            selected.append({"prediction": entry, "homeWon": row["homeWon"],
                             "inputFingerprint": row["inputFingerprint"]})
        if len(selected) >= model.PROTOCOL["testMinimum"]: break
    report = {"status": "ACCUMULATING_FRESH_SUCCESSOR_TEST", "prospectiveCount": len(selected),
              "skippedIncompleteSlateDates": skipped_dates}
    if len(selected) >= model.PROTOCOL["testMinimum"]:
        evidence = {"version": VERSION, "artifactDigest": frozen["artifactDigest"],
                    "sealedAtUtc": now.isoformat(), "rows": selected,
                    "testCanBeReopened": False, "skippedIncompleteSlateDates": skipped_dates}
        evidence["blockers"] = qualification_blockers(evidence, frozen)
        evidence["directionQualified"] = not evidence["blockers"]
        repo.once("QUALIFICATION", evidence)
        report.update({"status": "SEALED_QUALIFICATION_PASSED_AWAITING_REVIEW" if evidence["directionQualified"] else "SEALED_SUCCESSOR_TEST_FAILED",
                       "qualification": qualification_summary(evidence, frozen)})
    return report


def qualification_blockers(evidence, frozen):
    if evidence.get("artifactDigest") != frozen["artifactDigest"] or evidence.get("testCanBeReopened") is not False:
        raise ValueError("qualification artifact binding mismatch")
    scored, identities = [], set()
    for row in evidence["rows"]:
        entry = verify_prediction(row["prediction"], frozen)
        record = entry["record"]
        identity = (record["slateDateEt"], record["officialGamePk"])
        if identity in identities or row.get("homeWon") not in (0, 1) or isinstance(row.get("homeWon"), bool):
            raise ValueError("invalid prospective label or duplicate identity")
        identities.add(identity)
        scored.append({"probability": float(entry["homeProbability"]), "homeWon": int(row["homeWon"]),
                       "marketHomeProbability": float(record["marketHomeProbability"])})
    outcome = model.metrics.evaluate(scored, "probability", "homeWon", baseline_probability_key="marketHomeProbability")
    total = sum(int(v) for v in frozen["developmentCounts"].values()) + len(scored)
    return model.direction_blockers(outcome, total, len(scored))


def qualification_summary(evidence, frozen):
    rows = [{"probability": float(r["prediction"]["homeProbability"]), "homeWon": int(r["homeWon"]),
             "marketHomeProbability": float(r["prediction"]["record"]["marketHomeProbability"])} for r in evidence["rows"]]
    return {"artifactDigest": frozen["artifactDigest"], "qualificationDigest": model.fingerprint(evidence),
            "sealedAtUtc": evidence["sealedAtUtc"], "prospectiveCount": len(rows),
            "metrics": model.metrics.evaluate(rows, "probability", "homeWon", baseline_probability_key="marketHomeProbability"),
            "blockers": qualification_blockers(evidence, frozen), "testCanBeReopened": False,
            "firstActivationRequiresManualReview": True}


def review_and_activate(repo, *, artifact_digest, qualification_digest, reviewer, now):
    """Explicit operator-only action, never invoked by schedules or public HTTP.

Both digests must be supplied from the concrete review; this is direction-only.
The first activation is conditional and cannot replace an incumbent implicitly.
"""
    frozen, evidence = frozen_candidate(repo), repo.get("QUALIFICATION")
    if not reviewer or not reviewer.strip() or not frozen or not evidence:
        raise ValueError("reviewer and sealed evidence required")
    if frozen["artifactDigest"] != artifact_digest or model.fingerprint(evidence) != qualification_digest:
        raise ValueError("review is not bound to exact model and sealed evidence")
    blockers = qualification_blockers(evidence, frozen)
    if blockers: raise ValueError("qualification failed: " + ",".join(blockers))
    if now < model.timestamp(evidence["sealedAtUtc"]):
        raise ValueError("review precedes qualification")
    return repo.once("ACTIVE", {"version": VERSION, "artifactDigest": artifact_digest,
                               "qualificationDigest": qualification_digest, "reviewer": reviewer.strip(),
                               "reviewedAtUtc": now.isoformat(), "directionAuthorityEnabled": True,
                               "playabilityAuthorityEnabled": False, "automaticWagerAllowed": False,
                               "approvalMode": "manual_first_exact_artifact_direction_activation"})


def authority(repo):
    active = repo.get("ACTIVE")
    if not active: return None
    frozen, evidence = frozen_candidate(repo), repo.get("QUALIFICATION")
    if (not frozen or not evidence or active.get("artifactDigest") != frozen["artifactDigest"]
            or active.get("qualificationDigest") != model.fingerprint(evidence)
            or active.get("approvalMode") != "manual_first_exact_artifact_direction_activation"
            or not str(active.get("reviewer") or "").strip()
            or active.get("directionAuthorityEnabled") is not True
            or active.get("playabilityAuthorityEnabled") is not False
            or active.get("automaticWagerAllowed") is not False
            or model.timestamp(active["reviewedAtUtc"]) < model.timestamp(evidence["sealedAtUtc"])
            or qualification_blockers(evidence, frozen)):
        raise ValueError("qualified reviewed successor authority unavailable")
    return {"active": active, "frozen": frozen}


def public_model():
    verified = authority(repository())
    if not verified: return None
    return {"model_version": model.VERSION, "primaryAlgorithm": model.VERSION,
            "soleProductionAlgorithm": model.VERSION, "game_winner_model": model.VERSION,
            "artifactDigest": verified["frozen"]["artifactDigest"], "experimentId": model.EXPERIMENT_ID,
            "r7DeploymentIdentity": verified["frozen"]["deploymentIdentity"],
            "successorConsumerVersion": VERSION, "playabilityAuthorityEnabled": False}


def public_predictions(day, limit, expected_digest, repo=None):
    # Read-only, with a fresh authority check in the same request. No engine
    # fallback and no live provider calls or prediction writes are possible.
    datetime.strptime(day, "%Y-%m-%d")
    repo = repo or repository()
    verified = authority(repo)
    if not verified or verified["frozen"]["artifactDigest"] != expected_digest:
        raise ValueError("qualified champion changed during public read")
    rows = []
    for entry in repo.predictions(day):
        verify_prediction(entry, verified["frozen"])
        record = entry["record"]
        side = entry["predictedSide"]
        rows.append({"gameId": record["gameId"], "officialGamePk": record["officialGamePk"],
                     "slateDateEt": day, "commenceTime": record["commenceTime"],
                     "homeTeam": record["homeTeam"], "awayTeam": record["awayTeam"],
                     "predictedSide": side, "predictedWinner": record[side + "Team"],
                     "homeProbability": entry["homeProbability"], "awayProbability": entry["awayProbability"],
                     "homeModelWinProbability": entry["homeProbability"],
                     "awayModelWinProbability": entry["awayProbability"],
                     "modelWinProbability": entry["homeProbability"] if side == "home" else entry["awayProbability"],
                     "marketProbability": record["marketHomeProbability"] if side == "home" else record["marketAwayProbability"],
                     "signalScore": None, "pickReliability": None, "playable": False,
                     "artifactDigest": expected_digest, "capturedAtUtc": entry["capturedAtUtc"],
                     "featureLockAtUtc": record["featureLockAtUtc"],
                     "inputFingerprint": record["inputFingerprint"], "immutable": True,
                     "automaticWagerAllowed": False, "playabilityAuthorityEnabled": False})
    rows.sort(key=lambda r: (r["commenceTime"], r["officialGamePk"]))
    return {"ok": True, "date": day, "predictions": rows[:limit], "winner_predictions": rows[:limit],
            "count": len(rows[:limit]), "readOnly": True, "predictionSource": VERSION,
            "coverageStatus": "PERSISTED_CANONICAL_LOCKS_ONLY", "automaticWagerAllowed": False}
