from pathlib import Path

path = Path('work/tennis_lock_handler.py')
s = path.read_text()

if 'v5.1-tminus45-no-rescore-cached-reads' in s:
    print('handler already patched')
    raise SystemExit(0)

s = s.replace(
    'VERSION = "INQSI-TENNIS-PER-MATCH-LOCK-v5-tminus45-no-rescore"',
    'VERSION = "INQSI-TENNIS-PER-MATCH-LOCK-v5.1-tminus45-no-rescore-cached-reads"',
)

old = '''        self.archive = archive\n\n    def _pulls_and_manifest'''
new = '''        self.archive = archive\n        # Per-invocation caches eliminate O(matches) repeated DynamoDB reads.\n        # One scheduled run checks many matches from the same slate; loading the\n        # lock partition once preserves the immutable T-45 semantics.\n        self._snapshot_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}\n        self._pregame_cache: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}\n\n    def _prepare_snapshot_cache(self, slate_date: str) -> None:\n        if slate_date in self._snapshot_cache:\n            return\n        rows = self.store.query_items(\n            "snapshots", lock_pk(slate_date), limit=10000, ascending=True\n        )\n        self._snapshot_cache[slate_date] = {\n            str(row.get("SK") or ""): copy.deepcopy(row) for row in rows if row.get("SK")\n        }\n\n    def _snapshot_item(self, slate_date: str, sk: str) -> Optional[Dict[str, Any]]:\n        if slate_date in self._snapshot_cache:\n            item = self._snapshot_cache[slate_date].get(str(sk))\n            return copy.deepcopy(item) if item is not None else None\n        return self.store.get_item("snapshots", lock_pk(slate_date), sk)\n\n    def _remember_snapshot(self, slate_date: str, item: Optional[Dict[str, Any]]) -> None:\n        if not item or slate_date not in self._snapshot_cache:\n            return\n        sk = str(item.get("SK") or "")\n        if sk:\n            self._snapshot_cache[slate_date][sk] = copy.deepcopy(item)\n\n    def _prepare_pregame_cache(self, slate_date: str) -> None:\n        if slate_date in self._pregame_cache:\n            return\n        rows = self.store.query_items(\n            "predictions", pregame_prediction_pk(slate_date), limit=10000, ascending=True\n        )\n        indexed: Dict[str, List[Dict[str, Any]]] = {}\n        for item in rows:\n            if item.get("record_type") != PREGAME_RECORD_TYPE or item.get("sport") != "tennis":\n                continue\n            identity = str(item.get("match_identity") or "")\n            if not identity:\n                continue\n            data = copy.deepcopy(item.get("data") or {})\n            if not isinstance(data, dict):\n                continue\n            if str(data.get("matchIdentity") or data.get("gameIdentity") or "") != identity:\n                continue\n            if canonical_fingerprint(data) != str(item.get("prediction_payload_fingerprint") or ""):\n                continue\n            data["_pregame_pk"] = item.get("PK")\n            data["_pregame_sk"] = item.get("SK")\n            data["_pregame_fingerprint"] = item.get("prediction_payload_fingerprint")\n            indexed.setdefault(identity, []).append(data)\n        for values in indexed.values():\n            values.sort(key=_candidate_sort_key)\n        self._pregame_cache[slate_date] = indexed\n\n    def _pulls_and_manifest'''
assert old in s, 'constructor anchor missing'
s = s.replace(old, new, 1)

old = '''    def _get_lock(self, slate_date: str, identity: str) -> Optional[Dict[str, Any]]:\n        return self.store.get_item("snapshots", lock_pk(slate_date), canonical_lock_sk(identity))\n\n    def _get_outcome(self, slate_date: str, identity: str) -> Optional[Dict[str, Any]]:\n        return self.store.get_item("snapshots", lock_pk(slate_date), lock_outcome_sk(identity))\n\n    def _pregame_rows(self, slate_date: str, match: Dict[str, Any]) -> List[Dict[str, Any]]:\n        identity = event_identity(match)\n        rows = self.store.query_items(\n            "predictions", pregame_prediction_pk(slate_date), limit=10000, ascending=True\n        )\n        selected: List[Dict[str, Any]] = []\n        for item in rows:\n            if item.get("record_type") != PREGAME_RECORD_TYPE or item.get("sport") != "tennis":\n                continue\n            if str(item.get("match_identity") or "") != identity:\n                continue\n            data = copy.deepcopy(item.get("data") or {})\n            if not isinstance(data, dict):\n                continue\n            if str(data.get("matchIdentity") or data.get("gameIdentity") or "") != identity:\n                continue\n            if canonical_fingerprint(data) != str(item.get("prediction_payload_fingerprint") or ""):\n                continue\n            data["_pregame_pk"] = item.get("PK")\n            data["_pregame_sk"] = item.get("SK")\n            data["_pregame_fingerprint"] = item.get("prediction_payload_fingerprint")\n            selected.append(data)\n        selected.sort(key=_candidate_sort_key)\n        return selected\n'''
new = '''    def _get_lock(self, slate_date: str, identity: str) -> Optional[Dict[str, Any]]:\n        return self._snapshot_item(slate_date, canonical_lock_sk(identity))\n\n    def _get_outcome(self, slate_date: str, identity: str) -> Optional[Dict[str, Any]]:\n        return self._snapshot_item(slate_date, lock_outcome_sk(identity))\n\n    def _pregame_rows(self, slate_date: str, match: Dict[str, Any]) -> List[Dict[str, Any]]:\n        self._prepare_pregame_cache(slate_date)\n        identity = event_identity(match)\n        return copy.deepcopy(self._pregame_cache.get(slate_date, {}).get(identity, []))\n'''
assert old in s, 'pregame anchor missing'
s = s.replace(old, new, 1)

s = s.replace(
    'existing = self.store.get_item("snapshots", lock_pk(slate_date), readiness_sk(identity, minutes))',
    'existing = self._snapshot_item(slate_date, readiness_sk(identity, minutes))',
)
s = s.replace(
    'existing = self.store.get_item("snapshots", lock_pk(slate_date), key)',
    'existing = self._snapshot_item(slate_date, key)',
)

for marker in ('    def _write_outcome', '    def _write_release_assessment', '    def run_match'):
    old_return = '        return self.store.put_item("snapshots", item, if_absent=True).get("item") or item\n\n' + marker
    new_return = '        stored = self.store.put_item("snapshots", item, if_absent=True).get("item") or item\n        self._remember_snapshot(slate_date, stored)\n        return stored\n\n' + marker
    assert old_return in s, f'write return anchor missing before {marker}'
    s = s.replace(old_return, new_return, 1)

old = '''        write = self.store.put_item("snapshots", item, if_absent=True)\n        stored = write.get("item") or item\n'''
new = '''        write = self.store.put_item("snapshots", item, if_absent=True)\n        stored = write.get("item") or item\n        self._remember_snapshot(slate_date, stored)\n'''
assert old in s, 'lock write anchor missing'
s = s.replace(old, new, 1)

old = '''        results = [\n            self.run_match(slate_date, match, now=current, force=force) for match in matches\n        ]\n'''
new = '''        # Cache the lock partition once. Prediction rows are loaded lazily at most\n        # once if any match actually reaches a readiness/release/lock checkpoint.\n        self._prepare_snapshot_cache(slate_date)\n        results = [\n            self.run_match(slate_date, match, now=current, force=force) for match in matches\n        ]\n'''
assert old in s, 'run_lock anchor missing'
s = s.replace(old, new, 1)

old = '''        pulls, matches, manifest = self._pulls_and_manifest(slate_date)\n        rows: List[Dict[str, Any]] = []\n        for match in matches:\n'''
new = '''        pulls, matches, manifest = self._pulls_and_manifest(slate_date)\n        self._prepare_snapshot_cache(slate_date)\n        rows: List[Dict[str, Any]] = []\n        for match in matches:\n'''
assert old in s, 'status anchor missing'
s = s.replace(old, new, 1)

s = s.replace(
    '''                self.store.get_item(\n                    "snapshots", lock_pk(slate_date), readiness_sk(identity, minutes)\n                )''',
    '''                self._snapshot_item(slate_date, readiness_sk(identity, minutes))''',
)
s = s.replace(
    '''                self.store.get_item(\n                    "snapshots", lock_pk(slate_date), release_sk(identity, minutes)\n                )''',
    '''                self._snapshot_item(slate_date, release_sk(identity, minutes))''',
)

path.write_text(s)
print('patched', path, 'bytes=', len(s))
