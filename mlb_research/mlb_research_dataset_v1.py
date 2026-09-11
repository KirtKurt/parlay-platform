"""One conditional publisher merges original and reconstructed source indexes."""
import time
from mlb_research_store_v1 import digest, now
from mlb_feature_discovery_runner_v1 import publish as publish_feature_discovery


def publish_dataset(store):
    owner = None
    for _ in range(10):
        owner = store.acquire('dataset-publication', 120)
        if owner:
            break
        time.sleep(1)
    if not owner:
        raise ValueError('dataset publisher busy')
    try:
        for _ in range(3):
            pointers = [store.get(name) for name in ('historical-input.json','original-index.json')]
            days = {}
            historical = store.load(pointers[0]['artifact']) if pointers[0] else {'rows':[]}
            for row in historical['rows']:
                days.setdefault(row['slateDateEt'],[]).append(row)
            for day,pointer in (pointers[1] or {}).get('slates',{}).items():
                rows = store.load(pointer)['rows']
                if any(r['slateDateEt'] != day or r.get('originalObservation') is not True for r in rows):
                    raise ValueError('original source shard mismatch')
                days[day] = rows
            rows = [r for day in sorted(days) for r in days[day]]
            seen = set()
            for row in rows:
                pk=str(row['officialGamePk'])
                if pk in seen or row.get('featureFingerprint') != digest(row['features']) or row.get('slateComplete') is not True:
                    raise ValueError('invalid combined research data')
                seen.add(pk)
            if pointers != [store.get(name) for name in ('historical-input.json','original-index.json')]:
                continue
            value={'rows':rows,'rowsHash':digest(rows),'updatedAtUtc':now().isoformat(),
                   'sourcePointers':pointers,'originalRows':sum(r.get('originalObservation') is True for r in rows),
                   'developmentOnly':True,'prospectiveQualificationEvidence':False}
            pointer=store.artifact('datasets',value)
            store.latest('dataset.json',{'artifact':pointer,'rows':len(rows),'rowsHash':value['rowsHash'],
                                        'originalRows':value['originalRows'],'updatedAtUtc':value['updatedAtUtc']})
            # Discovery is a fail-isolated research sidecar. Its runner catches
            # its own failures so canonical dataset publication remains valid.
            publish_feature_discovery(store, value)
            return value
        raise ValueError('source indexes changed during dataset publication')
    finally:
        store.release('dataset-publication',owner)
