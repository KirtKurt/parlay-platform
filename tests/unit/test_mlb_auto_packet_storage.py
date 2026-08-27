from __future__ import annotations

import copy
import hashlib

import pytest
from botocore.exceptions import ClientError

import handler as subject


def _client_error(code: str, message: str, status: int = 400) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "PutItem",
    )


class MemoryTable:
    def __init__(self, primary_error: ClientError) -> None:
        self.primary_error = primary_error
        self.primary_failed = False
        self.items: dict[tuple[str, str], dict] = {}
        self.put_calls: list[dict] = []

    def put_item(self, **kwargs):
        self.put_calls.append(copy.deepcopy(kwargs))
        item = copy.deepcopy(kwargs["Item"])
        key = (item["PK"], item["SK"])
        if not self.primary_failed:
            self.primary_failed = True
            raise self.primary_error
        self.items[key] = item
        return {}

    def get_item(self, *, Key, **_kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item is not None else {}


class RejectingTable:
    def __init__(self, error: ClientError) -> None:
        self.error = error
        self.put_calls: list[dict] = []

    def put_item(self, **kwargs):
        self.put_calls.append(copy.deepcopy(kwargs))
        raise self.error


def _poorly_compressible_text() -> str:
    return "".join(
        hashlib.sha256(str(index).encode("ascii")).hexdigest()
        for index in range(10_000)
    )


def test_dynamodb_400_item_size_error_uses_lossless_packet_chunks(
    monkeypatch,
) -> None:
    table = MemoryTable(
        _client_error(
            "ValidationException",
            "Item size has exceeded the maximum allowed size",
        )
    )
    monkeypatch.setattr(subject, "TABLE", table)
    payload = {
        "slateDateEt": "2026-08-27",
        "expanded": True,
        "probability": 0.625,
        "providerPacket": _poorly_compressible_text(),
    }

    assert subject._put("PACKET#2026-08-27", "FINAL_INPUT", payload) is True

    chunk_keys = sorted(
        sk for pk, sk in table.items if pk == "PACKET#2026-08-27" and "#CHUNK#" in sk
    )
    assert len(chunk_keys) >= 2
    manifest = table.items[("PACKET#2026-08-27", "FINAL_INPUT")]["data"]
    assert manifest["storageEncoding"] == "gzip-json-chunked-v1"
    assert manifest["chunkCount"] == len(chunk_keys)
    assert subject._get("PACKET#2026-08-27", "FINAL_INPUT") == payload


@pytest.mark.parametrize(
    ("pk", "message"),
    [
        ("PACKET#2026-08-27", "One or more parameter values were invalid"),
        ("CARD#2026-08-27", "Item size has exceeded the maximum allowed size"),
    ],
)
def test_unrelated_or_non_packet_validation_error_is_not_reclassified(
    monkeypatch,
    pk: str,
    message: str,
) -> None:
    table = RejectingTable(_client_error("ValidationException", message))
    monkeypatch.setattr(subject, "TABLE", table)

    with pytest.raises(ClientError) as caught:
        subject._put(pk, "FINAL", {"value": "unchanged"})

    assert caught.value.response["Error"]["Message"] == message
    assert len(table.put_calls) == 1


def test_conditional_conflict_still_returns_false(monkeypatch) -> None:
    table = RejectingTable(
        _client_error(
            "ConditionalCheckFailedException",
            "The conditional request failed",
        )
    )
    monkeypatch.setattr(subject, "TABLE", table)

    assert (
        subject._put(
            "CARD#2026-08-27",
            "FINAL",
            {"value": "existing"},
            condition="attribute_not_exists(PK)",
        )
        is False
    )
    assert len(table.put_calls) == 1


def test_existing_http_413_packet_fallback_remains_supported(monkeypatch) -> None:
    table = MemoryTable(
        _client_error("RequestEntityTooLarge", "Request too large", status=413)
    )
    monkeypatch.setattr(subject, "TABLE", table)
    payload = {"slateDateEt": "2026-08-27", "providerPacket": "small"}

    assert subject._put("PACKET#2026-08-27", "DISCOVERY#test", payload) is True
    assert subject._get("PACKET#2026-08-27", "DISCOVERY#test") == payload
