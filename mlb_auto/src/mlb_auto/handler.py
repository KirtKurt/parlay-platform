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
from .team_form import TEAM_FORM_SOURCE, TEAM_FORM_VERSION, build_team_form_context

ET = ZoneInfo('America/New_York')
MIN_TRAIN = int(os.getenv('MLB_AUTO_MIN_TRAINING_EXAMPLES', '250'))
MIN_VALID = int(os.getenv('MLB_AUTO_MIN_VALIDATION_EXAMPLES', '50'))
MIN_NEW = int(os.getenv('MLB_AUTO_MIN_NEW_EXAMPLES', '25'))
MIN_OFFICIAL_PROB = float(os.getenv('MLB_AUTO_MIN_OFFICIAL_PROBABILITY', '0.58'))
LOCK_MINUTES = int(os.getenv('MLB_AUTO_LOCK_MINUTES', '10'))
LATE_LOCK_RECOVERY_WINDOW = timedelta(hours=24)
LATE_LOCK_RECOVERY_REASON = 'MISSED_SCHEDULED_LOCK_RECOVERED_FROM_PRE_CUTOFF_SNAPSHOT'
PLATFORM_VERSION = 'MLB-AUTO-v1.4-t10-authority-audit'
PUBLIC_PREDICTION_AUTHORITY_VERSION = 'MLB_AUTO_IMMUTABLE_PUBLIC_AUTHORITY_V2'
AUDIT_VERSION = 'MLB_AUTO_DUAL_LEDGER_AUDIT_V1'
OFFICIAL_PICK_POLICY = f'CHAMPION_CONFIDENCE_AT_OR_BEFORE_T{LOCK_MINUTES}'
CANONICAL_LOCK_HASH_VERSION = 'MLB_AUTO_CANONICAL_LOCK_SHA256_V1'


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


def _current_policy_model_from_item(item: dict) -> Model | None:
    """Reject champions trained for an obsolete lock horizon."""
    try:
        if int(item.get('training_lock_minutes')) != LOCK_MINUTES:
            return None
    except Exception:
        return None
    return _model_from_item(item)


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


def _canonical_lock_hash(row: dict) -> str:
    payload = {
        str(key): value
        for key, value in row.items()
        if str(key) not in ('PK', 'SK', 'canonical_lock_hash')
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode()
    ).hexdigest()


def _event_id(row: dict) -> str:
    return str(row.get('event_id') or row.get('SK') or '')


def _lock_minutes(row: dict) -> int:
    try:
        return int(row.get('lock_minutes') or LOCK_MINUTES)
    except Exception:
        return LOCK_MINUTES


def _lock_authority(row: dict) -> str:
    return f'IMMUTABLE_T{_lock_minutes(row)}_LOCK'


def _current_policy_training_examples(store: Store) -> tuple[list[dict], int]:
    from .training_guard import _authority_minutes

    all_examples = store.query_training_examples(limit=5000)
    return [row for row in all_examples if _authority_minutes(row) == LOCK_MINUTES], len(all_examples)


def canonical_prediction_rows(store: Store, slate: str) -> list[dict]:
    """Return the public slate with immutable locks taking precedence over mutable rows."""
    mutable = {_event_id(row): dict(row) for row in store.query_predictions(slate) if _event_id(row)}
    locks = {_event_id(row): dict(row) for row in store.query_locks(slate) if _event_id(row)}
    rows = []
    for event_id in set(mutable) | set(locks):
        if event_id in locks:
            published = mutable.get(event_id, {})
            row = {**published, **locks[event_id]}
            row.update({
                'event_id': event_id,
                'locked': True,
                'public_record_frozen': True,
                'prediction_authority': _lock_authority(locks[event_id]),
                'public_authority_version': PUBLIC_PREDICTION_AUTHORITY_VERSION,
                'published_record_present': bool(published),
                'published_predicted_winner': published.get('predicted_winner'),
                'published_official_pick': bool(published.get('official_pick')) if published else None,
                'published_source_pull_at': published.get('source_pull_at'),
                'published_lock_direction_changed': bool(
                    published and published.get('predicted_winner') != locks[event_id].get('predicted_winner')
                ),
            })
        else:
            row = dict(mutable[event_id])
            row.update({
                'event_id': event_id,
                'locked': False,
                'public_record_frozen': False,
                'prediction_authority': 'MUTABLE_PRELOCK_FORECAST',
                'public_authority_version': PUBLIC_PREDICTION_AUTHORITY_VERSION,
            })
        rows.append(row)
    return sorted(rows, key=lambda row: (str(row.get('commence_time') or ''), _event_id(row)))


def _lock_validation(lock: dict) -> tuple[bool, list[str]]:
    reasons = []
    if lock.get('immutable') is not True:
        reasons.append('NOT_IMMUTABLE')
    if lock.get('training_eligible') is not True:
        reasons.append('NOT_TRAINING_ELIGIBLE')
    if lock.get('source_before_or_at_cutoff') is not True:
        reasons.append('SOURCE_AUTHORITY_NOT_CONFIRMED')
    home, away = str(lock.get('home_team') or ''), str(lock.get('away_team') or '')
    if str(lock.get('predicted_winner') or '') not in (home, away):
        reasons.append('PREDICTED_WINNER_NOT_IN_MATCHUP')
    try:
        if _dt(lock.get('source_pull_at')) > _dt(lock.get('lock_cutoff_at')):
            reasons.append('POST_CUTOFF_SOURCE')
    except Exception:
        reasons.append('INVALID_AUTHORITY_TIMESTAMP')
    expected_hash = str(lock.get('canonical_lock_hash') or '')
    if expected_hash and expected_hash != _canonical_lock_hash(lock):
        reasons.append('CANONICAL_LOCK_HASH_MISMATCH')
    return not reasons, reasons


def _audit_metrics(rows: list[dict]) -> dict:
    graded = [row for row in rows if row.get('correct') is not None]
    wins = sum(1 for row in graded if row.get('correct') is True)
    losses = sum(1 for row in graded if row.get('correct') is False)
    return {
        'count': len(rows),
        'graded_count': len(graded),
        'wins': wins,
        'losses': losses,
        'accuracy': (wins / len(graded)) if graded else None,
        'ungraded_count': len(rows) - len(graded),
    }


def _grade_prediction(row: dict, final: dict | None, *, valid: bool, validation_errors: list[str]) -> dict:
    home, away = str(row.get('home_team') or ''), str(row.get('away_team') or '')
    result = {
        'event_id': _event_id(row),
        'away_team': away,
        'home_team': home,
        'commence_time': row.get('commence_time'),
        'predicted_winner': row.get('predicted_winner'),
        'official_pick': bool(row.get('official_pick')),
        'prediction_mode': row.get('prediction_mode'),
        'model_id': row.get('model_id'),
        'win_probability': row.get('win_probability'),
        'source_pull_at': row.get('source_pull_at'),
        'lock_cutoff_at': row.get('lock_cutoff_at'),
        'locked_at': row.get('locked_at'),
        'authority_valid': bool(valid),
        'authority_errors': list(validation_errors),
        'completed': bool(final and final.get('completed')),
        'home_score': None,
        'away_score': None,
        'actual_winner': None,
        'correct': None,
        'grade_status': 'INVALID_AUTHORITY' if not valid else 'MISSING_FINAL',
    }
    if not valid or not final or not final.get('completed'):
        return result
    scores = {str(item.get('name')): int(item.get('score') or 0) for item in final.get('scores') or []}
    if home not in scores or away not in scores or scores[home] == scores[away]:
        result['grade_status'] = 'INVALID_FINAL_SCORE'
        return result
    actual = home if scores[home] > scores[away] else away
    result.update({
        'home_score': scores[home],
        'away_score': scores[away],
        'actual_winner': actual,
        'correct': str(row.get('predicted_winner') or '') == actual,
        'grade_status': 'GRADED',
    })
    return result


def audit_slate(slate: str) -> dict:
    """Grade exact provider event IDs without reconstructing historical predictions."""
    # Validate the date early so malformed input cannot become a broad table query.
    datetime.fromisoformat(str(slate)).date()
    store = Store()
    mutable = {_event_id(row): dict(row) for row in store.query_predictions(slate) if _event_id(row)}
    locks = {_event_id(row): dict(row) for row in store.query_locks(slate) if _event_id(row)}
    score_rows = OddsApiClient().scores(days_from=3).data or []
    finals = {
        str(row.get('id') or ''): row
        for row in score_rows
        if row.get('completed') and str(row.get('id') or '')
    }

    canonical_rows = []
    published_rows = []
    drift = []
    for event_id, lock in locks.items():
        valid, errors = _lock_validation(lock)
        canonical = _grade_prediction(lock, finals.get(event_id), valid=valid, validation_errors=errors)
        canonical['record_authority'] = _lock_authority(lock)
        canonical['lock_minutes'] = _lock_minutes(lock)
        canonical_rows.append(canonical)

        published = mutable.get(event_id)
        if published:
            try:
                source_at = _dt(published.get('source_pull_at'))
                historical_cutoff_at = _dt(lock.get('lock_cutoff_at'))
                configured_cutoff_at = _dt(published.get('commence_time')) - timedelta(minutes=LOCK_MINUTES)
                historical_policy_compliant = source_at <= historical_cutoff_at
                published_pre_cutoff = source_at <= configured_cutoff_at
            except Exception:
                historical_policy_compliant = False
                published_pre_cutoff = False
                configured_cutoff_at = None
            published_errors = [] if published_pre_cutoff else [f'POST_T{LOCK_MINUTES}_MUTABLE_PUBLIC_ROW']
            issued = _grade_prediction(
                published,
                finals.get(event_id),
                # This ledger intentionally grades what was published even when it violated policy.
                valid=True,
                validation_errors=published_errors,
            )
            issued.update({
                'record_authority': 'RECORDED_MUTABLE_PUBLIC_FEED',
                'policy_compliant': published_pre_cutoff,
                'configured_lock_minutes': LOCK_MINUTES,
                'configured_lock_cutoff_at': _iso(configured_cutoff_at) if configured_cutoff_at else None,
                'historical_lock_policy_compliant': historical_policy_compliant,
                'authority_valid': published_pre_cutoff,
                'authority_errors': published_errors,
            })
            published_rows.append(issued)
            if (
                published.get('predicted_winner') != lock.get('predicted_winner')
                or bool(published.get('official_pick')) != bool(lock.get('official_pick'))
                or published.get('prediction_fingerprint') != lock.get('prediction_fingerprint')
            ):
                drift.append({
                    'event_id': event_id,
                    'away_team': lock.get('away_team'),
                    'home_team': lock.get('home_team'),
                    'locked_predicted_winner': lock.get('predicted_winner'),
                    'published_predicted_winner': published.get('predicted_winner'),
                    'locked_official_pick': bool(lock.get('official_pick')),
                    'published_official_pick': bool(published.get('official_pick')),
                    'locked_source_pull_at': lock.get('source_pull_at'),
                    'published_source_pull_at': published.get('source_pull_at'),
                    'direction_changed': published.get('predicted_winner') != lock.get('predicted_winner'),
                    'official_flag_changed': bool(published.get('official_pick')) != bool(lock.get('official_pick')),
                })

    canonical_rows.sort(key=lambda row: (str(row.get('commence_time') or ''), row['event_id']))
    published_rows.sort(key=lambda row: (str(row.get('commence_time') or ''), row['event_id']))
    canonical_official = [row for row in canonical_rows if row.get('official_pick')]
    published_official = [row for row in published_rows if row.get('official_pick')]
    return {
        'ok': True,
        'sport': 'mlb_auto',
        'slate_date': slate,
        'audit_version': AUDIT_VERSION,
        'historical_predictions_recomputed': False,
        'outcome_join': 'EXACT_PROVIDER_EVENT_ID',
        'canonical_authority': 'IMMUTABLE_LOCK_LEDGER',
        'configured_lock_minutes': LOCK_MINUTES,
        'published_feed_note': 'Grades persisted rows that the public endpoint exposed; policy violations remain flagged.',
        'canonical_locked_forecasts': {
            **_audit_metrics(canonical_rows),
            'rows': canonical_rows,
        },
        'canonical_official_picks': {
            **_audit_metrics(canonical_official),
            'rows': canonical_official,
        },
        'published_feed_forecasts': {
            **_audit_metrics(published_rows),
            'rows': published_rows,
        },
        'published_official_picks': {
            **_audit_metrics(published_official),
            'policy_compliant_count': sum(1 for row in published_official if row.get('policy_compliant')),
            'rows': published_official,
        },
        'public_lock_drift_count': len(drift),
        'public_lock_direction_drift_count': sum(1 for row in drift if row.get('direction_changed')),
        'public_lock_drift': drift,
        'missing_mutable_row_count': len(set(locks) - set(mutable)),
    }


def _qualifies_official_pick(champion: Model | None, win_probability: float) -> bool:
    """Use trained-model confidence only; price and expected value are observational."""
    return bool(champion and float(win_probability) >= MIN_OFFICIAL_PROB)


def _qualifies_official_pick_at_time(
    champion: Model | None,
    win_probability: float,
    now: datetime,
    cutoff: datetime,
) -> bool:
    return bool(now <= cutoff and _qualifies_official_pick(champion, win_probability))


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
    current_event_ids = {str(item.get('id') or '') for item in events if item.get('id')}
    previous_event_ids = {str(item) for item in (state.get('event_ids') or []) if item}
    observed_new_event_fraction = (
        len(current_event_ids - previous_event_ids) / max(1, len(current_event_ids))
        if previous_event_ids else (1.0 if current_event_ids else 0.0)
    )
    decision = decide_pull(
        now=now, events=events, last_pull_at=state.get('last_pull_at'),
        volatility=float(state.get('market_volatility') or 0),
        missing_market_fraction=float(state.get('missing_market_fraction') or 0),
        recent_signal_change=float(state.get('recent_signal_change') or 0),
        new_event_fraction=observed_new_event_fraction, force_reason=force_reason,
    )
    if not decision.should_pull:
        store.put_state('controller', {
            'heartbeat_at': _iso(now),
            'expected_interval_minutes': decision.next_interval_minutes,
            'next_due_at': decision.next_due_at_utc,
            'information_gain_score': decision.information_gain_score,
            'last_heartbeat_ok': True,
        })
        return {'ok': True, 'action': 'HEARTBEAT_ONLY', 'decision': decision.__dict__, 'event_count': len(events)}

    featured_response = client.featured_odds()
    featured = [x for x in (featured_response.data or []) if x.get('sport_key') == 'baseball_mlb']
    event_map = {str(x.get('id')): x for x in events}
    predictions = []
    discovery_errors = []
    team_form_errors = []
    team_form_available_count = 0
    market_key_total = missing = 0
    volatility_values = []
    signal_changes = []
    champion_item = store.get_model('CHAMPION')
    champion = _current_policy_model_from_item(champion_item)
    champion_id = champion_item.get('model_id') if champion else None
    locked_event_ids_by_slate: dict[str, set[str]] = {}
    frozen_public_count = 0

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
        if history:
            signal_changes.append(abs(float(fair.get('home') or .5) - float(history[-1])))
        history_plus = history + [float(fair.get('home') or .5)]
        home, away = str(base.get('home_team') or ''), str(base.get('away_team') or '')
        team_form = build_team_form_context(home, away, now, historical=False)
        if team_form.get('ok'):
            team_form_available_count += 1
        else:
            team_form_errors.append(f"{event_id}:{team_form.get('error') or 'TEAM_FORM_UNAVAILABLE'}")
        features = build_feature_vector(
            event=base,
            detail=detail,
            home_probability_history=history_plus,
            pulled_at=_iso(now),
            pull_count=len(history_plus),
            team_form_features=team_form.get('features') or {},
        )
        volatility_values.append(float(features.get('market_volatility') or 0))
        bootstrap = bootstrap_home_probability(features)
        home_prob = champion.predict(features) if champion else bootstrap
        away_prob = 1.0 - home_prob
        winner = home if home_prob >= .5 else away
        win_prob = max(home_prob, away_prob)
        price, price_book = _pick_price(base, winner)
        dec = american_decimal(price) if price is not None else None
        ev = (win_prob * dec - 1.0) if dec else None
        cutoff = start - timedelta(minutes=LOCK_MINUTES)
        pre_lock_cutoff = now <= cutoff
        official = _qualifies_official_pick_at_time(champion, win_prob, now, cutoff)
        row = {
            'sport': 'mlb_auto', 'provider_sport_key': 'baseball_mlb', 'slate_date': slate,
            'event_id': event_id, 'home_team': home, 'away_team': away, 'commence_time': _iso(start),
            'source_pull_at': _iso(now), 'lock_cutoff_at': _iso(cutoff), 'features': features,
            'home_probability': home_prob, 'away_probability': away_prob,
            'predicted_winner': winner, 'win_probability': win_prob,
            'american_odds': price, 'price_book': price_book, 'expected_value': ev,
            'expected_value_observational_only': True,
            'official_pick_policy': OFFICIAL_PICK_POLICY,
            'prediction_mode': 'ML_CHAMPION' if champion else 'MARKET_BOOTSTRAP', 'model_id': champion_id,
            'official_pick': official,
            'promotion_status': (
                'READY' if official else 'POST_CUTOFF_SHADOW' if not pre_lock_cutoff
                else 'SHADOW_BOOTSTRAP' if not champion else 'MODEL_PASS'
            ),
            'pull_count_for_event': len(history_plus), 'discovered_market_keys': discovered_keys,
            'market_discovery_errors': errors, 'platform_version': PLATFORM_VERSION,
            'pre_lock_cutoff': pre_lock_cutoff,
            'team_form_version': TEAM_FORM_VERSION,
            'team_form_source': TEAM_FORM_SOURCE,
            'team_form_available': bool(team_form.get('ok')),
            'team_form_as_of': (team_form.get('metadata') or {}).get('as_of'),
            'team_form_metadata': team_form.get('metadata') or {},
            'team_form_error': team_form.get('error') or '',
        }
        row['prediction_fingerprint'] = _prediction_fingerprint(row)
        store.put_snapshot(slate, _iso(now), {
            'event_id': event_id, 'source_pull_at': _iso(now), 'event': detail,
            'features': features, 'prediction': row,
            'prediction_fingerprint': row['prediction_fingerprint'],
        })
        if slate not in locked_event_ids_by_slate:
            locked_event_ids_by_slate[slate] = {
                _event_id(lock) for lock in store.query_locks(slate) if _event_id(lock)
            }
        locked_event_ids = locked_event_ids_by_slate[slate]
        if event_id in locked_event_ids:
            row['mutable_public_write_skipped'] = True
            row['immutable_lock_preserved'] = True
            frozen_public_count += 1
        else:
            store.put_prediction(slate, event_id, row)
        predictions.append(row)

    fraction_missing = (missing / len(featured)) if featured else 0.0
    next_state = {
        'last_pull_at': _iso(now), 'heartbeat_at': _iso(now), 'expected_interval_minutes': decision.next_interval_minutes,
        'next_due_at': decision.next_due_at_utc, 'information_gain_score': decision.information_gain_score,
        'event_count': len(events), 'featured_event_count': len(featured), 'prediction_count': len(predictions),
        'market_key_total': market_key_total, 'missing_market_fraction': fraction_missing,
        'market_volatility': max(volatility_values) if volatility_values else 0.0,
        'recent_signal_change': max(signal_changes) if signal_changes else 0.0,
        'new_event_fraction': observed_new_event_fraction,
        'event_ids': sorted(current_event_ids),
        'discovery_error_count': len(discovery_errors), 'last_ingest_ok': True,
        'last_heartbeat_ok': True,
        'prediction_mode': 'ML_CHAMPION' if champion else 'MARKET_BOOTSTRAP',
        'team_form_version': TEAM_FORM_VERSION,
        'team_form_available_count': team_form_available_count,
        'team_form_error_count': len(team_form_errors),
        'team_form_errors': team_form_errors[:20],
        'frozen_public_count': frozen_public_count,
    }
    store.put_state('controller', next_state)
    store.archive_json(f'mlb_auto/raw/{now:%Y/%m/%d/%H%M%S}.json', {
        'events': events,
        'featured': featured,
        'predictions': predictions,
        'errors': discovery_errors,
        'team_form_errors': team_form_errors,
    })
    return {
        'ok': True,
        'action': 'INGEST',
        'decision': decision.__dict__,
        'event_count': len(events),
        'prediction_count': len(predictions),
        'champion_model_id': champion_id,
        'errors': discovery_errors[:20],
        'team_form_version': TEAM_FORM_VERSION,
        'team_form_available_count': team_form_available_count,
        'team_form_error_count': len(team_form_errors),
        'team_form_errors': team_form_errors[:20],
        'frozen_public_count': frozen_public_count,
    }


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
            persisted = dict(prediction)
            persisted.setdefault('source_pull_at', source)
            candidates.append((source_dt, persisted))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def lock_due_games() -> dict:
    store = Store()
    now = _now()
    slates = {(now.astimezone(ET).date() + timedelta(days=d)).isoformat() for d in (-1, 0, 1)}
    created = skipped = late_recovered = late_recovery_expired = 0
    errors = []
    for slate in sorted(slates):
        existing = {_event_id(x) for x in store.query_locks(slate) if _event_id(x)}
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
            if now < cutoff:
                continue
            is_late_recovery = now >= start
            if is_late_recovery and now > start + LATE_LOCK_RECOVERY_WINDOW:
                skipped += 1
                late_recovery_expired += 1
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
            if is_late_recovery:
                lock.update({
                    'late_recovered': True,
                    'late_recovery_reason': LATE_LOCK_RECOVERY_REASON,
                })
            lock['canonical_lock_hash_version'] = CANONICAL_LOCK_HASH_VERSION
            lock['canonical_lock_hash'] = _canonical_lock_hash(lock)
            try:
                store.archive_json(
                    f"mlb_auto/locks/{slate}/{event_id}/{lock['canonical_lock_hash']}.json",
                    {
                        'sport': 'mlb_auto',
                        'slate_date': slate,
                        'event_id': event_id,
                        'canonical_lock_hash_version': CANONICAL_LOCK_HASH_VERSION,
                        'canonical_lock_hash': lock['canonical_lock_hash'],
                        'lock': lock,
                    },
                )
                store.put_lock_once(slate, event_id, lock)
                created += 1
                if is_late_recovery:
                    late_recovered += 1
            except Exception as exc:
                if 'ConditionalCheckFailed' in str(exc):
                    skipped += 1
                else:
                    errors.append(f'{event_id}:{type(exc).__name__}')
    return {
        'ok': not errors,
        'created': created,
        'skipped': skipped,
        'late_recovered_count': late_recovered,
        'late_recovery_expired_count': late_recovery_expired,
        'errors': errors,
    }


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
            'lock_minutes': _lock_minutes(lock),
            'lock_authority_policy': lock.get('official_pick_policy') or OFFICIAL_PICK_POLICY,
            'team_form_version': lock.get('team_form_version'),
            'team_form_source': lock.get('team_form_source'),
            'team_form_available': bool(lock.get('team_form_available')),
            'team_form_as_of': lock.get('team_form_as_of'),
            'team_form_metadata': lock.get('team_form_metadata') or {},
            'team_form_error': lock.get('team_form_error') or '',
        })
        settled += 1
    settlement_at = _iso()
    store.put_state('controller', {
        'last_settlement_at': settlement_at,
        'last_settlement_count': settled,
        'last_settlement_ok': True,
    })
    return {'ok': True, 'completed_scores_seen': len(rows), 'eligible_locks_examined': examined, 'settled': settled}


def train() -> dict:
    store = Store()
    examples, _ = _current_policy_training_examples(store)
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
    incumbent = _current_policy_model_from_item(incumbent_item)
    gate = promote_challenger(challenger=challenger, incumbent=incumbent, validation_rows=vr, validation_labels=vl)
    model_id = f'MLB_AUTO_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{hashlib.sha256(challenger.dumps().encode()).hexdigest()[:12]}'
    artifact = {
        'model_id': model_id,
        'created_at': _iso(),
        'model_json': challenger.dumps(),
        'training_count': len(tr),
        'validation_count': len(vr),
        'gate': gate,
        'sport': 'mlb_auto',
        'training_lock_minutes': LOCK_MINUTES,
        'lock_authority_policy': OFFICIAL_PICK_POLICY,
    }
    store.put_model(f'CHALLENGER#{model_id}', artifact)
    promoted = bool(gate.get('promote'))
    if promoted:
        store.put_model('CHAMPION', artifact)
    training_at = _iso()
    store.put_state('controller', {
        'last_training_at': training_at,
        'last_training_count': len(examples),
        'last_training_attempt_at': training_at,
        'last_training_attempt_count': len(examples),
        'last_training_attempt_git_sha': os.getenv('MLB_AUTO_DEPLOY_GIT_SHA', 'unknown'),
        'last_training_attempt_result': (
            'CHAMPION_PROMOTED'
            if promoted
            else f"CHALLENGER_REJECTED:{str(gate.get('reason') or 'AUDIT_GATE')[:120]}"
        ),
        'champion_model_id': model_id if promoted else incumbent_item.get('model_id'),
        'last_training_gate': gate,
    })
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
    examples, _ = _current_policy_training_examples(store)
    champion_item = store.get_model('CHAMPION')
    repair_state = {
        **state,
        'minutes_since_last_pull': minutes,
        'new_training_examples': max(0, len(examples) - int(
            state.get('last_training_attempt_count') or state.get('last_training_count') or 0
        )),
        'min_new_examples': MIN_NEW,
        'champion_corrupt': bool(champion_item and not _model_from_item(champion_item)),
        'champion_lock_policy_incompatible': bool(
            champion_item and _model_from_item(champion_item) and not _current_policy_model_from_item(champion_item)
        ),
    }
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
    repair_at = _iso()
    store.put_state('repair', {
        'last_repair_at': repair_at,
        'action_count': len(results),
        'actions': [x['action'] for x in results],
        'last_repair_ok': True,
    })
    return {'ok':True,'actions':results}


def status() -> dict:
    store=Store()
    state=store.get_state('controller')
    repair_state=store.get_state('repair')
    champion=store.get_model('CHAMPION')
    active_champion=_current_policy_model_from_item(champion)
    examples, all_example_count=_current_policy_training_examples(store)
    today=datetime.now(ET).date().isoformat()
    predictions=canonical_prediction_rows(store,today)
    return {
        'ok':True,'sport':'mlb_auto','provider_sport_key':'baseball_mlb','platform_version':PLATFORM_VERSION,
        'autonomous':True,'cost_throttling':False,'heartbeat_minutes':5,'lock_check_minutes':1,
        'official_pick_policy':OFFICIAL_PICK_POLICY,
        'expected_value_selection_gate':False,
        'minimum_official_probability':MIN_OFFICIAL_PROB,
        'prediction_mode':'ML_CHAMPION' if active_champion else 'MARKET_BOOTSTRAP',
        'champion_lock_policy_compatible':bool(active_champion),
        'champion_model_id':champion.get('model_id') if active_champion else None,
        'registered_champion_model_id':champion.get('model_id'),
        'training_examples':len(examples),
        'training_examples_all_lock_horizons':all_example_count,
        'training_lock_minutes':LOCK_MINUTES,
        'champion_training_count':champion.get('training_count'),
        'champion_validation_count':champion.get('validation_count'),
        'champion_promotion_gate':champion.get('gate') or {},
        'minimum_training_examples':MIN_TRAIN,'today_prediction_count':len(predictions),
        'today_official_pick_count':sum(1 for x in predictions if x.get('official_pick')),
        'today_locked_count':sum(1 for x in predictions if x.get('locked')),
        'public_prediction_authority':'IMMUTABLE_LOCK_WHEN_PRESENT',
        'public_authority_version':PUBLIC_PREDICTION_AUTHORITY_VERSION,
        'lock_minutes':LOCK_MINUTES,
        'controller':state,'repair':repair_state,
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
            rows=canonical_prediction_rows(Store(),date)
            return _response({'ok':True,'sport':'mlb_auto','slate_date':date,'count':len(rows),
                              'official_pick_count':sum(1 for x in rows if x.get('official_pick')),
                              'published_official_pick_count':sum(1 for x in rows if x.get('published_official_pick')),
                              'locked_count':sum(1 for x in rows if x.get('locked')),
                              'prediction_authority':'IMMUTABLE_LOCK_WHEN_PRESENT',
                              'public_authority_version':PUBLIC_PREDICTION_AUTHORITY_VERSION,
                              'configured_lock_minutes':LOCK_MINUTES,
                              'predictions':rows})
        if path.endswith('/audit'):
            date=((event.get('queryStringParameters') or {}).get('date') or (datetime.now(ET).date()-timedelta(days=1)).isoformat())
            try:
                return _response(audit_slate(date))
            except (TypeError, ValueError):
                return _response({'ok':False,'error':'INVALID_SLATE_DATE','slate_date':date},400)
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
    if action in ('AUDIT','MLB_AUTO_AUDIT'):
        return audit_slate(str(event.get('date') or (datetime.now(ET).date()-timedelta(days=1)).isoformat()))
    return {'ok':False,'error':'UNKNOWN_ACTION','action':action}
