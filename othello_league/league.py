import random

from .dojo import MAJOR_DOJOS, assign_buff_multiplier, maybe_awaken

# ============================================================
# リーグ定員・昇降格人数（固定値。ここを崩すと人数が発散するので注意）
# ============================================================
LEAGUE_CAPACITY = {"A": 8, "B": 16, "C": 16, "D": 16}
PROMOTE_DOWN_COUNTS = {
    ("A", "B"): 2,   # A下位2名がBへ降格、B上位2名がAへ昇格
    ("B", "C"): 3,   # B下位3名がCへ降格、C上位3名がBへ昇格
}
# C⇄D間は「降格」が無い。Dの上位3名がCへ昇格するのみ。
D_TO_C_PROMOTE = 3

# C・D 定員超過時の引退ルール
MIN_SEASONS_BEFORE_RETIREMENT_ELIGIBLE = 2  # このシーズン数未満は引退対象から除外
MAX_TOTAL_SEASONS = 30  # 年齢の暫定版：通算30シーズンで強制引退
D_MAX_SEASONS_IN_D = 5  # Dリーグ在籍上限：この期間内にCへ昇格できなければ、実力に関わらず引退（up-or-out）

DISCIPLES_PER_DOJO_PER_SEASON = 1  # 各道場から毎シーズン何名弟子を出すか
WILD_DISCIPLE_CHANCE = 1.0          # 無流派の新規参入。8大流派とは別枠で、毎シーズン必ず1名を完全ランダムに追加

MUTATION_RATE = 0.2
MUTATION_STRENGTH = 0.25
PARAM_KEYS = [
    "corner_weight", "danger_zone_weight", "mobility_weight", "edge_stability_weight",
    "frontier_weight", "disc_weight", "parity_weight", "center_weight",
]


def promote_and_relegate(rosters, season, name_registry=None):
    """
    rosters: {"A": [LeagueIndividual, ...], "B": [...], "C": [...], "D": [...]}
             各リストは事前に「そのシーズンの成績順」にソート済みであること（先頭が1位）
    戻り値: 更新後の rosters（同じ dict を書き換えて返す）
    """
    A, B, C, D = rosters["A"], rosters["B"], rosters["C"], rosters["D"]

    # --- 通算30シーズンに達した個体は、リーグを問わず強制引退させる（年齢の暫定ルール） ---
    age_retired = []
    def _filter_aged_out(members):
        keep, retired = [], []
        for ind in members:
            if ind.total_seasons >= MAX_TOTAL_SEASONS:
                ind.retired = True
                retired.append(ind)
            else:
                keep.append(ind)
        return keep, retired

    A, r = _filter_aged_out(A); age_retired += r
    B, r = _filter_aged_out(B); age_retired += r
    C, r = _filter_aged_out(C); age_retired += r
    D, r = _filter_aged_out(D); age_retired += r

    # --- 各リーグの昇降格対象を、"今季本来の成績"だけで独立に確定する ---
    # （他リーグから移動してきたばかりの個体が、同じ処理内で即座に再降格に巻き込まれるバグを防ぐため、
    #   ここでは元のリストから素直にスライスするだけに留め、リストの組み立ては最後にまとめて行う）
    ab_n = PROMOTE_DOWN_COUNTS[("A", "B")]
    bc_n = PROMOTE_DOWN_COUNTS[("B", "C")]

    a_relegate = A[-ab_n:]
    a_remain = A[:-ab_n]

    b_promote_to_a = B[:ab_n]
    b_relegate_to_c = B[-bc_n:]
    b_remain = B[ab_n:-bc_n]

    c_promote_to_b = C[:bc_n]
    c_remain = C[bc_n:]

    d_promote_to_c = D[:D_TO_C_PROMOTE]
    d_remain = D[D_TO_C_PROMOTE:]

    # --- ここで初めて、移動結果をまとめて新しいロスターに組み立てる ---
    A = a_remain + b_promote_to_a
    B = b_remain + a_relegate + c_promote_to_b
    C = c_remain + b_relegate_to_c + d_promote_to_c
    D = d_remain

    # --- Dリーグ在籍上限（up-or-out）：5シーズン以内に昇格できなければ、実力に関わらず引退 ---
    d_up_or_out_retired = []
    d_keep = []
    for ind in D:
        if ind.seasons_in_league >= D_MAX_SEASONS_IN_D:
            ind.retired = True
            d_up_or_out_retired.append(ind)
        else:
            d_keep.append(ind)
    D = d_keep

    # 昇降格したので、リーグ移動があった個体は在籍シーズン数をリセットする
    for ind in A:
        if ind.league != "A":
            ind.seasons_in_league = 0
        ind.league = "A"
    for ind in B:
        if ind.league != "B":
            ind.seasons_in_league = 0
        ind.league = "B"
    for ind in C:
        if ind.league != "C":
            ind.seasons_in_league = 0
        ind.league = "C"
    for ind in D:
        ind.league = "D"

    # --- 在籍シーズン数を加算 ---
    for league_list in (A, B, C, D):
        for ind in league_list:
            ind.seasons_in_league += 1
            ind.total_seasons += 1

    # --- 新弟子の補充（Dリーグに追加） ---
    all_individuals_before = A + B + C + D  # 道場主候補を探すため、昇降格後の全体から選ぶ
    new_disciples, name_registry = generate_disciples(season, all_individuals_before, name_registry)
    D = D + new_disciples

    # --- C・D の定員超過分を、実力（Elo）が低い順に引退させる（在籍猶予は維持） ---
    C, retired_c = _enforce_capacity_with_retirement(C, LEAGUE_CAPACITY["C"])
    D, retired_d = _enforce_capacity_with_retirement(D, LEAGUE_CAPACITY["D"])
    retired_this_season = retired_c + retired_d + age_retired + d_up_or_out_retired

    rosters["A"], rosters["B"], rosters["C"], rosters["D"] = A, B, C, D
    return rosters, new_disciples, name_registry, retired_this_season


def _enforce_capacity_with_retirement(league_list, capacity):
    """
    定員を超えている場合、在籍シーズン数が MIN_SEASONS_BEFORE_RETIREMENT_ELIGIBLE 以上の個体の中から
    実力（Elo）が低い順に引退させ、定員に収める（在籍年数ではなく実力を基準にする）。
    引退対象が見つからない場合（全員が新入りの場合）は、超過をそのまま許容する。
    戻り値: (残ったリスト, 引退した個体のリスト)
    """
    excess = len(league_list) - capacity
    if excess <= 0:
        return league_list, []

    eligible = [ind for ind in league_list if ind.seasons_in_league >= MIN_SEASONS_BEFORE_RETIREMENT_ELIGIBLE]
    eligible_sorted = sorted(eligible, key=lambda ind: ind.elo)  # Eloが低い順に先頭へ

    retire_targets = eligible_sorted[:excess]
    retire_ids = {ind.id for ind in retire_targets}

    for ind in retire_targets:
        ind.retired = True

    remaining = [ind for ind in league_list if ind.id not in retire_ids]
    return remaining, retire_targets


def generate_disciples(season, all_individuals, name_registry=None):
    """
    8大流派から毎シーズン新弟子を生成する。
    各道場に所属する（引退していない）個体からランダムに1体選び、その変異クローンを弟子とする
    （＝各流派から最大1名という均衡は自然に守られる）。
    道場に所属者が1人もいない場合は、パラメータ無しの新規個体として生成する（開祖扱い）。
    それとは別枠で、道場に属さない「無流派」を毎シーズン必ず1名、完全ランダムに追加する。
    """
    from .names import NameRegistry
    registry = name_registry or NameRegistry()

    disciples = []

    for dojo in MAJOR_DOJOS:
        dojo_members = [ind for ind in all_individuals if ind.dojo == dojo and not ind.retired]
        new_id = f"S{season}-{dojo}-{format(random.randint(0, 4095), 'x').upper()}"

        if dojo_members:
            parent = random.choice(dojo_members)
            child_params = _mutate_params(parent.params)
        else:
            child_params = _random_params()

        child_params, awakened_key = maybe_awaken(child_params, individual_id=new_id)

        child = LeagueIndividualLazy(new_id, "D", dojo=dojo, generation=season,
                                      params=child_params, parent_a_id=parent.id if dojo_members else None,
                                      display_name=registry.generate())
        child.buff_multiplier = assign_buff_multiplier()
        child.awakened_param = awakened_key
        disciples.append(child)

    if random.random() < WILD_DISCIPLE_CHANCE:
        new_id = f"S{season}-Wild-{format(random.randint(0, 4095), 'x').upper()}"
        wild_params, awakened_key = maybe_awaken(_random_params(), individual_id=new_id)
        wild = LeagueIndividualLazy(new_id, "D", dojo=None, generation=season, params=wild_params,
                                     display_name=registry.generate())
        wild.awakened_param = awakened_key
        disciples.append(wild)
        print(f"  → 無流派の新規参入: {wild.id}（{wild.display_name}）")

    return disciples, registry


def _random_params():
    return {key: round(random.uniform(0.5, 10.0), 3) for key in PARAM_KEYS}


def _mutate_params(parent_params):
    child = {}
    for key in PARAM_KEYS:
        val = parent_params.get(key, 1.0)
        if random.random() < MUTATION_RATE:
            val *= random.uniform(1 - MUTATION_STRENGTH, 1 + MUTATION_STRENGTH)
        child[key] = round(max(0.01, val), 3)
    return child


def LeagueIndividualLazy(*args, **kwargs):
    """循環importを避けるための遅延インポート経由の生成ヘルパー"""
    from .individual import LeagueIndividual
    return LeagueIndividual(*args, **kwargs)
