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
# 海王戦：変則トーナメントで挑戦者を決定。5局制3本先取
# A1=スーパーシード(決勝から), A2=シード(準決勝から), A3=シード(準々決勝から)
# B1 vs C1 → 勝者 vs A3 → 勝者 vs A2 → 勝者 vs A1 → 挑戦者決定
# ============================================================
def determine_kaiou_challenger(a1, a2, a3, b1, c1, depth=4):
    bracket_log = []

    def single_game(ind_x, ind_y):
        winner, black, white, moves = _play_decisive_game(
            effective_params(ind_x), effective_params(ind_y), depth,
        )
        winner_ind = ind_x if winner == "black" else ind_y
        bracket_log.append({
            "a": ind_x.id, "b": ind_y.id, "winner": winner_ind.id,
            "black": black, "white": white, "moves": moves,
        })
        return winner_ind

    w1 = single_game(b1, c1)
    w2 = single_game(w1, a3)
    w3 = single_game(w2, a2)
    challenger = single_game(w3, a1)

    return challenger, bracket_log


def run_kaiou_challenge(challenger, titleholder_params, depth=4):
    challenger_params = effective_params(challenger)
    won, c_wins, t_wins, games = run_best_of_n_match(challenger_params, titleholder_params, wins_needed=3, depth=depth)
    return {
        "title": "海王", "challenger_id": challenger.id, "won": won,
        "challenger_wins": c_wins, "titleholder_wins": t_wins, "games": games,
    }


# ============================================================
# 空王戦：全員参加トーナメントで挑戦者決定（上位リーグほどシード優遇）。5局制3本先取
# ============================================================
LEAGUE_SEED_PRIORITY = {"A": 0, "B": 1, "C": 2, "D": 3}  # 数字が小さいほど上位シード


def determine_kuuou_challenger(all_members, depth=4):
    """
    全員参加のシード付きトーナメント。
    上位リーグ・上位順位の個体ほど、初戦の相手が弱い（下位）位置に配置される単純なシード方式。
    """
    seeded = sorted(all_members, key=lambda ind: (LEAGUE_SEED_PRIORITY.get(ind.league, 9), ind.elo * -1))

    # 参加人数を2のべき乗に切り詰める（余りはシード上位が不戦勝で温存される簡易処理）
    n = len(seeded)
    bracket_size = 1
    while bracket_size * 2 <= n:
        bracket_size *= 2
    participants = seeded[:bracket_size]

    bracket_log = []
    round_members = participants

    while len(round_members) > 1:
        next_round = []
        for i in range(0, len(round_members), 2):
            ind_x, ind_y = round_members[i], round_members[i + 1]
            winner, black, white, moves = _play_decisive_game(
                effective_params(ind_x), effective_params(ind_y), depth,
            )
            winner_ind = ind_x if winner == "black" else ind_y
            bracket_log.append({
                "a": ind_x.id, "b": ind_y.id, "winner": winner_ind.id,
                "black": black, "white": white, "moves": moves,
            })
            next_round.append(winner_ind)
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
