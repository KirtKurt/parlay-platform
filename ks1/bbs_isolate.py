"""BBS identity isolate-skip helpers for KS1 daily."""

ISOLATE_SKIP_REASON = 'missing_bbs_identity'
SKIP_UNMATCHED_BBS = True
HARD_ERRORS = (
    'multiple BBS IDs map to one official game',
    'BBS result may be truncated',
    'BBS match identity schema changed',
    'non-MLB BBS event',
    'duplicate BBS match ID',
)
