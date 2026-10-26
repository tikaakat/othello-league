import argparse
import json
import os
import math
import random

from othello_league.individual import LeagueIndividual
from othello_league.league import (
    relegate_and_retire, recruit_d_league, apply_clan_independence, LEAGUE_CAPACITY, RETIREMENT_AGE, D_INITIAL_ROSTER_SIZE,
)
from othello_league.buffs import effective_params, roll_age_multipliers
from othello_league.skills import ensure_skills, roll_form, season_growth, veteran_skills, BOOK_PARAMS
from othello_league.round_robin import run_round_robin, round_robin_gen
from othello_league.random_league import run_random_league, random_league_gen, plan_random_league
from othello_league.title import (
    run_seiryuu_challenge,
    run_suzaku_group_stage, run_suzaku_challenger_decision,
    determine_suzaku_qualifiers, assign_suzaku_groups, run_suzaku_challenge,
    byakko_challenger_gen, run_byakko_challenge,
    genbu_challenger_gen, run_genbu_challenge,
    plan_byakko_bracket, plan_genbu_bracket, NEWCOMER_SLOT_PREFIX,
    plan_kirin_bracket, kirin_challenger_gen, run_kirin_challenge, KIRIN_DEPTH, QUALIFIER_SLOT_PREFIX,
    drive,
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

def _random_volatility():
    """ムラ気（隠しパラメータ）をランダムに生成する。0.3〜2.0の範囲、平均1.0程度"""
    return random.uniform(0.3, 2.0)


def _random_initial_age():
    """初年度メンバーの年齢：18歳〜引退年齢直前までの実在しそうな年齢層を広く取り、最初から様々な世代がいる状態にする"""
    return random.randint(18, RETIREMENT_AGE - 1)


def _seed_styles(data_dir):
    """初期個体の棋風の元（data/seed_styles.json）。無ければ従来どおり完全ランダム"""
    path = os.path.join(data_dir, "seed_styles.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("styles") or []
    return []


def _estimated_strength(params):
    """リーグの振り分け用の、強さの大まかな見積もり（レート換算）。
    終盤力・定跡の効き目（skills.pyのシミュレーション値）と、棋風が強い配分にどれだけ近いかを足し合わせる"""
    eg = params.get("_endgame", 0.0)
    eg_elo = 67 * eg / 24 if eg <= 24 else 67 + (min(eg, 44) - 24) * (168 - 67) / 20
    book = params.get("_book", 0)
    book_elo = 113 * min(book, 16) / 16 + (10 * (min(book, 24) - 16) / 8 if book > 16 else 0)
    a = [params[k] for k in PARAM_KEYS]; b = [BOOK_PARAMS[k] for k in PARAM_KEYS]
    cos = sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return eg_elo + book_elo + (cos - 0.94) * 1500


def bootstrap_rosters(registry, data_dir="data"):
    """初回起動（リーグを初期化して始め直すとき）の初期個体を作る。

    以前はA/B/Cをリーグごとの能力合計上限で傾斜をつけた完全ランダムの個体で作っていたが、初期個体の棋風が
    その後に生まれる個体よりかなり弱く（強い配分への近さ 0.78 対 0.93〜0.95）、初期の新人が無双する原因になっていた。
    そこで、今の時代の選手の棋風（seed_styles.json）を元に少し崩した棋風で作り、年齢に応じて
    これまでのキャリアで身につけた終盤力・定跡（skills.py）を持たせる。リーグは強さの見積もり順に振り分ける。
    初期の新人は定跡をまだ知らないので、ベテランと戦えるようになるまで数季の下積みが要る"""
    styles = _seed_styles(data_dir)
    sizes = [("A", LEAGUE_CAPACITY["A"]), ("B", LEAGUE_CAPACITY["B"]), ("C", LEAGUE_CAPACITY["C"]), ("D", D_INITIAL_ROSTER_SIZE)]
    members = []
    for n in range(sum(size for _, size in sizes)):
        if styles:
            base = random.choice(styles)
            params = {k: round(max(0.1, base[k] * random.uniform(0.8, 1.25)), 3) for k in PARAM_KEYS}
        else:
            params = {k: round(random.uniform(0.5, 10.0), 3) for k in PARAM_KEYS}
        age = _random_initial_age()
        params.update(veteran_skills(age))
        display_name, gender = registry.generate()
        members.append((params, age, display_name, gender))
    members.sort(key=lambda m: -_estimated_strength(m[0]))
    rosters, pos = {}, 0
    for league, size in sizes:
        rosters[league] = []
        for i in range(size):
            params, age, display_name, gender = members[pos]; pos += 1
            ind = LeagueIndividual(
                f"{league}0-{i:03d}", league, params=params,
                display_name=display_name, gender=gender,
                # initial_ageは「参入時の年齢」。初期個体は第1季の時点でこの年齢
                initial_age=age,
            )
            ind.volatility = _random_volatility()
            rosters[league].append(ind)
    return rosters


LEAGUE_TIERS = {"A": 0, "B": 1, "C": 2, "D": 3}
SUZAKU_BOOTSTRAP_DEPTH = 1


def _build_seed_order(members, league_name, prev_standings_by_id):
    """
    同点タイブレーク順（前季順位）を決める。前季の全体順位どおり、
    上のリーグから降格してきた個体 → 前季もこのリーグに残留していた個体 → 下のリーグから
    昇格してきた個体 の順に並べ、それぞれの中は前季のリーグ内順位順とする。
    前季の順位が無い個体（新人・第1季の全員）は最後にID順で並べる。
    """
    ranked = []
    others = []
    for ind in members:
        prev = prev_standings_by_id.get(ind.id)
        if prev is not None and prev.get("league") in LEAGUE_TIERS:
            ranked.append((LEAGUE_TIERS[prev["league"]], prev["rank"], ind.id))
        else:
            others.append(ind.id)
    return [iid for _, _, iid in sorted(ranked)] + sorted(others)


def run_one_season(rosters, season, depth, swiss_rounds, state, prev_standings_by_id=None, suzaku_tiebreak=None):
    prev_standings_by_id = prev_standings_by_id or {}
    match_log = []

    print(f"=== Season {season} ===")

    # --- 年齢バフを毎シーズン再抽選する（対局が始まる前に） ---
    for league_list in rosters.values():
        for ind in league_list:
            ind.age_multipliers = roll_age_multipliers(ind.age, PARAM_KEYS)
            # 終盤力・定跡（skills.py）を持たない個体（導入前からの個体）には年齢相応の値を与え、
            # この季の調子（終盤力の上下）を抽選する
            ensure_skills(ind)
            roll_form(ind)

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

    # A〜Dリーグの対局と、白虎・玄武・麒麟の挑戦者決定戦（組み合わせは季の初めに決まっている）を
    # まとめて同時に進める（parallel.drive：各イベントの「次に指せる対局」を1度に並列実行するので、
    # どれかのイベントの終盤で対局が減ってもCPUが遊ばない）。B〜Dリーグの組み合わせも前の季の終わりに
    # 決めて公開したもの（新人の仮の枠は入団した新人に置き換える）を使う
    planned = state.get("planned_brackets") or {}
    if planned.get("season") != season:
        planned = {}
    league_pairs = planned.get("leagues") or {}
    newcomer_ids = planned.get("newcomer_ids") or {}
    print("  --- A〜Dリーグ・白虎/玄武/麒麟の挑戦者決定戦（同時進行） ---" + ("　※青龍在位者はAリーグの対局免除" if champion_ind else ""))
    # 同点（1位タイ含む）は前季順位の高い方を上位とする（B〜Dと同じ扱い。順位決定戦は行わない）
    league_gens = [round_robin_gen(
        competing_A, depth=depth, seed_order=_build_seed_order(rosters["A"], "A", prev_standings_by_id),
    )]
    for lg in ("B", "C", "D"):
        league_gens.append(random_league_gen(
            rosters[lg], games_per_individual=swiss_rounds, depth=depth, league_name=lg,
            seed_order=_build_seed_order(rosters[lg], lg, prev_standings_by_id),
            allow_rematch=False, planned_pairs=league_pairs.get(lg), slot_ids=newcomer_ids,
        ))
    tournament_members = [ind for lg in ("A", "B", "C", "D") for ind in rosters[lg]]
    outs = drive(*league_gens, *title_tournament_gens(tournament_members, state, season))
    (ranked_competing_A, log_A, _, record_A), (ranked_B, log_B, _, record_B), \
        (ranked_C, log_C, _, record_C), (ranked_D, log_D, _, record_D) = outs[:4]
    title_tournament_outs = outs[4:]
    match_log += log_A + log_B + log_C + log_D

    # 公式Aリーグ順位：青龍在位者がいれば1位に据え、以降は総当たり結果を続ける
    ranked_A = ([champion_ind] + ranked_competing_A) if champion_ind else ranked_competing_A
    if champion_ind:
        record_A[champion_ind.id] = {"win": 0, "loss": 0, "draw": 0}  # 対局免除のため記録なし

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
    title_results, title_extra_match_log, suzaku_group_snapshot, title_extra_protected_ids, title_demotion_relief_ids = _run_title_matches(
        ranked_A, ranked_competing_A, champion_ind, ranked_B, ranked_C, ranked_D, all_members, state, season,
        suzaku_tiebreak=suzaku_tiebreak, title_tournament_outs=title_tournament_outs,
    )
    title_match_log = _title_results_to_match_log(title_results, season)
    match_log += title_match_log
    match_log += title_extra_match_log

    # --- 昇降格・引退（Dリーグの新規補充は次シーズン開始前にrecruit_d_league()で行う）---
    rosters["A"], rosters["B"], rosters["C"], rosters["D"] = ranked_A, ranked_B, ranked_C, ranked_D
    suzaku_league_ids_after = set(state.get("suzaku_league", {}).get("red", [])) | \
        set(state.get("suzaku_league", {}).get("white", []))
    suzaku_league_ids = suzaku_league_ids_before | suzaku_league_ids_after
    rosters, retired, vacancy, demotion_events = relegate_and_retire(
        rosters, season, titleholders=titleholders, suzaku_league_ids=suzaku_league_ids,
        extra_protected_ids=title_extra_protected_ids, demotion_relief_ids=title_demotion_relief_ids,
    )
    # 季の終わりに、終盤力の成長・衰えと定跡の習得を反映する（年齢は relegate_and_retire で1つ進んでいる）
    for league_list in rosters.values():
        for ind in league_list:
            season_growth(ind)

    # --- 昇降格・新規・引退マークを確定する ---
    retired_ids = {ind.id for ind in retired}
    post_move_league_by_id = {ind.id: ind.league for league_list in rosters.values() for ind in league_list}
    # 降級点の「現在の保有点数」（この季終了時点）。結果タブで、何も動きが無かった季でも
    # 既に持っている点数を常時表示できるようにするため、引退者も含めて引き継ぐ
    demotion_points_by_id = {ind.id: ind.demotion_points for league_list in rosters.values() for ind in league_list}
    demotion_points_by_id.update({ind.id: ind.demotion_points for ind in retired})

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
        # Dリーグの降級点：この季に何か動きがあった場合のみセットする（値はleague.py参照）
        row["demotion_point_event"] = demotion_events.get(iid, "")
        row["demotion_points"] = demotion_points_by_id.get(iid, 0)

    # 朱雀紅白リーグの順位・残留/陥落は、上のA〜D用ロジック（movementの上書き）の対象外として、
    # _run_title_matchesで既に確定した正しい値のまま追加する
    standings_snapshot += suzaku_group_snapshot

    # standings_snapshotの各行のeloは、A〜Dリーグの総当たり・ランダム対戦が終わった直後
    # （_run_title_matchesを呼ぶ前）の値で記録されていた。しかしタイトル戦（青龍挑戦・
    # 朱雀紅白リーグ戦や紅白決定戦・白虎予選トーナメント・玄武抽選トーナメント等）は
    # 全てこの後に行われ、参加者のeloを変動させる。そのため「シーズン終了時Elo」として
    # 表示される値が、実際にはタイトル戦前の中間値のままになっており、同じ個体の
    # プロフィールページ・リーグタブの現在Eloと食い違って見える不具合になっていた。
    # ここで全行のeloを、今季の全対局（タイトル戦を含む）が終わった時点の最終値に
    # 揃え直す
    final_elo_by_id = {}
    for league_list in rosters.values():
        for ind in league_list:
            final_elo_by_id[ind.id] = ind.elo
    for ind in retired:
        final_elo_by_id[ind.id] = ind.elo
    for row in standings_snapshot:
        iid = row["individual_id"]
        if iid in final_elo_by_id:
            row["elo"] = round(final_elo_by_id[iid], 1)

    print(f"  引退: {len(retired)}名（{', '.join(i.display_name for i in retired)}）" if retired else "  引退: なし")
    print(f"  Dリーグ新人受け入れ枠（来季開始前に補充・固定）: {vacancy}名")

    # 歴代最高Elo（peak_elo）は、対局のたびにLeagueIndividual.elo setterが自動追跡するため、
    # ここでシーズン終了時の値を改めて比較する必要はない

    return rosters, match_log, title_results, retired, standings_snapshot, vacancy


def _result_from_winner_tag(tag):
    if tag == "draw":
        return "draw"
    return "win" if tag == "a" else "loss"


def _update_elo_per_game(ind_a, ind_b, games):
    """番勝負・決定戦のEloを1局ごとに更新する（gamesのwinnerは"a"/"b"/"draw"、aがind_a）"""
    for g in games:
        ind_a.elo, ind_b.elo = update_elo(
            ind_a.elo, ind_b.elo, _result_from_winner_tag(g.get("winner")),
            total_seasons_a=ind_a.total_seasons, total_seasons_b=ind_b.total_seasons,
        )


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
    """determine_*_challenger等のbracket_log（トーナメント）をmatch_logの形式に変換する。
    1対戦（先後を入れ替えた2局セット）を1件とし、勝敗は対戦の勝者で付ける"""
    return [{
        "league": league_name,
        "individual_a_id": m["a"],
        "individual_b_id": m["b"],
        "result": "win" if m["winner"] == m["a"] else "loss",
        "games": m.get("games", []),
    } for m in bracket_log]


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
            # 1対戦（先後を入れ替えた2局セット。決着しなければ追加のセット）を1件として記録し、
            # 勝敗は対戦の勝者で付ける（1局ごとに分けると、セットの勝者と各局の勝者が食い違い、
            # 結果サイトのトーナメント表で勝ち上がりを正しく復元できないため）
            x_id, y_id, winner_id = matchup["a"], matchup["b"], matchup["winner"]
            entry = {
                "league": f"{title}予選",
                "individual_a_id": x_id,
                "individual_b_id": y_id,
                "result": "win" if winner_id == x_id else "loss",
                "games": matchup.get("games", []),
            }
            # 玄武戦・麒麟戦は、ブロック分け表示のためstage（"block"/"final"等）とblockを付与する
            if "stage" in matchup:
                entry["stage"] = matchup["stage"]
            if "block" in matchup:
                entry["block"] = matchup["block"]
            entries.append(entry)

    return entries


def title_tournament_gens(all_members, state, season):
    """白虎・玄武・麒麟の挑戦者決定戦（予選・トーナメント）のジェネレータを作る。
    組み合わせは季の初めに決まっていて（前の季の終わりに公開したもの）、リーグ戦の結果にも互いの結果にも
    よらないので、リーグ戦と同時に進められる（parallel.drive）。番勝負は _run_title_matches で行う"""
    titleholders = state.setdefault("titleholders", {"青龍": None, "白虎": None, "玄武": None, "朱雀": None})
    titleholders.setdefault("麒麟", None)
    last_dethroned = state.setdefault("last_dethroned", {"白虎": None, "玄武": None})
    last_dethroned.setdefault("麒麟", None)
    holder_id = lambda title: (titleholders.get(title) or {}).get("id")
    # 季の初めに公開した組み合わせ（前の季の終了時点で決めたもの）があればそれを使う
    planned = state.get("planned_brackets") or {}
    if planned.get("season") != season:
        planned = {}
    kirin_plan = planned.get("麒麟") or plan_kirin_bracket(
        all_members, exclude_id=holder_id("麒麟"), groups=state.get("kirin_groups"),
        guaranteed_id=last_dethroned.get("麒麟"),
    )
    return [
        byakko_challenger_gen(
            all_members, exclude_id=holder_id("白虎"), depth=BYAKKO_LEAGUE_DEPTH, top_n=16,
            guaranteed_id=last_dethroned.get("白虎"), planned=planned.get("白虎"),
        ),
        genbu_challenger_gen(
            all_members, exclude_id=holder_id("玄武"), depth=GENBU_LEAGUE_DEPTH, bracket_size=64,
            titleholder_ids={info["id"] for info in titleholders.values() if info and info.get("id")},
            guaranteed_final_id=last_dethroned.get("玄武"),
            planned=planned.get("玄武"), newcomer_ids=planned.get("newcomer_ids"),
        ),
        kirin_challenger_gen(all_members, kirin_plan, newcomer_ids=planned.get("newcomer_ids"), depth=KIRIN_DEPTH),
    ]


def _run_title_matches(ranked_A, ranked_competing_A, champion_ind, ranked_B, ranked_C, ranked_D, all_members, state, season,
                       suzaku_tiebreak=None, title_tournament_outs=None):
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
    titleholders.setdefault("麒麟", None)
    titleholder_params.setdefault("麒麟", None)

    def _current_holder_params(title, holder_ind):
        # 在位者も挑戦者と同じく、毎季の年齢補正を反映した「今季の能力」で戦う
        # （獲得した季の能力のまま固定すると、防衛中は年を取らないことになるため）。
        # 在位者がロスターに見当たらない場合のみ、獲得時に保存した能力を使う
        if holder_ind is not None:
            return effective_params(holder_ind)
        return titleholder_params[title]
    all_members_by_id = {ind.id: ind for ind in all_members}

    # 前季このタイトルを失冠した個体のid（白虎・玄武のみ対応）。前季失冠が無かった、
    # または1季前のため既に使い切った場合はNoneになる（優遇は失冠した直後の1季限り）
    last_dethroned = state.setdefault("last_dethroned", {"白虎": None, "玄武": None})
    last_dethroned.setdefault("麒麟", None)

    # 引退免除対象の拡大用：この季の各タイトル戦の挑戦者（青龍・朱雀・白虎・玄武）を集める
    # （在位者と同様、年齢・Dリーグ降級点による強制引退から一時的に保護する。
    # 挑戦するところまで勝ち上がったのに、同じ季のうちに引退させてしまうのを防ぐため）
    extra_protected_ids = set()
    # Dリーグ降級点の「減点」対象用：この季の白虎トーナメント出場者（16名）・
    # 玄武ブロック優勝者（4名）を集める。挑戦者ほどの重みではないので強制引退からの
    # 完全な免除ではなく、降級点を1点減らす救済にとどめる
    demotion_relief_ids = set()

    # ============================================================
    # 青龍：Aリーグ総当たり1位が挑戦（青龍在位者は対局免除で待ち受ける）
    # ============================================================
    a_challenger = ranked_competing_A[0]
    extra_protected_ids.add(a_challenger.id)
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
            a_challenger, _current_holder_params("青龍", champion_ind), depth=SEIRYUU_TITLE_DEPTH,
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
            _update_elo_per_game(a_challenger, champion_ind, result["games"])
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
        # 初代メンバー決定トーナメントは対局数が多いため、探索深さは1で固定する（オーナー指定）
        bootstrap_qualifiers, bootstrap_bracket_log = determine_suzaku_qualifiers(
            bootstrap_pool, depth=SUZAKU_BOOTSTRAP_DEPTH, num_blocks=10,
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
            red_others, white_others, depth=SUZAKU_LEAGUE_DEPTH, tiebreak=suzaku_tiebreak,
        )
        extra_match_log += group_match_log

        red_relegate_n = min(2, len(red_ranked))
        white_relegate_n = min(2, len(white_ranked))
        red_stay = red_ranked[:len(red_ranked) - red_relegate_n]
        white_stay = white_ranked[:len(white_ranked) - white_relegate_n]
        red_returning = red_stay + red_holder_parked
        white_returning = white_stay + white_holder_parked
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
        extra_protected_ids.add(challenger.id)
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
                challenger, _current_holder_params("朱雀", defending_ind_for_volatility), depth=SUZAKU_TITLE_DEPTH,
                titleholder_volatility=defending_ind_for_volatility.volatility if defending_ind_for_volatility else 1.0,
            )
            print(f"  朱雀戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
                  f" → {'奪取！' if result['won'] else '防衛'}")
            if result["won"]:
                titleholders["朱雀"] = {"id": challenger.id, "name": challenger.display_name}
                titleholder_params["朱雀"] = effective_params(challenger)
                holder_name, holder_id = challenger.display_name, challenger.id
                # 奪取された旧保持者は、防衛専念枠を外れて来季の紅白リーグに無条件で復帰する
                # （青龍・白虎・玄武と同様、失冠した個体がそのまま母集団に戻るのが本来の仕様）。
                # ただしこの個体は今季開始時点でholder_parkedとして既にreturningへ
                # merge済みなので、ここで改めて追加すると二重登録になってしまうため何もしない
            else:
                holder_name, holder_id = defending_holder["name"], defending_holder["id"]
            defending_ind = all_members_by_id.get(defending_holder["id"])
            if defending_ind is not None:
                _update_elo_per_game(challenger, defending_ind, result["games"])
            results.append({
                "title": "朱雀", "season": season, **result,
                "challenger_name": challenger.display_name,
                "holder_name": holder_name, "holder_id": holder_id,
                "defender_id": defending_holder["id"],
            })

        # 新王者になった挑戦者は、来季は防衛専念枠に回る。ただし来季以降も
        # suzaku_group_standingsへの記録（＝紅白リーグ通算/連続在籍期数の継続）を
        # 途切れさせないため、ここでreturningから除外することはしない。
        # returningに残したままにしておけば、assign_suzaku_groups()で来季の
        # red/white振り分けに含まれ、来季以降は_prep_suzaku_group()のsuzaku_holder_id
        # 判定によって自動的に「在籍しているが対局免除（holder_parked）」として
        # 扱われる（既存の在位者と全く同じ仕組み）
        new_holder_id = titleholders["朱雀"]["id"]

        # 紅白リーグは「実際に総当たりで戦う実働10名（紅白各5名）」＋「来季の在位者（対局免除で
        # 在籍のみ）」で構成する。在位者は枠を占めないので、来季の在位者（new_holder_id）を
        # 除いた残留者を基準に、実働10名になるよう補充する（在位者がいる季は通常5名補充）。
        # 来季の在位者で判定するので、防衛（在位者は引き続き免除）・奪取（旧在位者は実働に戻り、
        # 新在位者が免除に回る）・初代襲名（今季戦った新在位者が来季から免除）のいずれでも正しく数える
        next_active_returning = [m for m in returning if m.id != new_holder_id]
        next_parked = [m for m in returning if m.id == new_holder_id]
        num_new_needed = max(0, 10 - len(next_active_returning))
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

        # 実働メンバーだけで紅白各5名に振り分け、在位者は元いた組に在籍のみ加える
        # （在位者込みで均等配分すると、在位者が入った組の実働が4名になってしまう）
        red_ids_next, white_ids_next = assign_suzaku_groups(next_active_returning, qualifiers)
        red_returning_ids = {m.id for m in red_returning}
        for m in next_parked:
            (red_ids_next if m.id in red_returning_ids else white_ids_next).append(m.id)
        suzaku_state["red"], suzaku_state["white"] = red_ids_next, white_ids_next
    else:
        print("  朱雀戦: 紅白リーグの参加者不足のため今季は見送り")

    # ============================================================
    # 白虎：Elo上位16名（前年白虎在位者は防衛専念枠として除外）による正式シードトーナメント
    # ============================================================
    # 白虎・玄武・麒麟の挑戦者決定戦は、季の初めにリーグ戦と同時に済ませてある（run_one_season）。
    # ここでは番勝負とEloの記録を棋戦ごとに順に行う
    byakko_out, genbu_out, kirin_out = title_tournament_outs
    challenger, bracket_log, byakko_entrants = byakko_out
    extra_protected_ids.add(challenger.id)
    demotion_relief_ids.update(ind.id for ind in byakko_entrants)
    # 失冠優遇は1季限りなので、使ったかどうかに関わらずここでリセットする
    # （このあと、この季に新たに失冠が発生すればその個体のidで上書きされる）
    last_dethroned["白虎"] = None

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
            challenger, _current_holder_params("白虎", defending_ind_for_volatility), depth=BYAKKO_TITLE_DEPTH,
            titleholder_volatility=defending_ind_for_volatility.volatility if defending_ind_for_volatility else 1.0,
        )
        print(f"  白虎戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        defending_ind = all_members_by_id.get(defending_holder["id"])
        if defending_ind is not None:
            _update_elo_per_game(challenger, defending_ind, result["games"])
        if result["won"]:
            titleholders["白虎"] = {"id": challenger.id, "name": challenger.display_name}
            titleholder_params["白虎"] = effective_params(challenger)
            holder_name, holder_id = challenger.display_name, challenger.id
            # 奪取された旧保持者は、来季の白虎トーナメントにEloに関わらず無条件で
            # 出場できる（他タイトルと同様、優遇なしで母集団に戻るだけでは酷なため）
            last_dethroned["白虎"] = defending_holder["id"]
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
    challenger, bracket_log, genbu_block_champions = genbu_out
    extra_protected_ids.add(challenger.id)
    demotion_relief_ids.update(ind.id for ind in genbu_block_champions)
    # 失冠優遇は1季限りなので、使ったかどうかに関わらずここでリセットする
    last_dethroned["玄武"] = None

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
            challenger, _current_holder_params("玄武", defending_ind_for_volatility), depth=GENBU_TITLE_DEPTH,
            titleholder_volatility=defending_ind_for_volatility.volatility if defending_ind_for_volatility else 1.0,
        )
        print(f"  玄武戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        defending_ind = all_members_by_id.get(defending_holder["id"])
        if defending_ind is not None:
            _update_elo_per_game(challenger, defending_ind, result["games"])
        if result["won"]:
            titleholders["玄武"] = {"id": challenger.id, "name": challenger.display_name}
            titleholder_params["玄武"] = effective_params(challenger)
            holder_name, holder_id = challenger.display_name, challenger.id
            # 奪取された旧保持者は、来季の玄武戦で予選ブロックを経ずに本戦（挑戦者決定
            # トーナメント）へ直接合流できる
            last_dethroned["玄武"] = defending_holder["id"]
        else:
            holder_name, holder_id = defending_holder["name"], defending_holder["id"]
        results.append({
            "title": "玄武", "season": season, **result,
            "challenger_name": challenger.display_name,
            "holder_name": holder_name, "holder_id": holder_id,
            "defender_id": defending_holder["id"],
            "bracket": bracket_log,
        })

    # ============================================================
    # 麒麟：組別の予選→本戦（ベスト4から敗者復活あり）→挑戦者決定戦→7番勝負
    # ============================================================
    challenger, bracket_log, next_kirin_groups, kirin_entrants = kirin_out
    state["kirin_groups"] = next_kirin_groups
    # 組の在籍期数（同じ組に連続で何期いるか。組み合わせ表示用）
    backfill_kirin_tenure(state)
    old_tenure = state.get("kirin_tenure") or {}
    state["kirin_tenure"] = {
        iid: {"group": g, "seasons": (old_tenure[iid]["seasons"] + 1
                                       if old_tenure.get(iid, {}).get("group") == g else 1)}
        for g in ("1", "2") for iid in next_kirin_groups.get(g, [])
    }
    extra_protected_ids.add(challenger.id)
    demotion_relief_ids.update(ind.id for ind in kirin_entrants)
    last_dethroned["麒麟"] = None
    for matchup in bracket_log:
        ind_a, ind_b = all_members_by_id.get(matchup["a"]), all_members_by_id.get(matchup["b"])
        if ind_a and ind_b:
            _update_elo_per_decided_games(ind_a, ind_b, matchup.get("games", []))

    if titleholders.get("麒麟") is None:
        titleholders["麒麟"] = {"id": challenger.id, "name": challenger.display_name}
        titleholder_params["麒麟"] = effective_params(challenger)
        print(f"  ★ 麒麟 初代襲名: {challenger.display_name}")
        results.append({
            "title": "麒麟", "season": season, "event": "初代襲名",
            "new_holder": challenger.display_name, "holder_id": challenger.id, "bracket": bracket_log,
        })
    else:
        defending_holder = titleholders["麒麟"]
        defending_ind = all_members_by_id.get(defending_holder["id"])
        result = run_kirin_challenge(
            challenger, _current_holder_params("麒麟", defending_ind), depth=KIRIN_DEPTH,
            titleholder_volatility=defending_ind.volatility if defending_ind else 1.0,
        )
        print(f"  麒麟戦: {challenger.display_name} {result['challenger_wins']}-{result['titleholder_wins']}"
              f" → {'奪取！' if result['won'] else '防衛'}")
        if defending_ind is not None:
            _update_elo_per_game(challenger, defending_ind, result["games"])
        if result["won"]:
            titleholders["麒麟"] = {"id": challenger.id, "name": challenger.display_name}
            titleholder_params["麒麟"] = effective_params(challenger)
            holder_name, holder_id = challenger.display_name, challenger.id
            # 奪取された旧保持者は、来季は1組として本戦から出場する
            last_dethroned["麒麟"] = defending_holder["id"]
        else:
            holder_name, holder_id = defending_holder["name"], defending_holder["id"]
        results.append({
            "title": "麒麟", "season": season, **result,
            "challenger_name": challenger.display_name,
            "holder_name": holder_name, "holder_id": holder_id,
            "defender_id": defending_holder["id"],
            "bracket": bracket_log,
        })

    return results, extra_match_log, suzaku_group_snapshot, extra_protected_ids, demotion_relief_ids


def _update_elo_per_decided_games(ind_a, ind_b, games):
    """予選・トーナメントの1対戦（決着まで打ち直した全局）を1局ずつEloに反映する"""
    for g in games:
        if g["result"] == "draw":
            outcome_a = "draw"
        else:
            outcome_a = "win" if (g["result"] == "black") == g["x_was_black"] else "loss"
        ind_a.elo, ind_b.elo = update_elo(
            ind_a.elo, ind_b.elo, outcome_a,
            total_seasons_a=ind_a.total_seasons, total_seasons_b=ind_b.total_seasons,
        )


SUZAKU_GROUP_LEAGUES = ("朱雀紅組", "朱雀白組")


def _load_suzaku_tiebreak(data_dir, season):
    """紅白リーグの同点順位付け用に、前季の紅白順位（在位者として在籍のみだった行は0）と
    紅白通算在籍期数（過去の全季のstandingsから数える）を集める"""
    prev_rank, tenure = {}, {}
    for s in range(1, season):
        path = os.path.join(data_dir, "standings", f"season_{s}.json")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        seen = set()
        for row in rows:
            if row.get("league") not in SUZAKU_GROUP_LEAGUES:
                continue
            iid = row["individual_id"]
            if iid not in seen:
                tenure[iid] = tenure.get(iid, 0) + 1
                seen.add(iid)
            if s == season - 1:
                prev_rank[iid] = row.get("rank", 99)
    return {"prev_rank": prev_rank, "tenure": tenure}


UPCOMING_BRACKETS_FILE = "upcoming_brackets.json"


def plan_next_brackets(rosters, state, next_season, newcomer_slots, games_per_individual=8):
    """次の季のB〜Dリーグ・白虎戦・玄武戦・麒麟戦の組み合わせを、この季の終了時点（Elo・所属リーグ・麒麟戦の組）で決めて
    state に記録する。季の初めから組み合わせを公開するため。新人の枠は「NEW1」等の仮の枠にしておき、
    新人が決まったら当てはめる"""
    members = [ind for league in LEAGUE_TIERS for ind in sorted(rosters[league], key=lambda ind: -ind.elo)]
    titleholders = state.get("titleholders") or {}
    last_dethroned = state.get("last_dethroned") or {}
    holder_id = lambda title: (titleholders.get(title) or {}).get("id")
    state["planned_brackets"] = {
        "season": next_season,
        "白虎": plan_byakko_bracket(members, exclude_id=holder_id("白虎"), top_n=16,
                                   guaranteed_id=last_dethroned.get("白虎")),
        "玄武": plan_genbu_bracket(members, exclude_id=holder_id("玄武"), bracket_size=64,
                                  titleholder_ids={info["id"] for info in titleholders.values() if info and info.get("id")},
                                  guaranteed_final_id=last_dethroned.get("玄武"), newcomer_slots=newcomer_slots),
        "麒麟": plan_kirin_bracket(members, exclude_id=holder_id("麒麟"), groups=state.get("kirin_groups"),
                                  guaranteed_id=last_dethroned.get("麒麟"), newcomer_slots=newcomer_slots),
        # B〜Dリーグ（ランダム対戦）の組み合わせ。Dリーグには新人の仮の枠も入れておく
        "leagues": {
            lg: plan_random_league(
                [ind.id for ind in rosters[lg]]
                + ([f"{NEWCOMER_SLOT_PREFIX}{k + 1}" for k in range(newcomer_slots)] if lg == "D" else []),
                games_per_individual,
            )
            for lg in ("B", "C", "D")
        },
        "newcomer_ids": {},
    }


def assign_planned_newcomers(state, season, new_disciples):
    """季の初めに入った新人を、玄武戦の組み合わせの新人枠（NEW1, NEW2, ...）に入団順で当てはめる。
    新人が枠より多ければ、余った新人は空き枠（不戦勝の相手）に入れる"""
    planned = state.get("planned_brackets") or {}
    if planned.get("season") != season:
        return
    genbu = planned["玄武"]
    slots = [pid for pid in genbu if pid and pid.startswith(NEWCOMER_SLOT_PREFIX)]
    planned["newcomer_ids"] = {slot: ind.id for slot, ind in zip(slots, new_disciples)}
    for ind in new_disciples[len(slots):]:
        if None not in genbu:
            break
        genbu[genbu.index(None)] = ind.id


def backfill_kirin_tenure(state):
    """組の在籍期数（kirin_tenure）を記録し始める前の季の分を、麒麟戦の対局記録から復元する（1回限り）。
    各季の次の組は、1組＝本戦の準々決勝に出た8名、2組＝本戦1回戦の敗者＋予選ブロック決勝の敗者"""
    if "kirin_tenure" in state:
        return
    tenure = {}
    for t in sorted((x for x in state.get("title_history", []) if x["title"] == "麒麟"), key=lambda x: x["season"]):
        log = [m for m in t.get("bracket", []) if m.get("games")]
        best8 = {i for m in log if m.get("stage") == "main2" for i in (m["a"], m["b"])}
        losers = [m["b"] if m["winner"] == m["a"] else m["a"] for m in log if m.get("stage") == "main1"]
        last_round = {}
        for m in log:
            st = m.get("stage", "")
            if st.startswith("prelim"):
                key = (st.split("-")[0], m.get("block"))
                last_round[key] = max(last_round.get(key, 0), int(st.split("-")[1]))
        for m in log:
            st = m.get("stage", "")
            if st.startswith("prelim") and int(st.split("-")[1]) == last_round[(st.split("-")[0], m.get("block"))]:
                losers.append(m["b"] if m["winner"] == m["a"] else m["a"])
        new = {}
        for g, ids in (("1", best8), ("2", losers)):
            for iid in ids:
                new[iid] = {"group": g, "seasons": tenure[iid]["seasons"] + 1 if tenure.get(iid, {}).get("group") == g else 1}
        tenure = new
    state["kirin_tenure"] = tenure


def write_upcoming_brackets(data_dir, state, rosters):
    """組み合わせを結果サイト表示用に書き出す（名前・所属リーグ・Eloつき）"""
    planned = state.get("planned_brackets")
    if not planned:
        return
    # elo_rank：現役全体でのEloの順位（白虎戦の出場条件などを示すため）
    by_elo = sorted((ind for league in LEAGUE_TIERS for ind in rosters[league]), key=lambda ind: -ind.elo)
    elo_rank = {ind.id: k + 1 for k, ind in enumerate(by_elo)}
    info = {ind.id: {"id": ind.id, "name": ind.display_name, "league": league, "elo": round(ind.elo),
                     "elo_rank": elo_rank[ind.id]}
            for league in LEAGUE_TIERS for ind in rosters[league]}
    newcomer_ids = planned.get("newcomer_ids") or {}

    def slot(pid):
        if not pid:
            return None
        if pid.startswith(NEWCOMER_SLOT_PREFIX):
            filled = info.get(newcomer_ids.get(pid))
            return {"newcomer": pid, **(filled or {"id": None, "name": None})}
        return info.get(pid) or {"id": pid, "name": None}

    titleholders = state.get("titleholders") or {}
    last_dethroned = state.get("last_dethroned") or {}
    out = {
        "season": planned["season"],
        "byakko": {"holder": titleholders.get("白虎"), "slots": [slot(p) for p in planned["白虎"]]},
        "genbu": {"holder": titleholders.get("玄武"), "num_blocks": 4,
                  "guaranteed_final": slot(last_dethroned.get("玄武")),
                  "slots": [slot(p) for p in planned["玄武"]]},
    }
    out["leagues"] = {lg: [[slot(a), slot(b)] for a, b in pairs] for lg, pairs in (planned.get("leagues") or {}).items()}
    kirin = planned.get("麒麟")
    if kirin:
        def main_slot(pid):
            if pid and pid.startswith(QUALIFIER_SLOT_PREFIX):
                k = int(pid[len(QUALIFIER_SLOT_PREFIX):]) - 1
                group = kirin["prelim"][k]["group"]
                in_group = sum(1 for b in kirin["prelim"][:k] if b["group"] == group)
                return {"qualifier": k, "group": group, "block_in_group": in_group}
            return slot(pid)
        backfill_kirin_tenure(state)
        tenure = state.get("kirin_tenure") or {}

        def group1_entry(pid):
            # 1組に入っている理由と、1組の在籍期数
            e = dict(slot(pid) or {})
            if pid == last_dethroned.get("麒麟"):
                e["reason"] = "前季失冠"
            elif not state.get("kirin_groups"):
                e["reason"] = "Aリーグ所属（初回）"
            else:
                e["reason"] = "前季本戦ベスト8"
            t = tenure.get(pid)
            e["tenure"] = t["seasons"] if t and t["group"] == "1" else 1
            return e

        def group2_entry(pid):
            e = dict(slot(pid) or {})
            t = tenure.get(pid)
            e["tenure"] = t["seasons"] if t and t["group"] == "2" else 1
            return e
        out["kirin"] = {
            "holder": titleholders.get("麒麟"),
            "group1": [group1_entry(p) for p in kirin["groups"]["1"]],
            "group2": [group2_entry(p) for p in kirin["groups"]["2"]],
            "prelim": [{"group": b["group"], "slots": [slot(p) for p in b["slots"]]} for b in kirin["prelim"]],
            "main": [main_slot(p) for p in kirin["main"]],
        }
    with open(os.path.join(data_dir, UPCOMING_BRACKETS_FILE), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


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
    parser.add_argument("--swiss-rounds", type=int, default=8)
    args = parser.parse_args()
    print(f"[DEBUG] パース済み引数: data_dir={args.data_dir}, seasons={args.seasons}, depth={args.depth}, swiss_rounds={args.swiss_rounds}", flush=True)

    state = load_season_state(args.data_dir)
    registry = NameRegistry.from_dict(state.get("name_registry", {}))

    rosters = load_rosters(args.data_dir)
    print(f"[DEBUG] load_rosters結果: {'None（新規作成へ）' if rosters is None else 'ロード成功'}", flush=True)
    if rosters is None:
        print("初回起動：ロスターを新規作成します", flush=True)
        rosters = bootstrap_rosters(registry, args.data_dir)
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

        # 新人受け入れ（毎季固定D_NEWCOMER_INTAKE名。state["pending_newcomer_slots"]に
        # 記録済み）を、今季の対局が始まる前に行う。新人リーグもこの固定枠数を
        # そのまま募集人数として使っているため、ここで対局前に補充することで
        # 「新人リーグが対象とした季」＝「実際に出走する季」に揃う
        # （以前は対局後に補充していたため、実際の出走は1季後にずれていた）
        registry = NameRegistry.from_dict(state.get("name_registry", {}))
        suzaku_league_ids = set(state.get("suzaku_league", {}).get("red", [])) | \
            set(state.get("suzaku_league", {}).get("white", []))
        rosters, new_disciples, registry, recruit_retired = recruit_d_league(
            rosters, season, registry,
            pending_characters=(pending_characters if i == 0 else None),
            titleholders=state.get("titleholders"),
            suzaku_league_ids=suzaku_league_ids,
            # 第1季（ブートストラップ直後）のみ、自動生成の新弟子での穴埋めを止める。
            # 通常はここで固定枠（D_NEWCOMER_INTAKE）まで即座に埋めてしまい、
            # 少人数スタートの意図が無効になるため
            auto_fill_vacancy=(season != 1),
        )
        state["name_registry"] = registry.to_dict()
        assign_planned_newcomers(state, season, new_disciples)
        write_upcoming_brackets(args.data_dir, state, rosters)
        if new_disciples:
            print(f"[DEBUG] 第{season}季開始前にDリーグへ{len(new_disciples)}名補充しました", flush=True)
        if recruit_retired:
            state.setdefault("retired_archive", [])
            state["retired_archive"] += [ind.to_dict() for ind in recruit_retired]
            print(f"[DEBUG] Dリーグ補充時の定員超過調整で{len(recruit_retired)}名引退しました", flush=True)

        # 新弟子の参入で弟子（引退した弟子も含む通算）が規定人数を超えた師匠は、弟子を引き連れて一門から独立する
        independence_events = apply_clan_independence(
            [ind for league in ("A", "B", "C", "D") for ind in rosters[league]],
            state.setdefault("retired_archive", []), season,
        )
        for ev in independence_events:
            print(f"  一門独立: {ev['name']}（{ev['id']}）が{ev['from_clan_id']}一門から独立", flush=True)
        state.setdefault("clan_independence_history", []).extend(independence_events)

        prev_standings_by_id = {}
        if season > 1:
            prev_path = os.path.join(args.data_dir, "standings", f"season_{season - 1}.json")
            if os.path.exists(prev_path):
                with open(prev_path, "r", encoding="utf-8") as f:
                    prev_rows = json.load(f)
                # standingsには朱雀紅白リーグの行も同じ個体IDで含まれる（A〜Dの行の後ろに追記）。
                # IDだけで辞書化すると紅白在籍者の行が紅白の行で上書きされ、前季順位が
                # 失われるため、A〜Dの行だけを使う
                prev_standings_by_id = {row["individual_id"]: row for row in prev_rows
                                        if row.get("league") in LEAGUE_TIERS}

        suzaku_tiebreak = _load_suzaku_tiebreak(args.data_dir, season)
        rosters, match_log, title_results, retired, standings_snapshot, vacancy = run_one_season(
            rosters, season, args.depth, args.swiss_rounds, state, prev_standings_by_id,
            suzaku_tiebreak=suzaku_tiebreak,
        )
        state["current_season"] = season
        state.setdefault("retired_archive", [])
        state["retired_archive"] += [ind.to_dict() for ind in retired]
        state.setdefault("title_history", [])
        state["title_history"] += title_results
        # 新人リーグ（次回AM/PM実行）で何名を昇格させるかの目安として、今季確定した
        # 新人受け入れ枠数（毎季固定）を記録しておく（次シーズン開始前にrecruit_d_league()で補充される）
        state["pending_newcomer_slots"] = vacancy
        plan_next_brackets(rosters, state, season + 1, vacancy, args.swiss_rounds)
        write_upcoming_brackets(args.data_dir, state, rosters)

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
