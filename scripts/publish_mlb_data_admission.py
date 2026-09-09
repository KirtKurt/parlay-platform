"""Publish only data-admission reports using the existing race-safe publisher."""
import json
from pathlib import Path
from publish_mlb_audit_evidence import _publish_bundle, created

FILES = ('runtime_reports/mlb_data_admission_latest.json',
         'runtime_reports/mlb_data_admission_rows_latest.json')


def publish(root):
    root = Path(root).resolve()
    bundle = {path:(root/path).read_bytes() for path in FILES}
    if created(bundle[FILES[0]]) != created(bundle[FILES[1]]):
        raise ValueError('admission reports belong to different runs')
    return _publish_bundle(root, bundle, FILES[0], 3)


if __name__=='__main__':
    print(json.dumps(publish(Path(__file__).resolve().parents[1])))
