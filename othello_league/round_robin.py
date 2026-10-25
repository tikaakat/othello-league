import itertools
import random
import time

from .play import play_decisive_match
from .parallel import run_matches
from .elo import update_elo


def run_round_robin(members, depth=4, league_name="A", log_prefix="A", seed_order=None,
                    suzaku_tiebreak=None, relegate_n=0):
    """
    総当たり戦（Aリーグ、および朱雀戦の紅白リーグで使用）。全ペアが1回ずつ対局する。
    seed_order（前季順位継承のID列）を渡した場合、同点は1位タイも含めて全てseed_orderで
    決め、順位決定戦は行わない（Aリーグ用）。
    渡さない場合（紅白リーグ）は_rank_suzaku_groupで順位を決める（1位タイは決定戦、
    それ以外の同点は前季順位→紅白通算在籍期数→残留/陥落の境目をまたぐ場合のみ決定戦）。
    決定戦の勝敗は順位表の成績（record）には含めず、対局ログに"{league_name}-playoff"として残す。
    戻り値: (順位確定済みリスト, 対局ログ, 勝ち点, 個体ごとの勝敗分dict)
    """
    score = {ind.id: 0.0 for ind in members}
    record = {ind.id: {"win": 0, "loss": 0, "draw": 0} for ind in members}
    by_id = {ind.id: ind for ind in members}
    match_log = []

    pairs = list(itertools.combinations(members, 2))
    random.shuffle(pairs)
    total = len(pairs)
    start = time.time()

    # 個体が重ならない対局をまとめて並列に打つ（先後の割り振り・乱数は1局ずつ打った場合と同じ規則。結果は元の順に反映）
    all_results = run_matches(pairs, depth=depth, allow_rematch=False)
    for i, ((ind_a, ind_b), (outcome_a, games)) in enumerate(zip(pairs, all_results), 1):
        ind_a.elo, ind_b.elo = update_elo(
            ind_a.elo, ind_b.elo, outcome_a,
            total_seasons_a=ind_a.total_seasons, total_seasons_b=ind_b.total_seasons,
        )

        if outcome_a == "win":
            score[ind_a.id] += 1.0
            record[ind_a.id]["win"] += 1
            record[ind_b.id]["loss"] += 1
        elif outcome_a == "loss":
            score[ind_b.id] += 1.0
            record[ind_b.id]["win"] += 1
            record[ind_a.id]["loss"] += 1
        else:
            score[ind_a.id] += 0.5
            score[ind_b.id] += 0.5
            record[ind_a.id]["draw"] += 1
            record[ind_b.id]["draw"] += 1

        match_log.append({
            "individual_a_id": ind_a.id, "individual_b_id": ind_b.id,
            "result": outcome_a, "games": games, "league": league_name,
        })

        name_a = getattr(ind_a, "display_name", None) or ind_a.id
        name_b = getattr(ind_b, "display_name", None) or ind_b.id
        elapsed = time.time() - start
        print(f"    {log_prefix}局{i}/{total}: {name_a} vs {name_b} → {outcome_a}（経過{elapsed:.0f}秒）")

    if seed_order:
        seed_rank = {iid: n for n, iid in enumerate(seed_order)}
        ranked_ids = sorted(score.keys(), key=lambda i: (-score[i], seed_rank.get(i, len(seed_rank))))
        return [by_id[i] for i in ranked_ids], match_log, score, record

    ranked_ids = _rank_suzaku_group(score, by_id, depth, match_log, league_name,
                                    suzaku_tiebreak or {}, relegate_n)
    return [by_id[i] for i in ranked_ids], match_log, score, record


def _playoff_round(ids, by_id, depth, match_log, league_name):
    """idsの総当たり決定戦（引き分けなしで決着）を1回行い、勝数dictを返す。Eloは1局ごとに更新する"""
    wins = {i: 0 for i in ids}
    for a_id, b_id in itertools.combinations(ids, 2):
        ind_a, ind_b = by_id[a_id], by_id[b_id]
        outcome_a, games = play_decisive_match(ind_a, ind_b, depth=depth)
        for g in games:
            ind_a.elo, ind_b.elo = update_elo(
                ind_a.elo, ind_b.elo, g["outcome_a"],
                total_seasons_a=ind_a.total_seasons, total_seasons_b=ind_b.total_seasons,
            )
        if outcome_a == "win":
            wins[a_id] += 1
        elif outcome_a == "loss":
            wins[b_id] += 1
        match_log.append({
            "individual_a_id": a_id, "individual_b_id": b_id,
            "result": outcome_a, "games": games, "league": f"{league_name}-playoff",
        })
    return wins


def _playoff_full_order(ids, by_id, depth, match_log, league_name, fallback_key, _round=1, _max_rounds=5):
    """決定戦で並び順を確定させる（同勝数が残ればその中だけで再度決定戦）"""
    if len(ids) <= 1 or _round > _max_rounds:
        return sorted(ids, key=fallback_key)
    wins = _playoff_round(ids, by_id, depth, match_log, league_name)
    ordered = []
    for w in sorted(set(wins.values()), reverse=True):
        group = [i for i in ids if wins[i] == w]
        ordered += _playoff_full_order(group, by_id, depth, match_log, league_name, fallback_key, _round + 1, _max_rounds)
    return ordered


def _rank_suzaku_group(score, by_id, depth, match_log, league_name, tiebreak, relegate_n):
    """紅白リーグの順位付け。
    ・1位タイ：決定戦で1位を決める（決まらなければ再度、勝数が並んだ者だけで決定戦）
    ・それ以外の同点：前季の紅白リーグ順位（在位者として在籍のみだった季は最上位扱い、
      前季に紅白にいなかった個体は最下位扱い）→ 紅白リーグ通算在籍期数の長い方 →
      それでも並び、かつ残留/陥落の境目をまたぐ場合のみ決定戦（またがない場合はID順）"""
    prev_rank = tiebreak.get("prev_rank", {})
    tenure = tiebreak.get("tenure", {})

    def key(i):
        return (prev_rank.get(i, 99), -tenure.get(i, 0), i)

    n = len(score)
    cutoff = n - relegate_n  # この順位（1始まり）までが残留
    ranked = []
    remaining = sorted(score.keys(), key=lambda i: -score[i])
    first_tie_done = False
    while remaining:
        top = score[remaining[0]]
        group = [i for i in remaining if score[i] == top]
        remaining = [i for i in remaining if score[i] != top]

        if not first_tie_done and len(group) > 1:
            first_tie_done = True
            print(f"    1位タイ（{len(group)}名）→ 決定戦を実施")
            contenders = list(group)
            rnd = 1
            while len(contenders) > 1 and rnd <= 5:
                wins = _playoff_round(contenders, by_id, depth, match_log, league_name)
                best = max(wins.values())
                contenders = [i for i in contenders if wins[i] == best]
                rnd += 1
            winner = sorted(contenders, key=key)[0]
            ranked.append(winner)
            group = [i for i in group if i != winner]
        first_tie_done = True

        start_pos = len(ranked) + 1
        ordered = sorted(group, key=key)
        # 前季順位・在籍期数まで同じ組は、残留/陥落の境目をまたぐ場合のみ決定戦
        result = []
        idx = 0
        while idx < len(ordered):
            k = key(ordered[idx])[:2]
            same = [i for i in ordered[idx:] if key(i)[:2] == k]
            pos_from = start_pos + idx
            pos_to = pos_from + len(same) - 1
            if len(same) > 1 and relegate_n > 0 and pos_from <= cutoff < pos_to:
                print(f"    残留/陥落の境目で同率（{len(same)}名）→ 決定戦を実施")
                same = _playoff_full_order(same, by_id, depth, match_log, league_name, key)
            result += same
            idx += len(same)
        ranked += result
    return ranked
