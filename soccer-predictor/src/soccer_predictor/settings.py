FEATURES = ["elo_gap", "home_gf8", "home_ga8", "away_gf8", "away_ga8", "home_pts5", "away_pts5"]
OPTIONAL_FEATURES = ["mkt_h", "mkt_d", "mkt_a", "mkt_ou25", "xg_h_bbd", "xg_a_bbd", "lineup_h_bbd", "lineup_a_bbd", "injuries_h_bbd", "injuries_a_bbd"]
MODEL_FEATURES = FEATURES + OPTIONAL_FEATURES
FEATURE_DEFAULTS = {"mkt_h": 1/3, "mkt_d": 1/3, "mkt_a": 1/3, "mkt_ou25": .5,
                    "xg_h_bbd": 1.35, "xg_a_bbd": 1.35, "lineup_h_bbd": 0.0, "lineup_a_bbd": 0.0, "injuries_h_bbd": 0.0, "injuries_a_bbd": 0.0}
CLASSES = ("H", "D", "A")
HISTORY_START = {"E0": 2010, "D1": 2015, "I1": 2015, "SP1": 2015, "F1": 2015,
                 "N1": 2018, "P1": 2018, "T1": 2018}
HISTORY_END = 2026
SEED = 42

def sources():
    return [(f"{y % 100:02d}{(y + 1) % 100:02d}", div)
            for div, start in HISTORY_START.items() for y in range(start, HISTORY_END + 1)]
