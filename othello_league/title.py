import random

from . import board as B
from . import engine as E
from .dojo import effective_params

MAX_DRAW_REPLAY = 5  # 引き分けが続いた場合、その1局分を最大何回まで打ち直すか


def _play_decisive_game(params_black, params_white, depth):
    """
    1局対局し、必ず勝敗が付くまで打ち直す（タイトル戦の"1局"としてカウントするため）。
    MAX_DRAW_REPLAY回試しても決着しない場合は、コイントスで決める（安全策）。
    """
    for _ in range(MAX_DRAW_REPLAY):
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
            mv = E.choose_move(bd, color, params, depth=depth)
            B.apply_move(bd, mv, color)
            move_history.append({"pos": mv, "color": color})
            color = B.opponent(color)

        black, white = B.count_discs(bd)
        if black != white:
            winner = "black" if black > white else "white"
            return winner, black, white, move_history

    # 安全策：規定回数打ち直しても決着しない場合はコイントス
    winner = random.choice(["black", "white"])
    return winner, black, white, move_history


def run_best_of_n_match(params_a, params_b, wins_needed, depth):
    """
    先取制のタイトル戦本戦。1局ごとに先後を入れ替える（初戦はAが黒）。
    戻り値: (challenger_won: bool, a_wins, b_wins, games_log)
    """
    a_wins, b_wins = 0, 0
    games_log = []
    game_num = 0

    while a_wins < wins_needed and b_wins < wins_needed:
        a_is_black = (game_num % 2 == 0)
        if a_is_black:
            winner, black, white, moves = _play_decisive_game(params_a, params_b, depth)
        else:
            winner, black, white, moves = _play_decisive_game(params_b, params_a, depth)

        a_won_this_game = (winner == "black") == a_is_black
        if a_won_this_game:
            a_wins += 1
        else:
            b_wins += 1

        games_log.append({
            "game_num": game_num + 1, "a_was_black": a_is_black,
            "black": black, "white": white, "winner": "a" if a_won_this_game else "b",
            "moves": moves,
        })
        print(f"      第{game_num + 1}局: {'挑戦者' if a_won_this_game else 'ホルダー'}の勝ち（{black}-{white}）"
              f"　現在 挑戦者{a_wins}勝 - ホルダー{b_wins}勝")
        game_num += 1

    return a_wins > b_wins, a_wins, b_wins, games_log


# ============================================================
# 陸王戦：Aリーグ優勝者が自動でタイトルホルダーに挑戦。7局制4本先取
# ============================================================
def run_rikuou_challenge(a_champion, titleholder_params, depth=4):
    challenger_params = effective_params(a_champion)
    won, a_wins, b_wins, games = run_best_of_n_match(challenger_params, titleholder_params, wins_needed=4, depth=depth)
    return {
        "title": "陸王", "challenger_id": a_champion.id, "won": won,
        "challenger_wins": a_wins, "titleholder_wins": b_wins, "games": games,
    }


# ============================================================
# 海王戦：段階的な勝ち上がり（ラダー）方式で挑戦者を決定。5局制3本先取
# 予選: B1 vs C1 → 勝者 vs A6 → 勝者 vs A5 → 勝者 vs A4 → 勝者 vs A3
#      → 勝者 vs A2（前陸王 or Aリーグ総当たり1位）→ 勝者 vs A1（陸王 or 新陸王）→ 挑戦者決定
# ============================================================
def determine_kaiou_challenger(a_slots, b1, c1, depth=4):
    """
    a_slots: [A1, A2, A3, A4, A5, A6] の6名（陸王の在位状況に応じて run_season.py 側で組み立てる）。
    A1が海王在位者自身と同一人物の場合（自分自身への挑戦を避けるため）、
    run_season.py側でA1にNoneを渡すことで、その関門を不戦勝扱いにできる。
    """
    bracket_log = []

    def single_game(ind_x, ind_y):
        if ind_x is None:
            return ind_y
        if ind_y is None:
            return ind_x
        winner, black, white, moves = _play_decisive_game(
            effective_params(ind_x), effective_params(ind_y), depth,
        )
        winner_ind = ind_x if winner == "black" else ind_y
        bracket_log.append({
            "a": ind_x.id, "b": ind_y.id, "winner": winner_ind.id,
            "black": black, "white": white, "moves": moves,
        })
        return winner_ind

    a1, a2, a3, a4, a5, a6 = a_slots
    winner = single_game(b1, c1)
    winner = single_game(winner, a6)
    winner = single_game(winner, a5)
    winner = single_game(winner, a4)
    winner = single_game(winner, a3)
    winner = single_game(winner, a2)
    challenger = single_game(winner, a1)

    return challenger, bracket_log


def run_kaiou_challenge(challenger, titleholder_params, depth=4):
    challenger_params = effective_params(challenger)
    won, c_wins, t_wins, games = run_best_of_n_match(challenger_params, titleholder_params, wins_needed=3, depth=depth)
    return {
        "title": "海王", "challenger_id": challenger.id, "won": won,
        "challenger_wins": c_wins, "titleholder_wins": t_wins, "games": games,
    }


# ============================================================
# 空王戦：Elo上位8名（前年空王在位者は防衛専念枠として除外）による正式シード付きトーナメント。5局制3本先取
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


def determine_kuuou_challenger(all_members, exclude_id=None, depth=4, top_n=8):
    """
    Elo上位top_n名（既定8名）による正式シードトーナメント。
    前年空王在位者（exclude_id）は防衛専念枠のため、この母集団からは除外する。
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
        winner, black, white, moves = _play_decisive_game(
            effective_params(ind_x), effective_params(ind_y), depth,
        )
        winner_ind = ind_x if winner == "black" else ind_y
        bracket_log.append({
            "a": ind_x.id, "b": ind_y.id, "winner": winner_ind.id,
            "black": black, "white": white, "moves": moves,
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


def run_kuuou_challenge(challenger, titleholder_params, depth=4):
    challenger_params = effective_params(challenger)
    won, c_wins, t_wins, games = run_best_of_n_match(challenger_params, titleholder_params, wins_needed=3, depth=depth)
    return {
        "title": "空王", "challenger_id": challenger.id, "won": won,
        "challenger_wins": c_wins, "titleholder_wins": t_wins, "games": games,
    }
