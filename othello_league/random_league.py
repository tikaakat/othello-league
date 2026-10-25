import random
import time

from .parallel import match_waves, drive
from .elo import update_elo


def _circulant_pairs(order, degree):
    """
    ランダムに並べた円環上で、各個体を前後degree/2人ずつと結ぶ（circulant graph）。
    degreeが偶数であれば、人数の偶奇に関わらず必ず全員ちょうどdegree戦になる。
    """
    n = len(order)
    half = degree // 2
    pairs = set()
    for i in range(n):
        for k in range(1, half + 1):
            j = (i + k) % n
            pairs.add(frozenset((order[i], order[j])))
    return pairs


def _build_schedule(ids, games_per_individual):
    """
    各個体がちょうど games_per_individual 人のユニークな相手と対戦する
    組み合わせを作る。参加人数が少なく全員と当たっても対戦数に届かない
    場合は、全員総当たり（人数-1戦）にフォールバックする。

    対戦数が偶数の場合は、ランダムに並べた円環上で各個体を前後N/2人ずつと
    結ぶ（circulant graph）ことで、人数の偶奇に関わらず必ず全員ちょうど
    games_per_individual戦の組み合わせになる。奇数の場合は、1人少ない
    偶数次数のcirculant graphを作った上で、ランダムな重複なしペアリングで
    1戦ずつ積み増す（人数×対戦数が奇数の場合、理論上全員を同数にはできない
    ため、その時だけ1名だけ対戦数が1少なくなる）。
    """
    n = len(ids)
    if n <= 1:
        return []

    k = min(games_per_individual, n - 1)
    if k >= n - 1:
        # 総当たり（全員と1回ずつ対戦）
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                pairs.append((ids[i], ids[j]))
        return pairs

    order = list(ids)
    random.shuffle(order)

    if k % 2 == 0:
        return [tuple(p) for p in _circulant_pairs(order, k)]

    # 奇数対戦数：まず(k-1)次数のcirculant graphを作り、残り1戦分を
    # まだ上限に届いていない個体同士でランダムにマッチングして積み増す。
    # 人数×対戦数が奇数の場合は理論上全員を+1できないため、その時だけ
    # 1名だけ対戦数が1少なくなる（誰になるかはランダムに選ぶ）。
    base_pairs = _circulant_pairs(order, k - 1)
    pool = list(order)
    if (n * k) % 2 != 0:
        pool.remove(random.choice(pool))

    for _attempt in range(50):
        remaining = list(pool)
        random.shuffle(remaining)
        extra_pairs = set()
        ok = True
        while remaining:
            a = remaining.pop()
            matched_idx = None
            for idx, b in enumerate(remaining):
                if frozenset((a, b)) not in base_pairs and frozenset((a, b)) not in extra_pairs:
                    matched_idx = idx
                    break
            if matched_idx is None:
                ok = False
                break
            b = remaining.pop(matched_idx)
            extra_pairs.add(frozenset((a, b)))
        if ok:
            return [tuple(p) for p in (base_pairs | extra_pairs)]

    # 50回試しても全員を組み切れなかった場合（極端な人数・対戦数の組み合わせ）は、
    # 組めた分だけ積み増して返す（一部の個体だけ対戦数が1少なくなる）
    return [tuple(p) for p in (base_pairs | extra_pairs)]


def plan_random_league(member_ids, games_per_individual=8):
    """組み合わせ（IDの組のリスト・対局順）を決める。季の初めに公開できるよう、前の季の終わりに呼ぶ。
    member_idsには、まだ入団していない新人の仮の枠（NEW1等）を含めてよい"""
    pairs = _build_schedule(list(member_ids), games_per_individual)
    random.shuffle(pairs)
    return [list(p) for p in pairs]


def run_random_league(*args, **kwargs):
    return drive(random_league_gen(*args, **kwargs))[0]


def random_league_gen(members, games_per_individual=8, depth=4, league_name="B", seed_order=None, allow_rematch=True,
                      planned_pairs=None, slot_ids=None):
    """
    B/C/Dリーグの対戦方式。スイス方式（得点ベースの組み合わせ）ではなく、
    季ごとにランダムに相手を割り振り、各個体がちょうど games_per_individual
    戦（デフォルト8戦）を行う。
    全員と総当たりはせず、毎季ランダムな対戦カードになるため波乱が起きやすい。
    seed_order: 同点時のタイブレークに使う順序（前季順位継承）のID列。
    指定が無い場合は、後方互換のためElo順にフォールバックする。
    planned_pairs: 前の季の終わりに plan_random_league で決めて公開した組み合わせ。新人の仮の枠は
    slot_ids（枠名→ID）で置き換え、いなくなった選手・埋まらなかった枠を含む対局は行わない。
    無ければこの場で決める。
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

    if planned_pairs is not None:
        slot_ids = slot_ids or {}
        pairs = [(slot_ids.get(a, a), slot_ids.get(b, b)) for a, b in planned_pairs]
        pairs = [(a, b) for a, b in pairs if a in by_id and b in by_id]
        # 公開した組み合わせに入っていない選手（予定外に加わった新人など）は、この場で相手を決める
        missing = [i for i in by_id if not any(i in p for p in pairs)]
        if missing:
            pairs += _build_schedule(missing, games_per_individual) if len(missing) > 1 else []
    else:
        pairs = _build_schedule(list(by_id.keys()), games_per_individual)
        random.shuffle(pairs)
    print(f"    {league_name}リーグ（ランダム{games_per_individual}戦・{len(pairs)}局）")

    # 個体が重ならない対局をまとめて並列に打つ（先後の割り振り・乱数は1局ずつ打った場合と同じ規則。結果は元の順に反映）
    # （ジェネレータとして書いてあり、parallel.drive で他のリーグ戦・タイトル戦の予選と同時に進められる）
    all_results = yield from match_waves([(by_id[a], by_id[b]) for a, b in pairs], depth=depth, allow_rematch=allow_rematch)
    for j, ((a_id, b_id), (outcome_a, games)) in enumerate(zip(pairs, all_results), 1):
        ind_a, ind_b = by_id[a_id], by_id[b_id]

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
