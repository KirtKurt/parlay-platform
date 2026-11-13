from mlb_auto.ml import Model, train_logistic


def test_scaled_model_round_trip_preserves_live_probability():
    rows=[]; labels=[]
    for i in range(40):
        x=float(i)
        rows.append({'large_market_depth': x*1000.0, 'small_probability_move': x/1000.0})
        labels.append(1 if i >= 20 else 0)
    model=train_logistic(rows,labels,feature_names=('large_market_depth','small_probability_move'),epochs=80)
    restored=Model.loads(model.dumps())
    probe={'large_market_depth': 35000.0, 'small_probability_move': .035}
    assert abs(model.predict(probe)-restored.predict(probe)) < 1e-12
    assert restored.metadata['standardization'] == 'training_population_zscore_v1'


def test_interaction_scaler_survives_round_trip():
    rows=[]; labels=[]
    for i in range(40):
        a=(i%10)/10.0
        b=((i*3)%10)/10.0
        rows.append({'a':a,'b':b})
        labels.append(1 if a*b > .2 else 0)
    model=train_logistic(rows,labels,feature_names=('a','b','a__x__b'),epochs=80)
    restored=Model.loads(model.dumps())
    assert abs(model.predict({'a':.8,'b':.7})-restored.predict({'a':.8,'b':.7})) < 1e-12
