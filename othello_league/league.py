import random

from .individual import LeagueIndividual
from .dojo import MAJOR_DOJOS, assign_buff_multiplier, maybe_awaken
from .names import NameRegistry

# リーグの定員
LEAGUE_CAPACITY = {"A": 8, "B": 12, "C": 16, "D": 20}

# 昇降格の定員設定
A_TO_B_RELEGATE = 2  # A→B降格人数（＝B→A昇格人数）
B_TO_C_RELEGATE = 3  # B→C降格人数（＝C→B昇格人数）
D_TO_C_PROMOTE = 4   # D→C昇格人数

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

    # --- Dリーグ：2シーズン連続で負け越したら、実力・在籍年数に関わらず即引退 ---
    d_up_or_out_retired = []
    d_keep = []
    for ind in D:
        if ind.loss_this_season > ind.win_this_season:
            ind.consecutive_losing_seasons += 1
        else:
            ind.consecutive_losing_seasons = 0
        if ind.consecutive_losing_seasons >= D_CONSECUTIVE_LOSING_LIMIT:
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

    # C・Dリーグの定員超過調整（Elo下位をカットして引退扱い）
    c_over_retired = []
    if len(C) > LEAGUE_CAPACITY["C"]:
        C.sort(key=lambda ind: ind.elo, reverse=True)
        c_over_retired = C[LEAGUE_CAPACITY["C"]:]
        C = C[:LEAGUE_CAPACITY["C"]]
        for ind in c_over_retired:
            ind.retired = True

    d_over_retired = []
    if len(D) > LEAGUE_CAPACITY["D"]:
        D.sort(key=lambda ind: ind.elo, reverse=True)
        d_over_retired = D[LEAGUE_CAPACITY["D"]:]
        D = D[:LEAGUE_CAPACITY["D"]]
        for ind in d_over_retired:
            ind.retired = True

    all_retired.extend(c_over_retired + d_over_retired)

    new_rosters = {"A": A, "B": B, "C": C, "D": D}
    return new_rosters, new_disciples, name_registry, all_retired


def generate_disciples(count, season, pool, name_registry):
    """新弟子（Dリーグ参入個体）を生成する"""
    disciples = []
    for i in range(count):
        ind_id = f"D{season}-{i:03d}"
        display_name = name_registry.generate()

        # 有効な個体プール（引退していない者）から親を選択
        active_pool = [ind for ind in pool if not ind.retired]
        if len(active_pool) >= 2:
            parent_a, parent_b = random.sample(active_pool, 2)
            params, gen = breed_params(parent_a, parent_b)
            p_a_id, p_b_id = parent_a.id, parent_b.id
            dojo = parent_a.dojo if random.random() < 0.5 else parent_b.dojo
        elif len(active_pool) == 1:
            parent_a = active_pool[0]
            params, gen = breed_params(parent_a, parent_a)
            p_a_id, p_b_id = parent_a.id, None
            dojo = parent_a.dojo
        else:
            params = {k: round(random.uniform(0.5, 5.0), 3) for k in PARAM_KEYS}
            gen = 0
            p_a_id, p_b_id = None, None
            dojo = random.choice(MAJOR_DOJOS)

        # 覚醒判定
        params, awakened = maybe_awaken(params, individual_id=ind_id)

        ind = LeagueIndividual(
            ind_id, "D", params=params, dojo=dojo, generation=gen,
            parent_a_id=p_a_id, parent_b_id=p_b_id, display_name=display_name,
        )
        ind.awakened_param = awakened
        if dojo:
            ind.buff_multiplier = assign_buff_multiplier()

        # 親の平均ムラ気を継承しつつ、少し変異（ノイズ）を加える
        if p_a_id and p_b_id:
            parent_a_obj = next((x for x in active_pool if x.id == p_a_id), None)
            parent_b_obj = next((x for x in active_pool if x.id == p_b_id), None)
            base_vol = (parent_a_obj.volatility + parent_b_obj.volatility) / 2.0 if (parent_a_obj and parent_b_obj) else 1.0
        elif p_a_id:
            parent_a_obj = next((x for x in active_pool if x.id == p_a_id), None)
            base_vol = parent_a_obj.volatility if parent_a_obj else 1.0
        else:
            base_vol = random.uniform(0.3, 2.0)

        # ムラ気の変異（±0.2程度）
        vol = base_vol + random.uniform(-0.2, 0.2)
        ind.volatility = max(0.1, min(3.0, round(vol, 2)))

        disciples.append(ind)

    return disciples


def breed_params(parent_a, parent_b):
    """2個体のパラメータを交叉・変異させて新しいパラメータセットを生成する"""
    child_params = {}
    for k in PARAM_KEYS:
        val_a = parent_a.params.get(k, 1.0)
        val_b = parent_b.params.get(k, 1.0)
        # 交叉：親の平均
        base = (val_a + val_b) / 2.0
        # 変異：±15%程度のノイズ
        mutation = random.uniform(0.85, 1.15)
        child_params[k] = max(0.1, round(base * mutation, 3))

    max_gen = max(parent_a.generation, parent_b.generation)
    return child_params, max_gen + 1
