"""Separate documented automatic count events from physically thrown pitches."""
import math


PLATE_APPEARANCE_EVENTS = frozenset((
    'single', 'double', 'triple', 'home_run', 'strikeout',
    'strikeout_double_play', 'walk', 'intent_walk', 'hit_by_pitch',
    'field_out', 'force_out', 'field_error', 'fielders_choice',
    'fielders_choice_out', 'grounded_into_double_play', 'double_play',
    'triple_play', 'sac_fly', 'sac_fly_double_play', 'sac_bunt',
    'sac_bunt_double_play', 'catcher_interf', 'batter_interference',
    'fan_interference', 'strike_out', 'strikeout_triple_play',
    'os_ruling_pending_primary',
))
WOBA_EXCLUDED_EVENTS = frozenset(('intent_walk', 'catcher_interf', 'sac_bunt',
                                  'sac_bunt_double_play'))
NON_PA_AT_BAT_END_EVENTS = frozenset((
    'caught_stealing_2b', 'caught_stealing_3b', 'caught_stealing_home',
    'pickoff_1b', 'pickoff_2b', 'pickoff_3b',
    'pickoff_caught_stealing_2b', 'pickoff_caught_stealing_3b',
    'pickoff_caught_stealing_home', 'other_out',
))


def is_plate_appearance(row):
    # wOBA's denominator omits some official PAs (e.g. sacrifice bunts).
    # Baserunning outs ending an inning are events, but are not batters faced.
    return str(row.get('events') or '').lower() in PLATE_APPEARANCE_EVENTS


def complete_pa_outcome(row):
    """A counted PA must not silently disappear from a wOBA/xwOBA sample."""
    event = str(row.get('events') or '').lower()
    if event == 'os_ruling_pending_primary':
        return False  # An unresolved scoring decision is not a final outcome.
    try:
        denom = float(row.get('woba_denom'))
        if event in WOBA_EXCLUDED_EVENTS:
            return denom == 0
        value = float(row.get('woba_value'))
        return denom == 1 and math.isfinite(value) and value >= 0
    except (TypeError, ValueError):
        return False


def credited_at_bat_ids(rows):
    """Return provider at-bats that can correspond to official batter PAs.

    A blank outcome remains counted: physical evidence must not depend on the
    outcome fields it was introduced to separate. Only an explicit, known
    baserunning out can prove that a pitched at-bat ended without charging the
    batter a plate appearance.
    """
    grouped = {}
    for row in rows:
        identity = (str(row.get('game_pk')), str(row.get('at_bat_number')))
        state = grouped.setdefault(identity, {'pa': False, 'non_pa': False})
        event = str(row.get('events') or '').lower()
        state['pa'] = state['pa'] or is_plate_appearance(row)
        state['non_pa'] = state['non_pa'] or event in NON_PA_AT_BAT_END_EVENTS
    return {identity for identity, state in grouped.items()
            if state['pa'] or not state['non_pa']}


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
