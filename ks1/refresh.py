"""Pregame lineup evidence and semantic inputs for selective KS1 refreshes."""
from datetime import timedelta
import hashlib

from ks1.features import utc
from ks1.inventory import encode

SIDES = ('home', 'away')
CONTRACT = 'KS1-refresh-v1'


def mapping(value):
    return value if isinstance(value, dict) else {}


def pregame_status(value):
    """MLB Warmup is coded P/PW even though its abstract category is Live.

    The exact tuple is documented by MLB's /api/v1/gameStatus catalogue and
    retained in production run 34653093544. Callers still enforce T-10.
    In-progress, suspended, final and unknown Live states are never admitted.
    """
    status = mapping(value)
    if status.get('abstractGameState') == 'Preview':
        return status.get('detailedState') not in ('Postponed', 'Cancelled')
    return (status.get('abstractGameState') == 'Live'
            and status.get('codedGameState') == 'P'
            and status.get('statusCode') == 'PW'
            and status.get('detailedState') == 'Warmup')


def player_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def batting_order(team):
    """Same evidence rules as the repo's mlb_statsapi_team_context._lineup.

    Do not use postgame orders, season OPS, or infer a missing confirmation.
    """
    team = mapping(team)
    order = team.get('battingOrder')
    if not isinstance(order, list) or len(order) != 9 or not all(player_id(x) for x in order) or len(set(order)) != 9:
        return None
    for slot, identity in enumerate(order, 1):
        player = mapping(mapping(team.get('players')).get('ID'+str(identity)))
        if (mapping(player.get('person')).get('id') != identity
                or str(player.get('battingOrder')) != str(slot*100)
                or mapping(player.get('gameStatus')).get('isSubstitute') is not False):
            return None
    return order


def observe(game, entry, as_of):
    """Optional feed errors fall back to the freshly captured schedule/team prior.

    Game, teams, start, capture time, Preview state and nine original batting
    slots must agree before a feed can contribute identities. Hash failures
    are corruption, not an optional provider outage, and remain fatal.
    """
    result = {'lineup_source_status': 'feed_unavailable',
              'starter_source': 'existing_MLB_schedule_probablePitcher'}
    for side in SIDES:
        probable = mapping(game['teams'][side].get('probablePitcher'))
        identity = probable.get('id')
        result.update({side+'_starter_id': str(identity) if player_id(identity) else None,
                       side+'_starter_name': probable.get('fullName') if player_id(identity) else None,
                       side+'_lineup_status': 'projected', side+'_lineup_ids': None,
                       side+'_offense_source': 'team_prior'})
    payload, receipt = (entry or {}).get('payload'), (entry or {}).get('receipt', {})
    if payload is not None:
        if hashlib.sha256(encode(payload)).hexdigest() != receipt.get('sha256'):
            raise ValueError('lineup payload hash mismatch')
        try:
            at = utc(receipt['as_of'])
            timely = (receipt.get('status') == 200 and 0 <= (utc(as_of)-at).total_seconds() <= 900
                      and at <= utc(game['gameDate'])-timedelta(minutes=10))
            data = payload.get('gameData', {})
            boxes = payload.get('liveData', {}).get('boxscore', {}).get('teams', {})
            valid = (timely and data.get('game', {}).get('pk') == game['gamePk']
                     and utc(data.get('datetime', {}).get('dateTime')) == utc(game['gameDate'])
                     and pregame_status(data.get('status'))
                     and all(boxes.get(s, {}).get('team', {}).get('id') == game['teams'][s]['team']['id'] for s in SIDES))
        except (KeyError, ValueError, TypeError, AttributeError):
            valid = False
        if valid:
            result['starter_source'] = 'existing_MLB_feed_probablePitchers'
            result['lineup_source_status'] = 'verified_pregame_feed'
            for side in SIDES:
                order = batting_order(boxes[side])
                probable = mapping(mapping(data.get('probablePitchers')).get(side))
                identity = probable.get('id')
                # The later valid feed is authoritative, including a cleared
                # probable. Never resurrect a scratched pitcher from schedule.
                result.update({side+'_starter_id': str(identity) if player_id(identity) else None,
                               side+'_starter_name': probable.get('fullName') if player_id(identity) else None,
                               side+'_lineup_status': 'confirmed' if order else 'projected',
                               side+'_lineup_ids': encode(order).decode() if order else None})
        else:
            result['lineup_source_status'] = 'unverified_feed'
    for side in SIDES:
        result[side+'_starter_status'] = 'probable' if result[side+'_starter_id'] else 'missing'
    result['lineup_status'] = 'confirmed' if all(result[s+'_lineup_status'] == 'confirmed' for s in SIDES) else 'projected'
    result['starter_feature_source'] = 'team_starter_prior'
    result['status'] = ('projected_missing_starter' if any(result[s+'_starter_id'] is None for s in SIDES)
                        else 'confirmed_lineups' if result['lineup_status'] == 'confirmed' else 'projected')
    result['prediction_status'] = result['status']
    return result


def fingerprint(row, features, needed):
    # Retrieval timestamps are audit metadata; including them would rewrite
    # every game on every poll. Quote values/status DO participate in this hash.
    values = {k: v for k, v in row.items() if k not in ('as_of', 'history_source_as_of', 'input_fingerprint')}
    return hashlib.sha256(encode({'contract': CONTRACT, 'row': values,
                                 'features': {k: features[k] for k in sorted(needed)}})).hexdigest()


def change_reason(previous, current):
    if previous is None:
        return 'new_game'
    if not previous.get('status'):
        return 'phase4_upgrade'
    if any(previous.get(s+'_starter_id') != current[s+'_starter_id'] for s in SIDES):
        return 'starter_changed'
    if any(previous.get(s+'_lineup_ids') != current[s+'_lineup_ids']
           or previous.get(s+'_lineup_status') != current[s+'_lineup_status'] for s in SIDES):
        return 'lineup_changed'
    return 'inputs_changed'
