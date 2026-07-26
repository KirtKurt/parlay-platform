"""Leakage-safe features for the MLB supervised shadow challenger.

The feature compiler has no production authority. It consumes only information
available by each game T-minus-45 plus strictly earlier completed-slate labels.
Same-day outcomes are never used to build another game on the same slate.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "MLB-SUPERVISED-FEATURES-v2-market-team-fundamentals-v8-missingness"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _nested(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _first(mapping: Any, paths: Iterable[Tuple[str, ...]]) -> Any:
    for path in paths:
        value = _nested(mapping, *path)
        if value not in (None, ""):
            return value
    return None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _team(value: Any) -> str:
    text = " ".join(str(value or "").strip().lower().split())
    aliases = {
        "oakland athletics": "athletics",
        "a's": "athletics",
        "oakland a's": "athletics",
        "az diamondbacks": "arizona diamondbacks",
        "la angels": "los angeles angels",
        "la dodgers": "los angeles dodgers",
        "ny mets": "new york mets",
        "ny yankees": "new york yankees",
        "sd padres": "san diego padres",
        "sf giants": "san francisco giants",
        "tb rays": "tampa bay rays",
    }
    return aliases.get(text, text)


def _day(record: Mapping[str, Any]) -> str:
    return str(record.get("slateDateEt") or "")


def _market_home_probability(record: Mapping[str, Any]) -> float:
    home = record.get("homeSignal") or {}
    return _clip(
        _f(home.get("marketConsensusProbability", home.get("fairProbability")), 0.5),
        0.02,
        0.98,
    )


def _american_probability(value: Any) -> float:
    price = _f(value, 0.0)
    if price == 0.0:
        return 0.5
    return abs(price) / (abs(price) + 100.0) if price < 0 else 100.0 / (price + 100.0)


def _tag(signal: Mapping[str, Any], name: str) -> float:
    return 1.0 if name in {str(value) for value in signal.get("tags") or []} else 0.0


def _horizon(signal: Mapping[str, Any], horizon: str, field: str) -> float:
    return _f(_nested(signal, "temporalFeatures", "horizons", horizon, field), 0.0)


def _pattern(signal: Mapping[str, Any], field: str) -> float:
    return _f(_nested(signal, "oddsPatternFeatures", field), 0.0)


def _derived(signal: Mapping[str, Any], field: str) -> float:
    return _f(_nested(signal, "derivedFeatures", field), 0.0)


@dataclass
class TeamLedger:
    elo: float = 1500.0
    games: int = 0
    wins: int = 0
    recent: Tuple[int, ...] = ()
    streak: int = 0
    last_date: Optional[str] = None


def _rate(row: TeamLedger, window: int) -> float:
    values = row.recent[-window:]
    return sum(values) / len(values) if values else 0.5


def _rest(row: TeamLedger, game_day: str) -> float:
    if not row.last_date:
        return 3.0
    try:
        return float(_clip((date.fromisoformat(game_day) - date.fromisoformat(row.last_date)).days, 0, 10))
    except Exception:
        return 3.0


def _team_history(ledger: Mapping[str, TeamLedger], home: str, away: str, game_day: str) -> Dict[str, float]:
    h = ledger.get(home, TeamLedger())
    a = ledger.get(away, TeamLedger())
    return {
        "team_elo_diff": (h.elo - a.elo) / 400.0,
        "team_home_elo": (h.elo - 1500.0) / 400.0,
        "team_away_elo": (a.elo - 1500.0) / 400.0,
        "team_recent10_diff": _rate(h, 10) - _rate(a, 10),
        "team_recent30_diff": _rate(h, 30) - _rate(a, 30),
        "team_home_recent10": _rate(h, 10) - 0.5,
        "team_away_recent10": _rate(a, 10) - 0.5,
        "team_streak_diff": _clip(h.streak - a.streak, -10, 10) / 10.0,
        "team_rest_diff": (_rest(h, game_day) - _rest(a, game_day)) / 7.0,
        "team_history_experience": math.log1p(min(h.games, a.games)) / math.log(220.0),
        "team_history_available": 1.0 if h.games >= 5 and a.games >= 5 else 0.0,
    }


def add_strictly_past_team_history(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for raw in records:
        row = copy.deepcopy(dict(raw))
        if _day(row):
            by_day.setdefault(_day(row), []).append(row)
    ledger: Dict[str, TeamLedger] = {}
    output: List[Dict[str, Any]] = []
    for game_day in sorted(by_day):
        slate = sorted(by_day[game_day], key=lambda row: str(row.get("officialGamePk") or ""))
        for row in slate:
            home = _team(row.get("homeTeam"))
            away = _team(row.get("awayTeam"))
            row["teamHistoryFeatures"] = _team_history(ledger, home, away, game_day)
            row["teamHistoryLeakageBoundary"] = "strictly_prior_complete_slate_days"
            output.append(row)
        # Update only after every game on this slate received its features. This
        # prevents an earlier same-day result, including game one of a
        # doubleheader, from leaking into another same-day prediction.
        for row in slate:
            home = _team(row.get("homeTeam"))
            away = _team(row.get("awayTeam"))
            home_won = int(row.get("homeWon") or 0)
            old_home = ledger.get(home, TeamLedger())
            old_away = ledger.get(away, TeamLedger())
            expected = 1.0 / (1.0 + 10.0 ** ((old_away.elo - old_home.elo) / 400.0))

            def update(old: TeamLedger, won: int, elo: float) -> TeamLedger:
                streak = old.streak + 1 if won and old.streak >= 0 else 1 if won else old.streak - 1 if old.streak <= 0 else -1
                return TeamLedger(
                    elo=elo,
                    games=old.games + 1,
                    wins=old.wins + won,
                    recent=tuple((list(old.recent) + [won])[-30:]),
                    streak=streak,
                    last_date=game_day,
                )

            ledger[home] = update(old_home, home_won, old_home.elo + 20.0 * (home_won - expected))
            ledger[away] = update(old_away, 1 - home_won, old_away.elo + 20.0 * ((1 - home_won) - (1.0 - expected)))
    return sorted(output, key=lambda row: (_day(row), str(row.get("officialGamePk") or "")))


def _fundamentals_payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    for value in (
        record.get("frozenFundamentalsSnapshot"),
        record.get("fundamentalsSnapshot"),
        record.get("pregameFundamentals"),
        record.get("fundamentals"),
    ):
        if isinstance(value, Mapping):
            return value
    return {}


def _side_fundamentals(payload: Mapping[str, Any], side: str) -> Mapping[str, Any]:
    for key in (side, f"{side}Team", f"{side}Side"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _metric(side: Mapping[str, Any], names: Sequence[str]) -> Optional[float]:
    for name in names:
        value = side.get(name)
        if value not in (None, ""):
            parsed = _f(value, float("nan"))
            if math.isfinite(parsed):
                return parsed
    return None


def _fundamentals_features(record: Mapping[str, Any]) -> Dict[str, float]:
    payload = _fundamentals_payload(record)
    home = _side_fundamentals(payload, "home")
    away = _side_fundamentals(payload, "away")
    definitions = {
        "starter_quality": ("starterQuality", "starterRating", "startingPitcherRating", "starterProjection"),
        "starter_recent": ("starterRecentForm", "startingPitcherRecentForm", "recentStarterRating"),
        "starter_velocity": ("starterVelocity", "pitchVelocity", "velocityRating"),
        "starter_command": ("starterCommand", "commandRating", "kMinusBbRate"),
        "starter_expected_innings": ("starterExpectedInnings", "expectedInnings"),
        "bullpen_quality": ("bullpenQuality", "bullpenRating"),
        "bullpen_freshness": ("bullpenFreshness", "bullpenAvailability", "bullpenRestRating"),
        "lineup_quality": ("lineupQuality", "offenseRating", "confirmedLineupRating"),
        "lineup_absence": ("lineupAbsenceImpact", "missingHitterImpact", "absencePenalty"),
        "platoon": ("platoonMatchup", "platoonRating"),
        "defense": ("defenseRating", "fieldingRating"),
        "travel_rest": ("travelRestRating", "restTravelRating"),
    }
    result: Dict[str, float] = {}
    observed = 0
    for feature, names in definitions.items():
        h = _metric(home, names)
        a = _metric(away, names)
        if h is not None and a is not None:
            observed += 1
        result[f"fund_{feature}_diff"] = _f(h) - _f(a)
        result[f"fund_{feature}_available"] = 1.0 if h is not None and a is not None else 0.0
    result["fund_park_run_factor"] = _f(_first(payload, (("parkRunFactor",), ("park", "runFactor"))), 1.0) - 1.0
    result["fund_weather_run_factor"] = _f(_first(payload, (("weatherRunFactor",), ("weather", "runFactor"))), 0.0)
    result["fundamentals_group_coverage"] = observed / float(len(definitions))
    result["fundamentals_available"] = 1.0 if payload else 0.0
    return result


def _v8_payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    for value in (
        record.get("oddsMarketExpansionFeatures"),
        _nested(record, "homeSignal", "oddsMarketExpansionFeatures"),
        _nested(record, "awaySignal", "oddsMarketExpansionFeatures"),
    ):
        if isinstance(value, Mapping):
            return value
    return {}


def _v8_value(payload: Mapping[str, Any], market: str, side: str, suffix: str) -> Optional[float]:
    value = payload.get(f"{market}_{side.replace(' ', '_')}{suffix}")
    if value is None:
        return None
    parsed = _f(value, float("nan"))
    return parsed if math.isfinite(parsed) else None


BASE_FEATURES = (
    "market_home_centered", "market_abs_edge", "market_home_price_probability",
    "market_away_price_probability", "market_book_count_min", "market_divergence_max",
)
TEMPORAL_FEATURES = (
    "delta_diff", "delta_sum", "reversal_diff", "reversal_sum", "pull_count_min",
    "coverage_min", "book_count_diff", "divergence_diff", "velocity15_diff",
    "velocity60_diff", "velocity180_diff", "velocity_full_diff", "acceleration180_diff",
    "volatility60_sum", "volatility180_sum", "steam_diff", "instability_diff",
    "agreement_both", "favorite_home", "pattern_fingerprint_diff", "pattern_regime_diff",
    "pattern_curve_diff", "pattern_leadership_diff", "pattern_shock_diff",
    "pattern_consensus_diff", "pattern_entropy_sum", "derived_movement_sqrt_diff",
    "derived_agreement_diff", "derived_velocity_interaction_diff",
    "derived_acceleration_interaction_diff", "derived_instability_sum",
    "derived_velocity_gap_diff", "derived_regime_trend_diff", "derived_regime_chaos_sum",
    "derived_curve_efficiency_diff", "derived_curve_shock_sum",
    "derived_book_leadership_diff", "derived_book_followthrough_diff",
)
TEAM_FEATURES = (
    "team_elo_diff", "team_home_elo", "team_away_elo", "team_recent10_diff",
    "team_recent30_diff", "team_home_recent10", "team_away_recent10",
    "team_streak_diff", "team_rest_diff", "team_history_experience",
    "team_history_available",
)
FUNDAMENTAL_FEATURES = tuple(
    [f"fund_{name}_diff" for name in (
        "starter_quality", "starter_recent", "starter_velocity", "starter_command",
        "starter_expected_innings", "bullpen_quality", "bullpen_freshness", "lineup_quality",
        "lineup_absence", "platoon", "defense", "travel_rest",
    )]
    + [f"fund_{name}_available" for name in (
        "starter_quality", "starter_recent", "starter_velocity", "starter_command",
        "starter_expected_innings", "bullpen_quality", "bullpen_freshness", "lineup_quality",
        "lineup_absence", "platoon", "defense", "travel_rest",
    )]
    + ["fund_park_run_factor", "fund_weather_run_factor", "fundamentals_group_coverage", "fundamentals_available"]
)
V8_FEATURES = (
    "v8_available", "v8_h2h_home_minus_away", "v8_h2h_dispersion_sum",
    "v8_h2h_book_count_min", "v8_f5_home_minus_away", "v8_f5_available",
    "v8_full_f5_home_gap", "v8_full_f5_away_gap", "v8_spread_home", "v8_spread_away",
    "v8_f5_spread_home", "v8_f5_spread_away",
    "v8_home_starter_bullpen_spread_divergence", "v8_late_inning_run_environment",
)
FEATURE_GROUPS = {
    "market": BASE_FEATURES,
    "market_temporal": BASE_FEATURES + TEMPORAL_FEATURES,
    "market_temporal_team": BASE_FEATURES + TEMPORAL_FEATURES + TEAM_FEATURES,
    "market_temporal_team_fundamentals": BASE_FEATURES + TEMPORAL_FEATURES + TEAM_FEATURES + FUNDAMENTAL_FEATURES,
    "market_temporal_team_fundamentals_v8": BASE_FEATURES + TEMPORAL_FEATURES + TEAM_FEATURES + FUNDAMENTAL_FEATURES + V8_FEATURES,
}


def feature_map(record: Mapping[str, Any]) -> Dict[str, float]:
    home = record.get("homeSignal") or {}
    away = record.get("awaySignal") or {}
    market_home = _market_home_probability(record)
    values: Dict[str, float] = {
        "market_home_centered": market_home - 0.5,
        "market_abs_edge": abs(market_home - 0.5),
        "market_home_price_probability": _american_probability(home.get("americanOdds")) - 0.5,
        "market_away_price_probability": _american_probability(away.get("americanOdds")) - 0.5,
        "market_book_count_min": min(_f(home.get("bookCount")), _f(away.get("bookCount"))) / 12.0,
        "market_divergence_max": max(_f(home.get("bookDivergence")), _f(away.get("bookDivergence"))),
        "delta_diff": _f(home.get("delta")) - _f(away.get("delta")),
        "delta_sum": _f(home.get("delta")) + _f(away.get("delta")),
        "reversal_diff": (_f(home.get("reversalCount")) - _f(away.get("reversalCount"))) / 6.0,
        "reversal_sum": (_f(home.get("reversalCount")) + _f(away.get("reversalCount"))) / 12.0,
        "pull_count_min": min(_f(home.get("pullCountForGame")), _f(away.get("pullCountForGame"))) / 64.0,
        "coverage_min": min(_horizon(home, "full", "coverageRatio"), _horizon(away, "full", "coverageRatio")),
        "book_count_diff": (_f(home.get("bookCount")) - _f(away.get("bookCount"))) / 12.0,
        "divergence_diff": _f(home.get("bookDivergence")) - _f(away.get("bookDivergence")),
        "velocity15_diff": _horizon(home, "15m", "velocityPpHr") - _horizon(away, "15m", "velocityPpHr"),
        "velocity60_diff": _horizon(home, "60m", "velocityPpHr") - _horizon(away, "60m", "velocityPpHr"),
        "velocity180_diff": _horizon(home, "180m", "velocityPpHr") - _horizon(away, "180m", "velocityPpHr"),
        "velocity_full_diff": _horizon(home, "full", "velocityPpHr") - _horizon(away, "full", "velocityPpHr"),
        "acceleration180_diff": _horizon(home, "180m", "accelerationPpHr2") - _horizon(away, "180m", "accelerationPpHr2"),
        "volatility60_sum": _horizon(home, "60m", "volatilityPpPerPull") + _horizon(away, "60m", "volatilityPpPerPull"),
        "volatility180_sum": _horizon(home, "180m", "volatilityPpPerPull") + _horizon(away, "180m", "volatilityPpPerPull"),
        "steam_diff": _tag(home, "STEAM") - _tag(away, "STEAM"),
        "instability_diff": _tag(home, "LATE_INSTABILITY") - _tag(away, "LATE_INSTABILITY"),
        "agreement_both": _tag(home, "BOOK_AGREEMENT") * _tag(away, "BOOK_AGREEMENT"),
        "favorite_home": 1.0 if str(home.get("marketSide") or "") == "favorite" else -1.0 if str(away.get("marketSide") or "") == "favorite" else 0.0,
        "pattern_fingerprint_diff": _pattern(home, "fingerprintScore") - _pattern(away, "fingerprintScore"),
        "pattern_regime_diff": _pattern(home, "regimeScore") - _pattern(away, "regimeScore"),
        "pattern_curve_diff": _pattern(home, "curveScore") - _pattern(away, "curveScore"),
        "pattern_leadership_diff": _pattern(home, "bookLeadershipScore") - _pattern(away, "bookLeadershipScore"),
        "pattern_shock_diff": _pattern(home, "shockPersistence") - _pattern(away, "shockPersistence"),
        "pattern_consensus_diff": _pattern(home, "consensusPersistence") - _pattern(away, "consensusPersistence"),
        "pattern_entropy_sum": _pattern(home, "pathEntropy") + _pattern(away, "pathEntropy"),
        "derived_movement_sqrt_diff": _derived(home, "movementSqrt") - _derived(away, "movementSqrt"),
        "derived_agreement_diff": _derived(home, "agreementMomentum") - _derived(away, "agreementMomentum"),
        "derived_velocity_interaction_diff": _derived(home, "velocityInteraction") - _derived(away, "velocityInteraction"),
        "derived_acceleration_interaction_diff": _derived(home, "accelerationInteraction") - _derived(away, "accelerationInteraction"),
        "derived_instability_sum": _derived(home, "instabilityInteraction") + _derived(away, "instabilityInteraction"),
        "derived_velocity_gap_diff": _derived(home, "velocityGap") - _derived(away, "velocityGap"),
        "derived_regime_trend_diff": _derived(home, "regimeTrend") - _derived(away, "regimeTrend"),
        "derived_regime_chaos_sum": _derived(home, "regimeChaos") + _derived(away, "regimeChaos"),
        "derived_curve_efficiency_diff": _derived(home, "curveEfficiency") - _derived(away, "curveEfficiency"),
        "derived_curve_shock_sum": _derived(home, "curveShock") + _derived(away, "curveShock"),
        "derived_book_leadership_diff": _derived(home, "bookLeadership") - _derived(away, "bookLeadership"),
        "derived_book_followthrough_diff": _derived(home, "bookFollowThrough") - _derived(away, "bookFollowThrough"),
    }
    values.update({name: _f(value) for name, value in (record.get("teamHistoryFeatures") or {}).items()})
    values.update(_fundamentals_features(record))

    payload = _v8_payload(record)
    home_team = str(record.get("homeTeam") or "")
    away_team = str(record.get("awayTeam") or "")
    h2h_home = _v8_value(payload, "h2h", home_team, "MedianImpliedProbability")
    h2h_away = _v8_value(payload, "h2h", away_team, "MedianImpliedProbability")
    f5_home = _v8_value(payload, "h2h_1st_5_innings", home_team, "MedianImpliedProbability")
    f5_away = _v8_value(payload, "h2h_1st_5_innings", away_team, "MedianImpliedProbability")
    values.update({
        "v8_available": 1.0 if payload else 0.0,
        "v8_h2h_home_minus_away": _f(h2h_home) - _f(h2h_away),
        "v8_h2h_dispersion_sum": _f(_v8_value(payload, "h2h", home_team, "ProbabilityDispersion")) + _f(_v8_value(payload, "h2h", away_team, "ProbabilityDispersion")),
        "v8_h2h_book_count_min": min(_f(_v8_value(payload, "h2h", home_team, "BookCount")), _f(_v8_value(payload, "h2h", away_team, "BookCount"))) / 12.0,
        "v8_f5_home_minus_away": _f(f5_home) - _f(f5_away),
        "v8_f5_available": 1.0 if f5_home is not None and f5_away is not None else 0.0,
        "v8_full_f5_home_gap": _f(h2h_home) - _f(f5_home),
        "v8_full_f5_away_gap": _f(h2h_away) - _f(f5_away),
        "v8_spread_home": _f(_v8_value(payload, "spreads", home_team, "MedianPoint")),
        "v8_spread_away": _f(_v8_value(payload, "spreads", away_team, "MedianPoint")),
        "v8_f5_spread_home": _f(_v8_value(payload, "spreads_1st_5_innings", home_team, "MedianPoint")),
        "v8_f5_spread_away": _f(_v8_value(payload, "spreads_1st_5_innings", away_team, "MedianPoint")),
        "v8_home_starter_bullpen_spread_divergence": _f(payload.get("homeStarterBullpenSpreadDivergence")),
        "v8_late_inning_run_environment": _f(payload.get("impliedLateInningRunEnvironment")),
    })
    names = set().union(*FEATURE_GROUPS.values())
    return {name: _f(values.get(name), 0.0) for name in names}


@dataclass(frozen=True)
class Example:
    day: str
    game_id: str
    outcome: int
    market_probability: float
    features: Dict[str, float]
    home_team: str
    away_team: str


def prepare_examples(records: Sequence[Mapping[str, Any]]) -> List[Example]:
    examples: List[Example] = []
    for row in add_strictly_past_team_history(records):
        if not _day(row) or row.get("homeWon") not in {0, 1}:
            continue
        examples.append(Example(
            day=_day(row),
            game_id=str(row.get("officialGamePk") or ""),
            outcome=int(row.get("homeWon") or 0),
            market_probability=_market_home_probability(row),
            features=feature_map(row),
            home_team=str(row.get("homeTeam") or ""),
            away_team=str(row.get("awayTeam") or ""),
        ))
    return sorted(examples, key=lambda row: (row.day, row.game_id))
