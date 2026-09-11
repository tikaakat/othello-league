import random

from . import board as B
from . import engine as E
from .dojo import effective_params


def _play_one_game(params_black, params_white, depth, noise_black=1.0, noise_white=1.0):
    """1局対局し、(勝敗 'black'/'white'/'draw', 黒石数, 白石数, 着手履歴) を返す。打ち直しは行わない"""
    bd = B.initial_board()
    color = B.BLACK
    move_history = []
    for _ in range(64):
        if B.is_game_over(bd):
            break
        moves = B.legal_moves(bd, color)
        if not moves:
            color = B.opponent(color)
            continue
        params = params_black if color == B.BLACK else params_white
        noise_scale = noise_black if color == B.BLACK else noise_white
        mv = E.choose_move(bd, color, params, depth=depth, noise_scale=noise_scale)
        B.apply_move(bd, mv, color)
        move_history.append({"pos": mv, "color": color})
        color = B.opponent(color)

    black, white = B.count_discs(bd)
    if black > white:
        result = "black"
    elif white > black:
        result = "white"
    else:
        result = "draw"
    return result, black, white, move_history


def run_best_of_n_match(params_a, params_b, wins_needed, depth, noise_a=1.0, noise_b=1.0):
    """
    先取制のタイトル戦本戦。1局ごとに先後を入れ替える（初戦はAが黒）。
    引き分けの局も「1局」として記録するが、先取数には加算しない（将棋の千日手と同様の扱い）。
    どちらかが規定数を先取するまで対局を続ける。
    戻り値: (challenger_won: bool, a_wins, b_wins, games_log)
    """
    a_wins, b_wins = 0, 0
    games_log = []
    game_num = 0
    # 安全策：極端な連続引き分けでプロセスが実質無限ループにならないよう、上限だけは設ける
    # （通常のプレイでは到達しない、十分大きな値）
    MAX_GAMES_SAFETY = 500

    while a_wins < wins_needed and b_wins < wins_needed and game_num < MAX_GAMES_SAFETY:
        a_is_black = (game_num % 2 == 0)
        if a_is_black:
            result, black, white, moves = _play_one_game(params_a, params_b, depth, noise_a, noise_b)
        else:
            result, black, white, moves = _play_one_game(params_b, params_a, depth, noise_b, noise_a)

        if result == "draw":
            outcome_for_log = "draw"
            note = "　（引き分け。先取数には加算せず）"
        else:
            a_won_this_game = (result == "black") == a_is_black
            if a_won_this_game:
                a_wins += 1
                outcome_for_log = "a"
            else:
                b_wins += 1
                outcome_for_log = "b"
            note = ""

        games_log.append({
            "game_num": game_num + 1, "a_was_black": a_is_black,
            "black": black, "white": white, "winner": outcome_for_log,
            "moves": moves,
        })
        if outcome_for_log == "draw":
            print(f"      第{game_num + 1}局: 引き分け（{black}-{black}）{note}"
                  f"　現在 挑戦者{a_wins}勝 - ホルダー{b_wins}勝")
        else:
            winner_label = "挑戦者" if outcome_for_log == "a" else "ホルダー"
            print(f"      第{game_num + 1}局: {winner_label}の勝ち（{black}-{white}）"
                  f"　現在 挑戦者{a_wins}勝 - ホルダー{b_wins}勝")
        game_num += 1

    return a_wins > b_wins, a_wins, b_wins, games_log


# ============================================================
# 青龍戦：Aリーグ優勝者が自動でタイトルホルダーに挑戦。7局制4本先取
# ============================================================
def run_seiryuu_challenge(a_champion, titleholder_params, depth=, titleholder_volatility=1.0):
    challenger_params = effective_params(a_champion)
    won, a_wins, b_wins, games = run_best_of_n_match(
        challenger_params, titleholder_params, wins_needed=4, depth=3,
        noise_a=a_champion.volatility, noise_b=titleholder_volatility,
    )
    return {
        "title": "青龍", "challenger_id": a_champion.id, "won": won,
        "challenger_wins": a_wins, "titleholder_wins": b_wins, "games": games,
    }


def _play_until_decided(params_x, params_y, depth, noise_x=1.0, noise_y=1.0, max_attempts=100):
    """
    先後を入れ替えながら、決着がつくまで打ち直す（B〜Dリーグの引き分け処理と同じ考え方）。
    予選（トーナメント・ラダー）は勝者を1人に絞る必要があるため、この方式を使う。
    全ての対局（引き分けも含む）を記録し、最後に決着した対局の勝者を返す。
    """
    x_is_black = True
    games = []
    for _ in range(max_attempts):
        if x_is_black:
            result, black, white, moves = _play_one_game(params_x, params_y, depth, noise_x, noise_y)
        else:
            result, black, white, moves = _play_one_game(params_y, params_x, depth, noise_y, noise_x)
        games.append({
            "black": black, "white": white, "moves": moves,
            "x_was_black": x_is_black, "result": result,
        })
        if result != "draw":
            x_won = (result == "black") == x_is_black
            return x_won, games
        x_is_black = not x_is_black
    # 安全策（実質到達しない想定）：それでも決着しなければXを勝者扱いにする
    return True, games


# ============================================================
# 朱雀戦：C1×B1の勝者がA1と対戦→Y、A2×D1の勝者がA0（青龍）と対戦→W、Y×Wで挑戦者決定。
# 5局制3本先取
# ============================================================
def determine_suzaku_challenger(a0, a1, a2, b1, c1, d1, depth=2):
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


def run_suzaku_challenge(challenger, titleholder_params, depth=, titleholder_volatility=1.0):
    challenger_params = effective_params(challenger)
    won, c_wins, t_wins, games = run_best_of_n_match(
        challenger_params, titleholder_params, wins_needed=3, depth=depth,
        noise_a=challenger.volatility, noise_b=titleholder_volatility,
    )
    return {
        "title": "朱雀", "challenger_id": challenger.id, "won": won,
        "challenger_wins": c_wins, "titleholder_wins": t_wins, "games": games,
    }


# ============================================================
# 白虎戦：Elo上位16名（前年白虎在位者は防衛専念枠として除外）による正式シード付きトーナメント。5局制3本先取
# ============================================================
def _bracket_seed_order(n):
    """
    標準的なトーナメントのシード配置順を返す（0-indexed）。
    例：n=8 → [0,7,3,4,1,6,2,5]（1位vs8位、4位vs5位、2位vs7位、3位vs6位）
    この順に並べてから隣同士を組めば、1位と2位は決勝まで当たらない、という
    本来のシード制の性質が保証される。
    """
    if n == 1:
        return [0]
    prev = _bracket_seed_order(n // 2)
    result = []
    for s in prev:
        result.append(s)
        result.append(n - 1 - s)
    return result


def determine_byakko_challenger(all_members, exclude_id=None, depth=, top_n=16):
    """
    Elo上位top_n名（既定16名）による正式シードトーナメント。
    前年白虎在位者（exclude_id）は防衛専念枠のため、この母集団からは除外する。
    """
    pool = [ind for ind in all_members if ind.id != exclude_id]
    ranked = sorted(pool, key=lambda ind: -ind.elo)[:top_n]

    n = len(ranked)
    bracket_size = 1
    while bracket_size < n:
        bracket_size *= 2
    slots = ranked + [None] * (bracket_size - n)

    order = _bracket_seed_order(bracket_size)
    bracketed = [slots[i] for i in order]

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

    round_members = bracketed
    while len(round_members) > 1:
        next_round = []
        for i in range(0, len(round_members), 2):
            winner = single_game(round_members[i], round_members[i + 1])
            next_round.append(winner)
        round_members = next_round

    challenger = round_members[0]
    return challenger, bracket_log


def run_byakko_challenge(challenger, titleholder_params, depth=, titleholder_volatility=1.0):
    challenger_params = effective_params(challenger)
    won, c_wins, t_wins, games = run_best_of_n_match(
        challenger_params, titleholder_params, wins_needed=3, depth=depth,
        noise_a=challenger.volatility, noise_b=titleholder_volatility,
    )
    return {
        "title": "白虎", "challenger_id": challenger.id, "won": won,
        "challenger_wins": c_wins, "titleholder_wins": t_wins, "games": games,
    }


# ============================================================
# 玄武戦：完全ランダム抽選トーナメント（ブラケットサイズ64、Elo上位者は1回戦バイ）。
# 探索深さ2の超早指し戦
# ============================================================
def determine_genbu_challenger(all_members, exclude_id=None, depth=1, bracket_size=64):
    """
    全所属個体が参加する、ほぼ完全ランダムの抽選トーナメント。
    バイ（1回戦不戦勝＝2回戦から登場）の人数は bracket_size - 参加人数 で自動算出し、
    Elo上位からその人数分を割り当てる（上位シード同士が早期に当たらないよう分散配置）。
    バイに入らない残り全員は、完全ランダムに1回戦を組む。
    前年玄武在位者（exclude_id）は防衛専念枠のため、この母集団からは除外する。
    """
    pool = [ind for ind in all_members if ind.id != exclude_id]
    n = len(pool)
    if n > bracket_size:
        # 参加人数がbracket_sizeを超えることは通常想定していないが、
        # 万一超えた場合はElo下位から間引く
        pool = sorted(pool, key=lambda ind: -ind.elo)[:bracket_size]
        n = bracket_size

    bye_count = bracket_size - n
    elo_ranked = sorted(pool, key=lambda ind: -ind.elo)
    seeded = elo_ranked[:bye_count]   # 上位bye_count名：1回戦バイ（2回戦から登場）
    rest = elo_ranked[bye_count:]     # 残り：完全ランダムに1回戦を組む
    random.shuffle(rest)

    # 標準シード配置で、上位シード（バイ勢）を互いに離れた山に均等配置する。
    # Noneは「不在」を表し、Noneと当たった側が不戦勝で2回戦へ進む。
    seed_list = seeded + rest + [None] * bye_count
    order = _bracket_seed_order(bracket_size)
    bracketed = [seed_list[i] for i in order]

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

    round_members = bracketed
    while len(round_members) > 1:
        next_round = []
        for i in range(0, len(round_members), 2):
            winner = single_game(round_members[i], round_members[i + 1])
            next_round.append(winner)
        round_members = next_round

    challenger = round_members[0]
    return challenger, bracket_log


def run_genbu_challenge(challenger, titleholder_params, depth=1, titleholder_volatility=1.0):
    challenger_params = effective_params(challenger)
    won, c_wins, t_wins, games = run_best_of_n_match(
        challenger_params, titleholder_params, wins_needed=3, depth=depth,
        noise_a=challenger.volatility, noise_b=titleholder_volatility,
    )
    return {
        "title": "玄武", "challenger_id": challenger.id, "won": won,
        "challenger_wins": c_wins, "titleholder_wins": t_wins, "games": games,
    }
