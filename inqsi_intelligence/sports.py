"""Sport policy for the independent-intelligence retrofit.

These are diagnostic/shadow requirements only. Existing serving authorities,
lock times and promotion gates remain unchanged.
"""
SPORTS={
 "mlb":{"system":"KS1","outcomes":2,"authority":"UNCHANGED"},
 "nfl":{"system":"NFL","outcomes":2,"authority":"UNCHANGED"},
 "tennis":{"system":"TENNIS","outcomes":2,"authority":"UNCHANGED"},
 "soccer":{"system":"KSS1","outcomes":3,"authority":"UNCHANGED"},
 "nba":{"system":"NBA","outcomes":2,"authority":"UNCHANGED"},
}
REQUIRED_PATHWAYS=("fundamentals_only","market_only","fundamentals_plus_market")
REQUIRED_SEGMENTS=("favorite","underdog")
