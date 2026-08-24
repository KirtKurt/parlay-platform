import json

from soccer_auto import collector
from soccer_auto.odds_api import OddsApiRateLimitError


def test_worker_requeues_distributed_rate_limit_contention(monkeypatch):
    class Store:
        def __init__(self): self.enqueued=[]
        def enqueue(self, job, delay_seconds=0): self.enqueued.append((job, delay_seconds))
    store=Store()
    monkeypatch.setattr(collector, "SoccerStore", lambda: store)
    monkeypatch.setattr(collector, "_client", lambda: object())
    monkeypatch.setattr(collector, "process_job", lambda *a, **k: (_ for _ in ()).throw(OddsApiRateLimitError("DISTRIBUTED_RATE_LIMIT_CONTENTION")))
    job={"version":collector.JOB_VERSION,"action":"FETCH_EVENT","event":{"event_key":"EVENT#x","commence_time":"2099-01-01T00:00:00Z"}}
    result=collector.worker_handler({"Records":[{"body":json.dumps(job),"messageId":"m1","attributes":{"ApproximateReceiveCount":"1"}}]},None)
    assert result["batchItemFailures"] == []
    assert result["processed"][0]["retry_reenqueued"] is True
    assert result["processed"][0]["reason"] == "DISTRIBUTED_RATE_LIMIT_CONTENTION"
    assert len(store.enqueued) == 1
    assert store.enqueued[0][1] >= 2
