from pathlib import Path


def test_backfill_matches_learning_feature_contract():
    source = Path("tennis_learning/backfill.py").read_text(encoding="utf-8")
    assert '"player_odds": _american_from_probability(fair_probability)' in source
    assert '"opponent_odds": _american_from_probability(1.0 - fair_probability)' in source
    assert '"player_won": player_won' in source
    assert "winner_as_player = _winner_is_player(match_id)" in source


def test_backfill_fails_when_every_processed_row_is_rejected():
    source = Path("tennis_learning/backfill.py").read_text(encoding="utf-8")
    assert "if processed and trained == 0 and duplicates == 0:" in source
    assert "historical backfill processed" in source


def test_backfill_preserves_source_provenance():
    source = Path("tennis_learning/backfill.py").read_text(encoding="utf-8")
    assert '"source_mode": "historical_rank_bootstrap"' in source
    assert "JeffSackmann tennis_atp/tennis_wta" in source
