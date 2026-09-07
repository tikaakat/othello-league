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
    load_rosters, save_rosters, load_season_state, save_season_state, save_match_log,
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

    print("  --- Aリーグ（総当たり） ---")
    ranked_A, log_A, _ = run_round_robin(rosters["A"], depth=depth)
    match_log += log_A

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

    # --- タイトル戦 ---
    title_results = _run_title_matches(ranked_A, ranked_B, ranked_C, ranked_D, rosters, depth, state)

    # --- 昇降格・弟子補充・引退 ---
    rosters["A"], rosters["B"], rosters["C"], rosters["D"] = ranked_A, ranked_B, ranked_C, ranked_D
    registry = NameRegistry.from_dict(state.get("name_registry", {}))
    rosters, new_disciples, registry, retired = promote_and_relegate(rosters, season, registry)
    state["name_registry"] = registry.to_dict()

    print(f"  引退: {len(retired)}名（{', '.join(i.display_name for i in retired)}）" if retired else "  引退: なし")
    print(f"  新弟子: {len(new_disciples)}名")

    return rosters, match_log, title_results, retired


def _run_title_matches(ranked_A, ranked_B, ranked_C, ranked_D, rosters, depth, state):
    results = []
    titleholders = state.setdefault("titleholders", {"陸王": None, "海王": None, "空王": None})
    titleholder_params = state.setdefault("titleholder_params", {"陸王": None, "海王": None, "空王": None})

    # --- 陸王：Aリーグ1位が挑戦（初代不在なら無条件襲名） ---
    a_champion = ranked_A[0]
    if titleholders["陸王"] is None:
        titleholders["陸王"] = a_champion.display_name
        titleholder_params["陸王"] = effective_params(a_champion)
        print(f"  ★ 陸王 初代襲名: {a_champion.display_name}")
        results.append({"title": "陸王", "event": "初代襲名", "new_holder": a_champion.display_name})
    else:
        result = run_rikuou_challenge(a_champion, titleholder_params["陸王"], depth=depth)
        print(f"  陸王戦: {a_champion.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        if result["won"]:
            titleholders["陸王"] = a_champion.display_name
            titleholder_params["陸王"] = effective_params(a_champion)
        results.append({"title": "陸王", **result, "challenger_name": a_champion.display_name})

    # --- 海王：A1-3, B1, C1 の変則トーナメントで挑戦者決定 ---
    if len(ranked_A) >= 3 and ranked_B and ranked_C:
        challenger, bracket_log = determine_kaiou_challenger(
            ranked_A[0], ranked_A[1], ranked_A[2], ranked_B[0], ranked_C[0], depth=depth,
        )
        if titleholders["海王"] is None:
            titleholders["海王"] = challenger.display_name
            titleholder_params["海王"] = effective_params(challenger)
            print(f"  ★ 海王 初代襲名: {challenger.display_name}")
            results.append({"title": "海王", "event": "初代襲名", "new_holder": challenger.display_name})
        else:
            result = run_kaiou_challenge(challenger, titleholder_params["海王"], depth=depth)
            print(f"  海王戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
                  f" → {'奪取！' if result['won'] else '防衛'}")
            if result["won"]:
                titleholders["海王"] = challenger.display_name
                titleholder_params["海王"] = effective_params(challenger)
            results.append({"title": "海王", **result, "challenger_name": challenger.display_name, "bracket": bracket_log})

    # --- 空王：全員参加トーナメントで挑戦者決定 ---
    all_members = ranked_A + ranked_B + ranked_C + ranked_D
    challenger, bracket_log = determine_kuuou_challenger(all_members, depth=depth)
    if titleholders["空王"] is None:
        titleholders["空王"] = challenger.display_name
        titleholder_params["空王"] = effective_params(challenger)
        print(f"  ★ 空王 初代襲名: {challenger.display_name}")
        results.append({"title": "空王", "event": "初代襲名", "new_holder": challenger.display_name})
    else:
        result = run_kuuou_challenge(challenger, titleholder_params["空王"], depth=depth)
        print(f"  空王戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        if result["won"]:
            titleholders["空王"] = challenger.display_name
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
        rosters, match_log, title_results, retired = run_one_season(
            rosters, season, args.depth, args.swiss_rounds, state,
        )
        state["current_season"] = season
        state.setdefault("retired_archive", [])
        state["retired_archive"] += [ind.to_dict() for ind in retired]
        state.setdefault("title_history", [])
        state["title_history"] += title_results

        save_rosters(args.data_dir, rosters)
        save_match_log(args.data_dir, season, match_log)
        save_season_state(args.data_dir, state)
        print(f"Season {season} 完了・保存しました\n")


if __name__ == "__main__":
    main()
