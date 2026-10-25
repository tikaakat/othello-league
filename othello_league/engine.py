"""
オセロの評価関数とミニマックス（アルファベータ枝刈り）探索。

評価関数は「個性パラメータ」で重み付けされた8つの指標の合計：
  - corner_weight        角の確保
  - danger_zone_weight   角周りの危険マスを避ける
  - mobility_weight      着手可能数のコントロール
  - edge_stability_weight 辺全体の安定石
  - frontier_weight      開放度（守り重視か前進重視か）
  - disc_weight          石数そのもの（終盤ほど重要度が上がる）
  - parity_weight        パリティ（終盤の手番管理）
  - center_weight        序盤の中央支配

計算コストを抑えるため、どの指標も軽量な集計のみで求める
（盤面全体を1〜2回走査する程度。探索の重さには影響しない）。
"""
import math
import random

from . import board as B
from .skills import BOOK_PARAMS

CORNERS = {0, 7, 56, 63}
X_SQUARES = {9, 14, 49, 54}       # 角の斜め隣（一番危険なマス）
C_SQUARES = {1, 6, 8, 15, 48, 55, 57, 62}  # 角の辺隣（やや危険なマス）
EDGE_SQUARES = {i for i in range(64) if i < 8 or i >= 56 or i % 8 == 0 or i % 8 == 7} - CORNERS
CENTER_SQUARES = {27, 28, 35, 36, 20, 21, 22, 25, 26, 29, 30, 33, 34, 37, 38, 41, 42, 43}

# 手の並び替え用の静的なマス評価（角を高く、危険マスを低く）。
# 探索そのものの評価には使わず、アルファベータの枝刈り効率を上げるためだけに使う。
_STATIC_WEIGHTS = [0] * 64
for _sq in CORNERS:
    _STATIC_WEIGHTS[_sq] = 100
for _sq in X_SQUARES:
    _STATIC_WEIGHTS[_sq] = -50
for _sq in C_SQUARES:
    _STATIC_WEIGHTS[_sq] = -20



# 評価ノイズの基準幅（evaluate()末尾のコメント参照）
EVAL_NOISE_BASE = 5.0

def _order_moves(moves):
    """良さそうな手を先に並べる（枝刈り効率を上げるためだけの処理）"""
    return sorted(moves, key=lambda mv: -_STATIC_WEIGHTS[mv])


def _neighbors(sq):
    row, col = divmod(sq, 8)
    return tuple((row + dr) * 8 + (col + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                 if (dr or dc) and 0 <= row + dr < 8 and 0 <= col + dc < 8)


# 各マスの隣接マス（起動時に一度だけ計算。_is_frontierで毎回盤端を判定し直さないため）
_NEIGHBORS = tuple(_neighbors(sq) for sq in range(64))


def _is_frontier(bd, sq):
    """空きマスに隣接している（＝将来ひっくり返されるリスクがある）かどうか"""
    for n in _NEIGHBORS[sq]:
        if bd[n] == B.EMPTY:
            return True
    return False


def evaluate(bd, color, params, noise_scale=1.0):
    """color視点での評価値（正が有利）。paramsは個性パラメータの辞書（8項目）"""
    opp = B.opponent(color)
    phase = B.game_phase(bd)

    # --- 角の確保 ---
    my_corners = sum(1 for c in CORNERS if bd[c] == color)
    opp_corners = sum(1 for c in CORNERS if bd[c] == opp)
    corner_score = (my_corners - opp_corners) * 25

    # --- 危険マス（角がまだ取られていない場合のペナルティ）---
    danger_score = 0
    for sq in X_SQUARES:
        if bd[sq] == color:
            danger_score -= 1
        elif bd[sq] == opp:
            danger_score += 1
    for sq in C_SQUARES:
        if bd[sq] == color:
            danger_score -= 0.5
        elif bd[sq] == opp:
            danger_score += 0.5

    # --- 機動力（着手可能数の差） ---
    my_moves_list = B.legal_moves(bd, color)
    opp_moves_list = B.legal_moves(bd, opp)
    my_moves, opp_moves = len(my_moves_list), len(opp_moves_list)
    total_moves = my_moves + opp_moves
    mobility_score = 100 * (my_moves - opp_moves) / total_moves if total_moves > 0 else 0

    # --- 辺の安定石（外周ラインで、自分の石がどれだけ多いか。簡易近似） ---
    my_edge = sum(1 for sq in EDGE_SQUARES if bd[sq] == color)
    opp_edge = sum(1 for sq in EDGE_SQUARES if bd[sq] == opp)
    total_edge = my_edge + opp_edge
    edge_stability_score = 100 * (my_edge - opp_edge) / total_edge if total_edge > 0 else 0

    # --- 開放度（空きマスに隣接する自分の石が多いほど不利、という考え方） ---
    my_frontier = opp_frontier = 0
    for sq in range(64):
        v = bd[sq]
        if v == B.EMPTY:
            continue
        if _is_frontier(bd, sq):
            if v == color:
                my_frontier += 1
            else:
                opp_frontier += 1
    total_frontier = my_frontier + opp_frontier
    frontier_score = 100 * (opp_frontier - my_frontier) / total_frontier if total_frontier > 0 else 0

    # --- 石数（終盤ほど重要） ---
    black, white = B.count_discs(bd)
    my_discs = black if color == B.BLACK else white
    opp_discs = white if color == B.BLACK else black
    total_discs = my_discs + opp_discs
    disc_score = 100 * (my_discs - opp_discs) / total_discs if total_discs > 0 else 0

    # --- パリティ（残り空きマス数の偶奇。終盤の手番管理の簡易近似） ---
    empty_count = 64 - black - white
    parity_score = 0
    if phase == "endgame":
        # 空きマスが偶数なら、先に打つ側がやや不利になりやすい、という簡易ヒューリスティック
        parity_score = 10 if (empty_count % 2 == 0) == (color != B.BLACK) else -10

    # --- 序盤の中央支配 ---
    my_center = sum(1 for sq in CENTER_SQUARES if bd[sq] == color)
    opp_center = sum(1 for sq in CENTER_SQUARES if bd[sq] == opp)
    total_center = my_center + opp_center
    center_score = 100 * (my_center - opp_center) / total_center if total_center > 0 else 0

    # --- フェーズごとの重み調整（石数は終盤ほど、中央支配は序盤ほど重要） ---
    phase_disc_multiplier = {"opening": 0.1, "midgame": 0.4, "endgame": 1.5}[phase]
    phase_center_multiplier = {"opening": 1.5, "midgame": 0.5, "endgame": 0.0}[phase]

    score = (
        params["corner_weight"] * corner_score
        + params["danger_zone_weight"] * danger_score
        + params["mobility_weight"] * mobility_score
        + params["edge_stability_weight"] * edge_stability_score
        + params["frontier_weight"] * frontier_score
        + params["disc_weight"] * disc_score * phase_disc_multiplier
        + params["parity_weight"] * parity_score
        + params["center_weight"] * center_score * phase_center_multiplier
    )

    # ごく僅かなランダムノイズを加える。同じ相手・同じ先後の組み合わせでも、
    # 毎回全く同じ対局が再現されてしまう（決定論的すぎる）のを防ぐための"揺らぎ"。
    # 検証の結果、±1.5では評価スコアの規模（数百〜数千）に対して小さすぎて実質無効だった一方、
    # ±30以上では逆にノイズが実力差そのものを打ち消してしまい、進化の意味が失われることが判明した。
    # 当初は±10としていたが、実力差が勝敗に出にくく、レーティングのばらつき（標準偏差）が
    # プロ将棋（約150）に対して66程度と小さすぎた。現役12人の総当たりシミュレーションで、
    # ±5（半分）にすると実力差が約1.5倍に開く一方、それ以上小さくすると同じ組み合わせで
    # 似た対局が続き先後で勝敗が決まりやすくなり、かえって差が縮むことを確認したため±5とした。
    # noise_scaleは個体固有の「ムラ気」パラメータで、この基準値(±5)をさらに倍率調整する。
    score += random.uniform(-EVAL_NOISE_BASE, EVAL_NOISE_BASE) * noise_scale

    return score


def minimax(bd, color, depth, alpha, beta, maximizing_color, params, noise_scale=1.0):
    # 末端（depth == 0）では合法手を使わないので、列挙する前に評価して返す
    # （以前は末端でも合法手を列挙しており、その分だけ無駄に時間がかかっていた。結果は同じ）
    if depth == 0:
        return evaluate(bd, maximizing_color, params, noise_scale), None

    moves = B.legal_moves(bd, color)

    if not moves:
        opp_moves = B.legal_moves(bd, B.opponent(color))
        if not opp_moves:
            return evaluate(bd, maximizing_color, params, noise_scale), None  # 両者とも打てず終局
        value, _ = minimax(bd, B.opponent(color), depth - 1, alpha, beta, maximizing_color, params, noise_scale)
        return value, None

    moves = _order_moves(moves)
    best_move = None
    is_maximizing = (color == maximizing_color)

    if is_maximizing:
        value = -math.inf
        for mv in moves:
            new_bd = bd.copy()
            B.apply_move(new_bd, mv, color)
            child_value, _ = minimax(new_bd, B.opponent(color), depth - 1, alpha, beta, maximizing_color, params, noise_scale)
            if child_value > value:
                value, best_move = child_value, mv
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return value, best_move
    else:
        value = math.inf
        for mv in moves:
            new_bd = bd.copy()
            B.apply_move(new_bd, mv, color)
            child_value, _ = minimax(new_bd, B.opponent(color), depth - 1, alpha, beta, maximizing_color, params, noise_scale)
            if child_value < value:
                value, best_move = child_value, mv
            beta = min(beta, value)
            if alpha >= beta:
                break
        return value, best_move


def choose_move(bd, color, params, depth=5, noise_scale=1.0):
    """個体のパラメータを使って1手選ぶ。noise_scaleは個体固有の「ムラ気」倍率。
    paramsに終盤力（_endgame）・定跡（_book）があれば反映する（skills.py参照）：
      - 序盤、定跡を知っている手数まで（空きマスが 60-定跡 以上）は、共通の強い評価・揺らぎなしで指す
      - 空きマスが終盤力以下になったら、読みを1手深くする"""
    moves = B.legal_moves(bd, color)
    if not moves:
        return None
    book = params.get("_book", 0)
    endgame = params.get("_endgame", 0)
    if book or endgame:
        empty = bd.count(B.EMPTY)
        if book and empty >= 60 - book:
            params, noise_scale = BOOK_PARAMS, 0.0
        elif endgame and empty <= endgame:
            depth += 1
    _, move = minimax(bd, color, depth, -math.inf, math.inf, color, params, noise_scale)
    if move is None:
        move = random.choice(moves)
    return move
