import itertools
import random

from . import board as B
from . import engine as E
from .buffs import effective_params
from .round_robin import run_round_robin
from .parallel import run_calls, drive

# 各タイトル戦の探索深さ。1つの棋戦の中では、予選・リーグ・挑戦者決定戦・番勝負まで同じ深さで指す
# （番勝負だけ深くすると、予選やリーグで強い選手と番勝負で強い選手が別物になり、
# 終盤力・定跡などの個性の効き方も棋戦の中で変わってしまうため）。棋戦の格（序列）が上ほど深い：
# 麒麟・青龍（竜王・名人格）4、朱雀・白虎 3、玄武（早指し）2。
# 「LEAGUE」「TITLE」の2つの名前は、呼び出し側との互換のために残している（値は同じ）
SEIRYUU_LEAGUE_DEPTH = 4
SEIRYUU_TITLE_DEPTH = 4
SUZAKU_LEAGUE_DEPTH = 3
SUZAKU_TITLE_DEPTH = 3
BYAKKO_LEAGUE_DEPTH = 3
BYAKKO_TITLE_DEPTH = 3
GENBU_LEAGUE_DEPTH = 2
GENBU_TITLE_DEPTH = 2
KIRIN_DEPTH = 4


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
    先取制のタイトル戦本戦。1局目の先後は抽選し、1局ごとに先後を入れ替える。勝ち数だけで決まる。
    両者があと1勝で並んだら、最終局の先後は改めて抽選する（振り駒）。
    引き分けの局も「1局」として記録するが、先取数には加算しない（将棋の千日手と同様の扱い）。
    どちらかが規定数を先取するまで対局を続ける。
    戻り値: (challenger_won: bool, a_wins, b_wins, games_log)
    """
    a_wins, b_wins = 0, 0
    games_log = []
    game_num = 0
    # 1局目の先後は抽選（振り駒）し、以降は交互。7番勝負のように局数が奇数だと1局目の黒番側が
    # 黒番を1局多く持てるため、以前のように常に挑戦者が1局目の黒番だと挑戦者が有利になっていた
    a_first_black = random.random() < 0.5
    # 安全策：極端な連続引き分けでプロセスが実質無限ループにならないよう、上限だけは設ける
    # （通常のプレイでは到達しない、十分大きな値）
    MAX_GAMES_SAFETY = 500

    final_game = wins_needed - 1  # 両者がこの勝ち数に並んだら、次が最終局
    while a_wins < wins_needed and b_wins < wins_needed and game_num < MAX_GAMES_SAFETY:
        if a_wins == b_wins == final_game:
            # 最終局：先後を改めて抽選する（振り駒）。局数が奇数の番勝負では、交互に指すと1局目の黒番の側が
            # 黒番を1局多く持つことになるため、最後の1局の先後は抽選で決める（引き分けの指し直しも同様）
            a_black_list = [random.random() < 0.5]
        else:
            # 決着（または最終局）までに必要になりうる局数をまとめて同時に指し、先頭から順に数えて
            # 決着した時点で残りは捨てる（1局ずつ順に指した場合と同じ結果の分布になる）
            batch = max(1, (wins_needed - a_wins) + (wins_needed - b_wins) - 2)
            a_black_list = [((game_num + k) % 2 == 0) == a_first_black for k in range(batch)]
        args = [(params_a, params_b, depth, noise_a, noise_b) if ab else (params_b, params_a, depth, noise_b, noise_a)
                for ab in a_black_list]
        outs = run_calls(_play_one_game, args)
        final_before = a_wins == b_wins == final_game
        for a_is_black, (result, black, white, moves) in zip(a_black_list, outs):
            if a_wins >= wins_needed or b_wins >= wins_needed or (not final_before and a_wins == b_wins == final_game):
                break
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
        challenger_params, titleholder_params, wins_needed=4, depth=depth,
        noise_a=a_champion.volatility, noise_b=titleholder_volatility,
    )
    return {
        "title": "青龍", "challenger_id": a_champion.id, "won": won,
        "challenger_wins": a_wins, "titleholder_wins": b_wins, "games": games,
    }


def _disc_margin(games, x_key="x_was_black"):
    """Xから見た石数の差の合計"""
    return sum((g["black"] - g["white"]) * (1 if g[x_key] else -1) for g in games)


def _play_until_decided(params_x, params_y, depth, noise_x=1.0, noise_y=1.0, max_attempts=50):
    """
    トーナメント（予選・挑戦者決定戦）の1対戦。先後を入れ替えた2局を1セットとして指し、
    勝ち数の多い方、同じなら2局の石数の合計が多い方を勝ちとする（それも同じならもう1セット）。
    上位の選手どうしは全員が同じ定跡で序盤を指すため黒番が大きく有利で、1局だけだと
    実力より先後で勝敗が決まりやすい。先後を入れ替えた2局で比べると、強い方が勝ちやすくなる。
    全ての対局を記録し、(Xが勝ったか, 対局の一覧) を返す。
    """
    games = []
    x_first_black = random.random() < 0.5
    for _ in range(max_attempts):
        pair = []
        for x_is_black in (x_first_black, not x_first_black):
            if x_is_black:
                result, black, white, moves = _play_one_game(params_x, params_y, depth, noise_x, noise_y)
            else:
                result, black, white, moves = _play_one_game(params_y, params_x, depth, noise_y, noise_x)
            pair.append({"black": black, "white": white, "moves": moves,
                         "x_was_black": x_is_black, "result": result})
        games += pair
        x_wins = sum(g["result"] != "draw" and (g["result"] == "black") == g["x_was_black"] for g in pair)
        y_wins = sum(g["result"] != "draw" and (g["result"] == "black") != g["x_was_black"] for g in pair)
        if x_wins != y_wins:
            return x_wins > y_wins, games
        margin = _disc_margin(pair)
        if margin != 0:
            return margin > 0, games
    # 安全策（実質到達しない想定）
    return True, games


# ============================================================
# トーナメントを並列に進める仕組み（parallel.drive）。
# 各トーナメントは「次に指す対局の一覧」をyieldし、結果の一覧を受け取って先へ進むジェネレータとして書く。
# 白虎・玄武・麒麟のように組み合わせが季の初めに決まっている棋戦は、リーグ戦と同時に進められる。
# 対局は個体の状態を変えない（Eloの更新は呼び出し側が後で記録順に行う）ので、同じ個体が
# 別の棋戦の対局に同時に出ていても結果は変わらない
# ============================================================
def _round(pairs, depth, log, tags=None):
    """1回戦分の対局（Noneの相手は不戦勝）。勝者の一覧を返す（yield from で使う）"""
    real = [(k, x, y) for k, (x, y) in enumerate(pairs) if x is not None and y is not None]
    jobs = [(_play_until_decided, (effective_params(x), effective_params(y), depth, x.volatility, y.volatility))
            for _, x, y in real]
    outs = (yield jobs) if jobs else []
    winners = [x if x is not None else y for x, y in pairs]
    for (k, x, y), (x_won, games) in zip(real, outs):
        w = x if x_won else y
        winners[k] = w
        log.append({"a": x.id, "b": y.id, "winner": w.id, "games": games, **(tags(k) if tags else {})})
    return winners


def _knockout(bracket, depth, log, tags=None):
    """単純なトーナメント（Noneは不在＝相手は不戦勝）。優勝者を返す（yield from で使う）"""
    rnd = 1
    while len(bracket) > 1:
        pairs = [(bracket[i], bracket[i + 1]) for i in range(0, len(bracket), 2)]
        bracket = yield from _round(pairs, depth, log, (lambda k, r=rnd: tags(k, r)) if tags else None)
        rnd += 1
    return bracket[0]


# ============================================================
# 朱雀戦：紅白2組（各5名）の永続サブリーグ方式。
# 毎季、紅組・白組それぞれで総当たりを行い、両組1位同士の挑戦者決定戦（3局制2本先取）で
# 朱雀への挑戦者を決める。両組の下位2名（計4名）は陥落し、紅白リーグ外の全個体による
# 4ブロックトーナメント（ブロックごとに優勝者1名、シードはタイトル保持者＞Aリーグ順位）で
# 入れ替えの4名を決定する。残留6名（紅白各3名）と新規4名は、来季また紅白各3名+2名に
# ランダムに組み直される。
# ============================================================
def run_suzaku_group_stage(red_members, white_members, depth=1, tiebreak=None):
    """
    紅組・白組それぞれで総当たりを行う。tiebreakは同点時の順位付けに使う
    {"prev_rank": {id: 前季の紅白順位}, "tenure": {id: 紅白通算在籍期数}}。
    戻り値: (紅組の順位確定済みリスト, 白組の順位確定済みリスト, 紅組の勝敗record, 白組の勝敗record, 対局ログ)
    """
    match_log = []
    red_ranked, red_log, _, red_record = run_round_robin(
        red_members, depth=depth, league_name="朱雀紅組", log_prefix="朱雀紅",
        suzaku_tiebreak=tiebreak, relegate_n=min(2, len(red_members)),
    )
    match_log += red_log
    white_ranked, white_log, _, white_record = run_round_robin(
        white_members, depth=depth, league_name="朱雀白組", log_prefix="朱雀白",
        suzaku_tiebreak=tiebreak, relegate_n=min(2, len(white_members)),
    )
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
    紅白リーグ外の全個体による入れ替え戦。ブロック割り振りは毎年ランダムだが、
    シード（タイトル保持者＞Aリーグ順位）は優先度順に各ブロックへ分散（スネーク配分）し、
    ブロック内でもシード位置に置くことで、上位同士（Aリーグ同士）が初戦で当たらないようにする。
    シード以外の個体はランダムな順で、人数の少ないブロックから順に配る。
    ブロックごとにシード付き単独トーナメントを行い、ブロック優勝者（計num_blocks名）を返す。
    戻り値: (優勝者のリスト, 対局ログ)
    """
    if num_blocks <= 0:
        return [], []
    pool = [ind for ind in all_members if ind.id not in exclude_ids]
    a_rank_by_id = {iid: rank for rank, iid in enumerate(a_league_order)}
    not_in_a = len(a_league_order)

    def seed_priority(ind):
        is_title = ind.id in titleholder_ids
        a_rank = a_rank_by_id.get(ind.id, not_in_a)
        return (0 if is_title else 1, a_rank)

    seeds = sorted((ind for ind in pool if ind.id in titleholder_ids or ind.id in a_rank_by_id), key=seed_priority)
    unseeded = [ind for ind in pool if not (ind.id in titleholder_ids or ind.id in a_rank_by_id)]
    random.shuffle(unseeded)

    blocks = [[] for _ in range(num_blocks)]
    b, direction = 0, 1
    for ind in seeds:
        blocks[b].append(ind)
        if direction == 1:
            b += 1
            if b == num_blocks:
                b, direction = num_blocks - 1, -1
        else:
            b -= 1
            if b < 0:
                b, direction = 0, 1
    for ind in unseeded:
        min(blocks, key=len).append(ind)

    # 各ブロックのトーナメントを並べ、同じ回戦をまとめて同時に対局する
    brackets = []
    for block in blocks:
        if not block:
            continue
        bracket_size = 1
        while bracket_size < len(block):
            bracket_size *= 2
        slots = block + [None] * (bracket_size - len(block))
        brackets.append([slots[i] for i in _bracket_seed_order(bracket_size)])
    bracket_log = []
    winners = drive(*[_knockout(b, depth, bracket_log, lambda k, r, blk=blk: {"_block": blk}) for blk, b in enumerate(brackets)])
    # 記録はブロックごとにまとめる（以前の1ブロックずつ順に指していた頃と同じ並び）
    bracket_log.sort(key=lambda m: m["_block"])
    for m in bracket_log:
        del m["_block"]
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
        challenger_params, titleholder_params, wins_needed=4, depth=depth,
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


def plan_byakko_bracket(all_members, exclude_id=None, top_n=16, guaranteed_id=None):
    """白虎トーナメントの組み合わせ（ID・空き枠Noneの並び）を決める。季の初めに組み合わせを公開できるよう、
    前の季の終了時点で呼ぶ（その時点のEloで上位top_n名を選ぶ。前季に失冠した選手は無条件で出場）"""
    pool = [ind for ind in all_members if ind.id != exclude_id]
    guaranteed_ind = next((ind for ind in pool if ind.id == guaranteed_id), None) if guaranteed_id else None
    elo_pool = [ind for ind in pool if guaranteed_ind is None or ind.id != guaranteed_ind.id]
    elo_quota = top_n - 1 if guaranteed_ind is not None else top_n
    ranked = sorted(elo_pool, key=lambda ind: -ind.elo)[:elo_quota]
    if guaranteed_ind is not None:
        ranked.append(guaranteed_ind)
    bracket_size = 1
    while bracket_size < len(ranked):
        bracket_size *= 2
    slots = [ind.id for ind in ranked] + [None] * (bracket_size - len(ranked))
    return [slots[i] for i in _bracket_seed_order(bracket_size)]


def byakko_challenger_gen(all_members, exclude_id=None, depth=1, top_n=16, guaranteed_id=None, planned=None):
    """
    Elo上位top_n名（既定16名）による正式シードトーナメント。
    前年白虎在位者（exclude_id）は防衛専念枠のため、この母集団からは除外する。
    guaranteed_idが与えられた場合（前季このタイトルを失冠した個体のid）、その個体は
    現在のEloに関わらず無条件でトーナメント入りする（Elo下位からtop_n-1名のみをElo選抜し、
    guaranteed_id分の1枠を確保する）。
    """
    # 組み合わせは季の初めに公開したもの（planned：前の季の終了時点で plan_byakko_bracket で決めたIDの並び）を使う。
    # （引退は季の終わりにだけ起き、組み合わせはその後に決めるため、組み合わせの選手が欠けることは無い）。plannedが無ければこの場で決める
    by_id = {ind.id: ind for ind in all_members}
    if planned is None:
        planned = plan_byakko_bracket(all_members, exclude_id=exclude_id, top_n=top_n, guaranteed_id=guaranteed_id)
    bracketed = [by_id.get(pid) if pid else None for pid in planned]

    bracket_log = []
    challenger = yield from _knockout(bracketed, depth, bracket_log)
    # 引退免除判定（白虎戦トーナメント出場者）用に、実際の出場者をそのまま返す
    # （bracket_logから復元すると、バイ（1回戦不戦勝）で1回も対局しなかった個体を見逃すため）
    return challenger, bracket_log, [ind for ind in bracketed if ind is not None]


def determine_byakko_challenger(*args, **kwargs):
    return drive(byakko_challenger_gen(*args, **kwargs))[0]


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
NEWCOMER_SLOT_PREFIX = "NEW"  # 玄武戦の組み合わせで、新人リーグの勝者が入る予定の枠（NEW1, NEW2, ...）


# ------------------------------------------------------------
# 段階シード：格の高い選手ほど後の回戦から登場する（勝たなければならない回数が少ない）組み合わせ。
# 各選手に「登場する段」（0＝1回戦から、1＝2回戦から…）を与え、段eの選手は 2^e 枠ぶんの場所を占める
# （隣の枠を空けておき、不戦勝で勝ち上がった位置から登場する）。上位の選手どうしは上半分・下半分に
# 交互に振り分け、できるだけ遅くまで当たらないようにする
# ------------------------------------------------------------
def tiered_slots(entries, size=None):
    """entries：[(ID, 登場する段)]（優先度の高い順）。トーナメントの並び（空き枠はNone）を返す。
    size（2のべき乗）を省くと、全員が収まる最小の大きさ以下で一番近い2のべき乗にする。
    - 枠が足りなければ、優先度の低い選手から2人ずつ「予備戦」（1回戦の前の1局）に回す
    - 枠が余れば、優先度の高い選手から順に、さらに1つ後の段から登場させる（不戦勝を与える）"""
    entries = [list(e) for e in entries]
    need = sum(2 ** e for _, e in entries)
    if size is None:
        size = 1
        while size * 2 <= need:
            size *= 2
    while need < size:
        for item in entries:
            if need + 2 ** item[1] <= size:
                need += 2 ** item[1]
                item[1] += 1
                break
        else:
            break
    playin = 0
    if need > size:
        # 予備戦に回る人数：はみ出した分の2倍（2人で1枠）。全員の段を1つ上げ、予備戦の選手だけ段0にする
        playin = 2 * (need - size)
        for k, item in enumerate(entries):
            item[1] = item[1] + 1 if k < len(entries) - playin else 0
        size *= 2

    def place(items, size):
        if not items:
            return [None] * size
        if size == 1:
            return [items[0][0]]
        if len(items) == 1 and 2 ** items[0][1] >= size:
            return [items[0][0]] + [None] * (size - 1)
        half = size // 2
        sides = [[], []]
        room = [half, half]
        turn = 0
        for item in items:
            w = 2 ** item[1]
            # 空きの多い側へ（同じなら交互に）。上位どうしを上下の山に分ける
            if room[0] == room[1]:
                k = turn
                turn ^= 1
            else:
                k = 0 if room[0] > room[1] else 1
            if room[k] < w:
                k ^= 1
            sides[k].append(item)
            room[k] -= w
        return place(sides[0], half) + place(sides[1], half)

    return place(entries, size)


def plan_genbu_bracket(all_members, exclude_id=None, bracket_size=None, titleholder_ids=frozenset(),
                       guaranteed_final_id=None, newcomer_slots=0, num_blocks=4, title_level=2, a_level=1):
    """玄武戦の組み合わせ（ID・新人枠「NEW1」等・空き枠Noneの並び）を決める。季の初めに公開できるよう、
    前の季の終了時点で呼ぶ。全員参加の4ブロック（bracket_sizeは互換のため受け取るだけで使わない）。タイトル保持者はtitle_level段目（既定は3回戦）から、
    Aリーグの選手はa_level段目（既定は2回戦）から、ほかは1回戦から登場する。
    シードの選手はブロックに均等に振り分け（優先度順に蛇行）、それ以外はランダムに振り分ける。
    全ブロックを同じ大きさにし、ブロックを順につないだ並びを返す"""
    members = [ind for ind in all_members if ind.id != exclude_id and ind.id != guaranteed_final_id]
    def level(ind):
        if ind.id in titleholder_ids:
            return title_level
        return a_level if ind.league == "A" else 0
    seeded = sorted([ind for ind in members if level(ind) > 0], key=lambda ind: (-level(ind), -ind.elo))
    others = [ind.id for ind in members if level(ind) == 0] + [f"{NEWCOMER_SLOT_PREFIX}{k + 1}" for k in range(newcomer_slots)]
    random.shuffle(others)
    blocks = [[] for _ in range(num_blocks)]
    b, step = 0, 1
    for ind in seeded:
        blocks[b].append((ind.id, level(ind)))
        if not (0 <= b + step < num_blocks):
            step = -step
        else:
            b += step
    for pid in others:
        k = min(range(num_blocks), key=lambda i: sum(2 ** e for _, e in blocks[i]))
        blocks[k].append((pid, 0))
    # ブロックの大きさ：一番小さいブロックがちょうど収まる2のべき乗以下。それより人数の多いブロックは、
    # はみ出した分を予備戦（1回戦の前の1局）に回す（余った枠を下位の選手の不戦勝にしない）。
    # 予備戦の有無でブロックの長さが違う場合は、短いブロックの後ろを空き枠で埋めて長さを揃える
    # （空き枠側には誰もいないので、ブロックの勝者はそのまま上がる）
    need = min(sum(2 ** e for _, e in bl) for bl in blocks)
    size = 1
    while size * 2 <= need:
        size *= 2
    blocks_slots = [tiered_slots(bl, size=size) for bl in blocks]
    width = max(len(x) for x in blocks_slots)
    out = []
    for x in blocks_slots:
        out += x + [None] * (width - len(x))
    return out


def genbu_challenger_gen(all_members, exclude_id=None, depth=1, bracket_size=64, num_blocks=4,
                                titleholder_ids=frozenset(), guaranteed_final_id=None, planned=None,
                                newcomer_ids=None):
    """
    全所属個体が参加する、ほぼ完全ランダムの抽選トーナメント。
    バイ（1回戦不戦勝＝2回戦から登場）の人数は bracket_size - 参加人数 で自動算出し、
    優先度の高い順にその人数分を割り当てる（上位シード同士が早期に当たらないよう分散配置）。
    優先度：①タイトル保持者（青龍・朱雀・白虎・玄武のいずれか） ②所属リーグとその季の順位
    （Elo順ではなく、A→B→C→Dの順に、各リーグ内はその季の成績順）。
    all_membersはrun_season.py側でranked_A+ranked_B+ranked_C+ranked_Dの順（各リーグ内は
    その季の成績順）で渡されるため、そのインデックスがそのまま優先度になる。
    バイに入らない残り全員は、完全ランダムに1回戦を組む。
    前年玄武在位者（exclude_id）は防衛専念枠のため、この母集団からは除外する。

    表示のため、bracket_size枠全体をnum_blocks個のブロック（既定4ブロック、各16名）に
    分割し、各ブロック内の抽選トーナメントで1名ずつ勝ち上がらせる（ここまでが「予選」）。
    guaranteed_final_idが与えられた場合（前季このタイトルを失冠した個体のid）、その個体は
    予選ブロックを経ずに、ブロック優勝者たちによる「挑戦者決定リーグ戦（本戦）」へ
    直接合流する（all_membersに実在し、exclude_id・既にブロック優勝者本人でない場合のみ）。
    本戦（挑戦者決定）は、人数（通常はnum_blocks名、前季失冠者合流時はnum_blocks+1名）に
    関わらず総当たりで行う。以前はトーナメント方式だったため、失冠者合流時に人数が
    2の累乗にならず不自然な大量バイが発生していたが、総当たり方式なら人数を問わず
    組める。最多勝者が複数名タイになった場合のみ、その中だけで決定戦（総当たり。
    これも複数名タイならさらに絞り込む）を行う。
    ブロック分けは生成済みブラケット（bracket_size枠）を等分した連続区間で行う。
    トーナメント表は各段階で必ず再帰的に閉じた部分木になる性質上、ブロック内の対戦は
    そのブロックの参加者だけで完結する。bracket_logの各要素にはstage（"block"・
    本戦総当たりの"final"・本戦タイ決定戦の"final_playoff"のいずれか）を、block段階では
    さらにそのブロック番号（0始まり）を付与する。
    """
    # guaranteed_final_idは予選ブロックを経ずに本戦へ直接合流させるため、ここで
    # pool自体から除く（exclude_idと同様）。除かずにいると予選ブロックの通常参加者として
    # 対局させてしまい、負けて敗退したにもかかわらずfinal_poolへ合流時に二重に扱われる上、
    # bye_count・シード優先度の算出も他の参加者分だけずれてしまう
    # 組み合わせは季の初めに公開したもの（planned：前の季の終了時点で plan_genbu_bracket で決めた並び）を使う。
    # 新人枠（NEW1等）は、この季に実際に入った新人（newcomer_ids：枠名→ID）に置き換える。新人が枠より
    # 少なく埋まらなかった新人枠は空き（相手は不戦勝）になる。plannedが無ければこの場で決める
    by_id = {ind.id: ind for ind in all_members}
    if planned is None:
        planned = plan_genbu_bracket(all_members, exclude_id=exclude_id, bracket_size=bracket_size,
                                     titleholder_ids=titleholder_ids, guaranteed_final_id=guaranteed_final_id)
    newcomer_ids = newcomer_ids or {}
    bracketed = [by_id.get(newcomer_ids.get(pid, pid)) if pid else None for pid in planned]

    bracket_log = []

    # --- ブロック段階（予選）：num_blocks名に絞られるまで。同じ回戦はまとめて同時に対局する ---
    round_members = bracketed
    while len(round_members) > num_blocks:
        num_pairs = len(round_members) // 2
        pairs_per_block = max(1, num_pairs // num_blocks)
        pairs = [(round_members[i], round_members[i + 1]) for i in range(0, len(round_members), 2)]
        round_members = yield from _round(pairs, depth, bracket_log,
                                          lambda k: {"stage": "block", "block": k // pairs_per_block})
    block_champions = list(round_members)

    # --- 前季失冠者の本戦合流（予選スキップ） ---
    final_pool = [ind for ind in block_champions if ind is not None]
    if guaranteed_final_id is not None:
        guaranteed_ind = next(
            (ind for ind in all_members
             if ind.id == guaranteed_final_id and ind.id != exclude_id
             and ind.id not in {c.id for c in final_pool}),
            None,
        )
        if guaranteed_ind is not None:
            final_pool.append(guaranteed_ind)

    # --- 挑戦者決定リーグ戦（本戦）：ブロック優勝者（＋前季失冠者）の総当たり。全対局を同時に指す ---
    # 前季失冠者が合流する季だけ人数が+1されるが、総当たりなら人数に関わらず同じ仕組みで扱える
    final_score = {ind.id: 0.0 for ind in final_pool}
    combos = list(itertools.combinations(final_pool, 2))
    winners = yield from _round(combos, depth, bracket_log, lambda k: {"stage": "final"})
    for w in winners:
        final_score[w.id] += 1.0

    ranked = sorted(final_pool, key=lambda ind: -final_score[ind.id])

    # 1位が複数タイの場合は、その中だけで決定戦を行う（stageを分けて記録し、
    # 本戦の総当たり成績とは別に表示できるようにする）
    top_score = final_score[ranked[0].id]
    tied_for_first = [ind for ind in ranked if final_score[ind.id] == top_score]
    while len(tied_for_first) > 1:
        tie_score = {ind.id: 0.0 for ind in tied_for_first}
        combos = list(itertools.combinations(tied_for_first, 2))
        winners = yield from _round(combos, depth, bracket_log, lambda k: {"stage": "final_playoff"})
        for w in winners:
            tie_score[w.id] += 1.0
        tied_for_first = sorted(tied_for_first, key=lambda ind: -tie_score[ind.id])
        new_top = tie_score[tied_for_first[0].id]
        tied_for_first = [ind for ind in tied_for_first if tie_score[ind.id] == new_top]

    challenger = tied_for_first[0]
    return challenger, bracket_log, block_champions


def determine_genbu_challenger(*args, **kwargs):
    return drive(genbu_challenger_gen(*args, **kwargs))[0]


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


# ============================================================
# 麒麟戦（序列1位、竜王格）：竜王戦の「組」と棋王戦の「敗者復活」を組み合わせた方式。7番勝負。
# - 組は麒麟戦の成績だけで毎季決め直す（所属リーグやEloとは無関係）。
#   1組（8名）は本戦から出場。2組（16名）と3組（残り全員・新人を含む）は、それぞれ組ごとの予選
#   （ブロック別トーナメント）を戦い、各ブロックの優勝者が本戦に進む。
#   次の季の1組＝本戦ベスト8、2組＝本戦1回戦の敗者＋予選ブロック決勝の敗者、残りは3組。
#   組の成績がまだ無い最初の季だけ、Aリーグ所属を1組の代わりにする。
# - 本戦は16名のトーナメント。ベスト4からは2敗で失格：準決勝・決勝の敗者は敗者復活戦に回り、
#   本戦優勝者と敗者復活戦の優勝者が挑戦者決定戦を行う（本戦優勝者は1勝、敗者復活側は2連勝が必要）。
# - 組み合わせは前の季の終わりに決めて公開する（plan_kirin_bracket）。前季の失冠者は1組として本戦から出る
# ============================================================
KIRIN_MAIN_SIZE = 16
KIRIN_GROUP1_SIZE = 8
QUALIFIER_SLOT_PREFIX = "Q"  # 本戦の組み合わせで、予選ブロックの優勝者が入る枠（Q1, Q2, ...＝予選の1, 2, ...番目のブロック）


def _deal_blocks(ids, num_blocks):
    """予選のブロック分け（ランダム）。各ブロックをトーナメントの並び（不在はNone）にして返す"""
    ids = list(ids)
    random.shuffle(ids)
    blocks = [ids[k::num_blocks] for k in range(num_blocks)]
    out = []
    for members in blocks:
        size = 1
        while size < len(members):
            size *= 2
        slots = members + [None] * (size - len(members))
        out.append([slots[i] for i in _bracket_seed_order(size)])
    return out


def plan_kirin_bracket(all_members, exclude_id=None, groups=None, guaranteed_id=None, newcomer_slots=0):
    """麒麟戦の組み合わせを決める。all_membersはA→B→C→Dの順。groupsは前季の結果による組
    {"1": [ID...（成績順）], "2": [ID...]}（無ければAリーグ所属を1組とする）。
    戻り値：{"groups": {"1": [...], "2": [...], "3": [...]}, "prelim": [{"group": 2or3, "slots": [...]}, ...],
            "main": [ID または "Q1" 等 または None ...]}"""
    present = [ind.id for ind in all_members if ind.id != exclude_id]
    present_set = set(present)
    if groups is None:
        g1 = [ind.id for ind in all_members if ind.league == "A" and ind.id != exclude_id]
        g2 = []
    else:
        g1 = [i for i in groups.get("1", []) if i in present_set]
        g2 = [i for i in groups.get("2", []) if i in present_set and i not in g1]
    if guaranteed_id and guaranteed_id in present_set and guaranteed_id not in g1:
        g1.insert(0, guaranteed_id)
        g2 = [i for i in g2 if i != guaranteed_id]
    overflow, g1 = g1[KIRIN_MAIN_SIZE - 2:], g1[:KIRIN_MAIN_SIZE - 2]
    g2 = overflow + g2
    in12 = set(g1) | set(g2)
    g3 = [i for i in present if i not in in12] + [f"{NEWCOMER_SLOT_PREFIX}{k + 1}" for k in range(newcomer_slots)]

    needed = KIRIN_MAIN_SIZE - len(g1)
    q2 = min(needed // 2, len(g2) // 2) if len(g2) >= 2 else 0
    if q2 == 0:
        g3, g2 = g2 + g3, []
    q3 = needed - q2
    prelim = [{"group": 2, "slots": b} for b in (_deal_blocks(g2, q2) if q2 else [])]
    prelim += [{"group": 3, "slots": b} for b in _deal_blocks(g3, q3)]

    entrants = g1 + [f"{QUALIFIER_SLOT_PREFIX}{k + 1}" for k in range(needed)]
    slots = entrants + [None] * (KIRIN_MAIN_SIZE - len(entrants))
    main = [slots[i] for i in _bracket_seed_order(KIRIN_MAIN_SIZE)]
    return {"groups": {"1": g1, "2": g2, "3": [i for i in g3 if not i.startswith(NEWCOMER_SLOT_PREFIX)]},
            "prelim": prelim, "main": main}


def kirin_challenger_gen(all_members, plan, newcomer_ids=None, depth=KIRIN_DEPTH):
    """plan（plan_kirin_bracket の戻り値）どおりに予選・本戦・敗者復活戦・挑戦者決定戦を行う。
    戻り値：(挑戦者, 対局記録, 次の季の組 {"1": [...], "2": [...]}, 本戦出場者)"""
    by_id = {ind.id: ind for ind in all_members}
    newcomer_ids = newcomer_ids or {}
    resolve = lambda pid: by_id.get(newcomer_ids.get(pid, pid)) if pid else None
    log = []

    # --- 予選：全ブロックの同じ回戦をまとめて同時に対局する ---
    blocks = [[resolve(pid) for pid in b["slots"]] for b in plan["prelim"]]
    block_final_losers = []
    rnd = 1
    while any(len(b) > 1 for b in blocks):
        pairs, owner = [], []
        for bi, b in enumerate(blocks):
            if len(b) > 1:
                for i in range(0, len(b), 2):
                    pairs.append((b[i], b[i + 1]))
                    owner.append(bi)
        # 段階はDBのstage列に入るため短い文字列にする（予選：prelim{組}-{回戦}、ブロック番号はblock）
        stage = lambda k: {"stage": f"prelim{plan['prelim'][owner[k]]['group']}-{rnd}", "block": owner[k]}
        winners = yield from _round(pairs, depth, log, stage)
        new_blocks = [list(b) for b in blocks]
        for bi in set(owner):
            new_blocks[bi] = []
        for k, (bi, w) in enumerate(zip(owner, winners)):
            new_blocks[bi].append(w)
            if len(blocks[bi]) == 2:  # ブロック決勝
                x, y = pairs[k]
                if x is not None and y is not None:
                    block_final_losers.append(y if w is x else x)
        blocks = new_blocks
        rnd += 1
    qualifiers = [b[0] if b else None for b in blocks]

    # --- 本戦：ベスト4まで ---
    def main_slot(pid):
        if pid and pid.startswith(QUALIFIER_SLOT_PREFIX):
            k = int(pid[len(QUALIFIER_SLOT_PREFIX):]) - 1
            return qualifiers[k] if k < len(qualifiers) else None
        return resolve(pid)
    bracket = [main_slot(pid) for pid in plan["main"]]
    entrants = [ind for ind in bracket if ind is not None]
    first_round_losers, qf_losers = [], []
    rnd = 1
    while len(bracket) > 4:
        pairs = [(bracket[i], bracket[i + 1]) for i in range(0, len(bracket), 2)]
        winners = yield from _round(pairs, depth, log, lambda k, r=rnd: {"stage": f"main{r}"})
        losers = [y if w is x else x for (x, y), w in zip(pairs, winners) if x is not None and y is not None]
        (first_round_losers if rnd == 1 else qf_losers).extend(losers)
        bracket = winners
        rnd += 1

    best4 = [ind for ind in bracket if ind is not None]
    if len(best4) < 4:  # 人数不足（実質起きない）：そのままトーナメントで決める
        challenger = yield from _knockout(bracket, depth, log, lambda k, r: {"stage": f"main{rnd - 1 + r}"})
        next_groups = {"1": [challenger.id] + [i.id for i in best4 if i is not challenger] + [i.id for i in qf_losers],
                       "2": [i.id for i in first_round_losers + block_final_losers]}
        return challenger, log, next_groups, entrants

    # --- ベスト4から2敗失格：準決勝 → 決勝と敗者復活1回戦（同時）→ 敗者復活決勝 → 挑戦者決定戦 ---
    semis = [(bracket[0], bracket[1]), (bracket[2], bracket[3])]
    sw = yield from _round(semis, depth, log, lambda k: {"stage": "semifinal"})
    sl = [y if w is x else x for (x, y), w in zip(semis, sw)]
    w2 = yield from _round([(sw[0], sw[1]), (sl[0], sl[1])], depth, log,
                           lambda k: {"stage": "final"} if k == 0 else {"stage": "rep1"})
    winner, rep = w2
    final_loser = sw[1] if winner is sw[0] else sw[0]
    rep_semi_loser = sl[1] if rep is sl[0] else sl[0]
    rep_final = yield from _round([(rep, final_loser)], depth, log, lambda k: {"stage": "rep2"})
    rep_winner = rep_final[0]
    rep_runner_up = final_loser if rep_winner is rep else rep
    challenger = winner
    for game_no in (1, 2):
        w = (yield from _round([(winner, rep_winner)], depth, log,
                               lambda k: {"stage": f"decision{game_no}"}))[0]
        if w is winner:
            break
        if game_no == 2:
            challenger = rep_winner
    other = rep_winner if challenger is winner else winner
    order = [challenger, other, rep_runner_up, rep_semi_loser] + qf_losers
    next_groups = {"1": [i.id for i in order], "2": [i.id for i in first_round_losers + block_final_losers]}
    return challenger, log, next_groups, entrants


def run_kirin_challenge(challenger, titleholder_params, depth=KIRIN_DEPTH, titleholder_volatility=1.0):
    won, c_wins, t_wins, games = run_best_of_n_match(
        effective_params(challenger), titleholder_params, wins_needed=4, depth=depth,
        noise_a=challenger.volatility, noise_b=titleholder_volatility,
    )
    return {
        "title": "麒麟", "challenger_id": challenger.id, "won": won,
        "challenger_wins": c_wins, "titleholder_wins": t_wins, "games": games,
    }
