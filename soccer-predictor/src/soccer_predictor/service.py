from datetime import datetime, timezone
import pandas as pd
from .io import day, TeamNames
from .model import Model
from .state import State
from .fixtures import utc_kickoff
from .odds import bookmaker


def predict_bundle(model_bundle, fixture_bundle, first, last, now=None, model_max_age_days=14, fixture_max_age_hours=36):
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    if now.tzinfo is None:
        raise ValueError("Prediction time must be timezone-aware")
    now = now.tz_convert("UTC")
    first, last = day(first), day(last)
    if first < day(now) or last < first:
        raise ValueError("Production prediction window must be today or later")
    if model_bundle.get("schema") != 1 or fixture_bundle.get("schema") != 1:
        raise ValueError("Unsupported input schema")
    if model_bundle["provenance"].get("partial_history", True):
        raise ValueError("Partial-history diagnostic models cannot publish production predictions")
    model, state = Model.from_dict(model_bundle["model"]), State.from_dict(model_bundle["state"])
    cutoff = day(model.meta["fit_cutoff"])
    if cutoff > day(now) or (day(now)-cutoff).days > model_max_age_days:
        raise ValueError("Model is future-dated or stale; retrain before publication")
    if state.last_date != model.meta["train_max_date"] or day(state.last_date) >= cutoff:
        raise ValueError("Persisted feature state and trained model cutoff do not agree")
    for history in state.history.values():
        if any(day(r["date"]) >= cutoff for r in history):
            raise ValueError("Persisted state contains future results")
    fetched = utc_kickoff(fixture_bundle["fetched_at"])
    age = (now-fetched).total_seconds()/3600
    if age < -5/60 or age > fixture_max_age_hours:
        raise ValueError("Fixture feed is stale or future-dated")
    if day(fixture_bundle["coverage_from"]) > first or day(fixture_bundle["coverage_to"]) < last:
        raise ValueError("Fixture feed does not cover requested prediction dates")
    status = fixture_bundle["source_status"]
    operator = status.get("operator_supplied", {}).get("ok", False)
    if not operator and not all(status.get(k, {}).get("ok", False) for k in ["E0", "UCL"]):
        raise ValueError("Both EPL and UCL feeds must be verified")
    names = TeamNames(model_bundle.get("team_names", {}))
    predictions, seen = [], set()
    for fixture in sorted(fixture_bundle["fixtures"], key=lambda x: (x["kickoff"], x["home"])):
        if fixture["match_id"] in seen:
            raise ValueError("Duplicate fixture IDs")
        seen.add(fixture["match_id"])
        kickoff = utc_kickoff(fixture["kickoff"])
        if kickoff <= now or not first <= day(kickoff) <= last:
            continue
        home, away = names.resolve(fixture["home"]), names.resolve(fixture["away"])
        row = {"date": day(kickoff), "home": home, "away": away, "div": fixture["div"], **state.features(home, away, kickoff)}
        prediction = model.predict(row)
        prediction.update(bookmaker({**fixture.get("odds", {}), "date": day(kickoff)}, prediction))
        predictions.append({**{k: fixture[k] for k in ["match_id", "kickoff", "div", "source"]}, "home": home, "away": away, **prediction})
    return {"schema": 1, "status": "ok", "generated_at": now.isoformat(),
            "model_id": model.meta["model_id"], "fit_cutoff": model.meta["fit_cutoff"],
            "from": first.isoformat(), "to": last.isoformat(), "source_status": status,
            "count": len(predictions), "predictions": predictions,
            "disclaimer": "This is not betting advice."}
