"""
オセロの盤面管理。
盤面は 0〜63 のマス番号を持つ、長さ64のリストで表現する。
値：0=空, 1=黒, 2=白
分かりやすさを優先し、ビットボード等の高速化テクニックはあえて使わない。
"""

BLACK = 1
WHITE = 2
EMPTY = 0

DIRECTIONS = [-9, -8, -7, -1, 1, 7, 8, 9]  # 8方向（盤端の判定は別途行う）


def initial_board():
    board = [EMPTY] * 64
    board[27] = WHITE  # d4
    board[28] = BLACK  # e4
    board[35] = BLACK  # d5
    board[36] = WHITE  # e5
    return board


def opponent(color):
    return WHITE if color == BLACK else BLACK


def _on_board(pos):
    return 0 <= pos < 64


def _same_row_step(pos, direction):
    """1マス動いた時に行をまたいでいないか（盤の端で回り込まないか）を確認する"""
    row, col = divmod(pos, 8)
    new_pos = pos + direction
    if not _on_board(new_pos):
        return None
    new_row, new_col = divmod(new_pos, 8)
    if abs(new_row - row) > 1:
        return None  # 上下に2行以上ジャンプ＝盤外扱い
    if abs(new_col - col) > 1:
        return None  # 左右の回り込み
    return new_pos


def _flips_in_direction(board, pos, color, direction):
    """posに color の石を置いたとき、direction方向にひっくり返せる石の座標リストを返す"""
    flips = []
    cur = _same_row_step(pos, direction)
    opp = opponent(color)
    while cur is not None and board[cur] == opp:
        flips.append(cur)
        cur = _same_row_step(cur, direction)
    if cur is not None and board[cur] == color and flips:
        return flips
    return []


def legal_moves(board, color):
    """colorが打てる合法手のマス番号一覧を返す"""
    moves = []
    for pos in range(64):
        if board[pos] != EMPTY:
            continue
        for direction in DIRECTIONS:
            if _flips_in_direction(board, pos, color, direction):
                moves.append(pos)
                break
    return moves


def apply_move(board, pos, color):
    """boardを直接書き換えて着手を反映する（呼び出し側でコピーを渡すこと）"""
    board[pos] = color
    for direction in DIRECTIONS:
        for flip_pos in _flips_in_direction(board, pos, color, direction):
            board[flip_pos] = color


def is_game_over(board):
    return not legal_moves(board, BLACK) and not legal_moves(board, WHITE)


def count_discs(board):
    black = sum(1 for c in board if c == BLACK)
    white = sum(1 for c in board if c == WHITE)
    return black, white


def game_phase(board):
    """序盤/中盤/終盤の判定（空きマス数ベース）。評価関数の重み切り替えに使う"""
    empty = sum(1 for c in board if c == EMPTY)
    if empty > 44:
        return "opening"
    elif empty > 16:
        return "midgame"
    else:
        return "endgame"
