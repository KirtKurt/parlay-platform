from pathlib import Path

path = Path('soccer_auto/health.py')
text = path.read_text()
old_scan = '''def _scan(table: Any, *, limit: int = HEALTH_SCAN_LIMIT) -> tuple[list[dict[str, Any]], bool]:
    response = table.scan(ConsistentRead=True, Limit=limit)
    return [plain(row) for row in response.get("Items") or []], bool(
        response.get("LastEvaluatedKey")
    )
'''
new_scan = '''def _scan(table: Any, *, limit: int = HEALTH_SCAN_LIMIT) -> tuple[list[dict[str, Any]], bool]:
    """Read up to ``limit`` rows across DynamoDB's 1 MiB response pages."""
    rows: list[dict[str, Any]] = []
    exclusive_start_key: Mapping[str, Any] | None = None
    seen_keys: set[str] = set()
    while len(rows) < limit:
        kwargs: dict[str, Any] = {
            "ConsistentRead": True,
            "Limit": max(1, limit - len(rows)),
        }
        if exclusive_start_key is not None:
            kwargs["ExclusiveStartKey"] = exclusive_start_key
        response = table.scan(**kwargs)
        next_key = response.get("LastEvaluatedKey")
        if exclusive_start_key is not None and next_key == exclusive_start_key:
            return rows, True
        rows.extend(plain(row) for row in response.get("Items") or [])
        if not next_key:
            return rows, False
        if len(rows) >= limit:
            return rows[:limit], True
        fingerprint = repr(plain(next_key))
        if fingerprint in seen_keys:
            return rows, True
        seen_keys.add(fingerprint)
        exclusive_start_key = next_key
    return rows[:limit], True
'''
old_conflicts = '''def _conflicted_events(store: SoccerStore) -> tuple[set[str], bool]:
    if not hasattr(store.ops, "query"):
        rows, truncated = _scan(store.ops)
    else:
        response = store.ops.query(
            KeyConditionExpression=Key("PK").eq("SETTLEMENT_CONFLICT"),
            ConsistentRead=True,
            Limit=HEALTH_SCAN_LIMIT,
        )
        rows = [plain(row) for row in response.get("Items") or []]
        truncated = bool(response.get("LastEvaluatedKey"))
    return (
'''
new_conflicts = '''def _conflicted_events(store: SoccerStore) -> tuple[set[str], bool]:
    if not hasattr(store.ops, "query"):
        rows, truncated = _scan(store.ops)
    else:
        rows: list[dict[str, Any]] = []
        exclusive_start_key: Mapping[str, Any] | None = None
        seen_keys: set[str] = set()
        truncated = False
        while len(rows) < HEALTH_SCAN_LIMIT:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": Key("PK").eq("SETTLEMENT_CONFLICT"),
                "ConsistentRead": True,
                "Limit": max(1, HEALTH_SCAN_LIMIT - len(rows)),
            }
            if exclusive_start_key is not None:
                kwargs["ExclusiveStartKey"] = exclusive_start_key
            response = store.ops.query(**kwargs)
            next_key = response.get("LastEvaluatedKey")
            if exclusive_start_key is not None and next_key == exclusive_start_key:
                truncated = True
                break
            rows.extend(plain(row) for row in response.get("Items") or [])
            if not next_key:
                break
            if len(rows) >= HEALTH_SCAN_LIMIT:
                truncated = True
                rows = rows[:HEALTH_SCAN_LIMIT]
                break
            fingerprint = repr(plain(next_key))
            if fingerprint in seen_keys:
                truncated = True
                break
            seen_keys.add(fingerprint)
            exclusive_start_key = next_key
    return (
'''
if old_scan not in text:
    raise SystemExit('old _scan anchor not found')
if old_conflicts not in text:
    raise SystemExit('old _conflicted_events anchor not found')
text = text.replace(old_scan, new_scan, 1).replace(old_conflicts, new_conflicts, 1)
path.write_text(text)
