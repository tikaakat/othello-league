import argparse
import random

from othello_league.individual import LeagueIndividual
from othello_league.league import promote_and_relegate, LEAGUE_CAPACITY
from othello_league.dojo import MAJOR_DOJOS, effective_params
from othello_league.round_robin import run_round_robin
from othello_league.swiss import run_swiss_league
from othello_league.title import (
    run_rikuou_challenge, determine_kaiou_challenger, run_kaiou_challenge,
    determine_kuuou_challenger, run_kuuou_challenge,
)
from othello_league.names import NameRegistry
from othello_league.io_utils import (
    load_rosters, save_rosters, load_season_state, save_season_state, save_match_log, save_standings,
)

PARAM_KEYS = [
    "corner_weight", "danger_zone_weight", "mobility_weight", "edge_stability_weight",
    "frontier_weight", "disc_weight", "parity_weight", "center_weight",
]

# 初年度のみ適用する、リーグごとのパラメータ合計上限（傾斜配置）
INITIAL_SUM_CAP = {"A": 50, "B": 45, "C": 40, "D": 35}


def _random_params_with_cap(sum_cap):
    """8パラメータをランダムに割り振り、合計がsum_capを超えないよう正規化する"""
    raw = {k: random.uniform(0.5, 10.0) for k in PARAM_KEYS}
    total = sum(raw.values())
    scale = sum_cap / total if total > sum_cap else 1.0
    return {k: round(v * scale, 3) for k, v in raw.items()}


def bootstrap_rosters(registry):
    """初回起動時：A/B/Cはランダム個体、Dは8大流派の開祖2名ずつで初期化する"""
    rosters = {}
    for league in ("A", "B", "C"):
        cap = LEAGUE_CAPACITY[league]
        rosters[league] = [
            LeagueIndividual(
                f"{league}0-{i:03d}", league,
                params=_random_params_with_cap(INITIAL_SUM_CAP[league]),
                display_name=registry.generate(),
            )
            for i in range(cap)
        ]

    d_members = []
    for i in range(LEAGUE_CAPACITY["D"]):
        dojo = MAJOR_DOJOS[i % len(MAJOR_DOJOS)]
        ind = LeagueIndividual(
            f"D0-{i:03d}", "D", dojo=dojo,
            params=_random_params_with_cap(INITIAL_SUM_CAP["D"]),
            display_name=registry.generate(),
        )
        d_members.append(ind)
    rosters["D"] = d_members
    return rosters


def run_one_season(rosters, season, depth, swiss_rounds, state):
    match_log = []

    print(f"=== Season {season} ===")

    # --- 陸王在位者は、Aリーグの総当たり（順位戦）を免除される（防衛専念枠） ---
    titleholders = state.setdefault("titleholders", {"陸王": None, "海王": None, "空王": None})
    rikuou_holder_id = (titleholders.get("陸王") or {}).get("id")

    a_roster = rosters["A"]
    champion_ind = next((ind for ind in a_roster if ind.id == rikuou_holder_id), None) if rikuou_holder_id else None
    competing_A = [ind for ind in a_roster if champion_ind is None or ind.id != champion_ind.id]

    print("  --- Aリーグ（総当たり） ---" + ("　※陸王在位者は防衛専念枠のため対局免除" if champion_ind else ""))
    ranked_competing_A, log_A, _ = run_round_robin(competing_A, depth=depth)
    match_log += log_A

    # 公式Aリーグ順位：陸王在位者がいれば1位に据え、以降は総当たり結果を続ける
    ranked_A = ([champion_ind] + ranked_competing_A) if champion_ind else ranked_competing_A

    print("  --- Bリーグ（スイス方式） ---")
    ranked_B, log_B, _ = run_swiss_league(rosters["B"], rounds=swiss_rounds, depth=depth, league_name="B")
    match_log += log_B

    print("  --- Cリーグ（スイス方式） ---")
    ranked_C, log_C, _ = run_swiss_league(rosters["C"], rounds=swiss_rounds, depth=depth, league_name="C")
    match_log += log_C

    print("  --- Dリーグ（スイス方式） ---")
    ranked_D, log_D, _ = run_swiss_league(rosters["D"], rounds=swiss_rounds, depth=depth, league_name="D")
    match_log += log_D

    for name, ranked in (("A", ranked_A), ("B", ranked_B), ("C", ranked_C), ("D", ranked_D)):
        top = ranked[0]
        print(f"  {name}リーグ1位: {top.display_name}（{top.id}）")

    # --- 順位スナップショットを記録（昇降格が起きる"前"の、今シーズンの結果としての順位） ---
    standings_snapshot = []
    for league_name, ranked in (("A", ranked_A), ("B", ranked_B), ("C", ranked_C), ("D", ranked_D)):
        for rank, ind in enumerate(ranked, 1):
            standings_snapshot.append({
                "season": season, "league": league_name, "rank": rank,
                "individual_id": ind.id, "display_name": ind.display_name,
                "dojo": ind.dojo, "elo": round(ind.elo, 1),
            })
    pre_move_league_by_id = {ind.id: league_name for league_name, ranked in
                              (("A", ranked_A), ("B", ranked_B), ("C", ranked_C), ("D", ranked_D))
                              for ind in ranked}

    # --- タイトル戦 ---
    all_members = ranked_A + ranked_B + ranked_C + ranked_D
    title_results = _run_title_matches(
        ranked_A, ranked_competing_A, champion_ind, ranked_B, ranked_C, all_members, depth, state,
    )

    # --- 昇降格・弟子補充・引退 ---
    rosters["A"], rosters["B"], rosters["C"], rosters["D"] = ranked_A, ranked_B, ranked_C, ranked_D
    registry = NameRegistry.from_dict(state.get("name_registry", {}))
    rosters, new_disciples, registry, retired = promote_and_relegate(rosters, season, registry)
    state["name_registry"] = registry.to_dict()

    # --- 昇降格・新規・引退マークを確定する ---
    new_ids = {ind.id for ind in new_disciples}
    retired_ids = {ind.id for ind in retired}
    post_move_league_by_id = {ind.id: ind.league for league_list in rosters.values() for ind in league_list}

    for row in standings_snapshot:
        iid = row["individual_id"]
        if iid in retired_ids:
            row["movement"] = "retired"
        elif iid in post_move_league_by_id and post_move_league_by_id[iid] != pre_move_league_by_id.get(iid):
            new_league, old_league = post_move_league_by_id[iid], pre_move_league_by_id.get(iid)
            league_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
            row["movement"] = "promoted" if league_rank[new_league] < league_rank[old_league] else "relegated"
        else:
            row["movement"] = "stay"
    for ind in new_disciples:
        standings_snapshot.append({
            "season": season, "league": "D", "rank": None,
            "individual_id": ind.id, "display_name": ind.display_name,
            "dojo": ind.dojo, "elo": round(ind.elo, 1), "movement": "new",
        })

    print(f"  引退: {len(retired)}名（{', '.join(i.display_name for i in retired)}）" if retired else "  引退: なし")
    print(f"  新弟子: {len(new_disciples)}名")

    return rosters, match_log, title_results, retired, standings_snapshot


def _run_title_matches(ranked_A, ranked_competing_A, champion_ind, ranked_B, ranked_C, all_members, depth, state):
    results = []
    titleholders = state.setdefault("titleholders", {"陸王": None, "海王": None, "空王": None})
    titleholder_params = state.setdefault("titleholder_params", {"陸王": None, "海王": None, "空王": None})

    # ============================================================
    # 陸王：Aリーグ総当たり1位が挑戦（陸王在位者は対局免除で待ち受ける）
    # ============================================================
    a_challenger = ranked_competing_A[0]
    rikuou_defended = None  # 防衛結果（海王シードの組み立てに使う）

    if titleholders["陸王"] is None:
        titleholders["陸王"] = {"id": a_challenger.id, "name": a_challenger.display_name}
        titleholder_params["陸王"] = effective_params(a_challenger)
        print(f"  ★ 陸王 初代襲名: {a_challenger.display_name}")
        results.append({"title": "陸王", "event": "初代襲名", "new_holder": a_challenger.display_name})
        new_rikuou = a_challenger
        old_rikuou = None
    else:
        result = run_rikuou_challenge(a_challenger, titleholder_params["陸王"], depth=depth)
        print(f"  陸王戦: {a_challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        old_rikuou = champion_ind
        if result["won"]:
            titleholders["陸王"] = {"id": a_challenger.id, "name": a_challenger.display_name}
            titleholder_params["陸王"] = effective_params(a_challenger)
            new_rikuou = a_challenger
            rikuou_defended = False
        else:
            new_rikuou = champion_ind
            rikuou_defended = True
        results.append({"title": "陸王", **result, "challenger_name": a_challenger.display_name})

    # ============================================================
    # 海王：A1〜A6・B1・C1によるラダー方式
    #   防衛時: A1=陸王, A2=Aリーグ総当たり1位
    #   奪取/初代時: A1=新陸王, A2=前陸王（前陸王がいなければ総当たり2位）
    # ============================================================
    if rikuou_defended is True:
        kaiou_a1, kaiou_a2 = new_rikuou, ranked_competing_A[0]
        remaining = ranked_competing_A[1:5]
    elif rikuou_defended is False:
        kaiou_a1, kaiou_a2 = new_rikuou, old_rikuou
        remaining = ranked_competing_A[1:5]
    else:
        # 初代襲名（前陸王が存在しない）：総当たり順位からそのままA1〜A6を割り当てる
        kaiou_a1, kaiou_a2 = ranked_competing_A[0], ranked_competing_A[1]
        remaining = ranked_competing_A[2:6]

    kaiou_slots = [kaiou_a1, kaiou_a2] + remaining
    if len(kaiou_slots) == 6 and ranked_B and ranked_C:
        challenger, bracket_log = determine_kaiou_challenger(
            kaiou_slots, ranked_B[0], ranked_C[0], depth=depth,
        )
        if titleholders["海王"] is None:
            titleholders["海王"] = {"id": challenger.id, "name": challenger.display_name}
            titleholder_params["海王"] = effective_params(challenger)
            print(f"  ★ 海王 初代襲名: {challenger.display_name}")
            results.append({"title": "海王", "event": "初代襲名", "new_holder": challenger.display_name})
        else:
            result = run_kaiou_challenge(challenger, titleholder_params["海王"], depth=depth)
            print(f"  海王戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
                  f" → {'奪取！' if result['won'] else '防衛'}")
            if result["won"]:
                titleholders["海王"] = {"id": challenger.id, "name": challenger.display_name}
                titleholder_params["海王"] = effective_params(challenger)
            results.append({"title": "海王", **result, "challenger_name": challenger.display_name, "bracket": bracket_log})
    else:
        print("  海王戦: 参加者不足のため今季は見送り")

    # ============================================================
    # 空王：Elo上位8名（前年空王在位者は防衛専念枠として除外）による正式シードトーナメント
    # ============================================================
    kuuou_holder_id = (titleholders.get("空王") or {}).get("id")
    challenger, bracket_log = determine_kuuou_challenger(all_members, exclude_id=kuuou_holder_id, depth=depth)

    if titleholders["空王"] is None:
        titleholders["空王"] = {"id": challenger.id, "name": challenger.display_name}
        titleholder_params["空王"] = effective_params(challenger)
        print(f"  ★ 空王 初代襲名: {challenger.display_name}")
        results.append({"title": "空王", "event": "初代襲名", "new_holder": challenger.display_name})
    else:
        result = run_kuuou_challenge(challenger, titleholder_params["空王"], depth=depth)
        print(f"  空王戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        if result["won"]:
            titleholders["空王"] = {"id": challenger.id, "name": challenger.display_name}
            titleholder_params["空王"] = effective_params(challenger)
        results.append({"title": "空王", **result, "challenger_name": challenger.display_name, "bracket": bracket_log})

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--seasons", type=int, default=1)
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--swiss-rounds", type=int, default=4)
    args = parser.parse_args()

    state = load_season_state(args.data_dir)
    registry = NameRegistry.from_dict(state.get("name_registry", {}))

    rosters = load_rosters(args.data_dir)
    if rosters is None:
        print("初回起動：ロスターを新規作成します")
        rosters = bootstrap_rosters(registry)
        state["name_registry"] = registry.to_dict()

    for _ in range(args.seasons):
        season = state["current_season"] + 1
        rosters, match_log, title_results, retired, standings_snapshot = run_one_season(
            rosters, season, args.depth, args.swiss_rounds, state,
        )
        state["current_season"] = season
        state.setdefault("retired_archive", [])
        state["retired_archive"] += [ind.to_dict() for ind in retired]
        state.setdefault("title_history", [])
        state["title_history"] += title_results

        save_rosters(args.data_dir, rosters)
        save_match_log(args.data_dir, season, match_log)
        save_standings(args.data_dir, season, standings_snapshot)
        save_season_state(args.data_dir, state)
        print(f"Season {season} 完了・保存しました\n")


if __name__ == "__main__":
    main()
