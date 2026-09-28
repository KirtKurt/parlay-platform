"""Possession-based score simulator."""
import random

def simulate(expected_possessions, home_ppp, away_ppp, n=10000, seed=0):
    if n < 10000: raise ValueError("production evaluation requires >=10,000 simulations")
    rng=random.Random(seed); scores=[]
    # Gaussian approximation is a baseline only; replace with validated possession model challenger.
    for _ in range(n):
        poss=max(1,rng.gauss(expected_possessions,4.0))
        h=max(0,round(rng.gauss(poss*home_ppp,10.5)))
        a=max(0,round(rng.gauss(poss*away_ppp,10.5)))
        scores.append((h,a))
    home=sum(h>a for h,a in scores)/n
    return {"home_win_probability":home,"away_win_probability":1-home,
            "expected_home":sum(h for h,_ in scores)/n,"expected_away":sum(a for _,a in scores)/n,
            "expected_margin":sum(h-a for h,a in scores)/n,"expected_total":sum(h+a for h,a in scores)/n}
