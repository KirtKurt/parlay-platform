"""One-shot isolated Soccer DLQ recovery, executed with the Soccer runtime role."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Mapping

import boto3


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    request = dict(event or {})
    if request.get("action") != "recover_collection_dlq":
        return {"ok": False, "system": "soccer_auto", "reason": "EXPLICIT_RECOVERY_ACTION_REQUIRED"}
    main_url = os.getenv("SOCCER_AUTO_COLLECTION_QUEUE_URL", "").strip()
    dlq_url = os.getenv("SOCCER_AUTO_COLLECTION_DLQ_URL", "").strip()
    if not main_url or not dlq_url:
        raise RuntimeError("soccer collection queue URLs are not configured")
    limit = max(1, min(5000, int(request.get("max_messages") or 5000)))
    sqs = boto3.client("sqs")
    now = datetime.now(timezone.utc)
    scanned = deleted = requeued = retired = malformed = 0
    empty_reads = 0
    while scanned < limit and empty_reads < 8:
        response = sqs.receive_message(
            QueueUrl=dlq_url,
            MaxNumberOfMessages=min(10, limit - scanned),
            VisibilityTimeout=120,
            WaitTimeSeconds=1,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )
        messages = response.get("Messages") or []
        if not messages:
            empty_reads += 1
            continue
        empty_reads = 0
        for message in messages:
            scanned += 1
            actionable = False
            body: dict[str, Any] = {}
            try:
                body = json.loads(message.get("Body") or "{}")
                job_event = dict(body.get("event") or {})
                raw = job_event.get("commence_time") or job_event.get("commenceTime") or ""
                commence = _parse_utc(str(raw)) if raw else None
                actionable = bool(
                    body.get("action") in {"DISCOVER_EVENT", "FETCH_EVENT"}
                    and commence
                    and commence > now
                )
            except Exception:
                malformed += 1
            if actionable:
                body["dlq_recovered_at"] = now.isoformat()
                body["dlq_recovery_count"] = int(body.get("dlq_recovery_count") or 0) + 1
                sqs.send_message(
                    QueueUrl=main_url,
                    MessageBody=json.dumps(body, separators=(",", ":"), default=str),
                    DelaySeconds=2,
                )
                requeued += 1
            else:
                retired += 1
            sqs.delete_message(QueueUrl=dlq_url, ReceiptHandle=message["ReceiptHandle"])
            deleted += 1
            if scanned >= limit:
                break
    attrs = sqs.get_queue_attributes(
        QueueUrl=dlq_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ],
    ).get("Attributes") or {}
    result = {
        "ok": True,
        "system": "soccer_auto",
        "component": "dlq_recovery",
        "observed_at": now.isoformat(),
        "scanned": scanned,
        "deleted": deleted,
        "requeued_future": requeued,
        "retired_stale": retired,
        "malformed": malformed,
        "remaining_visible": int(attrs.get("ApproximateNumberOfMessages") or 0),
        "remaining_inflight": int(attrs.get("ApproximateNumberOfMessagesNotVisible") or 0),
        "remaining_delayed": int(attrs.get("ApproximateNumberOfMessagesDelayed") or 0),
    }
    return result
