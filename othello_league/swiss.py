import time

from .play import play_league_match
from .elo import update_elo


def swiss_pairing(ranked_ids, played_pairs):
    """スコア順に並んだID列から、既対戦を避けつつペアを組む"""
    unpaired = list(ranked_ids)
    pairs = []
    while len(unpaired) >= 2:
        a = unpaired.pop(0)
        matched_idx = None
        for i, b in enumerate(unpaired):
            if frozenset((a, b)) not in played_pairs:
                matched_idx = i
                break
        if matched_idx is None:
            matched_idx = 0
        b = unpaired.pop(matched_idx)
        pairs.append((a, b))
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
        pairs = swiss_pairing(ranked_ids, played_pairs)
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
