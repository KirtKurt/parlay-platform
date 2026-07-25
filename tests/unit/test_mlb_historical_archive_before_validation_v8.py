import mlb_historical_optimizer_handler as handler

def test_fetch_archives_stale_provider_payload_before_freshness_policy(monkeypatch):
    payload = {'timestamp': '2025-04-14T00:00:00Z', 'data': []}
    headers = {'x-requests-last': 10}
    monkeypatch.setattr(handler, '_http_json', lambda url: (payload, headers))
    monkeypatch.setattr(
        handler.optimizer,
        'normalize_historical_snapshot',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            handler.optimizer.HistoricalOptimizerError(
                'historical response is too stale for a 15-minute grid'
            )
        ),
    )
    actual, actual_headers = handler._fetch_historical('2025-04-14T12:00:00Z')
    assert actual == payload
    assert actual_headers == headers
