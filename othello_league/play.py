from . import board as B
from . import engine as E
from .dojo import effective_params
import random

MAX_REMATCH_ATTEMPTS = 3  # 引き分けが続いた場合の再戦上限（それでも決着しなければ引き分け扱い）


def _play(params_black, params_white, depth_black, depth_white,
          noise_scale_black=1.0, noise_scale_white=1.0, max_moves=64):
    """1局対局し、(黒石数, 白石数, 着手履歴) を返す"""
    bd = B.initial_board()
    color = B.BLACK
    move_history = []

    for _ in range(max_moves):
        if B.is_game_over(bd):
            break
        moves = B.legal_moves(bd, color)
        if not moves:
            color = B.opponent(color)
            continue

        params = params_black if color == B.BLACK else params_white
        depth = depth_black if color == B.BLACK else depth_white
        noise_scale = noise_scale_black if color == B.BLACK else noise_scale_white
        mv = E.choose_move(bd, color, params, depth=depth, noise_scale=noise_scale)
        B.apply_move(bd, mv, color)
        move_history.append({"pos": mv, "color": color})
        color = B.opponent(color)

    black, white = B.count_discs(bd)
    return black, white, move_history


def _outcome_from_score(my_score, opp_score):
    if my_score > opp_score:
        return "win"
    elif my_score < opp_score:
        return "loss"
    return "draw"


def play_league_match(ind_a, ind_b, depth=4, allow_rematch=True):
    """
    個体同士のリーグ戦対局。道場バフを反映したパラメータを使う。
    先後は「それぞれの通算黒番回数が少ない方」を優先的に黒にすることで、
    運任せではなく確実に均等（またはそれに近い状態）を保つ。
    引き分けの場合、先後を入れ替えて再戦する（allow_rematch=Trueの場合、規定回数まで）。
    それでも決着しなければ、最終的に引き分けとして確定する。
    """
    params_a = effective_params(ind_a)
    params_b = effective_params(ind_b)

    # 黒番回数が少ない方を優先して黒にする。全く同数なら、五分五分の抽選で決める（ここだけは偏りようがない）
    if ind_a.black_count < ind_b.black_count:
        a_is_black = True
    elif ind_a.black_count > ind_b.black_count:
        a_is_black = False
    else:
        a_is_black = random.choice([True, False])

    all_games = []

    attempts = MAX_REMATCH_ATTEMPTS if allow_rematch else 1
    for attempt in range(attempts):
        if a_is_black:
            black, white, moves = _play(params_a, params_b, depth, depth,
                                         noise_scale_black=ind_a.volatility, noise_scale_white=ind_b.volatility)
            ind_a.black_count += 1; ind_b.white_count += 1
        else:
            black, white, moves = _play(params_b, params_a, depth, depth,
                                         noise_scale_black=ind_b.volatility, noise_scale_white=ind_a.volatility)
            ind_b.black_count += 1; ind_a.white_count += 1

        my_score = black if a_is_black else white
        opp_score = white if a_is_black else black
        outcome_a = _outcome_from_score(my_score, opp_score)

        all_games.append({
            "outcome_a": outcome_a, "moves": moves, "black": black, "white": white,
            "a_was_black": a_is_black,
        })

        if outcome_a != "draw":
            return outcome_a, all_games

        a_is_black = not a_is_black  # 先後を入れ替えて再戦

    # 規定回数まで引き分けが続いた場合は、引き分けとして確定する
    return "draw", all_games
