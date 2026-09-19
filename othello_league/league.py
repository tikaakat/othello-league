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


def relegate_and_retire(rosters, season, titleholders=None, suzaku_league_ids=None):
    """
    1シーズンの対局終了後の昇降格・強制引退の判定のみを行う（Dリーグの新規補充は
    recruit_d_league()が別関数として、次シーズンの対局が始まる前に行う）。

    以前はこの関数の中でDリーグの新規補充（新人リーグの勝者・自動生成の新弟子）まで
    一括で行っており、その季の対局後に新弟子が参入する形だった。そのため新人リーグの
    募集人数は「1つ前の季で確定した欠員数」という1サイクル遅れの推定値を使わざるを
    得ず、実際に出走するのは新人リーグが対象とした季の1つ後になっていた。
    補充処理を次シーズン開始前（対局前）に切り出すことで、新人リーグは「直前の季で
    ちょうど確定した欠員数」を正確な人数として使え、その勝者は新人リーグが対象とした
    季からそのまま出走できるようになる。

    suzaku_league_idsが与えられた場合、朱雀紅白リーグに在籍中の個体は、タイトル保持者と
    同様に強制引退（年齢・Dリーグ連続負け越し・Dリーグ定員超過カット）の対象から除外する。
    予選を勝ち抜いて来季から紅白リーグに加入する個体も、その加入前の季にこれらの条件で
    引退させてしまうと来季の紅白リーグに欠員が生じるため、同様に保護する
    （C〜Aリーグの定員超過による「降格」は引退ではなく在籍リーグが変わるだけなので対象外）。
    戻り値: (更新後のrosters dict（Dリーグは欠員分だけ定員割れのことがある）,
             引退者リスト, Dリーグの欠員数)
    """
    A = list(rosters["A"])
    B = list(rosters["B"])
    C = list(rosters["C"])
    D = list(rosters["D"])

    # --- 60歳以上、かつ無冠（どのタイトルも持っていない）の個体は強制引退させる。
    #     タイトルを1つでも持っていれば、全て失冠するまで猶予が続く。
    #     朱雀紅白リーグ在籍者（来季から加入予定の予選通過者も含む）も、在籍中は
    #     年齢引退に加えて、Dリーグ連続負け越し・Dリーグ定員超過カットも免除する
    #     （でないと紅白リーグ加入前に引退させてしまい、来季の紅白リーグに欠員が生じるため） ---
    titleholder_ids = set()
    if titleholders:
        for info in titleholders.values():
            if info and info.get("id"):
                titleholder_ids.add(info["id"])

    protected_ids = set(titleholder_ids)
    if suzaku_league_ids:
        protected_ids.update(suzaku_league_ids)

    age_retired = []
    def _filter_aged_out(members):
        keep, retired = [], []
        for ind in members:
            if ind.age >= RETIREMENT_AGE and ind.id not in protected_ids:
                ind.retired = True
                # 引退する個体も今季は実際に対局しているので、在籍シーズン数に数える
                # （数えないと、対局実績があるのに引退季の1つ手前までしか通算しておらず、
                #  在籍期間の表示が実際より1季ずれてしまう）
                ind.total_seasons += 1
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
    #     （ただしタイトル保持者・朱雀紅白リーグ在籍者は、age_retiredと同様に猶予対象）。
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
        if ind.consecutive_losing_seasons >= D_CONSECUTIVE_LOSING_LIMIT and ind.id not in protected_ids:
            ind.retired = True
            ind.total_seasons += 1  # age_retiredと同様、引退する今季分も在籍シーズン数に数える
            d_up_or_out_retired.append(ind)
        else:
            d_keep.append(ind)

    new_d_arrivals = c_relegate_to_d + c_relegate_overflow
    for ind in new_d_arrivals:
        ind.consecutive_losing_seasons = 0
    D = d_keep + new_d_arrivals

    # --- 年齢引退等でA・B・Cに定員割れが生じた場合、下位リーグから繰り上げて埋める。
    #     今季そのリーグに在籍して実際に対局した個体（remain_ids）の中から、今季の順位が
    #     高い順（＝remain_idsの並び順。b_remain/c_remain/d_keepは元々ランク順のため）に
    #     優先して繰り上げる。今季ちょうど1つ下のリーグから昇格/降格してきたばかりの個体は、
    #     今季そのリーグでの対局実績が無いため対象外とし、それでも枠が埋まらない場合に限り
    #     Elo上位から補う（滅多に起きない保険的な扱い）。
    #     exclude_idsは、今季ちょうど1つ下のリーグから昇格してきたばかりの個体を保険的な
    #     Elo補充からも除外する。除外しないと「今季C→B昇格 かつ タイトル獲得でEloが急騰」の
    #     ような個体が、同じ季のうちにB→Aへもバックフィルされ、CからAへ一気に飛び級してしまう ---
    def _backfill(upper, lower, capacity, remain_ids, exclude_ids=frozenset()):
        shortage = capacity - len(upper)
        if shortage <= 0 or not lower:
            return upper, lower
        remain_pool = [ind for ind in lower if ind.id in remain_ids and ind.id not in exclude_ids]
        take = remain_pool[:shortage]
        still_short = shortage - len(take)
        if still_short > 0:
            taken_ids = {ind.id for ind in take}
            rest_pool = [ind for ind in lower if ind.id not in remain_ids and ind.id not in exclude_ids
                         and ind.id not in taken_ids]
            rest_sorted = sorted(rest_pool, key=lambda ind: ind.elo, reverse=True)
            take = take + rest_sorted[:still_short]
        take_ids = {ind.id for ind in take}
        lower_remaining = [ind for ind in lower if ind.id not in take_ids]
        return upper + take, lower_remaining

    c_promote_to_b_ids = {ind.id for ind in c_promote_to_b}
    d_promote_to_c_ids = {ind.id for ind in d_promote_to_c}
    b_remain_ids = {ind.id for ind in b_remain}
    c_remain_ids = {ind.id for ind in c_remain}
    d_keep_ids = {ind.id for ind in d_keep}

    A, B = _backfill(A, B, LEAGUE_CAPACITY["A"], remain_ids=b_remain_ids, exclude_ids=c_promote_to_b_ids)
    B, C = _backfill(B, C, LEAGUE_CAPACITY["B"], remain_ids=c_remain_ids, exclude_ids=d_promote_to_c_ids)
    C, D = _backfill(C, D, LEAGUE_CAPACITY["C"], remain_ids=d_keep_ids)

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

    # Dリーグが定員を超えている場合（D_TO_C_PROMOTEとC_TO_D_RELEGATEの数が想定通りで
    # あれば通常は起きないが、初期ロスターが定員通りでない場合などへの保険）は、
    # 新規補充を待たずにここでElo下位をカットする（この時点では新規参入者がまだ
    # いないため、保護対象はタイトル保持者・朱雀紅白リーグ在籍者のみでよい）
    def _cut_d_overflow(members, capacity):
        if len(members) <= capacity:
            return members, []
        protected = [ind for ind in members if ind.id in protected_ids]
        cuttable = sorted(
            (ind for ind in members if ind.id not in protected_ids),
            key=lambda ind: ind.elo, reverse=True,
        )
        shortage = len(members) - capacity
        keep_cuttable = cuttable[:max(0, len(cuttable) - shortage)]
        over = cuttable[max(0, len(cuttable) - shortage):]
        for ind in over:
            ind.retired = True
        return protected + keep_cuttable, over

    D, d_over_retired = _cut_d_overflow(D, LEAGUE_CAPACITY["D"])
    all_retired.extend(d_over_retired)

    vacancy = LEAGUE_CAPACITY["D"] - len(D)

    new_rosters = {"A": A, "B": B, "C": C, "D": D}
    return new_rosters, all_retired, vacancy


def recruit_d_league(rosters, season, name_registry=None, pending_characters=None, titleholders=None,
                      suzaku_league_ids=None):
    """
    Dリーグの欠員（relegate_and_retire()が確定させたもの。rosters["D"]が定員割れの
    状態で渡ってくる）を、次シーズンの対局が始まる前に補充する。
    まずキャラクリエイト・新人リーグ経由のリクエスト（pending_characters）を優先的に
    参入させ、残り枠のみ通常の新弟子生成で埋める。
    キャラクリエイト個体も通常の新弟子と同様、必ず既存個体の中から師匠が自動選出される
    （本人が選ぶわけではない。新規開祖にはならないが、分岐で新しい一門の開祖になることはある）。
    pending_charactersの人数が実際の欠員数を上回る場合（新人リーグの募集人数の推定と
    実際の欠員がズレた場合の保険）は、既存メンバーのElo下位をカットして枠を確保する
    （新人リーグ勝者自身・タイトル保持者・朱雀紅白リーグ在籍者はカット対象から除外）。
    戻り値: (更新後のrosters dict, 新規参入者リスト, 更新後のname_registry, 引退者リスト)
    """
    if name_registry is None:
        name_registry = NameRegistry()

    A = list(rosters["A"])
    B = list(rosters["B"])
    C = list(rosters["C"])
    D = list(rosters["D"])

    titleholder_ids = set()
    if titleholders:
        for info in titleholders.values():
            if info and info.get("id"):
                titleholder_ids.add(info["id"])

    protected_ids = set(titleholder_ids)
    if suzaku_league_ids:
        protected_ids.update(suzaku_league_ids)

    d_departures = LEAGUE_CAPACITY["D"] - len(D)
    created_characters = []
    if pending_characters:
        creation_pool = [ind for ind in (A + B + C + D) if not ind.retired]
        eligible_creation_masters = [ind for ind in creation_pool if ind.age >= MASTER_MIN_AGE] or creation_pool
        created_characters = [
            _build_character_creation_individual(
                req, season, i,
                master=_pick_master(eligible_creation_masters, titleholder_ids, allow_new_founder=False),
            )
            for i, req in enumerate(pending_characters)
        ]
        D.extend(created_characters)

    new_disciples = []
    remaining_slots = d_departures - len(created_characters)
    if remaining_slots > 0:
        candidates = A + B + C + D
        new_disciples = generate_disciples(
            count=remaining_slots, season=season, pool=candidates, name_registry=name_registry,
            titleholder_ids=titleholder_ids,
        )
        D.extend(new_disciples)
    new_disciples = created_characters + new_disciples

    # Dリーグの定員超過調整（Elo下位をカットして引退扱い。タイトル保持者・朱雀紅白リーグ
    # 在籍者・今季参入したキャラクリ個体はカット対象から除外。
    # Dより下のリーグはないため、ここだけは引退させるしかない）
    created_character_ids = {ind.id for ind in created_characters}
    d_cut_exempt_ids = protected_ids | created_character_ids

    def _cut_overflow(members, capacity):
        if len(members) <= capacity:
            return members, []
        protected = [ind for ind in members if ind.id in d_cut_exempt_ids]
        cuttable = sorted(
            (ind for ind in members if ind.id not in d_cut_exempt_ids),
            key=lambda ind: ind.elo, reverse=True,
        )
        shortage = len(members) - capacity
        keep_cuttable = cuttable[:max(0, len(cuttable) - shortage)]
        over = cuttable[max(0, len(cuttable) - shortage):]
        for ind in over:
            ind.retired = True
        return protected + keep_cuttable, over

    D, d_over_retired = _cut_overflow(D, LEAGUE_CAPACITY["D"])

    new_rosters = {"A": A, "B": B, "C": C, "D": D}
    return new_rosters, new_disciples, name_registry, d_over_retired


MASTER_MIN_AGE = 30  # 師匠になれる最低年齢（師匠より年下の弟子が生まれないようにするため）
AWAKENED_INITIAL_AGE_RANGE = (14, 16)  # 覚醒個体の参入年齢（通常は18〜24歳）

# 一門イベントの確率（新弟子1人あたり）。
# 旧数値（0.03/0.01）だと一門がほぼ集約されてしまったため、分岐・新規開祖とも頻度を上げた。
# 新規開祖はさらに0.03→0.08に再度引き上げ（強い一門の弟子ばかりが生き残る展開を防ぎ、
# 新規参入の血統がもっと混ざるようにするため）
CLAN_BRANCH_CHANCE = 0.08       # 師匠の弟子になるが、本人が新しい一門の開祖として分岐する
CLAN_NEW_FOUNDER_CHANCE = 0.08  # 師匠を持たず、完全ランダムな能力の新規開祖として参入する


def _master_weight(ind, titleholder_ids):
    """師匠として選ばれやすさの重み。Eloが高いほど、タイトルを保持しているほど選ばれやすくする
    （強い一門ほど自然と子孫を残しやすくなる。ベースは1.0なので誰にでもチャンスはある）。
    旧数値（÷200, +3.0）だと強い一門への集約が急激すぎたため、やや緩めた"""
    elo_bonus = max(0.0, (ind.elo - 1500) / 300)
    title_bonus = 2.0 if ind.id in titleholder_ids else 0.0
    return 1.0 + elo_bonus + title_bonus


def _pick_master(eligible_masters, titleholder_ids, allow_new_founder=True):
    """師匠を自動選出する（Elo・タイトル保持で重み付けした抽選）。
    allow_new_founder=Trueの場合、CLAN_NEW_FOUNDER_CHANCEの確率で師匠を持たない
    新規開祖になる（通常の新弟子生成と同じ）。Falseの場合は必ず誰かの弟子になる
    （候補が1人もいない極端なケースのみ例外的にNoneを返す）"""
    if not eligible_masters:
        return None
    if allow_new_founder and random.random() < CLAN_NEW_FOUNDER_CHANCE:
        return None
    weights = [_master_weight(ind, titleholder_ids) for ind in eligible_masters]
    return random.choices(eligible_masters, weights=weights, k=1)[0]


# キャラクリエイト機能：サイトから指定されたタイプ傾向に応じて、該当パラメータの
# 抽選レンジを引き上げる（強制はせず、あくまで緩やかな傾向づけにとどめる）。
# タイプ傾向の代わりに、サイト側で直接8パラメータを割り振った場合（params）はそちらを優先する
CHARACTER_TYPE_BOOST_KEYS = {
    "balanced": [],
    "aggressive": ["mobility_weight", "frontier_weight"],
    "defensive": ["edge_stability_weight", "danger_zone_weight"],
    "corner": ["corner_weight", "center_weight"],
}

CHARACTER_PARAM_MIN = 0.1
CHARACTER_PARAM_MAX = 10.0
CHARACTER_PARAM_BUDGET = 40.0  # 8パラメータ合計の上限（サイト側の割り振りUIと一致させる）

# キャラクリエイト個体は、通常の新弟子（3%）よりも少しだけ覚醒しやすくする
CHARACTER_CREATION_AWAKENING_CHANCE = 0.05


def _character_creation_params(type_tendency):
    boosted = CHARACTER_TYPE_BOOST_KEYS.get(type_tendency, [])
    return {
        k: round(random.uniform(2.5, 6.0) if k in boosted else random.uniform(0.5, 5.0), 3)
        for k in PARAM_KEYS
    }


def _character_creation_params_from_custom(custom_params):
    """サイト側で直接割り振られた8パラメータを検証・正規化する。
    値の範囲・合計予算を超えていた場合は、比率を保ったまま予算内に収める"""
    values = {}
    for k in PARAM_KEYS:
        v = custom_params.get(k)
        if not isinstance(v, (int, float)):
            return None
        values[k] = max(CHARACTER_PARAM_MIN, min(CHARACTER_PARAM_MAX, float(v)))
    total = sum(values.values())
    if total > CHARACTER_PARAM_BUDGET:
        scale = CHARACTER_PARAM_BUDGET / total
        values = {k: v * scale for k, v in values.items()}
    return {k: round(v, 3) for k, v in values.items()}


def _build_character_creation_individual(request, season, index, master=None):
    """キャラクリエイトのリクエスト（{"name":, "type":, "params":, "awakened_param":}）から
    1体生成する。paramsが指定されていればそれを優先し、無ければtypeに応じたランダム
    生成にフォールバックする。

    この関数は新人リーグ参入時（新人リーグの対局に使う仮の個体を作る時）と、本戦での
    実際のDリーグ参入時（新人リーグの勝者を正式な個体にする時）の2回呼ばれ得る。
    params・awakened_paramは初回（新人リーグ参入時）にのみ決定し、requestに
    "awakened_param"キーが無い場合だけ抽選する。2回目の呼び出し時はrequest側に
    その時決定した値がそのまま入っているため再抽選せず、新人リーグで戦った個体と
    実際にDリーグへ参入する個体が食い違わないようにする。

    masterが指定された場合、通常の新弟子生成と同様にその個体の弟子として参入する
    （parent_a_id）。paramsは師匠から継承せず、あくまで本人の指定/抽選値を使う
    （キャラクリエイトの趣旨は本人の狙った性能で参戦することのため）。分岐
    （CLAN_BRANCH_CHANCEの確率で自分が新しい一門の開祖になる）は通常の新弟子と同じ"""
    ind_id = f"CC{season}-{index:03d}"
    custom_params = request.get("params")
    params = (_character_creation_params_from_custom(custom_params) if isinstance(custom_params, dict) else None) \
        or _character_creation_params(request.get("type", "balanced"))

    if "awakened_param" in request:
        awakened_key = request["awakened_param"]
    else:
        params, awakened_key = maybe_awaken(params, individual_id=ind_id, chance=CHARACTER_CREATION_AWAKENING_CHANCE)

    master_id = master.id if master is not None else None
    if master is not None:
        clan_root_id = ind_id if random.random() < CLAN_BRANCH_CHANCE else master.clan_root_id
    else:
        clan_root_id = ind_id

    ind = LeagueIndividual(
        ind_id, "D", params=params, generation=0,
        parent_a_id=master_id, parent_b_id=None,
        display_name=request.get("name") or ind_id, clan_root_id=clan_root_id,
        initial_age=random.randint(*AWAKENED_INITIAL_AGE_RANGE) if awakened_key else None,
    )
    ind.awakened_param = awakened_key
    ind.volatility = round(max(0.1, min(3.0, random.uniform(0.3, 2.0))), 2)
    return ind


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

        master = _pick_master(eligible_masters, titleholder_ids, allow_new_founder=True)

        if master:
            params, gen = mutate_params(master)
            master_id = master.id
            clan_root_id = ind_id if random.random() < CLAN_BRANCH_CHANCE else master.clan_root_id
        else:
            # 新規開祖は、既存の一門（世代を重ねて強化されてきた血統）に対抗できるよう、
            # 旧レンジ（0.5〜5.0、平均2.75）よりやや強めのベースラインで生成する
            params = {k: round(random.uniform(1.0, 7.0), 3) for k in PARAM_KEYS}
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
