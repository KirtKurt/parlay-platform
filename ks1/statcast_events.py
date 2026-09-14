"""Separate documented automatic count events from physically thrown pitches."""


def is_thrown_pitch(row):
    """Unknown or untracked pitches still count; blank tracking alone proves nothing.

    Automatic balls/strikes have no thrown pitch in the official play feed.
    Retain contradictory rows in the count so they cannot silently qualify.
    Callers must retain these events for plate-appearance outcomes.
    """
    return not (
        str(row.get('description') or '').lower() in ('automatic_ball', 'automatic_strike')
        and row.get('pitch_type') in (None, '')
        and row.get('release_speed') in (None, '')
    )
