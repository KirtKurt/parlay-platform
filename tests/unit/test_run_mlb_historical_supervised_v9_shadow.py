from scripts import run_mlb_historical_supervised_v9_shadow as report


class Runtime:
    @staticmethod
    def select_winner(home, away, policy):
        hp = float(home["p"])
        ap = 1.0 - hp
        scored_home = {"winProbability": hp, "score": hp * 100}
        scored_away = {"winProbability": ap, "score": ap * 100}
        return (scored_home if hp >= ap else scored_away), scored_home, scored_away


def _row(index, p, home_won):
    return {
        "slateDateEt": f"2026-07-{index:02d}",
        "officialGamePk": index,
        "homeTeam": f"HOME-{index}",
        "awayTeam": f"AWAY-{index}",
        "homeWon": home_won,
        "homeSignal": {"p": p},
        "awaySignal": {"p": 1.0 - p},
        "trainingEligible": True,
        "canonicalLockValid": True,
        "duplicateContaminated": False,
        "featureCutoff": "each_game_t_minus_45",
        "fingerprint": f"fp-{index}",
    }


def test_v9_report_emits_strong_lean_and_pass_bands():
    value = report._diagnostic_pick_rows(
        [_row(1, 0.72, True), _row(2, 0.60, False), _row(3, 0.53, True)],
        {},
        Runtime,
    )
    assert value["bandCounts"] == {"MLB_STRONG": 1, "MLB_LEAN": 1, "PASS": 1}
    assert value["selectedGameCount"] == 2
    assert [row["selectedPickBand"] for row in value["games"]] == ["MLB_STRONG", "MLB_LEAN", "PASS"]
    assert value["games"][0]["predictedWinner"] == "HOME-1"
    assert value["games"][1]["predictedWinner"] == "HOME-2"
    assert value["games"][2]["correct"] is None
    assert value["diagnosticOnly"] is True
    assert value["productionAuthority"] is False


def test_v9_report_can_limit_to_partition_dates():
    rows = [_row(1, 0.72, True), _row(2, 0.60, False)]
    value = report._diagnostic_pick_rows(rows, {}, Runtime, ["2026-07-02"])
    assert value["gameCount"] == 1
    assert value["games"][0]["gameId"] == "2"
