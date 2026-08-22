from mlb_auto.evolution import candidate_numeric_features, rank_features, discover_challenger


def _rows(n=140):
    rows=[]; labels=[]
    for i in range(n):
        signal=(i % 10) / 10.0
        label=1 if signal >= .5 else 0
        rows.append({
            'market_home_probability': .35 + .3*signal,
            'new_provider_signal': signal,
            'noise_signal': ((i*7)%13)/13.0,
            'book_divergence': .01 + .005*(i%3),
            'hours_to_first_pitch': 8 - (i%8),
            'outcome': label,
        })
        labels.append(label)
    return rows, labels


def test_outcome_fields_are_never_candidates():
    rows,labels=_rows()
    names=candidate_numeric_features(rows)
    assert 'new_provider_signal' in names
    assert 'outcome' not in names


def test_new_signal_can_be_discovered_without_hardcoding():
    rows,labels=_rows()
    ranked=rank_features(rows[:100],labels[:100])
    assert 'new_provider_signal' in ranked[:5]


def test_autonomous_challenger_search_returns_auditable_manifest():
    rows,labels=_rows()
    result=discover_challenger(rows,labels,min_train=80,min_validation=20)
    assert result.metrics['logLoss'] >= 0
    assert result.search_manifest['selectedFeatureCount'] > 0
    assert result.search_manifest['trainingRows'] >= 80
    assert result.search_manifest['validationRows'] >= 20
    assert result.model.metadata['autonomous'] is True
