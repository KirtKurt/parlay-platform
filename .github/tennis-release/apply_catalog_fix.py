from __future__ import annotations

import hashlib
import sys
from pathlib import Path

PREIMAGE = {
    "src/tennis_odds_api.py": "560c1d18316d1dbe3e5e847f26027ff846ed63f361672b9b8dc605bf3e45bb42",
    "src/tennis_ingest_handler.py": "f59623e6e411020780f8a9a14d056eb42eea786753d9b300c07a2e9fafd72776",
    "tests/test_odds_discovery.py": "e48e792f9793ec62874aa5520a44b5025c0992f2d7b827a8ead027d58082e5f5",
}

POSTIMAGE = {
    "src/tennis_odds_api.py": "cf046f9bb7a056c82041a84b54cf4753c27dfb17a575888070ee8d2367305e81",
    "src/tennis_ingest_handler.py": "c8231917f700fe399fe56c2dec067677c7413d6fe75ac11ae89c47770ba55099",
    "tests/test_odds_discovery.py": "14a5ed58b27dafc133be62c5a8badeb3713fd35f48eca5a9592cf6fd34f63d26",
}

OLD_DISCOVERY = '''    def discover_active_sports(self) -> Dict[str, Any]:
        """Discover every active Tennis competition key exposed by the provider."""
        api_key = self._require_key()
        rows = self._get_json("sports/", {"apiKey": api_key})
        if not isinstance(rows, list):
            raise OddsApiError("Odds API sports discovery did not return a list")
        sports: List[TennisSport] = []
        seen = set()
        for raw in rows:
            if not isinstance(raw, dict) or not self._is_tennis_sport(raw) or raw.get("active") is False:
                continue
            key = str(raw.get("key") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            sports.append(
                TennisSport(
                    key=key,
                    title=str(raw.get("title") or key),
                    group=str(raw.get("group") or "Tennis"),
                    description=str(raw.get("description") or ""),
                    active=bool(raw.get("active", True)),
                    has_outrights=bool(raw.get("has_outrights")),
                )
            )
        sports.sort(key=lambda row: row.key)
        return {
            "ok": True,
            "source": "the_odds_api_live_sports_discovery",
            "complete": True,
            "sport_count": len(sports),
            "sport_keys": [sport.key for sport in sports],
            "sports": [sport.as_dict() for sport in sports],
            "errors": [],
        }
'''

NEW_DISCOVERY = '''    def discover_active_sports(self) -> Dict[str, Any]:
        """Discover the provider's complete non-outright Tennis catalog.

        The default ``/sports`` response contains only competitions the provider
        currently labels in season. Tennis can legitimately have a short gap
        between covered tournaments even while bookmakers are beginning to list
        the next event. The documented ``all=true`` catalog is therefore the
        authoritative key inventory. We query the free events endpoint for every
        catalog key and let actual event rows determine availability.
        """

        api_key = self._require_key()
        rows = self._get_json("sports/", {"apiKey": api_key, "all": "true"})
        if not isinstance(rows, list):
            raise OddsApiError("Odds API all-sports discovery did not return a list")
        sports: List[TennisSport] = []
        seen = set()
        for raw in rows:
            if (
                not isinstance(raw, dict)
                or not self._is_tennis_sport(raw)
                or bool(raw.get("has_outrights"))
            ):
                continue
            key = str(raw.get("key") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            sports.append(
                TennisSport(
                    key=key,
                    title=str(raw.get("title") or key),
                    group=str(raw.get("group") or "Tennis"),
                    description=str(raw.get("description") or ""),
                    active=bool(raw.get("active", False)),
                    has_outrights=False,
                )
            )
        sports.sort(key=lambda row: row.key)
        if not sports:
            raise OddsApiError("Odds API all-sports catalog contained no Tennis competition keys")
        active_count = sum(1 for sport in sports if sport.active)
        return {
            "ok": True,
            "source": "the_odds_api_all_sports_catalog_discovery",
            "complete": True,
            "sport_count": len(sports),
            "active_sport_count": active_count,
            "inactive_sport_count": len(sports) - active_count,
            "sport_keys": [sport.key for sport in sports],
            "sports": [sport.as_dict() for sport in sports],
            "all_catalog_keys_included": True,
            "non_outright_only": True,
            "events_endpoint_determines_current_availability": True,
            "errors": [],
        }
'''

NEW_DISCOVERY_TEST = '''from tennis_config import Settings
from tennis_odds_api import OddsApiClient, merge_event_roster_with_odds


class StubDiscoveryClient(OddsApiClient):
    def _get_json(self, path, params):
        assert path == "sports/"
        assert params["all"] == "true"
        return [
            {"key": "tennis_atp_alpha", "group": "Tennis", "title": "ATP Alpha", "active": True},
            {"key": "tennis_wta_beta", "group": "Tennis", "title": "WTA Beta", "active": True},
            {"key": "tennis_itf_gamma", "group": "Other", "title": "ITF Gamma", "active": False},
            {"key": "tennis_atp_next", "group": "Tennis", "title": "ATP Next", "active": False},
            {
                "key": "tennis_atp_title_winner",
                "group": "Tennis",
                "title": "ATP Winner",
                "active": True,
                "has_outrights": True,
            },
            {"key": "basketball_nba", "group": "Basketball", "title": "NBA", "active": True},
        ]


class EmptyCatalogClient(OddsApiClient):
    def _get_json(self, path, params):
        return [{"key": "basketball_nba", "group": "Basketball", "active": True}]


def test_complete_catalog_discovery_includes_active_and_inactive_non_outright_tennis_keys():
    client = StubDiscoveryClient(Settings(odds_api_key="test"))
    result = client.discover_active_sports()
    assert result["complete"] is True
    assert result["source"] == "the_odds_api_all_sports_catalog_discovery"
    assert result["all_catalog_keys_included"] is True
    assert result["active_sport_count"] == 2
    assert result["inactive_sport_count"] == 2
    assert result["sport_keys"] == [
        "tennis_atp_alpha",
        "tennis_atp_next",
        "tennis_itf_gamma",
        "tennis_wta_beta",
    ]
    assert "tennis_atp_title_winner" not in result["sport_keys"]


def test_empty_live_catalog_uses_configured_fallback_instead_of_silently_reporting_zero_keys():
    client = EmptyCatalogClient(
        Settings(
            odds_api_key="test",
            fallback_sport_keys=("tennis_atp_fallback", "tennis_wta_fallback"),
        )
    )
    result = client.discover_active_sports_with_fallback()
    assert result["complete"] is False
    assert result["source"] == "configured_fallback_after_live_discovery_failure"
    assert result["sport_keys"] == ["tennis_atp_fallback", "tennis_wta_fallback"]
    assert "no Tennis competition keys" in result["errors"][0]["error"]


def test_exact_id_left_join_keeps_roster_matches_without_odds(helpers):
    event = helpers["provider_event"]
    odds = helpers["provider_odds"]
    events = [
        event("tennis_atp_alpha", "e1", "2026-07-23T08:00:00Z", "A", "B"),
        event("tennis_atp_alpha", "e2", "2026-07-23T09:00:00Z", "C", "D"),
    ]
    odds_rows = [
        odds("tennis_atp_alpha", "e1", "2026-07-23T08:00:00Z", "A", "B"),
        odds("tennis_atp_alpha", "orphan", "2026-07-23T10:00:00Z", "E", "F"),
    ]
    merged = merge_event_roster_with_odds("tennis_atp_alpha", events, odds_rows)
    assert merged["event_roster_count"] == 2
    assert merged["events_without_odds_count"] == 1
    assert merged["odds_only_count"] == 1
    assert [row["provider_event_id"] for row in merged["events"]] == ["e1", "e2"]
    assert merged["events"][1]["bookmakers"] == []
'''


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_digest(root: Path, mapping: dict[str, str], phase: str) -> None:
    mismatches = []
    for rel, expected in mapping.items():
        path = root / rel
        actual = digest(path) if path.is_file() else None
        if actual != expected:
            mismatches.append({"path": rel, "expected": expected, "actual": actual})
    if mismatches:
        raise SystemExit(f"Tennis catalog fix {phase} mismatch: {mismatches}")


def apply(root: Path) -> None:
    require_digest(root, PREIMAGE, "preimage")

    odds_path = root / "src/tennis_odds_api.py"
    odds_text = odds_path.read_text(encoding="utf-8")
    if odds_text.count(OLD_DISCOVERY) != 1:
        raise SystemExit("Tennis discovery preimage block is not unique")
    odds_path.write_text(odds_text.replace(OLD_DISCOVERY, NEW_DISCOVERY), encoding="utf-8")

    ingest_path = root / "src/tennis_ingest_handler.py"
    ingest_text = ingest_path.read_text(encoding="utf-8")
    old_line = '"fallback_used": discovery.get("source") != "the_odds_api_live_sports_discovery",'
    new_line = '"fallback_used": not str(discovery.get("source") or "").startswith("the_odds_api_"),'
    if ingest_text.count(old_line) != 1:
        raise SystemExit("Tennis fallback marker preimage is not unique")
    ingest_path.write_text(ingest_text.replace(old_line, new_line), encoding="utf-8")

    (root / "tests/test_odds_discovery.py").write_text(NEW_DISCOVERY_TEST, encoding="utf-8")
    require_digest(root, POSTIMAGE, "postimage")
    print("PASS: applied exact Tennis all=true catalog discovery fix")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_catalog_fix.py /path/to/tennis_predictive_platform_v2")
    apply(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    main()
