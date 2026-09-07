import itertools
import random

from .play import play_league_match


def run_round_robin(members, depth=4):
    """
    Aリーグの総当たり戦。全ペアが1回ずつ対局する。
    戻り値: (順位確定済みリスト, 対局ログ)
    """
    score = {ind.id: 0.0 for ind in members}
    by_id = {ind.id: ind for ind in members}
    match_log = []

    pairs = list(itertools.combinations(members, 2))
    random.shuffle(pairs)

    for ind_a, ind_b in pairs:
        outcome_a, games = play_league_match(ind_a, ind_b, depth=depth)

        if outcome_a == "win":
            score[ind_a.id] += 1.0
        elif outcome_a == "loss":
            score[ind_b.id] += 1.0
        else:
            score[ind_a.id] += 0.5
            score[ind_b.id] += 0.5

        match_log.append({
            "individual_a_id": ind_a.id, "individual_b_id": ind_b.id,
            "result": outcome_a, "games": games, "league": "A",
        })

    ranked_ids = sorted(score.keys(), key=lambda i: -score[i])

    # 1位が複数タイの場合は、その中で順位決定戦を行う
    top_score = score[ranked_ids[0]]
    tied_for_first = [i for i in ranked_ids if score[i] == top_score]
    if len(tied_for_first) > 1:
        ranked_ids = _resolve_first_place_tie(tied_for_first, ranked_ids, by_id, depth, match_log)

    ranked_members = [by_id[i] for i in ranked_ids]
    return ranked_members, match_log, score


def _resolve_first_place_tie(tied_ids, ranked_ids, by_id, depth, match_log):
    """1位タイの場合のみ、当事者同士で順位決定戦を行う（2名なら1局、3名以上なら総当たり）"""
    tied_members = [by_id[i] for i in tied_ids]
    tie_score = {i: 0.0 for i in tied_ids}

    for ind_a, ind_b in itertools.combinations(tied_members, 2):
        outcome_a, games = play_league_match(ind_a, ind_b, depth=depth, allow_rematch=True)
        if outcome_a == "win":
            tie_score[ind_a.id] += 1.0
        elif outcome_a == "loss":
            tie_score[ind_b.id] += 1.0
        else:
            tie_score[ind_a.id] += 0.5
            tie_score[ind_b.id] += 0.5

        match_log.append({
            "individual_a_id": ind_a.id, "individual_b_id": ind_b.id,
            "result": outcome_a, "games": games, "league": "A-playoff",
        })

    tied_ids_resolved = sorted(tied_ids, key=lambda i: -tie_score[i])
    others = [i for i in ranked_ids if i not in tied_ids]
    return tied_ids_resolved + others
