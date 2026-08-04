from __future__ import annotations

from types import SimpleNamespace

import run_mlb_v8_historical_context_backfill_entrypoint as entrypoint


class _Table:
    def __init__(self, item):
        self.item = item

    def get_item(self, **_kwargs):
        return {"Item": self.item} if self.item is not None else {}


def _module(delegate):
    return SimpleNamespace(
        overlay=SimpleNamespace(
            POINTER_PK="old",
            POINTER_SK="ACTIVE",
            VERSION="old-version",
            AUTHORITY="old-authority",
        ),
        VERSION="old",
        REPORT_TYPE="old",
        _plain=lambda value: dict(value),
        _load_previous_manifest=delegate,
    )


def test_legacy_bbs_pointer_is_not_carried_into_official_context():
    called = []
    module = _module(lambda *_args: called.append(True) or ({"legacy": True}, 7))
    entrypoint.install_pointer_isolation(module)
    table = _Table(
        {
            "record_type": "mlb_v8_historical_bbs_active_manifest_v1",
            "revision": 59,
            "data": {
                "authority": "V8_HISTORICAL_BBS_SHADOW_ONLY",
                "provider": "bigballsdata_stored_confirmation_plus_official_prior_context",
            },
        }
    )

    manifest, revision = module._load_previous_manifest(table, object())

    assert manifest is None
    assert revision == 59
    assert called == []


def test_official_pointer_delegates_to_verified_manifest_loader():
    called = []

    def delegate(table, s3):
        called.append((table, s3))
        return {"official": True}, 60

    module = _module(delegate)
    entrypoint.install_pointer_isolation(module)
    table = _Table(
        {
            "record_type": entrypoint.RECORD_TYPE,
            "revision": 60,
            "data": {
                "authority": entrypoint.AUTHORITY,
                "provider": "official_mlb_plus_internal_canonical",
            },
        }
    )
    s3 = object()

    manifest, revision = module._load_previous_manifest(table, s3)

    assert manifest == {"official": True}
    assert revision == 60
    assert called == [(table, s3)]
