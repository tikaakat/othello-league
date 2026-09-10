import time

from .play import play_league_match
from .elo import update_elo


def swiss_pairing(ranked_ids, played_pairs, scores):
    """
    オランダ式スイスペアリング。
    同じ得点（スコア）のグループごとに、上位半分×下位半分で組む
    （例：得点グループが8名なら、1位×5位、2位×6位、3位×7位、4位×8位）。
    既に対戦済みの組み合わせは避け、代わりの相手を探す（見つからなければ、
    グループの境界を越えて次のグループの選手を繰り上げる）。
    """
    remaining = list(ranked_ids)
    pairs = []

    while remaining:
        # 現在の得点グループ（先頭と同じ得点の人たち）を切り出す
        top_score = scores[remaining[0]]
        group = [x for x in remaining if scores[x] == top_score]
        # グループの人数が奇数、かつ他に残りがいる場合は、グループの最下位を
        # 次の得点グループに繰り下げて偶数に揃える（スイス方式の一般的な処理）
        if len(group) % 2 != 0 and len(group) < len(remaining):
            group = group[:-1]

        if len(group) < 2:
            # ペアを作れない場合（最後の1人など）は、残り全体から総当たり的に処理する
            a = remaining.pop(0)
            matched_idx = None
            for i, b in enumerate(remaining):
                if frozenset((a, b)) not in played_pairs:
                    matched_idx = i
                    break
            if matched_idx is None and remaining:
                matched_idx = 0
            if matched_idx is not None:
                b = remaining.pop(matched_idx)
                pairs.append((a, b))
            continue

        half = len(group) // 2
        top_half, bottom_half = group[:half], group[half:]

        for a in top_half:
            if a not in remaining:
                continue
            matched_b = None
            for b in bottom_half:
                if b in remaining and frozenset((a, b)) not in played_pairs:
                    matched_b = b
                    break
            if matched_b is None:
                # 下位半分の中に未対戦の相手がいない場合、グループ全体（上位半分含む）から探す
                for b in group:
                    if b != a and b in remaining and frozenset((a, b)) not in played_pairs:
                        matched_b = b
                        break
            if matched_b is None:
                # それでも見つからなければ、既対戦でも仕方なく組む（安全策）
                candidates = [b for b in group if b != a and b in remaining]
                matched_b = candidates[0] if candidates else None

            if matched_b is not None:
                pairs.append((a, matched_b))
                remaining.remove(a)
                remaining.remove(matched_b)

    return pairs


def run_swiss_league(members, rounds=4, depth=4, league_name="B"):
    """
    B/C/Dリーグのスイス方式トーナメント。
    戻り値: (順位確定済みリスト, 対局ログ, 勝ち点, 個体ごとの勝敗分dict)
    """
    by_id = {ind.id: ind for ind in members}
    score = {ind.id: 0.0 for ind in members}
    record = {ind.id: {"win": 0, "loss": 0, "draw": 0} for ind in members}
    played_pairs = set()
    match_log = []
    start = time.time()

    for rnd in range(rounds):
        ranked_ids = sorted(by_id.keys(), key=lambda i: (-score[i], -by_id[i].elo))
        pairs = swiss_pairing(ranked_ids, played_pairs, score)
        print(f"    {league_name}リーグ ラウンド{rnd + 1}/{rounds}（{len(pairs)}局）")

        for j, (a_id, b_id) in enumerate(pairs, 1):
            played_pairs.add(frozenset((a_id, b_id)))
            ind_a, ind_b = by_id[a_id], by_id[b_id]

            outcome_a, games = play_league_match(ind_a, ind_b, depth=depth, allow_rematch=True)
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

    ranked_ids_final = sorted(by_id.keys(), key=lambda i: (-score[i], -by_id[i].elo))
    ranked_members = [by_id[i] for i in ranked_ids_final]
    return ranked_members, match_log, score, record
