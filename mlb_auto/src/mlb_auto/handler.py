from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .engine import american_decimal, moneyline_consensus
from .features import bootstrap_home_probability, build_feature_vector
from .ml import Model, chronological_split, promote_challenger, train_logistic
from .odds_api import OddsApiClient, merge_event_odds
from .repair import diagnose, validate_self_repair
from .schedule_controller import decide_pull
from .storage import Store

ET = ZoneInfo('America/New_York')
MIN_TRAIN = int(os.getenv('MLB_AUTO_MIN_TRAINING_EXAMPLES', '250'))
MIN_VALID = int(os.getenv('MLB_AUTO_MIN_VALIDATION_EXAMPLES', '50'))
MIN_NEW = int(os.getenv('MLB_AUTO_MIN_NEW_EXAMPLES', '25'))
MIN_OFFICIAL_PROB = float(os.getenv('MLB_AUTO_MIN_OFFICIAL_PROBABILITY', '0.58'))
LOCK_MINUTES = int(os.getenv('MLB_AUTO_LOCK_MINUTES', '45'))
PLATFORM_VERSION = 'MLB-AUTO-v1-autonomous-evolution'


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).astimezone(timezone.utc).isoformat()


def _dt(value: Any) -> datetime:
    d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _slate(value: Any) -> str:
    return _dt(value).astimezone(ET).date().isoformat()


def _response(payload: dict, status: int = 200):
    return {'statusCode': status, 'headers': {'content-type': 'application/json'}, 'body': json.dumps(payload, default=str, sort_keys=True)}


def _model_from_item(item: dict) -> Model | None:
    try:
        raw = item.get('model_json') or item.get('modelJson')
        return Model.loads(raw) if raw else None
    except Exception:
        return None


def _pick_price(event: dict, team: str) -> tuple[float | None, str | None]:
    priority = ['fanduel', 'draftkings', 'betmgm', 'caesars', 'fanatics', 'betrivers', 'bovada']
    books = event.get('bookmakers') or []
    by_key = {str(x.get('key')): x for x in books}
    ordered = [by_key[k] for k in priority if k in by_key] + [x for x in books if str(x.get('key')) not in priority]
    for book in ordered:
        market = next((m for m in book.get('markets') or [] if m.get('key') == 'h2h'), None)
        if not market:
            continue
        outcome = next((o for o in market.get('outcomes') or [] if str(o.get('name')) == str(team)), None)
        if outcome and outcome.get('price') is not None:
            return float(outcome['price']), str(book.get('key'))
    return None, None


def _prediction_fingerprint(row: dict) -> str:
    stable = {k: row.get(k) for k in ('event_id', 'commence_time', 'predicted_winner', 'home_probability', 'model_id', 'source_pull_at')}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


def _latest_home_history(store: Store, slate: str, event_id: str) -> list[float]:
    rows = store.query_snapshots(slate, event_id=event_id, limit=500)
    vals = []
    for row in rows:
        try:
            vals.append(float((row.get('features') or {}).get('market_home_probability')))
        except Exception:
            pass
    return vals


def _merge_detail(client: OddsApiClient, event: dict) -> tuple[dict, list[str], list[str]]:
    event_id = str(event.get('id') or '')
    errors: list[str] = []
    try:
        discovered = client.event_markets(event_id).data
        keys = client.useful_markets(discovered)
    except Exception as exc:
        keys = []
        errors.append(f'market_discovery:{type(exc).__name__}')
    additional = [k for k in keys if k not in ('h2h', 'spreads', 'totals')]
    if not additional:
        return event, keys, errors
    detail = event
    for i in range(0, len(additional), 12):
        try:
            payload = client.event_odds(event_id, additional[i:i + 12]).data
            if isinstance(payload, dict):
                detail = merge_event_odds(detail, payload)
        except Exception as exc:
            errors.append(f'event_odds:{type(exc).__name__}:{i}')
    return detail, keys, errors


def ingest(*, force_reason: str | None = None) -> dict:
    store = Store()
    client = OddsApiClient()
    now = _now()
    state = store.get_state('controller')
    events_response = client.events()
    events = [x for x in (events_response.data or []) if x.get('sport_key') in (None, 'baseball_mlb')]
    decision = decide_pull(
        now=now, events=events, last_pull_at=state.get('last_pull_at'),
        volatility=float(state.get('market_volatility') or 0),
        missing_market_fraction=float(state.get('missing_market_fraction') or 0),
        recent_signal_change=float(state.get('recent_signal_change') or 0),
        new_event_fraction=float(state.get('new_event_fraction') or 0), force_reason=force_reason,
    )
    if not decision.should_pull:
        store.put_state('controller', {**state, 'heartbeat_at': _iso(now), 'expected_interval_minutes': decision.next_interval_minutes, 'next_due_at': decision.next_due_at_utc, 'information_gain_score': decision.information_gain_score})
        return {'ok': True, 'action': 'HEARTBEAT_ONLY', 'decision': decision.__dict__, 'event_count': len(events)}

    featured_response = client.featured_odds()
    featured = [x for x in (featured_response.data or []) if x.get('sport_key') == 'baseball_mlb']
    event_map = {str(x.get('id')): x for x in events}
    predictions = []
    discovery_errors = []
    market_key_total = missing = 0
    volatility_values = []
    champion_item = store.get_model('CHAMPION')
    champion = _model_from_item(champion_item)
    champion_id = champion_item.get('model_id') if champion else None

    for base in featured:
        event_id = str(base.get('id') or '')
        if not event_id or event_id not in event_map:
            continue
        start = _dt(base.get('commence_time'))
        if start <= now:
            continue
        detail, discovered_keys, errors = _merge_detail(client, base)
        discovery_errors.extend([f'{event_id}:{e}' for e in errors])
        market_key_total += len(discovered_keys)
        if not discovered_keys:
            missing += 1
        slate = _slate(start)
        history = _latest_home_history(store, slate, event_id)
        fair = moneyline_consensus(base)
        history_plus = history + [float(fair.get('home') or .5)]
        features = build_feature_vector(event=base, detail=detail, home_probability_history=history_plus, pulled_at=_iso(now), pull_count=len(history_plus))
        volatility_values.append(float(features.get('market_volatility') or 0))
        bootstrap = bootstrap_home_probability(features)
        home_prob = champion.predict(features) if champion else bootstrap
        away_prob = 1.0 - home_prob
        home, away = str(base.get('home_team') or ''), str(base.get('away_team') or '')
        winner = home if home_prob >= .5 else away
        win_prob = max(home_prob, away_prob)
        price, price_book = _pick_price(base, winner)
        dec = american_decimal(price) if price is not None else None
        ev = (win_prob * dec - 1.0) if dec else None
        official = bool(champion and win_prob >= MIN_OFFICIAL_PROB and ev is not None and ev >= 0)
        cutoff = start - timedelta(minutes=LOCK_MINUTES)
        row = {
            'sport': 'mlb_auto', 'provider_sport_key': 'baseball_mlb', 'slate_date': slate,
            'event_id': event_id, 'home_team': home, 'away_team': away, 'commence_time': _iso(start),
            'source_pull_at': _iso(now), 'lock_cutoff_at': _iso(cutoff), 'features': features,
            'home_probability': home_prob, 'away_probability': away_prob,
            'predicted_winner': winner, 'win_probability': win_prob,
            'american_odds': price, 'price_book': price_book, 'expected_value': ev,
            'prediction_mode': 'ML_CHAMPION' if champion else 'MARKET_BOOTSTRAP', 'model_id': champion_id,
            'official_pick': official, 'promotion_status': 'READY' if official else ('SHADOW_BOOTSTRAP' if not champion else 'MODEL_PASS'),
            'pull_count_for_event': len(history_plus), 'discovered_market_keys': discovered_keys,
            'market_discovery_errors': errors, 'platform_version': PLATFORM_VERSION,
            'pre_lock_cutoff': now <= cutoff,
        }
        row['prediction_fingerprint'] = _prediction_fingerprint(row)
        store.put_snapshot(slate, _iso(now), {
            'event_id': event_id, 'source_pull_at': _iso(now), 'event': detail,
            'features': features, 'prediction': row,
            'prediction_fingerprint': row['prediction_fingerprint'],
        })
        store.put_prediction(slate, event_id, row)
        predictions.append(row)

    fraction_missing = (missing / len(featured)) if featured else 0.0
    next_state = {
        'last_pull_at': _iso(now), 'heartbeat_at': _iso(now), 'expected_interval_minutes': decision.next_interval_minutes,
        'next_due_at': decision.next_due_at_utc, 'information_gain_score': decision.information_gain_score,
        'event_count': len(events), 'featured_event_count': len(featured), 'prediction_count': len(predictions),
        'market_key_total': market_key_total, 'missing_market_fraction': fraction_missing,
        'market_volatility': max(volatility_values) if volatility_values else 0.0,
        'discovery_error_count': len(discovery_errors), 'last_ingest_ok': True,
        'prediction_mode': 'ML_CHAMPION' if champion else 'MARKET_BOOTSTRAP',
    }
    store.put_state('controller', next_state)
    store.archive_json(f'mlb_auto/raw/{now:%Y/%m/%d/%H%M%S}.json', {'events': events, 'featured': featured, 'predictions': predictions, 'errors': discovery_errors})
    return {'ok': True, 'action': 'INGEST', 'decision': decision.__dict__, 'event_count': len(events), 'prediction_count': len(predictions), 'champion_model_id': champion_id, 'errors': discovery_errors[:20]}


def _precutoff_prediction(store: Store, slate: str, event_id: str, cutoff: datetime) -> dict | None:
    candidates = []
    for snapshot in store.query_snapshots(slate, event_id=event_id, limit=500):
        prediction = snapshot.get('prediction') or {}
        source = prediction.get('source_pull_at') or snapshot.get('source_pull_at')
        try:
            source_dt = _dt(source)
        except Exception:
            continue
        if prediction and source_dt <= cutoff:
            candidates.append((source_dt, prediction))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def lock_due_games() -> dict:
    store = Store()
    now = _now()
    slates = {(now.astimezone(ET).date() + timedelta(days=d)).isoformat() for d in (-1, 0, 1)}
    created = skipped = 0
    errors = []
    for slate in sorted(slates):
        existing = {str(x.get('SK')) for x in store.query_locks(slate)}
        for current in store.query_predictions(slate):
            event_id = str(current.get('event_id') or current.get('SK') or '')
            if not event_id or event_id in existing:
                continue
            try:
                start = _dt(current['commence_time'])
            except Exception:
                skipped += 1
                continue
            cutoff = start - timedelta(minutes=LOCK_MINUTES)
            if now < cutoff or now >= start:
                continue
            prediction = _precutoff_prediction(store, slate, event_id, cutoff)
            if not prediction:
                skipped += 1
                errors.append(f'{event_id}:NO_PERSISTED_PRE_CUTOFF_PREDICTION')
                continue
            source = _dt(prediction['source_pull_at'])
            if source > cutoff:
                errors.append(f'{event_id}:POST_CUTOFF_SOURCE_REJECTED')
                continue
            lock = dict(prediction)
            lock.update({
                'locked_at': _iso(now), 'lock_minutes': LOCK_MINUTES,
                'lock_cutoff_at': _iso(cutoff), 'source_before_or_at_cutoff': True,
                'immutable': True, 'training_eligible': True,
            })
            try:
                store.put_lock_once(slate, event_id, lock)
                created += 1
            except Exception as exc:
                if 'ConditionalCheckFailed' in str(exc):
                    skipped += 1
                else:
                    errors.append(f'{event_id}:{type(exc).__name__}')
    return {'ok': not errors, 'created': created, 'skipped': skipped, 'errors': errors}


def settle() -> dict:
    store = Store()
    rows = OddsApiClient().scores(days_from=3).data or []
    settled = examined = 0
    for game in rows:
        if not game.get('completed'):
            continue
        event_id = str(game.get('id') or '')
        if not event_id:
            continue
        slate = _slate(game.get('commence_time'))
        lock = {str(x.get('SK')): x for x in store.query_locks(slate)}.get(event_id)
        if not lock or not lock.get('training_eligible') or not lock.get('source_before_or_at_cutoff'):
            continue
        examined += 1
        scores = {str(x.get('name')): int(x.get('score') or 0) for x in game.get('scores') or []}
        home, away = str(lock.get('home_team') or ''), str(lock.get('away_team') or '')
        if home not in scores or away not in scores or scores[home] == scores[away]:
            continue
        label = 1 if scores[home] > scores[away] else 0
        store.put_training_example(slate, event_id, {
            'sport': 'mlb_auto', 'provider_sport_key': 'baseball_mlb', 'event_id': event_id,
            'slate_date': slate, 'commence_time': lock.get('commence_time'), 'settled_at': _iso(),
            'home_team': home, 'away_team': away, 'home_score': scores[home], 'away_score': scores[away],
            'label_home_win': label, 'features': lock.get('features') or {}, 'locked_at': lock.get('locked_at'),
            'lock_cutoff_at': lock.get('lock_cutoff_at'), 'prediction_fingerprint': lock.get('prediction_fingerprint'),
            'source_pull_at': lock.get('source_pull_at'), 'official_pick': bool(lock.get('official_pick')),
            'predicted_winner': lock.get('predicted_winner'),
        })
        settled += 1
    current = store.get_state('controller')
    store.put_state('controller', {**current, 'last_settlement_at': _iso(), 'last_settlement_count': settled})
    return {'ok': True, 'completed_scores_seen': len(rows), 'eligible_locks_examined': examined, 'settled': settled}


def train() -> dict:
    store = Store()
    examples = store.query_training_examples(limit=5000)
    examples.sort(key=lambda x: (str(x.get('commence_time') or ''), str(x.get('SK') or '')))
    if len(examples) < MIN_TRAIN:
        return {'ok': True, 'trained': False, 'reason': 'INSUFFICIENT_EXAMPLES', 'count': len(examples), 'minimum': MIN_TRAIN}
    rows = [x.get('features') or {} for x in examples]
    labels = [int(x.get('label_home_win')) for x in examples]
    tr, yl, vr, vl = chronological_split(rows, labels, .2)
    if len(vr) < MIN_VALID:
        return {'ok': True, 'trained': False, 'reason': 'INSUFFICIENT_VALIDATION', 'count': len(examples), 'validation_count': len(vr), 'minimum_validation': MIN_VALID}
    challenger = train_logistic(tr, yl)
    incumbent_item = store.get_model('CHAMPION')
    incumbent = _model_from_item(incumbent_item)
    gate = promote_challenger(challenger=challenger, incumbent=incumbent, validation_rows=vr, validation_labels=vl)
    model_id = f'MLB_AUTO_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{hashlib.sha256(challenger.dumps().encode()).hexdigest()[:12]}'
    artifact = {'model_id': model_id, 'created_at': _iso(), 'model_json': challenger.dumps(), 'training_count': len(tr), 'validation_count': len(vr), 'gate': gate, 'sport': 'mlb_auto'}
    store.put_model(f'CHALLENGER#{model_id}', artifact)
    promoted = bool(gate.get('promote'))
    if promoted:
        store.put_model('CHAMPION', artifact)
    state = store.get_state('controller')
    store.put_state('controller', {**state, 'last_training_at': _iso(), 'last_training_count': len(examples), 'champion_model_id': model_id if promoted else incumbent_item.get('model_id'), 'last_training_gate': gate})
    return {'ok': True, 'trained': True, 'model_id': model_id, 'promoted': promoted, 'gate': gate, 'examples': len(examples)}


def repair() -> dict:
    store = Store()
    state = store.get_state('controller')
    now = _now()
    try:
        last = _dt(state.get('last_pull_at')) if state.get('last_pull_at') else None
        minutes = ((now-last).total_seconds()/60) if last else 10_000
    except Exception:
        minutes = 10_000
    examples = store.query_training_examples(limit=5000)
    champion_item = store.get_model('CHAMPION')
    repair_state = {**state, 'minutes_since_last_pull': minutes,
                    'new_training_examples': max(0,len(examples)-int(state.get('last_training_count') or 0)),
                    'min_new_examples': MIN_NEW, 'champion_corrupt': bool(champion_item and not _model_from_item(champion_item))}
    results=[]
    for action in diagnose(repair_state,now):
        validate_self_repair(action)
        if action.action in ('FORCE_INGEST','REDISCOVER_EVENT_MARKETS'):
            results.append({'action':action.action,'result':ingest(force_reason=action.reason)})
        elif action.action=='RUN_SETTLEMENT':
            results.append({'action':action.action,'result':settle()})
        elif action.action in ('RUN_TRAINING','RETRY_TRAINING_WITH_LAST_GOOD_DATA'):
            results.append({'action':action.action,'result':train()})
        elif action.action=='QUARANTINE_CHAMPION_AND_FALLBACK':
            store.put_model('QUARANTINED_CHAMPION',{**champion_item,'quarantined_at':_iso(),'reason':action.reason})
            store.models.delete_item(Key={'PK':'MLB_AUTO#MODEL_REGISTRY','SK':'CHAMPION'})
            results.append({'action':action.action,'result':{'ok':True,'fallback':'MARKET_BOOTSTRAP'}})
    store.put_state('repair',{'last_repair_at':_iso(),'action_count':len(results),'actions':[x['action'] for x in results]})
    return {'ok':True,'actions':results}


def status() -> dict:
    store=Store()
    state=store.get_state('controller')
    repair_state=store.get_state('repair')
    champion=store.get_model('CHAMPION')
    examples=store.query_training_examples(limit=5000)
    today=datetime.now(ET).date().isoformat()
    predictions=store.query_predictions(today)
    locks=store.query_locks(today)
    return {
        'ok':True,'sport':'mlb_auto','provider_sport_key':'baseball_mlb','platform_version':PLATFORM_VERSION,
        'autonomous':True,'cost_throttling':False,'heartbeat_minutes':5,'lock_check_minutes':1,
        'prediction_mode':'ML_CHAMPION' if _model_from_item(champion) else 'MARKET_BOOTSTRAP',
        'champion_model_id':champion.get('model_id'),'training_examples':len(examples),
        'minimum_training_examples':MIN_TRAIN,'today_prediction_count':len(predictions),
        'today_official_pick_count':sum(1 for x in predictions if x.get('official_pick')),
        'today_locked_count':len(locks),'controller':state,'repair':repair_state,
        'isolation_policy':'protected stacks are fingerprinted externally; runtime has no cross-stack identifiers',
    }


def handler(event,context):
    event=event or {}
    action=str(event.get('action') or event.get('detail-type') or '').upper()
    if event.get('requestContext'):
        path=str(event.get('rawPath') or '')
        if path.endswith('/status'):
            return _response(status())
        if path.endswith('/predictions'):
            date=((event.get('queryStringParameters') or {}).get('date') or datetime.now(ET).date().isoformat())
            rows=Store().query_predictions(date)
            return _response({'ok':True,'sport':'mlb_auto','slate_date':date,'count':len(rows),
                              'official_pick_count':sum(1 for x in rows if x.get('official_pick')),'predictions':rows})
        return _response({'ok':False,'error':'NOT_FOUND'},404)
    if action in ('INGEST','HEARTBEAT','MLB_AUTO_HEARTBEAT'):
        return ingest()
    if action in ('LOCK','MLB_AUTO_LOCK'):
        return lock_due_games()
    if action in ('SETTLE','MLB_AUTO_SETTLE'):
        return settle()
    if action in ('TRAIN','MLB_AUTO_TRAIN'):
        return train()
    if action in ('REPAIR','MLB_AUTO_REPAIR'):
        return repair()
    if action=='STATUS':
        return status()
    return {'ok':False,'error':'UNKNOWN_ACTION','action':action}
