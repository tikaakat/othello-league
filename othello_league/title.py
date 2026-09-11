# ============================================================
# 朱雀戦：C1×B1の勝者がA1と対戦→Y、A2×D1の勝者がA0（青龍）と対戦→W、Y×Wで挑戦者決定。
# 5局制3本先取
# ============================================================
def determine_suzaku_challenger(a0, a1, a2, b1, c1, d1, depth=4):
    """
    a0=青龍在位者（Aリーグの防衛専念枠）、a1/a2=Aリーグ総当たりの実質1位・2位、
    b1/c1/d1=B/C/Dリーグの今季1位。
    朱雀在位者自身と同一人物のスロットがあれば、run_season.py側でそのスロットにNoneを渡すことで
    不戦勝扱いにできる（どのスロットでも安全に機能する）。
    """
    bracket_log = []

    def single_game(ind_x, ind_y):
        if ind_x is None:
            return ind_y
        if ind_y is None:
            return ind_x
        x_won, games = _play_until_decided(
            effective_params(ind_x), effective_params(ind_y), depth,
            noise_x=ind_x.volatility, noise_y=ind_y.volatility,
        )
        winner_ind = ind_x if x_won else ind_y
        bracket_log.append({
            "a": ind_x.id, "b": ind_y.id, "winner": winner_ind.id, "games": games,
        })
        return winner_ind

    x = single_game(c1, b1)
    y = single_game(x, a1)
    z = single_game(a2, d1)
    w = single_game(z, a0)
    challenger = single_game(y, w)

    return challenger, bracket_log


def run_suzaku_challenge(challenger, titleholder_params, depth=4, titleholder_volatility=1.0):
    challenger_params = effective_params(challenger)
    won, c_wins, t_wins, games = run_best_of_n_match(
        challenger_params, titleholder_params, wins_needed=3, depth=depth,
        noise_a=challenger.volatility, noise_b=titleholder_volatility,
    )
    return {
        "title": "朱雀", "challenger_id": challenger.id, "won": won,
        "challenger_wins": c_wins, "titleholder_wins": t_wins, "games": games,
    }
