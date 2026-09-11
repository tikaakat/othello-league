import argparse
import json
import os
import random

from othello_league.individual import LeagueIndividual
from othello_league.league import promote_and_relegate, LEAGUE_CAPACITY
from othello_league.dojo import MAJOR_DOJOS, effective_params, roll_age_multipliers
from othello_league.round_robin import run_round_robin
from othello_league.swiss import run_swiss_league
from othello_league.title import (
    run_rikuou_challenge, determine_kaiou_challenger, run_kaiou_challenge,
    determine_kuuou_challenger, run_kuuou_challenge,
)
from othello_league.names import NameRegistry
from othello_league.elo import update_elo
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


def _random_volatility():
    """ムラ気（隠しパラメータ）をランダムに生成する。0.3〜2.0の範囲、平均1.0程度"""
    return random.uniform(0.3, 2.0)


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
        for ind in rosters[league]:
            ind.volatility = _random_volatility()

    d_members = []
    for i in range(LEAGUE_CAPACITY["D"]):
        dojo = MAJOR_DOJOS[i % len(MAJOR_DOJOS)]
        ind = LeagueIndividual(
            f"D0-{i:03d}", "D", dojo=dojo,
            params=_random_params_with_cap(INITIAL_SUM_CAP["D"]),
            display_name=registry.generate(),
        )
        ind.volatility = _random_volatility()
        d_members.append(ind)
    rosters["D"] = d_members
    return rosters


def _build_seed_order(members, league_name, prev_standings_by_id):
    """
    スイス方式の初期シード順（＝ラウンド1の並び順、同点時のタイブレーク）を決める。
    """
    stayed = []
    others = []
    for ind in members:
        prev = prev_standings_by_id.get(ind.id)
        if prev is not None and prev["league"] == league_name:
            stayed.append((ind.id, prev["rank"]))
        else:
            others.append(ind.id)

    stayed_sorted = [iid for iid, _ in sorted(stayed, key=lambda t: t[1])]
    others_sorted = sorted(others)

    return stayed_sorted + others_sorted


def run_one_season(rosters, season, depth, swiss_rounds, state, prev_standings_by_id=None):
    prev_standings_by_id = prev_standings_by_id or {}
    match_log = []

    print(f"=== Season {season} ===")

    # --- 年齢バフを毎シーズン再抽選する（対局が始まる前に） ---
    for league_list in rosters.values():
        for ind in league_list:
            ind.age_multipliers = roll_age_multipliers(ind.age, PARAM_KEYS)

    # --- 陸王在位者は、Aリーグの総当たり（順位戦）を免除される（防衛専念枠） ---
    titleholders = state.setdefault("titleholders", {"陸王": None, "海王": None, "空王": None})
    rikuou_holder_id = (titleholders.get("陸王") or {}).get("id")

    a_roster = rosters["A"]
    champion_ind = next((ind for ind in a_roster if ind.id == rikuou_holder_id), None) if rikuou_holder_id else None
    competing_A = [ind for ind in a_roster if champion_ind is None or ind.id != champion_ind.id]

    print("  --- Aリーグ（総当たり） ---" + ("　※陸王在位者は防衛専念枠のため対局免除" if champion_ind else ""))
    ranked_competing_A, log_A, _, record_A = run_round_robin(competing_A, depth=depth)
    match_log += log_A

    # 公式Aリーグ順位：陸王在位者がいれば1位に据え、以降は総当たり結果を続ける
    ranked_A = ([champion_ind] + ranked_competing_A) if champion_ind else ranked_competing_A
    if champion_ind:
        record_A[champion_ind.id] = {"win": 0, "loss": 0, "draw": 0}  # 対局免除のため記録なし

    print("  --- Bリーグ（スイス方式） ---")
    ranked_B, log_B, _, record_B = run_swiss_league(
        rosters["B"], rounds=swiss_rounds, depth=depth, league_name="B",
        seed_order=_build_seed_order(rosters["B"], "B", prev_standings_by_id),
    )
    match_log += log_B

    print("  --- Cリーグ（スイス方式） ---")
    ranked_C, log_C, _, record_C = run_swiss_league(
        rosters["C"], rounds=swiss_rounds, depth=depth, league_name="C",
        seed_order=_build_seed_order(rosters["C"], "C", prev_standings_by_id),
    )
    match_log += log_C

    print("  --- Dリーグ（スイス方式） ---")
    ranked_D, log_D, _, record_D = run_swiss_league(
        rosters["D"], rounds=swiss_rounds, depth=depth, league_name="D",
        seed_order=_build_seed_order(rosters["D"], "D", prev_standings_by_id),
    )
    match_log += log_D

    all_records = {**record_A, **record_B, **record_C, **record_D}

    for league_list in (ranked_A, ranked_B, ranked_C, ranked_D):
        for ind in league_list:
            rec = all_records.get(ind.id, {"win": 0, "loss": 0, "draw": 0})
            ind.win_this_season = rec["win"]
            ind.loss_this_season = rec["loss"]

    for name, ranked in (("A", ranked_A), ("B", ranked_B), ("C", ranked_C), ("D", ranked_D)):
        top = ranked[0]
        print(f"  {name}リーグ1位: {top.display_name}（{top.id}）")

    # --- 順位スナップショットを記録 ---
    standings_snapshot = []
    for league_name, ranked in (("A", ranked_A), ("B", ranked_B), ("C", ranked_C), ("D", ranked_D)):
        for rank, ind in enumerate(ranked, 1):
            rec = all_records.get(ind.id, {"win": 0, "loss": 0, "draw": 0})
            no_roundrobin = bool(champion_ind is not None and ind.id == champion_ind.id)
            standings_snapshot.append({
                "season": season, "league": league_name, "rank": rank,
                "individual_id": ind.id, "display_name": ind.display_name,
                "dojo": ind.dojo, "elo": round(ind.elo, 1),
                "win": rec["win"], "loss": rec["loss"], "draw": rec["draw"],
                "no_roundrobin": no_roundrobin,
            })
    pre_move_league_by_id = {ind.id: league_name for league_name, ranked in
                              (("A", ranked_A), ("B", ranked_B), ("C", ranked_C), ("D", ranked_D))
                              for ind in ranked}
    is_first_season_by_id = {ind.id: (ind.total_seasons == 0) for league_name, ranked in
                              (("A", ranked_A), ("B", ranked_B), ("C", ranked_C), ("D", ranked_D))
                              for ind in ranked}

    # --- タイトル戦 ---
    all_members = ranked_A + ranked_B + ranked_C + ranked_D
    title_results = _run_title_matches(
        ranked_A, ranked_competing_A, champion_ind, ranked_B, ranked_C, all_members, depth, state, season,
    )
    title_match_log = _title_results_to_match_log(title_results, season)
    match_log += title_match_log

    # --- 昇降格・弟子補充・引退 ---
    rosters["A"], rosters["B"], rosters["C"], rosters["D"] = ranked_A, ranked_B, ranked_C, ranked_D
    registry = NameRegistry.from_dict(state.get("name_registry", {}))
    rosters, new_disciples, registry, retired = promote_and_relegate(rosters, season, registry, titleholders=titleholders)
    state["name_registry"] = registry.to_dict()

    # --- 昇降格・新規・引退マークを確定する ---
    retired_ids = {ind.id for ind in retired}
    post_move_league_by_id = {ind.id: ind.league for league_list in rosters.values() for ind in league_list}

    for row in standings_snapshot:
        iid = row["individual_id"]
        tags = []
        if is_first_season_by_id.get(iid):
            tags.append("new")
        if iid in post_move_league_by_id and post_move_league_by_id[iid] != pre_move_league_by_id.get(iid):
            new_league, old_league = post_move_league_by_id[iid], pre_move_league_by_id.get(iid)
            league_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
            tags.append("promoted" if league_rank[new_league] < league_rank[old_league] else "relegated")
        if iid in retired_ids:
            tags = ["retired"]
        if not tags:
            tags = ["stay"]
        row["movement"] = ",".join(tags)

    print(f"  引退: {len(retired)}名（{', '.join(i.display_name for i in retired)}）" if retired else "  引退: なし")
    print(f"  新弟子: {len(new_disciples)}名")

    # 歴代最高Eloを更新する
    for league_list in rosters.values():
        for ind in league_list:
            if ind.elo > ind.peak_elo:
                ind.peak_elo = ind.elo
    for ind in retired:
        if ind.elo > ind.peak_elo:
            ind.peak_elo = ind.elo

    return rosters, match_log, title_results, retired, standings_snapshot


def _result_from_winner_tag(tag):
    if tag == "draw":
        return "draw"
    return "win" if tag == "a" else "loss"


def _title_results_to_match_log(title_results, season):
    entries = []
    for r in title_results:
        title = r["title"]

        if "games" in r and r.get("challenger_id"):
            for g in r["games"]:
                entries.append({
                    "league": title,
                    "individual_a_id": r["challenger_id"],
                    "individual_b_id": r.get("defender_id"),
                    "result": _result_from_winner_tag(g.get("winner")),
                    "games": [g],
                })

        for matchup in r.get("bracket", []):
            x_id, y_id, winner_id = matchup["a"], matchup["b"], matchup["winner"]
            for g in matchup.get("games", []):
                if g["result"] == "draw":
                    result_for_log = "draw"
                else:
                    x_won = (g["result"] == "black") == g["x_was_black"]
                    result_for_log = "win" if x_won else "loss"
                entries.append({
                    "league": f"{title}予選",
                    "individual_a_id": x_id,
                    "individual_b_id": y_id,
                    "result": result_for_log,
                    "games": [g],
                })

    return entries


def _run_title_matches(ranked_A, ranked_competing_A, champion_ind, ranked_B, ranked_C, all_members, depth, state, season):
    results = []
    titleholders = state.setdefault("titleholders", {"陸王": None, "海王": None, "空王": None})
    titleholder_params = state.setdefault("titleholder_params", {"陸王": None, "海王": None, "空王": None})
    all_members_by_id = {ind.id: ind for ind in all_members}

    # ============================================================
    # 陸王：Aリーグ総当たり1位が挑戦（陸王在位者は対局免除で待ち受ける）
    # ============================================================
    a_challenger = ranked_competing_A[0]
    rikuou_defended = None

    # 「titleholders[陸王]の記録はあるが、在位者本人が今季のロスターに見当たらない」
    # （引退等で消えた）場合も、記録が無い場合と同じく空位＝新規襲名として扱う
    rikuou_vacant = titleholders["陸王"] is not None and champion_ind is None
    if rikuou_vacant:
        print(f"  ※ 陸王在位者（{titleholders['陸王'].get('name')}）がロスターに見当たらないため、空位として扱います")

    if titleholders["陸王"] is None or rikuou_vacant:
        titleholders["陸王"] = {"id": a_challenger.id, "name": a_challenger.display_name}
        titleholder_params["陸王"] = effective_params(a_challenger)
        print(f"  ★ 陸王 初代襲名: {a_challenger.display_name}")
        results.append({"title": "陸王", "season": season, "event": "初代襲名", "new_holder": a_challenger.display_name})
        new_rikuou = a_challenger
        old_rikuou = None
    else:
        result = run_rikuou_challenge(
            a_challenger, titleholder_params["陸王"], depth=depth,
            titleholder_volatility=champion_ind.volatility if champion_ind is not None else 1.0,
        )
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
        if champion_ind is not None:
            a_challenger.elo, champion_ind.elo = update_elo(
                a_challenger.elo, champion_ind.elo, "win" if result["won"] else "loss",
                total_seasons_a=a_challenger.total_seasons, total_seasons_b=champion_ind.total_seasons,
            )
        results.append({
            "title": "陸王", "season": season, **result,
            "challenger_name": a_challenger.display_name,
            "holder_name": new_rikuou.display_name, "holder_id": new_rikuou.id,
            "defender_id": champion_ind.id if champion_ind is not None else None,
        })

    # ============================================================
    # 海王：A1〜A6・B1・C1によるラダー方式
    # ============================================================
    if rikuou_defended is True:
        kaiou_a1, kaiou_a2 = new_rikuou, ranked_competing_A[0]
        remaining = ranked_competing_A[1:5]
    elif rikuou_defended is False:
        kaiou_a1, kaiou_a2 = new_rikuou, old_rikuou
        remaining = ranked_competing_A[1:5]
    else:
        kaiou_a1, kaiou_a2 = ranked_competing_A[0], ranked_competing_A[1]
        remaining = ranked_competing_A[2:6]

    kaiou_slots = [kaiou_a1, kaiou_a2] + remaining

    kaiou_holder_id_check = (titleholders.get("海王") or {}).get("id")
    if kaiou_holder_id_check:
        kaiou_slots = [None if (s is not None and s.id == kaiou_holder_id_check) else s for s in kaiou_slots]

    if len(kaiou_slots) == 6 and ranked_B and ranked_C:
        kaiou_b1 = None if (kaiou_holder_id_check and ranked_B[0].id == kaiou_holder_id_check) else ranked_B[0]
        kaiou_c1 = None if (kaiou_holder_id_check and ranked_C[0].id == kaiou_holder_id_check) else ranked_C[0]
        challenger, bracket_log = determine_kaiou_challenger(
            kaiou_slots, kaiou_b1, kaiou_c1, depth=depth,
        )
        
        for matchup in bracket_log:
            ind_a, ind_b = all_members_by_id.get(matchup["a"]), all_members_by_id.get(matchup["b"])
            if ind_a and ind_b:
                for g in matchup.get("games", []):
                    if g["result"] == "draw":
                        outcome_a = "draw"
                    else:
                        x_won = (g["result"] == "black") == g["x_was_black"]
                        outcome_a = "win" if x_won else "loss"
                    ind_a.elo, ind_b.elo = update_elo(
                        ind_a.elo, ind_b.elo, outcome_a,
                        total_seasons_a=ind_a.total_seasons, total_seasons_b=ind_b.total_seasons,
                    )

        if titleholders["海王"] is None:
            titleholders["海王"] = {"id": challenger.id, "name": challenger.display_name}
            titleholder_params["海王"] = effective_params(challenger)
            print(f"  ★ 海王 初代襲名: {challenger.display_name}")
            results.append({
                "title": "海王", "season": season, "event": "初代襲名",
                "new_holder": challenger.display_name, "bracket": bracket_log,
            })
        else:
            defending_holder = titleholders["海王"]
            defending_ind_for_volatility = all_members_by_id.get(defending_holder["id"])
            result = run_kaiou_challenge(
                challenger, titleholder_params["海王"], depth=depth,
                titleholder_volatility=defending_ind_for_volatility.volatility if defending_ind_for_volatility else 1.0,
            )
            print(f"  海王戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
                  f" → {'奪取！' if result['won'] else '防衛'}")
            if result["won"]:
                titleholders["海王"] = {"id": challenger.id, "name": challenger.display_name}
                titleholder_params["海王"] = effective_params(challenger)
                holder_name, holder_id = challenger.display_name, challenger.id
            else:
                holder_name, holder_id = defending_holder["name"], defending_holder["id"]
            defending_ind = all_members_by_id.get(defending_holder["id"])
            if defending_ind is not None:
                challenger.elo, defending_ind.elo = update_elo(
                    challenger.elo, defending_ind.elo, "win" if result["won"] else "loss",
                    total_seasons_a=challenger.total_seasons, total_seasons_b=defending_ind.total_seasons,
                )
            results.append({
                "title": "海王", "season": season, **result,
                "challenger_name": challenger.display_name,
                "holder_name": holder_name, "holder_id": holder_id,
                "defender_id": defending_holder["id"],
                "bracket": bracket_log,
            })
    else:
        print("  海王戦: 参加者不足のため今季は見送り")

    # ============================================================
    # 空王：Elo上位8名による正式シードトーナメント
    # ============================================================
    kuuou_holder_id = (titleholders.get("空王") or {}).get("id")
    challenger, bracket_log = determine_kuuou_challenger(all_members, exclude_id=kuuou_holder_id, depth=depth)

    for matchup in bracket_log:
        ind_a, ind_b = all_members_by_id.get(matchup["a"]), all_members_by_id.get(matchup["b"])
        if ind_a and ind_b:
            for g in matchup.get("games", []):
                if g["result"] == "draw":
                    outcome_a = "draw"
                else:
                    x_won = (g["result"] == "black") == g["x_was_black"]
                    outcome_a = "win" if x_won else "loss"
                ind_a.elo, ind_b.elo = update_elo(
                    ind_a.elo, ind_b.elo, outcome_a,
                    total_seasons_a=ind_a.total_seasons, total_seasons_b=ind_b.total_seasons,
                )

    if titleholders["空王"] is None:
        titleholders["空王"] = {"id": challenger.id, "name": challenger.display_name}
        titleholder_params["空王"] = effective_params(challenger)
        print(f"  ★ 空王 初代襲名: {challenger.display_name}")
        results.append({
            "title": "空王", "season": season, "event": "初代襲名",
            "new_holder": challenger.display_name, "bracket": bracket_log,
        })
    else:
        defending_holder = titleholders["空王"]
        defending_ind_for_volatility = all_members_by_id.get(defending_holder["id"])
        result = run_kuuou_challenge(
            challenger, titleholder_params["空王"], depth=depth,
            titleholder_volatility=defending_ind_for_volatility.volatility if defending_ind_for_volatility else 1.0,
        )
        print(f"  空王戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        defending_ind = all_members_by_id.get(defending_holder["id"])
        if defending_ind is not None:
            challenger.elo, defending_ind.elo = update_elo(
                challenger.elo, defending_ind.elo, "win" if result["won"] else "loss",
                total_seasons_a=challenger.total_seasons, total_seasons_b=defending_ind.total_seasons,
            )
        if result["won"]:
            titleholders["空王"] = {"id": challenger.id, "name": challenger.display_name}
            titleholder_params["空王"] = effective_params(challenger)
            holder_name, holder_id = challenger.display_name, challenger.id
        else:
            holder_name, holder_id = defending_holder["name"], defending_holder["id"]
        results.append({
            "title": "空王", "season": season, **result,
            "challenger_name": challenger.display_name,
            "holder_name": holder_name, "holder_id": holder_id,
            "defender_id": defending_holder["id"],
            "bracket": bracket_log,
        })

    return results


def main():
    import sys, os as _os
    print(f"[DEBUG] run_season.py 開始。Python: {sys.version}", flush=True)
    print(f"[DEBUG] 現在のディレクトリ: {_os.getcwd()}", flush=True)
    print(f"[DEBUG] このファイルの場所: {_os.path.abspath(__file__)}", flush=True)
    print(f"[DEBUG] コマンドライン引数: {sys.argv}", flush=True)

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--seasons", type=int, default=1)
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--swiss-rounds", type=int, default=4)
    args = parser.parse_args()
    print(f"[DEBUG] パース済み引数: data_dir={args.data_dir}, seasons={args.seasons}, depth={args.depth}, swiss_rounds={args.swiss_rounds}", flush=True)

    state = load_season_state(args.data_dir)
    registry = NameRegistry.from_dict(state.get("name_registry", {}))

    rosters = load_rosters(args.data_dir)
    print(f"[DEBUG] load_rosters結果: {'None（新規作成へ）' if rosters is None else 'ロード成功'}", flush=True)
    if rosters is None:
        print("初回起動：ロスターを新規作成します", flush=True)
        rosters = bootstrap_rosters(registry)
        state["name_registry"] = registry.to_dict()

    print(f"[DEBUG] これから{args.seasons}シーズン分のループに入ります", flush=True)
    for _ in range(args.seasons):
        season = state["current_season"] + 1

        prev_standings_by_id = {}
        if season > 1:
            prev_path = os.path.join(args.data_dir, "standings", f"season_{season - 1}.json")
            if os.path.exists(prev_path):
                with open(prev_path, "r", encoding="utf-8") as f:
                    prev_rows = json.load(f)
                prev_standings_by_id = {row["individual_id"]: row for row in prev_rows}

        rosters, match_log, title_results, retired, standings_snapshot = run_one_season(
            rosters, season, args.depth, args.swiss_rounds, state, prev_standings_by_id,
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
    print("[DEBUG] __main__ ガード節に到達、main()を呼び出します", flush=True)
    main()
else:
    print(f"[DEBUG] __name__は'{__name__}'のため、main()は呼び出されません", flush=True)
