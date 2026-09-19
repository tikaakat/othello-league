import argparse
import json
import os
import random

from othello_league.individual import LeagueIndividual
from othello_league.league import (
    relegate_and_retire, recruit_d_league, LEAGUE_CAPACITY, RETIREMENT_AGE, D_NEWCOMER_GUARANTEE_FLOOR,
)
from othello_league.buffs import effective_params, roll_age_multipliers
from othello_league.round_robin import run_round_robin
from othello_league.swiss import run_swiss_league
from othello_league.title import (
    run_seiryuu_challenge,
    run_suzaku_group_stage, run_suzaku_challenger_decision,
    determine_suzaku_qualifiers, assign_suzaku_groups, run_suzaku_challenge,
    determine_byakko_challenger, run_byakko_challenge,
    determine_genbu_challenger, run_genbu_challenge,
    SEIRYUU_LEAGUE_DEPTH, SEIRYUU_TITLE_DEPTH,
    SUZAKU_LEAGUE_DEPTH, SUZAKU_TITLE_DEPTH,
    BYAKKO_LEAGUE_DEPTH, BYAKKO_TITLE_DEPTH,
    GENBU_LEAGUE_DEPTH, GENBU_TITLE_DEPTH,
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


def _random_initial_age():
    """初年度メンバーの年齢：師弟の年齢制限（MASTER_MIN_AGE）とは無関係に、
    18歳〜引退年齢直前までの実在しそうな年齢層を広く取り、最初から様々な世代がいる状態にする"""
    return random.randint(18, RETIREMENT_AGE - 1)


def bootstrap_rosters(registry):
    """初回起動時：A/B/Cはランダム個体、Dは8大流派の開祖2名ずつで初期化する。
    能力（パラメータ合計上限）はリーグが上がるほど高くなるよう傾斜配置するが、
    年齢はリーグに関係なく幅広くランダムにする"""
    rosters = {}
    for league in ("A", "B", "C"):
        cap = LEAGUE_CAPACITY[league]
        rosters[league] = [
            LeagueIndividual(
                f"{league}0-{i:03d}", league,
                params=_random_params_with_cap(INITIAL_SUM_CAP[league]),
                display_name=registry.generate(),
                initial_age=_random_initial_age(),
            )
            for i in range(cap)
        ]
        for ind in rosters[league]:
            ind.volatility = _random_volatility()

    # Dリーグは定員（LEAGUE_CAPACITY["D"]）より少なめの人数でスタートし、新人リーグ経由で
    # 徐々に定員まで育てる（D_NEWCOMER_GUARANTEE_FLOORは新人受け入れの最低保証ロジックと
    # 同じ基準人数を流用している）
    d_members = []
    for i in range(D_NEWCOMER_GUARANTEE_FLOOR):
        ind = LeagueIndividual(
            f"D0-{i:03d}", "D",
            params=_random_params_with_cap(INITIAL_SUM_CAP["D"]),
            display_name=registry.generate(),
            initial_age=_random_initial_age(),
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

# --- 旧タイトル名（陸王・空王）から新タイトル名（青龍・白虎）への1回限りの移行 ---
    old_th = state.get("titleholders")
    if old_th is not None and "陸王" in old_th:
        old_th["青龍"] = old_th.pop("陸王")
        old_th["白虎"] = old_th.pop("空王")
        old_th.setdefault("玄武", None)
    old_tp = state.get("titleholder_params")
    if old_tp is not None and "陸王" in old_tp:
        old_tp["青龍"] = old_tp.pop("陸王")
        old_tp["白虎"] = old_tp.pop("空王")
        old_tp.setdefault("玄武", None)
    
    # --- 青龍在位者は、Aリーグの総当たり（順位戦）を免除される（防衛専念枠） ---
    titleholders = state.setdefault("titleholders", {"青龍": None, "白虎": None, "玄武": None, "海王": None})
    seiryuu_holder_id = (titleholders.get("青龍") or {}).get("id")

    a_roster = rosters["A"]
    champion_ind = next((ind for ind in a_roster if ind.id == seiryuu_holder_id), None) if seiryuu_holder_id else None
    competing_A = [ind for ind in a_roster if champion_ind is None or ind.id != champion_ind.id]

    print("  --- Aリーグ（総当たり） ---" + ("　※青龍在位者は防衛専念枠のため対局免除" if champion_ind else ""))
    ranked_competing_A, log_A, _, record_A = run_round_robin(competing_A, depth=depth)
    match_log += log_A

    # 公式Aリーグ順位：青龍在位者がいれば1位に据え、以降は総当たり結果を続ける
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
                "elo": round(ind.elo, 1),
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
    # 朱雀紅白リーグの「今季開始時点」の在籍者を、_run_title_matchesが来季分に
    # 上書きする前に控えておく（下の年齢引退免除の判定に使うため。_run_title_matches内で
    # 今季中に陥落が決まった個体は、来季分のstate["suzaku_league"]には既に含まれなくなって
    # いるが、今季については紅白リーグの一員として戦っていたので、今季の年齢引退免除は
    # 引き続き適用すべき。免除対象を「今季開始時点の在籍者」と「来季も残る在籍者」の
    # 和集合にすることで、陥落と年齢引退が同じ季に重なった個体も今季だけは保護される）
    suzaku_league_ids_before = set(state.get("suzaku_league", {}).get("red", [])) | \
        set(state.get("suzaku_league", {}).get("white", []))

    all_members = ranked_A + ranked_B + ranked_C + ranked_D
    title_results, title_extra_match_log, suzaku_group_snapshot = _run_title_matches(
        ranked_A, ranked_competing_A, champion_ind, ranked_B, ranked_C, ranked_D, all_members, state, season,
    )
    title_match_log = _title_results_to_match_log(title_results, season)
    match_log += title_match_log
    match_log += title_extra_match_log

    # --- 昇降格・引退（Dリーグの新規補充は次シーズン開始前にrecruit_d_league()で行う）---
    rosters["A"], rosters["B"], rosters["C"], rosters["D"] = ranked_A, ranked_B, ranked_C, ranked_D
    suzaku_league_ids_after = set(state.get("suzaku_league", {}).get("red", [])) | \
        set(state.get("suzaku_league", {}).get("white", []))
    suzaku_league_ids = suzaku_league_ids_before | suzaku_league_ids_after
    rosters, retired, vacancy = relegate_and_retire(
        rosters, season, titleholders=titleholders, suzaku_league_ids=suzaku_league_ids,
    )

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

    # 朱雀紅白リーグの順位・残留/陥落は、上のA〜D用ロジック（movementの上書き）の対象外として、
    # _run_title_matchesで既に確定した正しい値のまま追加する
    standings_snapshot += suzaku_group_snapshot

    print(f"  引退: {len(retired)}名（{', '.join(i.display_name for i in retired)}）" if retired else "  引退: なし")
    print(f"  Dリーグ欠員（来季開始前に補充）: {vacancy}名")

    # 歴代最高Eloを更新する
    for league_list in rosters.values():
        for ind in league_list:
            if ind.elo > ind.peak_elo:
                ind.peak_elo = ind.elo
    for ind in retired:
        if ind.elo > ind.peak_elo:
            ind.peak_elo = ind.elo

    return rosters, match_log, title_results, retired, standings_snapshot, vacancy


def _result_from_winner_tag(tag):
    if tag == "draw":
        return "draw"
    return "win" if tag == "a" else "loss"


def _best_of_n_to_match_log(games, league_name, a_id, b_id):
    """run_best_of_n_matchのgames_log（先取制の本戦・決定戦）をmatch_logの形式に変換する"""
    entries = []
    for g in games:
        entries.append({
            "league": league_name,
            "individual_a_id": a_id,
            "individual_b_id": b_id,
            "result": _result_from_winner_tag(g.get("winner")),
            "games": [g],
        })
    return entries


def _bracket_log_to_match_log(bracket_log, league_name):
    """determine_*_challenger等のbracket_log（トーナメント）をmatch_logの形式に変換する"""
    entries = []
    for matchup in bracket_log:
        x_id, y_id = matchup["a"], matchup["b"]
        for g in matchup.get("games", []):
            if g["result"] == "draw":
                result_for_log = "draw"
            else:
                x_won = (g["result"] == "black") == g["x_was_black"]
                result_for_log = "win" if x_won else "loss"
            entries.append({
                "league": league_name,
                "individual_a_id": x_id,
                "individual_b_id": y_id,
                "result": result_for_log,
                "games": [g],
            })
    return entries


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
                entry = {
                    "league": f"{title}予選",
                    "individual_a_id": x_id,
                    "individual_b_id": y_id,
                    "result": result_for_log,
                    "games": [g],
                }
                # 玄武戦のみ、ブロック分け表示のためstage（"block"/"final"）とblockを付与する
                if "stage" in matchup:
                    entry["stage"] = matchup["stage"]
                if "block" in matchup:
                    entry["block"] = matchup["block"]
                entries.append(entry)

    return entries


def _run_title_matches(ranked_A, ranked_competing_A, champion_ind, ranked_B, ranked_C, ranked_D, all_members, state, season):
    results = []
    extra_match_log = []  # タイトル戦のうち、title_history（保持者の記録）には載せない付随対局（朱雀の紅白リーグ戦等）
    suzaku_group_snapshot = []  # 朱雀紅白リーグの順位・残留/陥落を、サイト側が独自に再計算せず正確に表示できるよう記録する

# --- 旧タイトル名（陸王・空王）から新タイトル名（青龍・白虎）への1回限りの移行 ---
    old_th = state.get("titleholders")
    if old_th is not None and "陸王" in old_th:
        old_th["青龍"] = old_th.pop("陸王")
        old_th["白虎"] = old_th.pop("空王")
        old_th.setdefault("玄武", None)
    old_th = state.get("titleholders")
    
    if old_th is not None and "海王" in old_th:
        old_th["朱雀"] = old_th.pop("海王")
    old_tp = state.get("titleholder_params")
    if old_tp is not None and "海王" in old_tp:
        old_tp["朱雀"] = old_tp.pop("海王")
        
    old_tp = state.get("titleholder_params")
    if old_tp is not None and "陸王" in old_tp:
        old_tp["青龍"] = old_tp.pop("陸王")
        old_tp["白虎"] = old_tp.pop("空王")
        old_tp.setdefault("玄武", None)
        
    titleholders = state.setdefault("titleholders", {"青龍": None, "白虎": None, "玄武": None, "朱雀": None})
    titleholder_params = state.setdefault("titleholder_params", {"青龍": None, "白虎": None, "玄武": None, "朱雀": None})
    all_members_by_id = {ind.id: ind for ind in all_members}

    # ============================================================
    # 青龍：Aリーグ総当たり1位が挑戦（青龍在位者は対局免除で待ち受ける）
    # ============================================================
    a_challenger = ranked_competing_A[0]
    seiryuu_defended = None

    seiryuu_vacant = titleholders["青龍"] is not None and champion_ind is None
    if seiryuu_vacant:
        print(f"  ※ 青龍在位者（{titleholders['青龍'].get('name')}）がロスターに見当たらないため、空位として扱います")

    if titleholders["青龍"] is None or seiryuu_vacant:
        titleholders["青龍"] = {"id": a_challenger.id, "name": a_challenger.display_name}
        titleholder_params["青龍"] = effective_params(a_challenger)
        print(f"  ★ 青龍 初代襲名: {a_challenger.display_name}")
        results.append({
            "title": "青龍", "season": season, "event": "初代襲名",
            "new_holder": a_challenger.display_name, "holder_id": a_challenger.id,
        })
        new_seiryuu = a_challenger
        old_seiryuu = None
    else:
        result = run_seiryuu_challenge(
            a_challenger, titleholder_params["青龍"], depth=SEIRYUU_TITLE_DEPTH,
            titleholder_volatility=champion_ind.volatility if champion_ind is not None else 1.0,
        )
        print(f"  青龍戦: {a_challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        old_seiryuu = champion_ind
        if result["won"]:
            titleholders["青龍"] = {"id": a_challenger.id, "name": a_challenger.display_name}
            titleholder_params["青龍"] = effective_params(a_challenger)
            new_seiryuu = a_challenger
            seiryuu_defended = False
        else:
            new_seiryuu = champion_ind
            seiryuu_defended = True
        if champion_ind is not None:
            a_challenger.elo, champion_ind.elo = update_elo(
                a_challenger.elo, champion_ind.elo, "win" if result["won"] else "loss",
                total_seasons_a=a_challenger.total_seasons, total_seasons_b=champion_ind.total_seasons,
            )
        results.append({
            "title": "青龍", "season": season, **result,
            "challenger_name": a_challenger.display_name,
            "holder_name": new_seiryuu.display_name, "holder_id": new_seiryuu.id,
            "defender_id": champion_ind.id if champion_ind is not None else None,
        })

    # ============================================================
    # 朱雀：紅白2組（各5名）の永続サブリーグ。両組の総当たり→両組1位による挑戦者決定戦→
    # 朱雀本戦。両組下位2名（計4名）が陥落し、紅白リーグ外の全個体による4ブロック入れ替え戦
    # （タイトル保持者＞Aリーグ順位のシード）で補充する。
    # ============================================================
    suzaku_holder_id = (titleholders.get("朱雀") or {}).get("id")
    suzaku_seed_titleholder_ids = {info["id"] for info in titleholders.values() if info and info.get("id")}
    suzaku_seed_a_order = [ind.id for ind in ranked_A]
    suzaku_state = state.setdefault("suzaku_league", {"red": [], "white": []})

    if not suzaku_state.get("red") and not suzaku_state.get("white"):
        # 初代メンバー10名は、既存個体をElo等で直接シードするのではなく、通常の入れ替え戦
        # （determine_suzaku_qualifiers）と同じ10ブロックのシード付きトーナメントで決める
        bootstrap_pool = [ind for ind in all_members if ind.id != suzaku_holder_id]
        bootstrap_qualifiers, bootstrap_bracket_log = determine_suzaku_qualifiers(
            bootstrap_pool, depth=SUZAKU_LEAGUE_DEPTH, num_blocks=10,
            titleholder_ids=suzaku_seed_titleholder_ids, a_league_order=suzaku_seed_a_order,
        )
        # 通常の入れ替え戦（"朱雀予選"）と同じ季に発生しうるうえ、参加プールが一部重複する
        # （初代決定戦の落選者がそのまま同じ季の入れ替え戦にも出場する）ため、同じリーグ名で
        # 記録すると、サイト側のブロック復元（対局した個体同士を連結して自動判定する方式）が
        # 誤って2つの別トーナメントを1つに混ぜてしまう。別リーグ名で区別して記録する
        extra_match_log += _bracket_log_to_match_log(bootstrap_bracket_log, "朱雀紅白決定戦")
        for matchup in bootstrap_bracket_log:
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
        chosen = list(bootstrap_qualifiers)
        random.shuffle(chosen)
        suzaku_state["red"], suzaku_state["white"] = (
            [ind.id for ind in chosen[:5]], [ind.id for ind in chosen[5:10]],
        )

    def _prep_suzaku_group(ids):
        # 在位者は防衛専念枠のため、紅白リーグに在籍していても今季の総当たりには参加させず、
        # 無条件で「残留」扱いにする（他タイトルの在位者除外パターンと同じ考え方）
        members = [all_members_by_id[i] for i in ids if i in all_members_by_id]
        holder_parked = [m for m in members if m.id == suzaku_holder_id]
        others = [m for m in members if m.id != suzaku_holder_id]
        return others, holder_parked

    red_others, red_holder_parked = _prep_suzaku_group(suzaku_state.get("red", []))
    white_others, white_holder_parked = _prep_suzaku_group(suzaku_state.get("white", []))

    if red_others and white_others:
        red_ranked, white_ranked, red_record, white_record, group_match_log = run_suzaku_group_stage(
            red_others, white_others, depth=SUZAKU_LEAGUE_DEPTH,
        )
        extra_match_log += group_match_log

        red_relegate_n = min(2, len(red_ranked))
        white_relegate_n = min(2, len(white_ranked))
        red_returning = red_ranked[:len(red_ranked) - red_relegate_n] + red_holder_parked
        white_returning = white_ranked[:len(white_ranked) - white_relegate_n] + white_holder_parked
        returning = red_returning + white_returning

        # 紅白リーグの順位・残留/陥落は、この時点で決まった正しい判定をそのまま記録する。
        # サイト側で対局結果から独自に順位を再計算すると、同率タイの並び順がここでの
        # 実際の判定と食い違う恐れがあるため（1位タイの決定戦は考慮できても、
        # 残留/陥落の境界で同率が起きた場合はサイト側では再現しようがない）
        for group_name, ranked, relegate_n, record, holder_parked in (
            ("朱雀紅組", red_ranked, red_relegate_n, red_record, red_holder_parked),
            ("朱雀白組", white_ranked, white_relegate_n, white_record, white_holder_parked),
        ):
            cutoff = len(ranked) - relegate_n
            for rank, ind in enumerate(ranked, 1):
                rec = record.get(ind.id, {"win": 0, "loss": 0, "draw": 0})
                suzaku_group_snapshot.append({
                    "season": season, "league": group_name, "rank": rank,
                    "individual_id": ind.id, "display_name": ind.display_name,
                    "elo": round(ind.elo, 1),
                    "win": rec["win"], "loss": rec["loss"], "draw": rec["draw"],
                    "no_roundrobin": False,
                    "movement": "relegated" if rank > cutoff else "stay",
                })
            for ind in holder_parked:
                suzaku_group_snapshot.append({
                    "season": season, "league": group_name, "rank": 0,
                    "individual_id": ind.id, "display_name": ind.display_name,
                    "elo": round(ind.elo, 1),
                    "win": 0, "loss": 0, "draw": 0,
                    "no_roundrobin": True,
                    "movement": "stay",
                })

        red_champion, white_champion = red_ranked[0], white_ranked[0]
        challenger, decision_info = run_suzaku_challenger_decision(red_champion, white_champion, depth=SUZAKU_LEAGUE_DEPTH)
        print(f"  朱雀・挑戦者決定戦: {red_champion.display_name}（紅組1位） {decision_info['red_wins']}"
              f"-{decision_info['white_wins']} {white_champion.display_name}（白組1位） → 挑戦者は{challenger.display_name}")
        extra_match_log += _best_of_n_to_match_log(
            decision_info["games"], "朱雀挑戦者決定戦", red_champion.id, white_champion.id,
        )
        for g in decision_info["games"]:
            outcome_red = _result_from_winner_tag(g.get("winner"))
            red_champion.elo, white_champion.elo = update_elo(
                red_champion.elo, white_champion.elo, outcome_red,
                total_seasons_a=red_champion.total_seasons, total_seasons_b=white_champion.total_seasons,
            )

        if titleholders["朱雀"] is None:
            titleholders["朱雀"] = {"id": challenger.id, "name": challenger.display_name}
            titleholder_params["朱雀"] = effective_params(challenger)
            print(f"  ★ 朱雀 初代襲名: {challenger.display_name}")
            results.append({
                "title": "朱雀", "season": season, "event": "初代襲名",
                "new_holder": challenger.display_name, "holder_id": challenger.id,
            })
        else:
            defending_holder = titleholders["朱雀"]
            defending_ind_for_volatility = all_members_by_id.get(defending_holder["id"])
            result = run_suzaku_challenge(
                challenger, titleholder_params["朱雀"], depth=SUZAKU_TITLE_DEPTH,
                titleholder_volatility=defending_ind_for_volatility.volatility if defending_ind_for_volatility else 1.0,
            )
            print(f"  朱雀戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
                  f" → {'奪取！' if result['won'] else '防衛'}")
            if result["won"]:
                titleholders["朱雀"] = {"id": challenger.id, "name": challenger.display_name}
                titleholder_params["朱雀"] = effective_params(challenger)
                holder_name, holder_id = challenger.display_name, challenger.id
                # 奪取された旧保持者は、防衛専念枠を外れて来季の紅白リーグに無条件で復帰する
                # （青龍・白虎・玄武と同様、失冠した個体がそのまま母集団に戻るのが本来の仕様）
                dethroned_ind = all_members_by_id.get(defending_holder["id"])
                if dethroned_ind is not None:
                    returning.append(dethroned_ind)
            else:
                holder_name, holder_id = defending_holder["name"], defending_holder["id"]
            defending_ind = all_members_by_id.get(defending_holder["id"])
            if defending_ind is not None:
                challenger.elo, defending_ind.elo = update_elo(
                    challenger.elo, defending_ind.elo, "win" if result["won"] else "loss",
                    total_seasons_a=challenger.total_seasons, total_seasons_b=defending_ind.total_seasons,
                )
            results.append({
                "title": "朱雀", "season": season, **result,
                "challenger_name": challenger.display_name,
                "holder_name": holder_name, "holder_id": holder_id,
                "defender_id": defending_holder["id"],
            })

        # 新王者になった挑戦者は、来季は防衛専念枠に回るため残留組からは外す
        new_holder_id = titleholders["朱雀"]["id"]
        returning = [ind for ind in returning if ind.id != new_holder_id]

        num_new_needed = max(0, 10 - len(returning))
        suzaku_exclude_ids = {m.id for m in red_others + white_others + red_holder_parked + white_holder_parked}
        if suzaku_holder_id:
            suzaku_exclude_ids.add(suzaku_holder_id)
        suzaku_exclude_ids.add(new_holder_id)
        qualifiers, qualifier_bracket_log = determine_suzaku_qualifiers(
            all_members, exclude_ids=suzaku_exclude_ids, depth=SUZAKU_LEAGUE_DEPTH, num_blocks=num_new_needed,
            titleholder_ids=suzaku_seed_titleholder_ids, a_league_order=suzaku_seed_a_order,
        )
        extra_match_log += _bracket_log_to_match_log(qualifier_bracket_log, "朱雀予選")

        for matchup in qualifier_bracket_log:
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

        red_ids_next, white_ids_next = assign_suzaku_groups(returning, qualifiers)
        suzaku_state["red"], suzaku_state["white"] = red_ids_next, white_ids_next
    else:
        print("  朱雀戦: 紅白リーグの参加者不足のため今季は見送り")

    # ============================================================
    # 白虎：Elo上位16名（前年白虎在位者は防衛専念枠として除外）による正式シードトーナメント
    # ============================================================
    byakko_holder_id = (titleholders.get("白虎") or {}).get("id")
    challenger, bracket_log = determine_byakko_challenger(all_members, exclude_id=byakko_holder_id, depth=BYAKKO_LEAGUE_DEPTH, top_n=16)

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

    if titleholders["白虎"] is None:
        titleholders["白虎"] = {"id": challenger.id, "name": challenger.display_name}
        titleholder_params["白虎"] = effective_params(challenger)
        print(f"  ★ 白虎 初代襲名: {challenger.display_name}")
        results.append({
            "title": "白虎", "season": season, "event": "初代襲名",
            "new_holder": challenger.display_name, "holder_id": challenger.id, "bracket": bracket_log,
        })
    else:
        defending_holder = titleholders["白虎"]
        defending_ind_for_volatility = all_members_by_id.get(defending_holder["id"])
        result = run_byakko_challenge(
            challenger, titleholder_params["白虎"], depth=BYAKKO_TITLE_DEPTH,
            titleholder_volatility=defending_ind_for_volatility.volatility if defending_ind_for_volatility else 1.0,
        )
        print(f"  白虎戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        defending_ind = all_members_by_id.get(defending_holder["id"])
        if defending_ind is not None:
            challenger.elo, defending_ind.elo = update_elo(
                challenger.elo, defending_ind.elo, "win" if result["won"] else "loss",
                total_seasons_a=challenger.total_seasons, total_seasons_b=defending_ind.total_seasons,
            )
        if result["won"]:
            titleholders["白虎"] = {"id": challenger.id, "name": challenger.display_name}
            titleholder_params["白虎"] = effective_params(challenger)
            holder_name, holder_id = challenger.display_name, challenger.id
        else:
            holder_name, holder_id = defending_holder["name"], defending_holder["id"]
        results.append({
            "title": "白虎", "season": season, **result,
            "challenger_name": challenger.display_name,
            "holder_name": holder_name, "holder_id": holder_id,
            "defender_id": defending_holder["id"],
            "bracket": bracket_log,
        })

    # ============================================================
    # 玄武：完全ランダム抽選トーナメント（ブラケットサイズ64、Elo上位者はバイ）
    # ============================================================
    genbu_holder_id = (titleholders.get("玄武") or {}).get("id")
    genbu_seed_titleholder_ids = {info["id"] for info in titleholders.values() if info and info.get("id")}
    genbu_seed_a_order = [ind.id for ind in ranked_A]
    challenger, bracket_log = determine_genbu_challenger(
        all_members, exclude_id=genbu_holder_id, depth=GENBU_LEAGUE_DEPTH, bracket_size=64,
        titleholder_ids=genbu_seed_titleholder_ids, a_league_order=genbu_seed_a_order,
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

    if titleholders["玄武"] is None:
        titleholders["玄武"] = {"id": challenger.id, "name": challenger.display_name}
        titleholder_params["玄武"] = effective_params(challenger)
        print(f"  ★ 玄武 初代襲名: {challenger.display_name}")
        results.append({
            "title": "玄武", "season": season, "event": "初代襲名",
            "new_holder": challenger.display_name, "holder_id": challenger.id, "bracket": bracket_log,
        })
    else:
        defending_holder = titleholders["玄武"]
        defending_ind_for_volatility = all_members_by_id.get(defending_holder["id"])
        result = run_genbu_challenge(
            challenger, titleholder_params["玄武"], depth=GENBU_TITLE_DEPTH,
            titleholder_volatility=defending_ind_for_volatility.volatility if defending_ind_for_volatility else 1.0,
        )
        print(f"  玄武戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        defending_ind = all_members_by_id.get(defending_holder["id"])
        if defending_ind is not None:
            challenger.elo, defending_ind.elo = update_elo(
                challenger.elo, defending_ind.elo, "win" if result["won"] else "loss",
                total_seasons_a=challenger.total_seasons, total_seasons_b=defending_ind.total_seasons,
            )
        if result["won"]:
            titleholders["玄武"] = {"id": challenger.id, "name": challenger.display_name}
            titleholder_params["玄武"] = effective_params(challenger)
            holder_name, holder_id = challenger.display_name, challenger.id
        else:
            holder_name, holder_id = defending_holder["name"], defending_holder["id"]
        results.append({
            "title": "玄武", "season": season, **result,
            "challenger_name": challenger.display_name,
            "holder_name": holder_name, "holder_id": holder_id,
            "defender_id": defending_holder["id"],
            "bracket": bracket_log,
        })

    return results, extra_match_log, suzaku_group_snapshot


def main():
    import sys, os as _os
    print(f"[DEBUG] run_season.py 開始。Python: {sys.version}", flush=True)
    print(f"[DEBUG] 現在のディレクトリ: {_os.getcwd()}", flush=True)
    print(f"[DEBUG] このファイルの場所: {_os.path.abspath(__file__)}", flush=True)
    print(f"[DEBUG] コマンドライン引数: {sys.argv}", flush=True)

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--seasons", type=int, default=1)
    # A〜Dリーグの探索深さ。青龍戦の挑戦権はAリーグ総当たりの結果で決まり、
    # B〜Dリーグも昇格を通じて最終的にAリーグ・青龍戦に繋がるため、
    # 青龍関連の深さ（SEIRYUU_LEAGUE_DEPTH）をデフォルトにする
    parser.add_argument("--depth", type=int, default=SEIRYUU_LEAGUE_DEPTH)
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

    # キャラクリエイト機能：このプロセスが処理する最初のシーズンにのみ適用する
    # （複数シーズンをまとめて回す場合、2季目以降で重複適用しないよう、読み込んだ時点で
    # ファイルは削除する）。新人リーグ（AM実行）の勝者ファイルがあればそちらを優先し、
    # 無ければ旧来の直接投稿ファイルにフォールバックする（新人リーグの導入前後で
    # 投稿フローが途切れないようにするため）
    newcomer_winners_path = os.path.join(args.data_dir, "newcomer_winners.json")
    pending_characters_path = os.path.join(args.data_dir, "pending_characters.json")
    pending_characters = None
    if os.path.exists(newcomer_winners_path):
        with open(newcomer_winners_path, "r", encoding="utf-8") as f:
            pending_characters = json.load(f) or None
        os.remove(newcomer_winners_path)
        if pending_characters:
            print(f"[DEBUG] 新人リーグ勝者{len(pending_characters)}件を今季のDリーグに適用します", flush=True)
    elif os.path.exists(pending_characters_path):
        with open(pending_characters_path, "r", encoding="utf-8") as f:
            pending_characters = json.load(f) or None
        os.remove(pending_characters_path)
        if pending_characters:
            print(f"[DEBUG] キャラクリエイトのリクエスト{len(pending_characters)}件を今季のDリーグに適用します", flush=True)

    print(f"[DEBUG] これから{args.seasons}シーズン分のループに入ります", flush=True)
    for i in range(args.seasons):
        season = state["current_season"] + 1

        # 前季末に確定した欠員（state["pending_newcomer_slots"]）を、今季の対局が
        # 始まる前に補充する。新人リーグはこの欠員数をそのまま募集人数として使っているため、
        # ここで対局前に補充することで「新人リーグが対象とした季」＝「実際に出走する季」に
        # 揃う（以前は対局後に補充していたため、実際の出走は1季後にずれていた）
        registry = NameRegistry.from_dict(state.get("name_registry", {}))
        suzaku_league_ids = set(state.get("suzaku_league", {}).get("red", [])) | \
            set(state.get("suzaku_league", {}).get("white", []))
        rosters, new_disciples, registry, recruit_retired = recruit_d_league(
            rosters, season, registry,
            pending_characters=(pending_characters if i == 0 else None),
            titleholders=state.get("titleholders"),
            suzaku_league_ids=suzaku_league_ids,
            # 第1季（ブートストラップ直後）のみ、自動生成の新弟子での定員までの穴埋めを止める。
            # 通常はここで即座に定員まで埋めてしまい、少人数スタートの意図が無効になった上、
            # その季の対局後に「新人受け入れ最低保証」により埋めたばかりの個体が
            # 強制的に引退させられてしまっていたため
            auto_fill_vacancy=(season != 1),
        )
        state["name_registry"] = registry.to_dict()
        if new_disciples:
            print(f"[DEBUG] 第{season}季開始前にDリーグへ{len(new_disciples)}名補充しました", flush=True)
        if recruit_retired:
            state.setdefault("retired_archive", [])
            state["retired_archive"] += [ind.to_dict() for ind in recruit_retired]
            print(f"[DEBUG] Dリーグ補充時の定員超過調整で{len(recruit_retired)}名引退しました", flush=True)

        prev_standings_by_id = {}
        if season > 1:
            prev_path = os.path.join(args.data_dir, "standings", f"season_{season - 1}.json")
            if os.path.exists(prev_path):
                with open(prev_path, "r", encoding="utf-8") as f:
                    prev_rows = json.load(f)
                prev_standings_by_id = {row["individual_id"]: row for row in prev_rows}

        rosters, match_log, title_results, retired, standings_snapshot, vacancy = run_one_season(
            rosters, season, args.depth, args.swiss_rounds, state, prev_standings_by_id,
        )
        state["current_season"] = season
        state.setdefault("retired_archive", [])
        state["retired_archive"] += [ind.to_dict() for ind in retired]
        state.setdefault("title_history", [])
        state["title_history"] += title_results
        # 新人リーグ（次回AM/PM実行）で何名を昇格させるかの目安として、今季確定した
        # Dリーグの欠員数を記録しておく（次シーズン開始前にrecruit_d_league()で補充される）
        state["pending_newcomer_slots"] = vacancy

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
