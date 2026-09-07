from .play import play_league_match


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
    戻り値: (順位確定済みリスト, 対局ログ)
    """
    by_id = {ind.id: ind for ind in members}
    score = {ind.id: 0.0 for ind in members}
    played_pairs = set()
    match_log = []

    for rnd in range(rounds):
        ranked_ids = sorted(by_id.keys(), key=lambda i: (-score[i], -by_id[i].elo))
        pairs = swiss_pairing(ranked_ids, played_pairs)

        for a_id, b_id in pairs:
            played_pairs.add(frozenset((a_id, b_id)))
            ind_a, ind_b = by_id[a_id], by_id[b_id]

            outcome_a, games = play_league_match(ind_a, ind_b, depth=depth)

            if outcome_a == "win":
                score[a_id] += 1.0
            elif outcome_a == "loss":
                score[b_id] += 1.0
            else:
                score[a_id] += 0.5
                score[b_id] += 0.5

            match_log.append({
                "individual_a_id": a_id, "individual_b_id": b_id,
                "result": outcome_a, "games": games, "league": league_name,
            })

    ranked_ids_final = sorted(by_id.keys(), key=lambda i: (-score[i], -by_id[i].elo))
    ranked_members = [by_id[i] for i in ranked_ids_final]
    return ranked_members, match_log, score
