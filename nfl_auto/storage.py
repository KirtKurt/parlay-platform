"""AWS persistence restricted to the dedicated nfl_auto tables and buckets."""
from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any, Iterable, Mapping

try:
    import boto3  # type: ignore
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore

from .canonical import canonical_json, digest, now_utc
from .config import Settings
from .features import FrozenFeatureRow, Game, TeamGameStats


def ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {str(key): ddb_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [ddb_safe(item) for item in value]
    return value


def ddb_plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): ddb_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [ddb_plain(item) for item in value]
    return value


class NflStore:
    def __init__(
        self,
        settings: Settings,
        *,
        dynamodb_resource: Any = None,
        s3_client: Any = None,
    ) -> None:
        if boto3 is None and (dynamodb_resource is None or s3_client is None):
            raise RuntimeError("BOTO3_REQUIRED_FOR_NFL_STORE")
        self.settings = settings
        ddb = dynamodb_resource or boto3.resource("dynamodb", region_name=settings.aws_region)
        self.s3 = s3_client or boto3.client("s3", region_name=settings.aws_region)
        self.state = ddb.Table(settings.state_table)
        self.games = ddb.Table(settings.games_table)
        self.odds = ddb.Table(settings.odds_table)
        self.features = ddb.Table(settings.features_table)
        self.predictions = ddb.Table(settings.predictions_table)
        self.models = ddb.Table(settings.models_table)
        self.ops = ddb.Table(settings.ops_table)

    @staticmethod
    def scan_all(table: Any, **kwargs: Any) -> Iterable[dict[str, Any]]:
        request = dict(kwargs)
        while True:
            response = table.scan(**request)
            for row in response.get("Items") or []:
                yield ddb_plain(row)
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                break
            request["ExclusiveStartKey"] = cursor

    def put_raw(self, *, provider: str, logical_key: str, payload: Any) -> dict[str, str]:
        body = canonical_json(payload).encode("utf-8")
        body_digest = digest(payload)
        key = f"{provider.lower()}/{logical_key}/{body_digest}.json"
        self.s3.put_object(
            Bucket=self.settings.raw_bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            Metadata={"sha256": body_digest, "provider": provider.lower()},
        )
        return {
            "provider": provider,
            "bucket": self.settings.raw_bucket,
            "key": key,
            "sha256": body_digest,
        }

    def put_artifact(self, *, logical_key: str, payload: Any) -> dict[str, str]:
        body = canonical_json(payload).encode("utf-8")
        body_digest = digest(payload)
        key = f"{logical_key}/{body_digest}.json"
        self.s3.put_object(
            Bucket=self.settings.artifact_bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            Metadata={"sha256": body_digest},
        )
        return {"bucket": self.settings.artifact_bucket, "key": key, "sha256": body_digest}

    def state_get(self, pk: str, sk: str = "CURRENT") -> dict[str, Any] | None:
        response = self.state.get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True)
        item = response.get("Item")
        return ddb_plain(item) if item else None

    def state_put(self, pk: str, payload: Mapping[str, Any], sk: str = "CURRENT") -> None:
        self.state.put_item(
            Item=ddb_safe({"PK": pk, "SK": sk, **dict(payload), "updated_at": now_utc()})
        )

    def acquire_lease(self, name: str, *, ttl_seconds: int = 840) -> bool:
        now_epoch = int(time.time())
        try:
            self.ops.put_item(
                Item={
                    "PK": f"LEASE#{name}",
                    "SK": "CURRENT",
                    "expires_at": now_epoch + int(ttl_seconds),
                    "acquired_at": now_utc(),
                },
                ConditionExpression="attribute_not_exists(PK) OR expires_at < :now",
                ExpressionAttributeValues={":now": now_epoch},
            )
            return True
        except Exception as exc:
            response = getattr(exc, "response", {})
            code = ((response.get("Error") or {}).get("Code")) if isinstance(response, Mapping) else None
            if code == "ConditionalCheckFailedException":
                return False
            raise

    def release_lease(self, name: str) -> None:
        self.ops.delete_item(Key={"PK": f"LEASE#{name}", "SK": "CURRENT"})

    def put_op(self, category: str, identifier: str, payload: Mapping[str, Any]) -> None:
        self.ops.put_item(
            Item=ddb_safe(
                {
                    "PK": category,
                    "SK": identifier,
                    **dict(payload),
                    "updated_at": now_utc(),
                }
            )
        )

    def put_game(self, game: Game, *, raw_provenance: Mapping[str, Any]) -> None:
        self.games.put_item(
            Item=ddb_safe(
                {
                    "PK": f"GAME#{game.game_id}",
                    "SK": "META",
                    "game_id": game.game_id,
                    "season": game.season,
                    "week": game.week,
                    "game_type": game.game_type,
                    "kickoff_utc": game.kickoff_utc,
                    "home_team": game.home_team,
                    "away_team": game.away_team,
                    "home_score": game.home_score,
                    "away_score": game.away_score,
                    "home_rest": game.home_rest,
                    "away_rest": game.away_rest,
                    "stadium": game.stadium,
                    "roof": game.roof,
                    "surface": game.surface,
                    "bbd_game_provenance": dict(raw_provenance),
                    "training_scope": True,
                    "updated_at": now_utc(),
                }
            )
        )

    def put_game_stats(
        self,
        game_id: str,
        stats: Mapping[str, TeamGameStats],
        *,
        raw_provenance: Mapping[str, Any],
        transport: list[Mapping[str, Any]],
    ) -> None:
        serialized = {
            team: {
                "team": row.team,
                "game_id": row.game_id,
                "offensive_epa": row.offensive_epa,
                "pass_epa": row.pass_epa,
                "rush_epa": row.rush_epa,
                "success_rate": row.success_rate,
                "explosive_rate": row.explosive_rate,
                "turnover_rate": row.turnover_rate,
                "early_down_pass_rate": row.early_down_pass_rate,
                "third_down_success": row.third_down_success,
                "defensive_epa_allowed": row.defensive_epa_allowed,
                "defensive_success_allowed": row.defensive_success_allowed,
                "plays": row.plays,
            }
            for team, row in stats.items()
        }
        self.games.put_item(
            Item=ddb_safe(
                {
                    "PK": f"GAME#{game_id}",
                    "SK": "BBD_STATS",
                    "game_id": game_id,
                    "team_stats": serialized,
                    "bbd_plays_provenance": dict(raw_provenance),
                    "transport": list(transport),
                    "updated_at": now_utc(),
                }
            )
        )

    def list_games(self) -> list[dict[str, Any]]:
        return sorted(
            [row for row in self.scan_all(self.games) if row.get("SK") == "META"],
            key=lambda row: (str(row.get("kickoff_utc") or ""), str(row.get("game_id") or "")),
        )

    def get_game_item(self, game_id: str, sk: str) -> dict[str, Any] | None:
        response = self.games.get_item(
            Key={"PK": f"GAME#{game_id}", "SK": sk}, ConsistentRead=True
        )
        return ddb_plain(response.get("Item")) if response.get("Item") else None

    def next_game_missing_stats(self) -> dict[str, Any] | None:
        for game in self.list_games():
            if self.get_game_item(str(game["game_id"]), "BBD_STATS") is None:
                return game
        return None

    def put_odds_snapshot(
        self,
        *,
        game_id: str,
        horizon_minutes: int,
        snapshot_at: str,
        consensus: Mapping[str, Any],
        raw_provenance: Mapping[str, Any],
        transport: Mapping[str, Any],
    ) -> None:
        self.odds.put_item(
            Item=ddb_safe(
                {
                    "PK": f"GAME#{game_id}",
                    "SK": f"HORIZON#{int(horizon_minutes):04d}",
                    "game_id": game_id,
                    "horizon_minutes": int(horizon_minutes),
                    "snapshot_at": snapshot_at,
                    "consensus": dict(consensus),
                    "odds_provenance": dict(raw_provenance),
                    "transport": dict(transport),
                    "updated_at": now_utc(),
                }
            )
        )

    def odds_for_game(self, game_id: str) -> dict[int, dict[str, Any]]:
        response = self.odds.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"GAME#{game_id}"},
            ConsistentRead=True,
        )
        output: dict[int, dict[str, Any]] = {}
        for raw in response.get("Items") or []:
            row = ddb_plain(raw)
            output[int(row["horizon_minutes"])] = row
        return output

    def next_game_missing_odds(self, required_horizons: tuple[int, ...]) -> dict[str, Any] | None:
        required = set(int(value) for value in required_horizons)
        for game in self.list_games():
            available = set(self.odds_for_game(str(game["game_id"])))
            if not required.issubset(available):
                return game
        return None

    def put_feature(self, row: FrozenFeatureRow) -> None:
        item = row.to_dict()
        item.update(
            {
                "PK": f"TARGET#{row.target}",
                "SK": f"KICKOFF#{row.kickoff_utc}#GAME#{row.event_key}",
                "updated_at": now_utc(),
            }
        )
        self.features.put_item(
            Item=ddb_safe(item),
            ConditionExpression="attribute_not_exists(PK) OR feature_hash = :feature_hash",
            ExpressionAttributeValues={":feature_hash": row.feature_hash},
        )

    def feature_for_game(self, target: str, kickoff_utc: str, event_key: str) -> dict[str, Any] | None:
        response = self.features.get_item(
            Key={
                "PK": f"TARGET#{target}",
                "SK": f"KICKOFF#{kickoff_utc}#GAME#{event_key}",
            },
            ConsistentRead=True,
        )
        return ddb_plain(response.get("Item")) if response.get("Item") else None

    def feature_rows(self, target: str) -> list[dict[str, Any]]:
        response = self.features.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"TARGET#{target}"},
            ConsistentRead=True,
        )
        rows = [ddb_plain(row) for row in response.get("Items") or []]
        while response.get("LastEvaluatedKey"):
            response = self.features.query(
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={":pk": f"TARGET#{target}"},
                ExclusiveStartKey=response["LastEvaluatedKey"],
                ConsistentRead=True,
            )
            rows.extend(ddb_plain(row) for row in response.get("Items") or [])
        return rows


    def put_prediction(self, payload: Mapping[str, Any]) -> None:
        event_key = str(payload["event_key"])
        target = str(payload["target"])
        horizon = int(payload.get("decision_horizon_minutes") or 10)
        item = {
            "PK": f"GAME#{event_key}",
            "SK": f"TARGET#{target}#T{horizon}",
            **dict(payload),
            "created_at": now_utc(),
        }
        self.predictions.put_item(
            Item=ddb_safe(item),
            ConditionExpression="attribute_not_exists(PK)",
        )

    def prediction(self, event_key: str, target: str, horizon: int = 10) -> dict[str, Any] | None:
        response = self.predictions.get_item(
            Key={
                "PK": f"GAME#{event_key}",
                "SK": f"TARGET#{target}#T{int(horizon)}",
            },
            ConsistentRead=True,
        )
        return ddb_plain(response.get("Item")) if response.get("Item") else None

    def put_model_candidate(
        self,
        *,
        target: str,
        model: Mapping[str, Any],
        report: Mapping[str, Any],
        authority_state: str,
    ) -> None:
        model_digest = str(model["model_digest"])
        self.models.put_item(
            Item=ddb_safe(
                {
                    "PK": f"MODEL#{target}",
                    "SK": f"VERSION#{model_digest}",
                    "target": target,
                    "model_digest": model_digest,
                    "model": dict(model),
                    "report": dict(report),
                    "authority_state": authority_state,
                    "created_at": now_utc(),
                }
            )
        )

    def promote_model(self, *, target: str, model: Mapping[str, Any], report: Mapping[str, Any]) -> None:
        model_digest = str(model["model_digest"])
        self.models.put_item(
            Item=ddb_safe(
                {
                    "PK": f"MODEL#{target}",
                    "SK": "CHAMPION",
                    "target": target,
                    "model_digest": model_digest,
                    "model": dict(model),
                    "report": dict(report),
                    "authority_state": "HISTORICAL_CHAMPION",
                    "promoted_at": now_utc(),
                }
            )
        )

    def champion(self, target: str) -> dict[str, Any] | None:
        response = self.models.get_item(
            Key={"PK": f"MODEL#{target}", "SK": "CHAMPION"}, ConsistentRead=True
        )
        return ddb_plain(response.get("Item")) if response.get("Item") else None
