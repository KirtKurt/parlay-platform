import ks1.historical_individual_bullpen_enrichment as subject


def profile(player_id, appearances_30d, *, fip7, fip15, fip30, kbb7, kbb15, kbb30):
    return {
        "player_id": str(player_id),
        "windows": {
            "7d": {
                "appearances": 99,
                "fip": fip7,
                "era": fip7 + .2,
                "k_bb_pct": kbb7,
            },
            "15d": {
                "appearances": 88,
                "fip": fip15,
                "era": fip15 + .2,
                "k_bb_pct": kbb15,
            },
            "30d": {
                "appearances": appearances_30d,
                "fip": fip30,
                "era": fip30 + .2,
                "k_bb_pct": kbb30,
            },
        },
    }


def test_recency_windows_keep_rank_identity_bound_to_prior_30d_usage():
    values = subject._ranked_values([
        profile(20, 8, fip7=2.1, fip15=2.5, fip30=2.9,
                kbb7=25, kbb15=22, kbb30=19),
        profile(10, 9, fip7=3.1, fip15=3.4, fip30=3.7,
                kbb7=20, kbb15=18, kbb30=16),
        profile(30, 7, fip7=4.1, fip15=4.3, fip30=4.6,
                kbb7=12, kbb15=11, kbb30=9),
    ])

    # Rank is fixed by prior 30-day appearances, not by whichever recency metric looks best.
    assert values["individual_bullpen_rank1_fip_7d"] == 3.1
    assert values["individual_bullpen_rank1_fip_15d"] == 3.4
    assert values["individual_bullpen_rank1_fip_30d"] == 3.7
    assert values["individual_bullpen_rank2_fip_7d"] == 2.1
    assert values["individual_bullpen_rank2_k_bb_pct_15d"] == 22.0
    assert values["individual_bullpen_rank3_k_bb_pct_30d"] == 9.0
    assert subject.WINDOWS == (7, 15, 30)
    assert subject.CONTRACT.endswith("-v2")
