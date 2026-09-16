import random

from .individual import LeagueIndividual
from .buffs import maybe_awaken
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

    # Dリーグを卒業（Cへ昇格）する個体は、「2連続負け越し」のカウンタをリセットする。
    # このカウンタはDリーグ在籍中の成績のみを反映すべきものなので、
    # リセットしないと「昔Dにいた時の負け越し1回」が記録に残ったまま何季も引き継がれ、
    # 何季も後にDへ舞い戻った際に、実際には連続していない負け越しで即引退扱いになってしまう。
    for ind in d_promote_to_c:
        ind.consecutive_losing_seasons = 0

    A = a_remain + b_promote_to_a
    B = b_remain + a_relegate + c_promote_to_b
    C = c_remain + b_relegate_to_c + d_promote_to_c

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

    # --- Dリーグ：2シーズン連続で負け越したら、実力・在籍年数に関わらず即引退
    #     （ただしタイトル保持者は、age_retiredと同様に猶予対象）。
    #     この判定は「今季も引き続きDに在籍していた個体（d_remain）」のみを対象にする。
    #     今季Cから降格してきた個体（c_relegate_to_d・c_relegate_overflow）は、
    #     まだDでの対局実績が無い（直前の成績はC所属時のもの）ため対象外とし、
    #     カウンタを0にリセットして「Dでの連続負け越し」を来季以降ゼロから数え直す。 ---
    d_up_or_out_retired = []
    d_keep = []
    for ind in d_remain:
        if ind.loss_this_season > ind.win_this_season:
            ind.consecutive_losing_seasons += 1
        else:
            ind.consecutive_losing_seasons = 0
        if ind.consecutive_losing_seasons >= D_CONSECUTIVE_LOSING_LIMIT and ind.id not in titleholder_ids:
            ind.retired = True
            d_up_or_out_retired.append(ind)
        else:
            d_keep.append(ind)

    new_d_arrivals = c_relegate_to_d + c_relegate_overflow
    for ind in new_d_arrivals:
        ind.consecutive_losing_seasons = 0
    D = d_keep + new_d_arrivals

    # --- 年齢引退等でA・B・Cに定員割れが生じた場合、下位リーグのElo上位から繰り上げて埋める。
    #     ただし、今季ちょうど1つ下のリーグから昇格してきたばかりの個体（exclude_ids）は
    #     対象から除外する。タイトル奪取による大幅なElo上昇は昇降格判定より先に反映されるため、
    #     除外しないと「今季C→B昇格 かつ タイトル獲得でEloが急騰」のような個体が、
    #     同じ季のうちにB→Aへもバックフィルされ、CからAへ一気に飛び級してしまう ---
    def _backfill(upper, lower, capacity, exclude_ids=frozenset()):
        shortage = capacity - len(upper)
        if shortage <= 0 or not lower:
            return upper, lower
        eligible = [ind for ind in lower if ind.id not in exclude_ids]
        lower_sorted = sorted(eligible, key=lambda ind: ind.elo, reverse=True)
        take = lower_sorted[:shortage]
        take_ids = {ind.id for ind in take}
        lower_remaining = [ind for ind in lower if ind.id not in take_ids]
        return upper + take, lower_remaining

    c_promote_to_b_ids = {ind.id for ind in c_promote_to_b}
    d_promote_to_c_ids = {ind.id for ind in d_promote_to_c}

    A, B = _backfill(A, B, LEAGUE_CAPACITY["A"], exclude_ids=c_promote_to_b_ids)
    B, C = _backfill(B, C, LEAGUE_CAPACITY["B"], exclude_ids=d_promote_to_c_ids)
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
            count=d_departures, season=season, pool=candidates, name_registry=name_registry,
            titleholder_ids=titleholder_ids,
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


MASTER_MIN_AGE = 30  # 師匠になれる最低年齢（師匠より年下の弟子が生まれないようにするため）
AWAKENED_INITIAL_AGE_RANGE = (14, 16)  # 覚醒個体の参入年齢（通常は18〜24歳）

# 一門イベントの確率（新弟子1人あたり）
CLAN_BRANCH_CHANCE = 0.03       # 師匠の弟子になるが、本人が新しい一門の開祖として分岐する
CLAN_NEW_FOUNDER_CHANCE = 0.01  # 師匠を持たず、完全ランダムな能力の新規開祖として参入する


def _master_weight(ind, titleholder_ids):
    """師匠として選ばれやすさの重み。Eloが高いほど、タイトルを保持しているほど選ばれやすくする
    （強い一門ほど自然と子孫を残しやすくなる。ベースは1.0なので誰にでもチャンスはある）"""
    elo_bonus = max(0.0, (ind.elo - 1500) / 200)
    title_bonus = 3.0 if ind.id in titleholder_ids else 0.0
    return 1.0 + elo_bonus + title_bonus


def generate_disciples(count, season, pool, name_registry, titleholder_ids=frozenset()):
    """
    新弟子（Dリーグ参入個体）を生成する。師弟関係のため、親（師匠）は常に1人。
    師匠はElo・タイトル保持で重み付けした抽選で選ばれる（強い一門ほど子孫を残しやすい）。
    稀に「分岐」（弟子ではあるが新しい一門の開祖になる）や「新規開祖」
    （師匠を持たず完全ランダムな能力で参入する）が起きる。
    """
    disciples = []

    active_pool_all = [ind for ind in pool if not ind.retired]
    # 師匠になれるのは一定年齢以上の個体のみ（該当者が誰もいない序盤などは制限なしにフォールバック）
    eligible_masters = [ind for ind in active_pool_all if ind.age >= MASTER_MIN_AGE] or active_pool_all

    for i in range(count):
        ind_id = f"D{season}-{i:03d}"
        display_name = name_registry.generate()

        if not eligible_masters:
            master = None  # 現役個体が誰もいない極端なケース：師匠なしで生成するしかない
        elif random.random() < CLAN_NEW_FOUNDER_CHANCE:
            master = None  # 新規開祖：あえて師匠を持たない
        else:
            weights = [_master_weight(ind, titleholder_ids) for ind in eligible_masters]
            master = random.choices(eligible_masters, weights=weights, k=1)[0]

        if master:
            params, gen = mutate_params(master)
            master_id = master.id
            clan_root_id = ind_id if random.random() < CLAN_BRANCH_CHANCE else master.clan_root_id
        else:
            params = {k: round(random.uniform(0.5, 5.0), 3) for k in PARAM_KEYS}
            gen = 0
            master_id = None
            clan_root_id = ind_id

        # 覚醒判定：覚醒した個体は「神童」的な扱いとして、通常より若い年齢で参入することがある
        params, awakened = maybe_awaken(params, individual_id=ind_id)
        initial_age = random.randint(*AWAKENED_INITIAL_AGE_RANGE) if awakened else None

        ind = LeagueIndividual(
            ind_id, "D", params=params, generation=gen,
            parent_a_id=master_id, parent_b_id=None, display_name=display_name,
            clan_root_id=clan_root_id, initial_age=initial_age,
        )
        ind.awakened_param = awakened

        # 師匠のムラ気を継承しつつ、少し変異（ノイズ）を加える
        base_vol = master.volatility if master else random.uniform(0.3, 2.0)

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
