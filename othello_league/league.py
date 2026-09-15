import random

from .individual import LeagueIndividual
from .dojo import MAJOR_DOJOS, assign_buff_multiplier, maybe_awaken, inherit_dojo
from .names import NameRegistry

# リーグの定員
LEAGUE_CAPACITY = {"A": 8, "B": 12, "C": 16, "D": 20}

# 昇降格の定員設定
A_TO_B_RELEGATE = 2  # A→B降格人数（＝B→A昇格人数）
B_TO_C_RELEGATE = 3  # B→C降格人数（＝C→B昇格人数）
D_TO_C_PROMOTE = 3   # D→C昇格人数（C→D降格人数と揃え、Cリーグの定員超過を防ぐ）

RETIREMENT_AGE = 60
D_CONSECUTIVE_LOSING_LIMIT = 2
C_TO_D_RELEGATE = 3  # C→D降格人数（B→Cと同数）

PARAM_KEYS = [
    "corner_weight", "danger_zone_weight", "mobility_weight", "edge_stability_weight",
    "frontier_weight", "disc_weight", "parity_weight", "center_weight",
]


def promote_and_relegate(rosters, season, name_registry=None, titleholders=None):
    """
    1シーズン終了後の昇降格、定員超過/不足の調整、新弟子の生成を行う。
    戻り値: (更新後のrosters dict, 新弟子リスト, 更新後のname_registry, 引退者リスト)
    """
    if name_registry is None:
        name_registry = NameRegistry()

    A = list(rosters["A"])
    B = list(rosters["B"])
    C = list(rosters["C"])
    D = list(rosters["D"])

    # --- 60歳以上、かつ無冠（どのタイトルも持っていない）の個体は強制引退させる。
    #     タイトルを1つでも持っていれば、全て失冠するまで猶予が続く ---
    titleholder_ids = set()
    if titleholders:
        for info in titleholders.values():
            if info and info.get("id"):
                titleholder_ids.add(info["id"])

    age_retired = []
    def _filter_aged_out(members):
        keep, retired = [], []
        for ind in members:
            if ind.age >= RETIREMENT_AGE and ind.id not in titleholder_ids:
                ind.retired = True
                retired.append(ind)
            else:
                keep.append(ind)
        return keep, retired

    A, r = _filter_aged_out(A); age_retired += r
    B, r = _filter_aged_out(B); age_retired += r
    C, r = _filter_aged_out(C); age_retired += r
    D, r = _filter_aged_out(D); age_retired += r

    # 昇降格枠数の決定
    ab_n = min(A_TO_B_RELEGATE, len(A), len(B))
    bc_n = min(B_TO_C_RELEGATE, len(B), len(C))

    a_relegate = A[-ab_n:]
    a_remain = A[:-ab_n]

    b_promote_to_a = B[:ab_n]
    b_relegate_to_c = B[-bc_n:]
    b_remain = B[ab_n:-bc_n]

    c_promote_to_b = C[:bc_n]
    c_relegate_to_d = C[-C_TO_D_RELEGATE:]
    c_remain = C[bc_n:-C_TO_D_RELEGATE]

    d_promote_to_c = D[:D_TO_C_PROMOTE]
    d_remain = D[D_TO_C_PROMOTE:]

    A = a_remain + b_promote_to_a
    B = b_remain + a_relegate + c_promote_to_b
    C = c_remain + b_relegate_to_c + d_promote_to_c
    D = d_remain + c_relegate_to_d

    # --- Cリーグが定員超過した場合はDへ降格させる（引退ではない。
    #     A〜Cリーグの引退条件は年齢のみとする方針のため、Elo下位を退場させるのではなく
    #     降格として扱う。本来D_TO_C_PROMOTEとC_TO_D_RELEGATEを揃えていれば
    #     超過しないはずだが、初期ロスターが定員通りでない場合などへの保険） ---
    def _relegate_overflow(members, capacity):
        if len(members) <= capacity:
            return members, []
        protected = [ind for ind in members if ind.id in titleholder_ids]
        sorted_members = sorted(
            (ind for ind in members if ind.id not in titleholder_ids),
            key=lambda ind: ind.elo, reverse=True,
        )
        shortage = len(members) - capacity
        keep = sorted_members[:max(0, len(sorted_members) - shortage)]
        overflow = sorted_members[max(0, len(sorted_members) - shortage):]
        return protected + keep, overflow

    C, c_relegate_overflow = _relegate_overflow(C, LEAGUE_CAPACITY["C"])
    D = D + c_relegate_overflow

    # --- Dリーグ：2シーズン連続で負け越したら、実力・在籍年数に関わらず即引退
    #     （ただしタイトル保持者は、age_retiredと同様に猶予対象） ---
    d_up_or_out_retired = []
    d_keep = []
    for ind in D:
        if ind.loss_this_season > ind.win_this_season:
            ind.consecutive_losing_seasons += 1
        else:
            ind.consecutive_losing_seasons = 0
        if ind.consecutive_losing_seasons >= D_CONSECUTIVE_LOSING_LIMIT and ind.id not in titleholder_ids:
            ind.retired = True
            d_up_or_out_retired.append(ind)
        else:
            d_keep.append(ind)
    D = d_keep

    # --- 年齢引退等でA・B・Cに定員割れが生じた場合、下位リーグのElo上位から繰り上げて埋める ---
    def _backfill(upper, lower, capacity):
        shortage = capacity - len(upper)
        if shortage <= 0 or not lower:
            return upper, lower
        lower_sorted = sorted(lower, key=lambda ind: ind.elo, reverse=True)
        take = lower_sorted[:shortage]
        take_ids = {ind.id for ind in take}
        lower_remaining = [ind for ind in lower if ind.id not in take_ids]
        return upper + take, lower_remaining

    A, B = _backfill(A, B, LEAGUE_CAPACITY["A"])
    B, C = _backfill(B, C, LEAGUE_CAPACITY["B"])
    C, D = _backfill(C, D, LEAGUE_CAPACITY["C"])

    # リーグ所属情報・在籍年数の更新
    all_retired = age_retired + d_up_or_out_retired

    for league_name, members in (("A", A), ("B", B), ("C", C), ("D", D)):
        for ind in members:
            if ind.league != league_name:
                ind.league = league_name
                ind.seasons_in_league = 0
            else:
                ind.seasons_in_league += 1
            ind.total_seasons += 1

    # Dリーグの補充（新弟子の生成）
    d_departures = LEAGUE_CAPACITY["D"] - len(D)
    new_disciples = []
    if d_departures > 0:
        candidates = A + B + C + D
        new_disciples = generate_disciples(
            count=d_departures, season=season, pool=candidates, name_registry=name_registry
        )
        D.extend(new_disciples)

    # Dリーグの定員超過調整（Elo下位をカットして引退扱い。タイトル保持者はカット対象から除外。
    # Dより下のリーグはないため、ここだけは引退させるしかない）
    def _cut_overflow(members, capacity):
        if len(members) <= capacity:
            return members, []
        protected = [ind for ind in members if ind.id in titleholder_ids]
        cuttable = sorted(
            (ind for ind in members if ind.id not in titleholder_ids),
            key=lambda ind: ind.elo, reverse=True,
        )
        shortage = len(members) - capacity
        keep_cuttable = cuttable[:max(0, len(cuttable) - shortage)]
        over = cuttable[max(0, len(cuttable) - shortage):]
        for ind in over:
            ind.retired = True
        return protected + keep_cuttable, over

    D, d_over_retired = _cut_overflow(D, LEAGUE_CAPACITY["D"])

    all_retired.extend(d_over_retired)

    new_rosters = {"A": A, "B": B, "C": C, "D": D}
    return new_rosters, new_disciples, name_registry, all_retired


def generate_disciples(count, season, pool, name_registry):
    """新弟子（Dリーグ参入個体）を生成する。師弟関係のため、親（師匠）は常に1人"""
    disciples = []

    # 現在ロスターに1人もいない道場（絶えかけている流派）があれば、
    # 今季の新弟子枠を使って優先的に再興させる（8大流派を恒久的に維持するため）
    active_pool_all = [ind for ind in pool if not ind.retired]
    existing_dojos = {ind.dojo for ind in active_pool_all if ind.dojo}
    missing_dojos = [d for d in MAJOR_DOJOS if d not in existing_dojos]
    random.shuffle(missing_dojos)

    for i in range(count):
        ind_id = f"D{season}-{i:03d}"
        display_name = name_registry.generate()

        force_dojo = missing_dojos.pop() if missing_dojos else None

        # 師匠（有効な個体プールからランダムに1人）を選び、その弟子として生成する
        active_pool = [ind for ind in pool if not ind.retired]
        if active_pool:
            master = random.choice(active_pool)
            params, gen = mutate_params(master)
            master_id = master.id
            dojo = force_dojo or inherit_dojo(master.dojo)
        else:
            params = {k: round(random.uniform(0.5, 5.0), 3) for k in PARAM_KEYS}
            gen = 0
            master_id = None
            dojo = force_dojo or random.choice(MAJOR_DOJOS)

        # 覚醒判定
        params, awakened = maybe_awaken(params, individual_id=ind_id)

        ind = LeagueIndividual(
            ind_id, "D", params=params, dojo=dojo, generation=gen,
            parent_a_id=master_id, parent_b_id=None, display_name=display_name,
        )
        ind.awakened_param = awakened
        if dojo:
            ind.buff_multiplier = assign_buff_multiplier()

        # 師匠のムラ気を継承しつつ、少し変異（ノイズ）を加える
        if master_id:
            master_obj = next((x for x in active_pool if x.id == master_id), None)
            base_vol = master_obj.volatility if master_obj else 1.0
        else:
            base_vol = random.uniform(0.3, 2.0)

        # ムラ気の変異（±0.2程度）
        vol = base_vol + random.uniform(-0.2, 0.2)
        ind.volatility = max(0.1, min(3.0, round(vol, 2)))

        disciples.append(ind)

    return disciples


def mutate_params(master):
    """師匠のパラメータを継承しつつ、弟子ごとに変異（±15%程度のノイズ）を加える"""
    child_params = {}
    for k in PARAM_KEYS:
        val = master.params.get(k, 1.0)
        mutation = random.uniform(0.85, 1.15)
        child_params[k] = max(0.1, round(val * mutation, 3))

    return child_params, master.generation + 1
