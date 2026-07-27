"""Non-production MLB V8 real-time shadow policy.

This policy lowers the evaluation target from 80% to 72% for V8 shadow
qualification while preserving production isolation. It does not grant live
production authority, enable wagering, or bypass prospective grading.
"""
from __future__ import annotations

VERSION = "MLB-V8-SHADOW-REALTIME-POLICY-v1-72pct"
TARGET_ACCURACY = 0.72
TARGET_ACCURACY_PCT = 72.0
AUTHORITY = "SHADOW_ONLY"
PRODUCTION_AUTHORITY_CHANGED = False
AUTOMATIC_WAGER_ALLOWED = False
HISTORICAL_LEARNING_ENABLED = True
LIVE_SHADOW_EVALUATION_ENABLED = True
REQUIRE_IMMUTABLE_PREGAME_LOCK = True
REQUIRE_SETTLED_RESULT_FOR_GRADING = True
EXCLUDE_PUSH_VOID_FROM_ACCURACY = True


def status() -> dict:
    return {
        "version": VERSION,
        "targetAccuracy": TARGET_ACCURACY,
        "targetAccuracyPct": TARGET_ACCURACY_PCT,
        "authority": AUTHORITY,
        "productionAuthorityChanged": PRODUCTION_AUTHORITY_CHANGED,
        "automaticWagerAllowed": AUTOMATIC_WAGER_ALLOWED,
        "historicalLearningEnabled": HISTORICAL_LEARNING_ENABLED,
        "liveShadowEvaluationEnabled": LIVE_SHADOW_EVALUATION_ENABLED,
        "requireImmutablePregameLock": REQUIRE_IMMUTABLE_PREGAME_LOCK,
        "requireSettledResultForGrading": REQUIRE_SETTLED_RESULT_FOR_GRADING,
        "excludePushVoidFromAccuracy": EXCLUDE_PUSH_VOID_FROM_ACCURACY,
    }
