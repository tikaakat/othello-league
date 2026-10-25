"""
オセロの盤面管理。
盤面は 0〜63 のマス番号を持つ、長さ64のリストで表現する。
値：0=空, 1=黒, 2=白
分かりやすさを優先し、ビットボード等の高速化テクニックはあえて使わない。
ただし本戦の所要時間の大半（約9割）が合法手の列挙に費やされていたため、各マスから8方向に
伸びる「盤内のマスの並び」を起動時に一度だけ計算しておき（_RAYS）、1マスずつ盤端を判定し直す
処理を省いている。結果（合法手・ひっくり返る石）は従来と完全に同じ。
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


def _build_rays():
    """各マスから DIRECTIONS の順に、盤内を一直線に進んだマス番号の並びを返す（長さ0の方向は省く）"""
    rays = []
    for pos in range(64):
        per_pos = []
        for direction in DIRECTIONS:
            ray = []
            cur = _same_row_step(pos, direction)
            while cur is not None:
                ray.append(cur)
                cur = _same_row_step(cur, direction)
            if len(ray) >= 2:  # 挟むには最低2マス必要
                per_pos.append(tuple(ray))
        rays.append(tuple(per_pos))
    return tuple(rays)


_RAYS = _build_rays()


def legal_moves(board, color):
    """colorが打てる合法手のマス番号一覧を返す"""
    opp = WHITE if color == BLACK else BLACK
    moves = []
    for pos in range(64):
        if board[pos] != EMPTY:
            continue
        for ray in _RAYS[pos]:
            if board[ray[0]] != opp:
                continue
            for sq in ray[1:]:
                v = board[sq]
                if v == opp:
                    continue
                if v == color:
                    moves.append(pos)
                break
            else:
                continue
            if moves and moves[-1] == pos:
                break
    return moves


def apply_move(board, pos, color):
    """boardを直接書き換えて着手を反映する（呼び出し側でコピーを渡すこと）"""
    opp = WHITE if color == BLACK else BLACK
    board[pos] = color
    for ray in _RAYS[pos]:
        if board[ray[0]] != opp:
            continue
        for i in range(1, len(ray)):
            v = board[ray[i]]
            if v == opp:
                continue
            if v == color:
                for sq in ray[:i]:
                    board[sq] = color
            break


def is_game_over(board):
    return not legal_moves(board, BLACK) and not legal_moves(board, WHITE)


def count_discs(board):
    return board.count(BLACK), board.count(WHITE)


def game_phase(board):
    """序盤/中盤/終盤の判定（空きマス数ベース）。評価関数の重み切り替えに使う"""
    empty = board.count(EMPTY)
    if empty > 44:
        return "opening"
    elif empty > 16:
        return "midgame"
    else:
        return "endgame"
