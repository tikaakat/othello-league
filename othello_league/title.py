import random

from . import board as B
from . import engine as E
from .buffs import effective_params
from .round_robin import run_round_robin


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
def run_seiryuu_challenge(a_champion, titleholder_params, depth=1, titleholder_volatility=1.0):
    challenger_params = effective_params(a_champion)
    won, a_wins, b_wins, games = run_best_of_n_match(
        challenger_params, titleholder_params, wins_needed=4, depth=2,
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
# 朱雀戦：紅白2組（各5名）の永続サブリーグ方式。
# 毎季、紅組・白組それぞれで総当たりを行い、両組1位同士の挑戦者決定戦（3局制2本先取）で
# 朱雀への挑戦者を決める。両組の下位2名（計4名）は陥落し、紅白リーグ外の全個体による
# 4ブロックトーナメント（ブロックごとに優勝者1名、シードはタイトル保持者＞Aリーグ順位）で
# 入れ替えの4名を決定する。残留6名（紅白各3名）と新規4名は、来季また紅白各3名+2名に
# ランダムに組み直される。
# ============================================================
def bootstrap_suzaku_league(all_members, titleholder_ids=frozenset(), a_league_order=(), size_per_group=5):
    """
    紅白リーグがまだ存在しない最初の季に、初期メンバー10名を選出して紅白に振り分ける。
    優先度はタイトル保持者＞Aリーグ順位＞Elo（玄武戦のシード優先度と同じ考え方）。
    戻り値: (red_ids, white_ids)
    """
    a_rank_by_id = {iid: rank for rank, iid in enumerate(a_league_order)}
    not_in_a = len(a_league_order)

    def seed_priority(ind):
        is_title = ind.id in titleholder_ids
        a_rank = a_rank_by_id.get(ind.id, not_in_a)
        return (0 if is_title else 1, a_rank, -ind.elo)

    ranked = sorted(all_members, key=seed_priority)
    chosen = ranked[:size_per_group * 2]
    random.shuffle(chosen)
    red = chosen[:size_per_group]
    white = chosen[size_per_group:size_per_group * 2]
    return [ind.id for ind in red], [ind.id for ind in white]


def run_suzaku_group_stage(red_members, white_members, depth=1):
    """
    紅組・白組それぞれで総当たりを行う。
    戻り値: (紅組の順位確定済みリスト, 白組の順位確定済みリスト, 紅組の勝敗record, 白組の勝敗record, 対局ログ)
    """
    match_log = []
    red_ranked, red_log, _, red_record = run_round_robin(red_members, depth=depth, league_name="朱雀紅組", log_prefix="朱雀紅")
    match_log += red_log
    white_ranked, white_log, _, white_record = run_round_robin(white_members, depth=depth, league_name="朱雀白組", log_prefix="朱雀白")
    match_log += white_log
    return red_ranked, white_ranked, red_record, white_record, match_log


def run_suzaku_challenger_decision(red_champion, white_champion, depth=1):
    """紅組1位 vs 白組1位で朱雀への挑戦者を決める（3局制2本先取）"""
    x_won, x_wins, y_wins, games = run_best_of_n_match(
        effective_params(red_champion), effective_params(white_champion), wins_needed=2, depth=depth,
        noise_a=red_champion.volatility, noise_b=white_champion.volatility,
    )
    challenger = red_champion if x_won else white_champion
    return challenger, {
        "red_id": red_champion.id, "white_id": white_champion.id,
        "winner_id": challenger.id, "red_wins": x_wins, "white_wins": y_wins, "games": games,
    }


def determine_suzaku_qualifiers(all_members, exclude_ids=frozenset(), depth=1, num_blocks=4,
                                 titleholder_ids=frozenset(), a_league_order=()):
    """
    紅白リーグ外の全個体による入れ替え戦。優先度（タイトル保持者＞Aリーグ順位＞Elo）順に
    num_blocks個のブロックへスネーク配分し、ブロックごとにシード付き単独トーナメントを行い、
    ブロック優勝者（計num_blocks名）を返す。
    戻り値: (優勝者のリスト, 対局ログ)
    """
    pool = [ind for ind in all_members if ind.id not in exclude_ids]
    a_rank_by_id = {iid: rank for rank, iid in enumerate(a_league_order)}
    not_in_a = len(a_league_order)

    def seed_priority(ind):
        is_title = ind.id in titleholder_ids
        a_rank = a_rank_by_id.get(ind.id, not_in_a)
        return (0 if is_title else 1, a_rank, -ind.elo)

    ranked = sorted(pool, key=seed_priority)

    # スネーク配分：1,2,3,4番目のシードを別々のブロックへ最初に割り当て、以降は折り返しながら配る
    blocks = [[] for _ in range(num_blocks)]
    b, direction = 0, 1
    for ind in ranked:
        blocks[b].append(ind)
        if direction == 1:
            b += 1
            if b == num_blocks:
                b, direction = num_blocks - 1, -1
        else:
            b -= 1
            if b < 0:
                b, direction = 0, 1

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

    winners = []
    for block in blocks:
        if not block:
            continue
        n = len(block)
        bracket_size = 1
        while bracket_size < n:
            bracket_size *= 2
        slots = block + [None] * (bracket_size - n)
        order = _bracket_seed_order(bracket_size)
        round_members = [slots[i] for i in order]
        while len(round_members) > 1:
            next_round = []
            for i in range(0, len(round_members), 2):
                next_round.append(single_game(round_members[i], round_members[i + 1]))
            round_members = next_round
        winners.append(round_members[0])

    return winners, bracket_log


def assign_suzaku_groups(returning, new_qualifiers, size_per_group=5):
    """
    紅白リーグの来季メンバーを決める：残留メンバー（通常6名）を各組3名を目安に均等に振り分け、
    新規メンバー（通常4名、入れ替え戦のブロック優勝者）を残り枠（各組2名を目安）に均等に配分する。
    在位者除外等の事情で残留・新規の人数が通常と異なる場合も、各組size_per_group名になるよう
    できるだけ均等に配分する（通常ケースでは各組「残留3名＋新規2名」になる）。
    戻り値: (red_ids, white_ids)
    """
    returning_shuffled = list(returning)
    random.shuffle(returning_shuffled)
    new_shuffled = list(new_qualifiers)
    random.shuffle(new_shuffled)

    target_returning_per_group = max(0, size_per_group - 2)  # 通常3名
    red, white = [], []
    for i, ind in enumerate(returning_shuffled):
        if len(red) < target_returning_per_group and (len(white) >= target_returning_per_group or i % 2 == 0):
            red.append(ind)
        elif len(white) < target_returning_per_group:
            white.append(ind)
        else:
            (red if len(red) <= len(white) else white).append(ind)

    for ind in new_shuffled:
        (red if len(red) <= len(white) else white).append(ind)

    return [ind.id for ind in red], [ind.id for ind in white]


def run_suzaku_challenge(challenger, titleholder_params, depth=1, titleholder_volatility=1.0):
    challenger_params = effective_params(challenger)
    won, c_wins, t_wins, games = run_best_of_n_match(
        challenger_params, titleholder_params, wins_needed=3, depth=2,
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


def determine_byakko_challenger(all_members, exclude_id=None, depth=1, top_n=16):
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


def run_byakko_challenge(challenger, titleholder_params, depth=1, titleholder_volatility=1.0):
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
# 超早指し戦
# ============================================================
def determine_genbu_challenger(all_members, exclude_id=None, depth=1, bracket_size=64,
                                titleholder_ids=frozenset(), a_league_order=()):
    """
    全所属個体が参加する、ほぼ完全ランダムの抽選トーナメント。
    バイ（1回戦不戦勝＝2回戦から登場）の人数は bracket_size - 参加人数 で自動算出し、
    優先度の高い順にその人数分を割り当てる（上位シード同士が早期に当たらないよう分散配置）。
    優先度：①タイトル保持者（青龍・朱雀・白虎・玄武のいずれか） ②Aリーグ順位（今季、上位ほど優先）
    ③どちらにも該当しない個体はElo順（最後のタイブレークとしてのみ使用）。
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
    a_rank_by_id = {iid: rank for rank, iid in enumerate(a_league_order)}
    not_in_a = len(a_league_order)  # Aリーグに所属していない個体は最下位扱い

    def seed_priority(ind):
        is_titleholder = ind.id in titleholder_ids
        a_rank = a_rank_by_id.get(ind.id, not_in_a)
        return (0 if is_titleholder else 1, a_rank, -ind.elo)

    priority_ranked = sorted(pool, key=seed_priority)
    seeded = priority_ranked[:bye_count]   # 優先度上位bye_count名：1回戦バイ（2回戦から登場）
    rest = priority_ranked[bye_count:]     # 残り：完全ランダムに1回戦を組む
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
