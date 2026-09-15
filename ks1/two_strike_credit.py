"""Require independent inherited-count and terminal-pitch proof for Rule 9.15(b).

The caller also verifies every pitch, substitution identity, source receipt,
and chronology. Final per-batter box totals remain a separate admission gate.
"""


def proven_strikeout(play, substitution, prior_pitches, terminal_row):
    from ks1.features import utc
    count = substitution.get('count', {})
    if (play['result'].get('eventType') != 'strikeout'
            or terminal_row.get('events') != 'strikeout'
            or substitution.get('isPitch') is not False
            or substitution.get('type') != 'action'
            or type(count.get('strikes')) is not int or count['strikes'] != 2
            or type(count.get('balls')) is not int or not 0 <= count['balls'] < 4
            or not prior_pitches):
        return False
    prior_count = prior_pitches[-1].get('count', {})
    terminal = play['playEvents'][-1]
    terminal_count = terminal.get('count', {})
    # Require the inherited count independently on the preceding thrown pitch.
    # A missing, boolean, or contradictory count cannot establish PA credit.
    if (any(type(prior_count.get(k)) is not int or prior_count[k] != count[k]
            for k in ('balls', 'strikes'))
            or terminal.get('isPitch') is not True or terminal.get('type') != 'pitch'
            or type(terminal_count.get('strikes')) is not int or terminal_count['strikes'] != 3
            or type(terminal_count.get('balls')) is not int or not 0 <= terminal_count['balls'] < 4
            or terminal_count['balls'] < count['balls']
            or any(type(play.get('count', {}).get(k)) is not int
                   or play['count'][k] != terminal_count[k] for k in ('balls', 'strikes'))
            or utc(terminal['endTime']) != utc(play['about']['endTime'])
            or terminal_row.get('type') != 'S'
            or isinstance(terminal_row.get('woba_value'), bool)
            or terminal_row.get('woba_value') not in (0, '0', '0.0')
            or isinstance(terminal_row.get('woba_denom'), bool)
            or terminal_row.get('woba_denom') not in (1, '1', '1.0')):
        return False
    return True
