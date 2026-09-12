from __future__ import annotations

import io
import json
import unittest
from urllib.request import Request

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.kss1_bbd import BbdClient, BbdError, extract_match_xg  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class BbdClientTests(unittest.TestCase):
    def test_rejects_non_uuid_match_routes(self):
        client = BbdClient("test-token", opener=lambda *args, **kwargs: FakeResponse({}))
        with self.assertRaises(BbdError):
            client.match_stats("not-a-uuid")

    def test_list_matches_uses_football_slug(self):
        seen = {}

        def opener(request: Request, timeout=0):
            seen["url"] = request.full_url
            seen["auth"] = request.get_header("Authorization")
            return FakeResponse({"data": [{"id": "11111111-1111-1111-1111-111111111111"}]})

        client = BbdClient("bbs_live_test", opener=opener)
        rows = client.list_matches("epl", limit=3)
        self.assertIn("sport=football", seen["url"])
        self.assertIn("league=epl", seen["url"])
        self.assertTrue(seen["auth"].startswith("Bearer "))
        self.assertEqual(len(rows), 1)

    def test_extract_xg(self):
        extracted = extract_match_xg({"home_xg": 1.8, "away_xg": 0.9})
        self.assertTrue(extracted["has_xg"])
        self.assertEqual(extracted["xg_home"], 1.8)


if __name__ == "__main__":
    unittest.main()
