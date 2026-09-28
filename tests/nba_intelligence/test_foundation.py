from datetime import datetime,timezone,timedelta
import pytest
from nba_intelligence.schema import Provenance,Observation
from nba_intelligence.odds import american_to_prob,devig_two_way
from nba_intelligence.features import rolling_before
from nba_intelligence.locking import ImmutableLedger,LockedPrediction
from nba_intelligence.grading import grade_moneyline,grade_spread,grade_total
from nba_intelligence.calibration import brier,logloss
from nba_intelligence.promotion import qualify
from nba_intelligence.simulation import simulate

UTC=timezone.utc
T=datetime(2026,10,1,23,tzinfo=UTC)

def test_odds_and_devig():
    assert american_to_prob(100)==.5
    a,b=devig_two_way(-110,-110); assert round(a+b,12)==1 and a==b

def test_provenance_rejects_future():
    p=Provenance("BBD",T+timedelta(minutes=1),T,"v1","g")
    with pytest.raises(ValueError): Observation("g","x","ortg",1,T,p)

def test_rolling_excludes_future_and_same_time():
    rows=[{"occurred_at":T-timedelta(days=1),"v":100},{"occurred_at":T,"v":999},{"occurred_at":T+timedelta(days=1),"v":999}]
    assert rolling_before(rows,game_time=T,key="x",value="v")["x_last_3"]==100

def _row():
    return LockedPrediction("g","ML","HOME",.6,.58,"NBA-0.1","none",T,10,"f","o","EXPECTED","KNOWN",{})

def test_lock_is_immutable():
    l=ImmutableLedger(); l.append(_row())
    with pytest.raises(RuntimeError): l.append(_row())

def test_grading():
    assert grade_moneyline("HOME",110,100)=="WIN"
    assert grade_spread("HOME",-4.5,110,100)=="WIN"
    assert grade_total("UNDER",220.5,110,100)=="WIN"

def test_metrics_and_promotion_fail_closed():
    rows=[{"p":.8,"y":1},{"p":.2,"y":0}]
    assert brier(rows) < .05 and logloss(rows) < .3
    q=qualify({"brier":.2,"logloss":.6,"ece":.1},{"brier":.19,"logloss":.59,"ece":.09},untouched_rows=299,provenance_ok=True,leakage_tests_ok=True,missingness_ok=True)
    assert not q["qualified"] and not q["automatic_promotion"]

def test_simulator_requires_10k():
    with pytest.raises(ValueError): simulate(98,1.15,1.10,n=9999)
    x=simulate(98,1.15,1.10,n=10000); assert 0<=x["home_win_probability"]<=1
