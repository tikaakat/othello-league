import random
import time

from .play import play_league_match
from .elo import update_elo


def _build_schedule(ids, games_per_individual):
    """
    各個体がちょうど games_per_individual 人のユニークな相手と対戦する
    組み合わせを作る。参加人数が少なく全員と当たっても対戦数に届かない
    場合は、全員総当たり（人数-1戦）にフォールバックする。

    対戦数は偶数（デフォルト8）を前提とする。偶数なら、ランダムに並べた
    円環上で各個体を前後 games_per_individual/2 人ずつと結ぶ（circulant
    graph）ことで、人数の偶奇に関わらず必ずちょうど games_per_individual
    戦の組み合わせが作れる。
    """
    n = len(ids)
    if n <= 1:
        return []

    if games_per_individual >= n - 1:
        # 総当たり（全員と1回ずつ対戦）
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                pairs.append((ids[i], ids[j]))
        return pairs

    half = games_per_individual // 2
    order = list(ids)
    random.shuffle(order)
    pairs = set()
    for i in range(n):
        for k in range(1, half + 1):
            j = (i + k) % n
            pairs.add(frozenset((order[i], order[j])))
    return [tuple(p) for p in pairs]


def run_random_league(members, games_per_individual=8, depth=4, league_name="B", seed_order=None, allow_rematch=True):
    """
    B/C/Dリーグの対戦方式。スイス方式（得点ベースの組み合わせ）ではなく、
    季ごとにランダムに相手を割り振り、各個体がちょうど games_per_individual
    戦（デフォルト8戦）を行う。
    全員と総当たりはせず、毎季ランダムな対戦カードになるため波乱が起きやすい。
    seed_order: 同点時のタイブレークに使う順序（前季順位継承）のID列。
    指定が無い場合は、後方互換のためElo順にフォールバックする。
    戻り値: (順位確定済みリスト, 対局ログ, 勝ち点, 個体ごとの勝敗分dict)
    """
    by_id = {ind.id: ind for ind in members}
    score = {ind.id: 0.0 for ind in members}
    record = {ind.id: {"win": 0, "loss": 0, "draw": 0} for ind in members}
    match_log = []
    start = time.time()

    if seed_order:
        seed_rank = {iid: i for i, iid in enumerate(seed_order)}
    else:
        seed_rank = {iid: -by_id[iid].elo for iid in by_id}  # 後方互換フォールバック

    pairs = _build_schedule(list(by_id.keys()), games_per_individual)
    random.shuffle(pairs)
    print(f"    {league_name}リーグ（ランダム{games_per_individual}戦・{len(pairs)}局）")

    for j, (a_id, b_id) in enumerate(pairs, 1):
        ind_a, ind_b = by_id[a_id], by_id[b_id]

        outcome_a, games = play_league_match(ind_a, ind_b, depth=depth, allow_rematch=allow_rematch)
        ind_a.elo, ind_b.elo = update_elo(
            ind_a.elo, ind_b.elo, outcome_a,
            total_seasons_a=ind_a.total_seasons, total_seasons_b=ind_b.total_seasons,
        )

        if outcome_a == "win":
            score[a_id] += 1.0
            record[a_id]["win"] += 1
            record[b_id]["loss"] += 1
        elif outcome_a == "loss":
            score[b_id] += 1.0
            record[b_id]["win"] += 1
            record[a_id]["loss"] += 1
        else:
            score[a_id] += 0.5
            score[b_id] += 0.5
            record[a_id]["draw"] += 1
            record[b_id]["draw"] += 1

        match_log.append({
            "individual_a_id": a_id, "individual_b_id": b_id,
            "result": outcome_a, "games": games, "league": league_name,
        })

        name_a = getattr(ind_a, "display_name", None) or a_id
        name_b = getattr(ind_b, "display_name", None) or b_id
        elapsed = time.time() - start
        print(f"      {league_name}局{j}/{len(pairs)}: {name_a} vs {name_b} → {outcome_a}（経過{elapsed:.0f}秒）")

    ranked_ids_final = sorted(by_id.keys(), key=lambda i: (-score[i], seed_rank.get(i, 0)))
    ranked_members = [by_id[i] for i in ranked_ids_final]
    return ranked_members, match_log, score, record
