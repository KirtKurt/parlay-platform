"""Market-separated grading primitives."""
def grade_moneyline(selection, home_score, away_score):
    if home_score == away_score: return "PUSH"
    winner="HOME" if home_score > away_score else "AWAY"
    return "WIN" if selection == winner else "LOSS"

def grade_spread(selection, line, home_score, away_score):
    margin=home_score-away_score
    adjusted=margin+line if selection=="HOME" else -margin+line
    return "WIN" if adjusted>0 else "LOSS" if adjusted<0 else "PUSH"

def grade_total(selection, line, home_score, away_score):
    total=home_score+away_score
    if total==line:return "PUSH"
    return "WIN" if (selection=="OVER" and total>line) or (selection=="UNDER" and total<line) else "LOSS"
