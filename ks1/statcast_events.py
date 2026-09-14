"""Separate documented automatic count events from physically thrown pitches."""


PLATE_APPEARANCE_EVENTS = frozenset((
    'single', 'double', 'triple', 'home_run', 'strikeout',
    'strikeout_double_play', 'walk', 'intent_walk', 'hit_by_pitch',
    'field_out', 'force_out', 'field_error', 'fielders_choice',
    'fielders_choice_out', 'grounded_into_double_play', 'double_play',
    'triple_play', 'sac_fly', 'sac_fly_double_play', 'sac_bunt',
    'sac_bunt_double_play', 'catcher_interf',
))


def is_plate_appearance(row):
    # wOBA's denominator omits some official PAs (e.g. sacrifice bunts).
    # Baserunning outs ending an inning are events, but are not batters faced.
    return str(row.get('events') or '').lower() in PLATE_APPEARANCE_EVENTS


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
