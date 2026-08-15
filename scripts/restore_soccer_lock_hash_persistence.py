from __future__ import annotations

from pathlib import Path


INFERENCE = Path("soccer_auto/inference.py")
TEST = Path("tests/soccer_auto/test_certified_lock_cohorts.py")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one source match, found {count}")
    return text.replace(old, new, 1)


def patch_inference() -> None:
    text = INFERENCE.read_text()
    text = replace_once(
        text,
        "from .storage import (\n    COVERAGE_CERTIFICATE_VERSION,\n    SoccerStore,\n    now_utc,\n    plain,\n)",
        "from .storage import (\n    COVERAGE_CERTIFICATE_VERSION,\n    SoccerStore,\n    ddb_safe,\n    now_utc,\n    plain,\n)",
        label="inference import",
    )
    text = replace_once(
        text,
        "    exclusion_reasons = []\n    if int(features[\"book_count\"]) < MIN_BOOKMAKERS:",
        "    # Bind the immutable feature hash to the exact numeric representation\n"
        "    # that DynamoDB persists. ddb_safe rounds floats to 12 decimals;\n"
        "    # hashing full-precision floats before that conversion makes a valid\n"
        "    # lock fail provenance validation after a storage round trip.\n"
        "    features = plain(ddb_safe(features))\n"
        "    exclusion_reasons = []\n"
        "    if int(features[\"book_count\"]) < MIN_BOOKMAKERS:",
        label="feature canonicalization",
    )
    INFERENCE.write_text(text)


def patch_test() -> None:
    text = TEST.read_text()
    text = replace_once(
        text,
        "    coverage_expected_batch_digests,\n    coverage_plan_digest,\n)",
        "    coverage_expected_batch_digests,\n    coverage_plan_digest,\n    ddb_safe,\n    plain,\n)",
        label="test imports",
    )
    text = replace_once(
        text,
        "        self.assertTrue(live_lock_coverage_provenance_valid(final_lock))\n\n    def test_same_plan_certificates_fall_back_to_exact_latest_baseline(self):",
        "        self.assertTrue(live_lock_coverage_provenance_valid(final_lock))\n"
        "        persisted_round_trip = plain(ddb_safe(final_lock))\n"
        "        self.assertTrue(\n"
        "            live_lock_coverage_provenance_valid(persisted_round_trip)\n"
        "        )\n"
        "        self.assertEqual(\n"
        "            final_lock[\"feature_hash\"],\n"
        "            persisted_round_trip[\"feature_hash\"],\n"
        "        )\n\n"
        "    def test_same_plan_certificates_fall_back_to_exact_latest_baseline(self):",
        label="round-trip regression test",
    )
    TEST.write_text(text)


if __name__ == "__main__":
    patch_inference()
    patch_test()
