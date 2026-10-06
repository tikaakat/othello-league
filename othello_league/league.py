import random

from .individual import LeagueIndividual
from .buffs import maybe_awaken
from .names import NameRegistry

# リーグの定員（Dリーグは「定員」ではなく、強制引退を起こさないための大きめのバッファ。
# 下記D_NEWCOMER_INTAKEによる固定枠の受け入れを人数に関わらず続けられるよう、
# 実際の在籍者数より十分大きい値にしてある）
LEAGUE_CAPACITY = {"A": 9, "B": 12, "C": 16, "D": 60}

# 昇降格の定員設定
A_TO_B_RELEGATE = 2  # A→B降格人数（＝B→A昇格人数）
B_TO_C_RELEGATE = 3  # B→C降格人数（＝C→B昇格人数）
D_TO_C_PROMOTE = 3   # D→C昇格人数（C→D降格人数と揃え、Cリーグの定員超過を防ぐ）

RETIREMENT_AGE = 60
D_DEMOTION_POINT_LIMIT = 2  # Dリーグでの降級点がこの数に達すると強制引退（連続でなくてもよい）
C_TO_D_RELEGATE = 3  # C→D降格人数（B→Cと同数）

# Dリーグの新人受け入れ枠（新人リーグの募集人数）。
# 以前は「定員に対する欠員数」から算出しており、在籍者が増えて欠員が無くなると
# 既存メンバーをElo下位から強制引退させてまで枠を確保していた。これだと一季の間に
# まとまった人数が不自然に引退してしまう上、在籍者数が増え続けると引退者も増え続ける
# 歪な構造になるため、在籍者数に関わらず毎季必ずこの人数だけを固定で受け入れる方式に変更した
# （在籍者数は増減してよく、それ自体で強制引退を発生させることはない）
D_NEWCOMER_INTAKE = 2
D_INITIAL_ROSTER_SIZE = 16  # ブートストラップ（初年度）時点でのDリーグ初期人数

PARAM_KEYS = [
    "corner_weight", "danger_zone_weight", "mobility_weight", "edge_stability_weight",
    "frontier_weight", "disc_weight", "parity_weight", "center_weight",
]


def relegate_and_retire(rosters, season, titleholders=None, suzaku_league_ids=None,
                         extra_protected_ids=None, demotion_relief_ids=None):
    """
    1シーズンの対局終了後の昇降格・強制引退の判定のみを行う（Dリーグの新規補充は
    recruit_d_league()が別関数として、次シーズンの対局が始まる前に行う）。

    以前はこの関数の中でDリーグの新規補充（新人リーグの勝者・自動生成の新弟子）まで
    一括で行っており、その季の対局後に新弟子が参入する形だった。
    補充処理を次シーズン開始前（対局前）に切り出すことで、新人リーグの勝者は
    新人リーグが対象とした季からそのまま出走できるようになる
    （募集人数自体はD_NEWCOMER_INTAKEの固定値で、在籍者数や欠員には左右されない）。

    suzaku_league_idsが与えられた場合、朱雀紅白リーグに在籍中の個体は、タイトル保持者と
    同様に強制引退（年齢・Dリーグ降級点）の対象から除外する。
    予選を勝ち抜いて来季から紅白リーグに加入する個体も、その加入前の季にこれらの条件で
    引退させてしまうと来季の紅白リーグに欠員が生じるため、同様に保護する
    （C〜Aリーグの定員超過による「降格」は引退ではなく在籍リーグが変わるだけなので対象外）。

    extra_protected_idsが与えられた場合も同様に保護する。この季の各タイトル戦の
    挑戦者（青龍・朱雀・白虎・玄武、最大4名）が対象。挑戦するところまで勝ち上がったのに、
    同じ季のうちに引退させてしまうのを防ぐため（勝って在位者になればtitleholder_idsの
    保護に引き継がれる）。

    demotion_relief_idsが与えられた場合、Dリーグ降級点の判定（下記）で対象者の降級点を
    1点減らす（0未満にはしない）。この季の白虎トーナメント出場者（16名）・玄武ブロック
    優勝者（8名）が対象。挑戦者ほどの重みではないため、強制引退からの完全な免除
    （extra_protected_ids）ではなく、勝ち越しと同じ「減点」による救済にとどめる。

    戻り値: (更新後のrosters dict, 引退者リスト, 来季のDリーグ新人受け入れ枠数
             （D_NEWCOMER_INTAKEの固定値）, 降級点イベント（individual_id => "gained"/"cleared"）)
    """
    A = list(rosters["A"])
    B = list(rosters["B"])
    C = list(rosters["C"])
    D = list(rosters["D"])

    # --- 60歳以上、かつ無冠（どのタイトルも持っていない）の個体は強制引退させる。
    #     タイトルを1つでも持っていれば、全て失冠するまで猶予が続く。
    #     朱雀紅白リーグ在籍者（来季から加入予定の予選通過者も含む）・この季のタイトル戦
    #     挑戦者・白虎トーナメント出場者・玄武ブロック優勝者（extra_protected_ids）も、
    #     在籍中（または対象になった季）は年齢引退に加えて、Dリーグ降級点による引退も
    #     免除する（でないと紅白リーグ加入前や、挑戦するところまで勝ち上がった
    #     直後に引退させてしまうため） ---
    titleholder_ids = set()
    if titleholders:
        for info in titleholders.values():
            if info and info.get("id"):
                titleholder_ids.add(info["id"])

    protected_ids = set(titleholder_ids)
    if suzaku_league_ids:
        protected_ids.update(suzaku_league_ids)
    if extra_protected_ids:
        protected_ids.update(extra_protected_ids)

    # Aリーグ所属による年齢引退免除：ただし「今季Aから降格する個体」はこの免除を受けない
    # （降格が決まった季に60歳以上かつ他の免除条件も無ければ、その季に引退する）。
    # ab_nの本来の算出（109行目）は年齢引退フィルタ後のA/B人数を使うが、ここでは
    # フィルタ前の人数で仮に算出する（A_TO_B_RELEGATEはA・Bの人数に対して十分小さく、
    # 年齢引退で後から人数が減っても降格人数が変わることは実質無い）
    ab_n_for_protection = min(A_TO_B_RELEGATE, len(A), len(rosters["B"]))
    a_relegating_ids = {ind.id for ind in A[-ab_n_for_protection:]} if ab_n_for_protection > 0 else set()
    protected_ids.update(ind.id for ind in A if ind.id not in a_relegating_ids)

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

    # 降級点の増減（結果タブ・リーグタブへの「点」「消」表示用）。
    # individual_id => "gained"（この季に降級点が1つ増えた） / "cleared"（0に戻った。
    # Dを卒業した際のリセット・減点が貯まり分を相殺しきった場合のいずれでも「消」扱いにする）
    demotion_events = {}

    # Dリーグを卒業（Cへ昇格）する個体は、降級点をリセットする。
    # 降級点はDリーグ在籍中の成績のみを反映すべきものなので、
    # リセットしないと「昔Dにいた時の降級点」が残ったまま何季も引き継がれ、
    # 何季も後にDへ舞い戻った際に、既に引退間際の状態で再出発することになってしまう。
    for ind in d_promote_to_c:
        if ind.demotion_points > 0:
            demotion_events[ind.id] = "cleared"
        ind.demotion_points = 0

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

    # --- Dリーグ：降級点制。負け越した季ごとに降級点が1つ積み重なり（連続していなくてもよい）、
    #     2つに達したら実力・在籍年数に関わらず即引退する（ただしタイトル保持者・朱雀紅白
    #     リーグ在籍者・extra_protected_idsは、age_retiredと同様に猶予対象）。
    #     以前は「2季連続で負け越した場合のみ」引退としていたが、勝ち越しを挟むと
    #     カウンタがリセットされてしまい、長期的に負け越しがちな個体がいつまでも
    #     居座れる不備があったため、点として積み重ねる方式に変更した。
    #     ただし積み上がる一方では救いが無さすぎるため、勝ち越した季・
    #     demotion_relief_ids該当（白虎トーナメント出場・玄武ブロック優勝）の季は
    #     それぞれ1点減らす（複数該当すれば重複して減らしてよい。0未満にはしない）。
    #     この判定は「今季も引き続きDに在籍していた個体（d_remain）」のみを対象にする。
    #     今季Cから降格してきた個体（c_relegate_to_d・c_relegate_overflow）は、
    #     まだDでの対局実績が無い（直前の成績はC所属時のもの）ため対象外とし、
    #     降級点を0にリセットして「Dでの降級点」を来季以降ゼロから数え直す。 ---
    demotion_relief_ids = demotion_relief_ids or set()
    d_up_or_out_retired = []
    d_keep = []
    for ind in d_remain:
        before = ind.demotion_points
        delta = 0
        if ind.loss_this_season > ind.win_this_season:
            delta += 1
        elif ind.win_this_season > ind.loss_this_season:
            delta -= 1
        if ind.id in demotion_relief_ids:
            delta -= 1
        ind.demotion_points = max(0, before + delta)
        if ind.demotion_points > before:
            demotion_events[ind.id] = "gained"
        elif ind.demotion_points == 0 and before > 0:
            demotion_events[ind.id] = "cleared"
        if ind.demotion_points >= D_DEMOTION_POINT_LIMIT and ind.id not in protected_ids:
            ind.retired = True
            ind.total_seasons += 1  # age_retiredと同様、引退する今季分も在籍シーズン数に数える
            d_up_or_out_retired.append(ind)
        else:
            d_keep.append(ind)

    new_d_arrivals = c_relegate_to_d + c_relegate_overflow
    for ind in new_d_arrivals:
        ind.demotion_points = 0
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

    # Dリーグの新人受け入れ枠は在籍者数や定員に関わらず毎季固定（D_NEWCOMER_INTAKE）。
    # 以前はここで在籍者数が定員（旧20名）に近づくとElo下位を強制引退させて枠を
    # 確保していたが、一季でまとまった引退者が出てしまう・在籍者が増えるほど
    # 引退者も増えるという歪みがあったため撤廃した。在籍者数は自然な引退
    # （年齢・降級点up-or-out）の分だけ増減し、それ以上の強制調整は行わない
    new_recruit_slots = D_NEWCOMER_INTAKE

    new_rosters = {"A": A, "B": B, "C": C, "D": D}
    return new_rosters, all_retired, new_recruit_slots, demotion_events


def recruit_d_league(rosters, season, name_registry=None, pending_characters=None, titleholders=None,
                      suzaku_league_ids=None, auto_fill_vacancy=True):
    """
    Dリーグの新人受け入れ（D_NEWCOMER_INTAKEの固定枠数）を、次シーズンの対局が
    始まる前に行う。まずキャラクリエイト・新人リーグ経由のリクエスト
    （pending_characters）を優先的に参入させ、残り枠のみ通常の新弟子生成で埋める。
    キャラクリエイト個体も通常の新弟子と同様、必ず既存個体の中から師匠が自動選出される
    （本人が選ぶわけではない。新規開祖にはならないが、分岐で新しい一門の開祖になることはある）。
    pending_charactersの人数が固定枠数を上回っても（新人リーグの募集人数のズレなど）、
    全員そのまま参入させる（既存メンバーを強制引退させて枠を確保することはしない）。

    auto_fill_vacancy=Falseの場合、pending_charactersで埋まらない残り枠は
    自動生成の新弟子で埋めない（ブートストラップ第1季用。少人数スタートの意図を
    対局前に自動で埋めてしまわないため）。
    戻り値: (更新後のrosters dict, 新規参入者リスト, 更新後のname_registry, 引退者リスト
             （常に空リスト。新人受け入れ自体による強制引退は発生しないため、
              呼び出し側との互換のためだけに残している）)
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

    d_departures = D_NEWCOMER_INTAKE
    created_characters = []
    if pending_characters:
        creation_pool = [ind for ind in (A + B + C + D) if not ind.retired]
        eligible_creation_masters = [ind for ind in creation_pool if ind.age >= MASTER_MIN_AGE] or creation_pool
        disciple_counts = count_existing_disciples(creation_pool)
        created_characters = []
        for i, req in enumerate(pending_characters):
            # 通常は新人リーグのロスターを組む時点で師弟関係が既に決まっている
            # （reqに"parent_a_id"が入っている）。入っていない場合（新人リーグを
            # 経由しないpending_characters.json経由の直接登録など）のみ、ここで初めて師匠を選ぶ
            if "parent_a_id" in req:
                master = None
            else:
                master = _pick_master(eligible_creation_masters, titleholder_ids, allow_new_founder=False)
            created_characters.append(
                _build_character_creation_individual(req, season, i, master=master, disciple_counts=disciple_counts)
            )
        D.extend(created_characters)

    new_disciples = []
    remaining_slots = d_departures - len(created_characters)
    if auto_fill_vacancy and remaining_slots > 0:
        candidates = A + B + C + D
        new_disciples = generate_disciples(
            count=remaining_slots, season=season, pool=candidates, name_registry=name_registry,
            titleholder_ids=titleholder_ids,
        )
        D.extend(new_disciples)
    new_disciples = created_characters + new_disciples

    new_rosters = {"A": A, "B": B, "C": C, "D": D}
    return new_rosters, new_disciples, name_registry, []


MASTER_MIN_AGE = 30  # 師匠になれる最低年齢（師匠より年下の弟子が生まれないようにするため）
AWAKENED_INITIAL_AGE_RANGE = (14, 16)  # 覚醒個体の参入年齢（通常は18〜24歳）

# 一門イベントの確率・基準（新弟子1人あたり）。
# 新規開祖は0.03→0.08に引き上げ（強い一門の弟子ばかりが生き残る展開を防ぎ、
# 新規参入の血統がもっと混ざるようにするため）
CLAN_NEW_FOUNDER_CHANCE = 0.08  # 師匠を持たず、完全ランダムな能力の新規開祖として参入する
# 一門の分岐は確率ではなく、師匠の弟子人数で決める：弟子がこの人数を超えたら
# （6人目以降）、本人が新しい一門の開祖として分岐する
CLAN_BRANCH_DISCIPLE_THRESHOLD = 5


def count_existing_disciples(pool):
    """poolに含まれる個体について、師匠ID（parent_a_id）ごとの現在の弟子数を数える。
    一門分岐判定（_resolve_clan_root）の初期値として使う"""
    counts = {}
    for ind in pool:
        if ind.parent_a_id:
            counts[ind.parent_a_id] = counts.get(ind.parent_a_id, 0) + 1
    return counts


def _resolve_clan_root(master, ind_id, disciple_counts):
    """新弟子の一門を決める：disciple_counts（{師匠ID: 弟子人数}、呼び出し元が
    バッチ生成全体で使い回す可変dict）を見て、師匠の弟子が既にCLAN_BRANCH_DISCIPLE_THRESHOLD
    人を超えていれば、本人を新しい一門の開祖にする（新弟子をdisciple_countsに加算するのも
    この関数の責務。同じバッチ内で同じ師匠に複数の新弟子が割り当たる場合も正しく積算される）"""
    count = disciple_counts.get(master.id, 0)
    disciple_counts[master.id] = count + 1
    if count >= CLAN_BRANCH_DISCIPLE_THRESHOLD:
        return ind_id
    return master.clan_root_id


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


def _build_character_creation_individual(request, season, index, master=None, disciple_counts=None):
    """キャラクリエイトのリクエスト（{"name":, "type":, "params":, "awakened_param":}）から
    1体生成する。paramsが指定されていればそれを優先し、無ければtypeに応じたランダム
    生成にフォールバックする。

    この関数は新人リーグのロスターを組む時点（新人リーグの対局に使う個体を作る時）と、
    本戦での実際のDリーグ参入時（新人リーグの勝者を正式な個体にする時）の2回呼ばれ得る。
    師弟関係（parent_a_id・clan_root_id・generation）とparams・awakened_param・initial_age・
    volatilityは初回（新人リーグのロスターを組む時点）にのみ決定する。requestに既に
    "parent_a_id"キーがある場合はその時決定した値をそのまま使い、再抽選しない
    （新人リーグで戦った個体と実際にDリーグへ参入する個体が食い違わないようにする。
    なお新人リーグを経由しないpending_characters.json経由の直接登録など、師弟関係が
    まだ決まっていないリクエストに対しては、ここで初めてmasterから決定する）。

    masterが指定された場合、通常の新弟子生成と同様にその個体の弟子として参入する
    （parent_a_id）。paramsは師匠から継承せず、あくまで本人の指定/抽選値を使う
    （キャラクリエイトの趣旨は本人の狙った性能で参戦することのため）。一門分岐は
    disciple_countsを渡せば_resolve_clan_root()（弟子人数ベース）で判定し、
    渡さない場合は分岐しない（常にmaster.clan_root_idに参加する）"""
    ind_id = f"CC{season}-{index:03d}"

    if "parent_a_id" in request:
        # 新人リーグのロスターを組む時点で既に師弟関係・パラメータ等が決まっている
        params = request["params"]
        master_id = request.get("parent_a_id")
        clan_root_id = request.get("clan_root_id") or ind_id
        generation = request.get("generation", 0)
        volatility = request.get("volatility")
        awakened_key = request.get("awakened_param")
    else:
        custom_params = request.get("params")
        params = (_character_creation_params_from_custom(custom_params) if isinstance(custom_params, dict) else None) \
            or _character_creation_params(request.get("type", "balanced"))
        params, awakened_key = maybe_awaken(params, individual_id=ind_id, chance=CHARACTER_CREATION_AWAKENING_CHANCE)

        master_id = master.id if master is not None else None
        if master is not None:
            clan_root_id = _resolve_clan_root(master, ind_id, disciple_counts) if disciple_counts is not None \
                else master.clan_root_id
        else:
            clan_root_id = ind_id
        generation = 0
        volatility = round(max(0.1, min(3.0, random.uniform(0.3, 2.0))), 2)

    # initial_ageが明示されていれば最優先で使う（新人リーグで持ち越し中の候補が、
    # 前回までの挑戦で重ねた年齢のまま再挑戦・参入する場合）。無指定なら従来通り、
    # 覚醒していれば若い年齢層、していなければLeagueIndividualの既定（18〜24歳）で抽選する
    if "initial_age" in request and request["initial_age"] is not None:
        resolved_initial_age = request["initial_age"]
    elif awakened_key:
        resolved_initial_age = random.randint(*AWAKENED_INITIAL_AGE_RANGE)
    else:
        resolved_initial_age = None

    ind = LeagueIndividual(
        ind_id, "D", params=params, generation=generation,
        parent_a_id=master_id, parent_b_id=None,
        display_name=request.get("name") or ind_id, clan_root_id=clan_root_id,
        initial_age=resolved_initial_age,
    )
    ind.awakened_param = awakened_key
    ind.volatility = volatility if volatility is not None else round(max(0.1, min(3.0, random.uniform(0.3, 2.0))), 2)
    return ind


def generate_disciples(count, season, pool, name_registry, titleholder_ids=frozenset()):
    """
    新弟子（Dリーグ参入個体）を生成する。師弟関係のため、親（師匠）は常に1人。
    師匠はElo・タイトル保持で重み付けした抽選で選ばれる（強い一門ほど子孫を残しやすい）。
    師匠の弟子がCLAN_BRANCH_DISCIPLE_THRESHOLD人を超えると「分岐」（弟子ではあるが
    新しい一門の開祖になる）が起き、稀に「新規開祖」（師匠を持たず完全ランダムな
    能力で参入する）も起きる。
    """
    disciples = []

    active_pool_all = [ind for ind in pool if not ind.retired]
    # 師匠になれるのは一定年齢以上の個体のみ（該当者が誰もいない序盤などは制限なしにフォールバック）
    eligible_masters = [ind for ind in active_pool_all if ind.age >= MASTER_MIN_AGE] or active_pool_all
    disciple_counts = count_existing_disciples(active_pool_all)

    for i in range(count):
        ind_id = f"D{season}-{i:03d}"
        display_name = name_registry.generate()

        master = _pick_master(eligible_masters, titleholder_ids, allow_new_founder=True)

        if master:
            params, gen = mutate_params(master)
            master_id = master.id
            clan_root_id = _resolve_clan_root(master, ind_id, disciple_counts)
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
